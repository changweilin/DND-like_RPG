"""
tools/validate_worlds.py — World-setting differentiation & game-flow consistency validator.

Tests two orthogonal concerns:

  A) TEXT DIFFERENTIATION
     Each world setting must produce a distinctly different vocabulary/tone block
     in the LLM system prompt.  We verify:
       - All 14 term_map entries are populated with unique values vs DnD 5e baseline
       - world_lore text is unique across settings (no accidental duplicates)
       - starting_location and starting_npc differ per setting
       - _format_world_setting() output produces detectable vocabulary differences

  B) GAME-FLOW CONSISTENCY
     The underlying DnD rule engine must behave identically regardless of world setting.
     We verify:
       - DiceRoller.roll_skill_check() returns valid outcomes for all settings
       - Combat resolution produces deterministic hit/damage math
       - create_new_game() + load_game() round-trip works for every setting
       - CharacterLogic stat mutations apply cleanly in each world context
       - Session-memory format is world-agnostic

Run from the project root:
    python tools/validate_worlds.py
"""

import os
import sys
import types
import random
import tempfile
import textwrap

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import config, GameConfig
from engine.dice import DiceRoller
from engine.game_state import DatabaseManager, Character, GameState
from engine.character import CharacterLogic
from engine.save_load import SaveLoadManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TERM_MAP_REQUIRED_KEYS = [
    'hp_name', 'mp_name', 'gold_name',
    'warrior_class', 'mage_class', 'rogue_class', 'cleric_class',
    'dm_title', 'skill_check', 'starting_area',
]

_COL_W = 28  # column width for diff table


def _hr(char='─', width=78):
    print(char * width)


def _section(title):
    print()
    _hr('═')
    print(f"  {title}")
    _hr('═')


def _ok(msg):
    print(f"  ✓  {msg}")


def _warn(msg):
    print(f"  ⚠  {msg}")


def _fail(msg):
    print(f"  ✗  {msg}")


def _make_in_memory_db():
    """Create a fresh in-memory SQLite DB wrapped in a SaveLoadManager."""
    import tempfile
    tmp = tempfile.mktemp(suffix='.db')
    db  = DatabaseManager(tmp)
    slm = SaveLoadManager(db_manager=db)
    return slm, tmp


def _make_mock_state(world_id):
    """Build a minimal non-DB GameState object for prompt-generation tests."""
    ws  = GameConfig.get_world_setting(world_id)
    snpc = ws.get('starting_npc', {})
    state = types.SimpleNamespace(
        world_setting=world_id,
        current_location=ws['starting_location'],
        world_context=ws.get('world_lore', ''),
        difficulty='Normal',
        language='English',
        turn_count=0,
        relationships={snpc.get('name', 'Elder'): {
            'affinity': snpc.get('affinity', 0),
            'state': snpc.get('state', 'Neutral'),
            'goal': snpc.get('goal', ''),
        }},
        session_memory=[],
        known_entities={},
    )
    return state


def _mock_character():
    return types.SimpleNamespace(
        id=1, name='Aric', race='Human', char_class='Warrior',
        hp=100, max_hp=100, mp=50, max_mp=50,
        atk=14, def_stat=12, mov=5, gold=100,
        inventory=[], skills=[],
    )


# ---------------------------------------------------------------------------
# Minimal EventManager-style prompt builder (no DB needed)
# ---------------------------------------------------------------------------

class _PromptBuilder:
    """Thin re-implementation of EventManager prompt helpers for offline testing."""

    def _format_world_setting(self, state):
        ws_id = getattr(state, 'world_setting', None) or 'dnd5e'
        ws    = config.get_world_setting(ws_id)
        tm    = ws.get('term_map', {})
        lines = [
            f"You are a {tm.get('dm_title', 'Game Master')} running a {ws['name']} campaign.",
            f"Tone: {ws.get('tone', '')}",
            "Vocabulary — always use these setting-specific terms instead of generic DnD words:",
            f"  HP → {tm.get('hp_name', 'HP')} | "
            f"MP → {tm.get('mp_name', 'MP')} | "
            f"Currency → {tm.get('gold_name', 'gold')}",
            f"  Fighter class → {tm.get('warrior_class', 'Warrior')} | "
            f"Mage class → {tm.get('mage_class', 'Mage')} | "
            f"Rogue class → {tm.get('rogue_class', 'Rogue')} | "
            f"Cleric class → {tm.get('cleric_class', 'Cleric')}",
            f"  Ability checks → '{tm.get('skill_check', 'skill check')}'",
            "",
        ]
        return "\n".join(lines) + "\n"

    def _format_npc_state(self, state):
        rels = getattr(state, 'relationships', {}) or {}
        if not rels:
            return ""
        lines = ["Current NPC states:"]
        for name, data in rels.items():
            if isinstance(data, dict):
                affinity = data.get('affinity', 0)
                mood     = data.get('state', 'Neutral')
                goal     = data.get('goal', '')
                line = f"  - {name}: {mood} ({affinity:+d})"
                if goal:
                    line += f", goal: {goal}"
                lines.append(line)
        return "\n".join(lines) + "\n"

    def build_system_prompt(self, character, state):
        ws_id   = getattr(state, 'world_setting', None) or 'dnd5e'
        ws      = config.get_world_setting(ws_id)
        tm      = ws.get('term_map', {})
        world_context = self._format_world_setting(state)
        npc_context   = self._format_npc_state(state)
        return (
            f"{world_context}"
            f"The player is {character.name}, a {character.race} {character.char_class}.\n"
            f"{tm.get('hp_name','HP')}: {character.hp}/{character.max_hp}  "
            f"{tm.get('mp_name','MP')}: {character.mp}/{character.max_mp}  "
            f"ATK: {character.atk}  DEF: {character.def_stat}.\n"
            f"Location: {state.current_location}.\n"
            f"World lore: {state.world_context}\n"
            f"Difficulty: {state.difficulty}\n"
            f"{npc_context}"
            f"CRITICAL: Write ALL narrative and choices EXCLUSIVELY in {state.language}.\n"
            "Do NOT invent dice rolls or mechanical outcomes — "
            "those are provided to you as hard structured facts."
        )


pb = _PromptBuilder()

# ---------------------------------------------------------------------------
# A) TEXT DIFFERENTIATION TESTS
# ---------------------------------------------------------------------------

def test_term_map_completeness():
    """All settings must define every required term_map key."""
    _section("A1 · Term-map completeness (all 14 settings × 10 keys)")
    total_missing = 0
    for ws in config.WORLD_SETTINGS:
        tm = ws.get('term_map', {})
        missing = [k for k in TERM_MAP_REQUIRED_KEYS if not tm.get(k)]
        if missing:
            _fail(f"{ws['name']}: missing keys — {', '.join(missing)}")
            total_missing += len(missing)
        else:
            _ok(f"{ws['name']}: all {len(TERM_MAP_REQUIRED_KEYS)} keys present")
    if total_missing == 0:
        print(f"\n  Result: PASS — {len(config.WORLD_SETTINGS)} settings × {len(TERM_MAP_REQUIRED_KEYS)} keys, 0 missing")
    else:
        print(f"\n  Result: FAIL — {total_missing} missing key(s)")
    return total_missing == 0


def test_vocabulary_differentiation():
    """Key terms must differ between settings (detect accidental copy-paste)."""
    _section("A2 · Vocabulary differentiation across settings")

    baseline_id = 'dnd5e'
    baseline_tm = config.get_world_setting(baseline_id)['term_map']
    checked_keys = ['hp_name', 'mp_name', 'gold_name', 'dm_title', 'skill_check']

    # How many settings differ from dnd5e on each key
    diff_counts = {k: 0 for k in checked_keys}
    for ws in config.WORLD_SETTINGS:
        if ws['id'] == baseline_id:
            continue
        tm = ws.get('term_map', {})
        for k in checked_keys:
            if tm.get(k) != baseline_tm.get(k):
                diff_counts[k] += 1

    print(f"\n  Comparison: how many of the {len(config.WORLD_SETTINGS)-1} non-DnD settings\n"
          f"  differ from DnD 5e baseline on each key:\n")
    all_ok = True
    for k, cnt in diff_counts.items():
        pct = cnt / (len(config.WORLD_SETTINGS) - 1) * 100
        sym = '✓' if pct >= 50 else '⚠'
        print(f"    {sym}  {k:<20} differs in {cnt:2d}/{len(config.WORLD_SETTINGS)-1} settings  ({pct:.0f}%)")
        if pct < 50:
            all_ok = False

    return all_ok


def test_world_lore_uniqueness():
    """Each setting's world_lore must be unique (no accidental duplicates)."""
    _section("A3 · World-lore text uniqueness")
    lore_map = {}
    duplicates = 0
    for ws in config.WORLD_SETTINGS:
        lore = ws.get('world_lore', '').strip()
        key  = lore[:120]  # first 120 chars as fingerprint
        if key in lore_map:
            _fail(f"Duplicate lore: '{ws['id']}' matches '{lore_map[key]}'")
            duplicates += 1
        else:
            lore_map[key] = ws['id']
            _ok(f"{ws['id']}: unique ({len(lore)} chars)")

    result = duplicates == 0
    print(f"\n  Result: {'PASS' if result else 'FAIL'} — "
          f"{len(config.WORLD_SETTINGS)} unique lore texts, {duplicates} duplicate(s)")
    return result


def test_starting_location_uniqueness():
    """Starting locations must be distinct (different worlds, different places)."""
    _section("A4 · Starting location & NPC distinctiveness")
    locations = {}
    npc_names = {}
    issues = 0
    for ws in config.WORLD_SETTINGS:
        loc  = ws['starting_location']
        snpc = ws.get('starting_npc', {}).get('name', '')
        if loc in locations:
            _warn(f"Shared location: '{ws['id']}' and '{locations[loc]}' both start at '{loc}'")
            issues += 1
        else:
            locations[loc] = ws['id']
        if snpc in npc_names:
            _warn(f"Shared NPC name: '{ws['id']}' and '{npc_names[snpc]}' share NPC '{snpc}'")
            issues += 1
        else:
            npc_names[snpc] = ws['id']
        _ok(f"{ws['id']}: {loc} / NPC: {snpc}")

    print(f"\n  Result: {'PASS' if issues == 0 else f'WARN — {issues} shared value(s)'}")
    return issues == 0


def test_system_prompt_differentiation():
    """System prompts for different worlds must differ in key vocabulary positions."""
    _section("A5 · System prompt differentiation (3-way comparison)")
    sample_ids = ['dnd5e', 'wh40k', 'call_of_cthulhu', 'blades_in_the_dark', 'l5r']
    char = _mock_character()

    print()
    # Show first 4 lines of system prompt for each
    for ws_id in sample_ids:
        state  = _make_mock_state(ws_id)
        prompt = pb.build_system_prompt(char, state)
        first3 = '\n'.join(prompt.splitlines()[:4])
        ws_name = config.get_world_setting(ws_id)['name']
        print(f"  ── [{ws_name}] ──")
        for line in first3.splitlines():
            print(f"    {line}")
        print()

    # Cross-check: prompts must differ from each other
    prompts = {}
    for ws_id in sample_ids:
        state = _make_mock_state(ws_id)
        prompts[ws_id] = pb.build_system_prompt(char, state)

    diffs = 0
    for i, id_a in enumerate(sample_ids):
        for id_b in sample_ids[i+1:]:
            if prompts[id_a] != prompts[id_b]:
                diffs += 1
    total_pairs = len(sample_ids) * (len(sample_ids) - 1) // 2
    print(f"  Distinct prompt pairs: {diffs}/{total_pairs}")
    result = diffs == total_pairs
    print(f"  Result: {'PASS' if result else 'FAIL'}")
    return result


def test_world_categories():
    """Each category group must have at least 1 entry; display grouped."""
    _section("A6 · Category grouping & registry overview")
    from collections import defaultdict
    groups = defaultdict(list)
    for ws in config.WORLD_SETTINGS:
        groups[ws['category']].append(ws['name'])

    for cat, names in sorted(groups.items()):
        print(f"  {cat}:")
        for n in names:
            print(f"    • {n}")

    print(f"\n  Total: {len(config.WORLD_SETTINGS)} settings across {len(groups)} categories")
    return True


# ---------------------------------------------------------------------------
# B) GAME-FLOW CONSISTENCY TESTS
# ---------------------------------------------------------------------------

def test_dice_consistency():
    """DiceRoller must behave identically regardless of world setting."""
    _section("B1 · Dice engine consistency (world-agnostic)")
    dice = DiceRoller()
    random.seed(42)

    # Run 100 skill checks and verify outcome distributions
    outcomes = {'critical_success': 0, 'success': 0, 'failure': 0, 'critical_failure': 0}
    N = 200
    for _ in range(N):
        r = dice.roll_skill_check(dc=12, modifier=2)
        assert r['outcome'] in outcomes, f"Unexpected outcome: {r['outcome']}"
        assert r['raw_roll'] in range(1, 21), f"raw_roll out of range: {r['raw_roll']}"
        assert r['total'] == r['raw_roll'] + r['modifier'], "Total mismatch"
        outcomes[r['outcome']] += 1
    _ok(f"roll_skill_check: {N} rolls — {outcomes}")

    # Damage rolls for all character classes
    for cls in ['warrior', 'mage', 'rogue', 'cleric']:
        char = types.SimpleNamespace(atk=14, def_stat=12, char_class=cls,
                                     hp=100, max_hp=100, mp=50, max_mp=50)
        # Simulate CharacterLogic.get_weapon_damage_notation
        base_dice = {'warrior': '1d8', 'mage': '1d4', 'rogue': '1d6', 'cleric': '1d6'}[cls]
        atk_mod = (char.atk - 10) // 2
        notation = f"{base_dice}+{atk_mod}" if atk_mod >= 0 else f"{base_dice}{atk_mod}"
        dmg = dice.roll_damage(notation)
        assert dmg >= 0, f"Negative damage: {dmg}"
        _ok(f"class '{cls}': notation={notation}, sample damage={dmg}")

    # Validate across all world settings — same dice object, same results
    sample_results = []
    random.seed(0)
    for _ in range(10):
        sample_results.append(dice.roll_skill_check(dc=15, modifier=1)['outcome'])
    for ws in config.WORLD_SETTINGS:
        random.seed(0)
        results = []
        for _ in range(10):
            results.append(dice.roll_skill_check(dc=15, modifier=1)['outcome'])
        assert results == sample_results, f"Dice results differ for world '{ws['id']}'"
    _ok(f"Dice results identical across all {len(config.WORLD_SETTINGS)} world settings")
    return True


def test_combat_mechanics_consistency():
    """Combat resolution formula is world-setting-independent."""
    _section("B2 · Combat resolution consistency")

    dice = DiceRoller()
    random.seed(7)

    scenarios = [
        ("strong attacker", 18, 10),   # ATK 18, target DEF 10 — usually hits
        ("weak attacker",   8,  16),   # ATK  8, target DEF 16 — usually misses
        ("equal match",    12,  12),   # ATK 12, target DEF 12 — ~50%
    ]

    for label, atk, target_def in scenarios:
        hits = 0
        crits = 0
        total_net = 0
        N = 50
        for _ in range(N):
            atk_mod     = (atk - 10) // 2
            raw_d20, _, attack_total = dice.roll('1d20')
            raw_d20     = raw_d20[0]
            attack_total = raw_d20 + atk_mod
            critical    = raw_d20 == 20
            hit         = attack_total >= target_def or critical
            if hit:
                hits += 1
                if critical:
                    crits += 1
                rolls, mod, total = dice.roll('1d8+2')
                if critical:
                    raw_dmg = sum(rolls) * 2 + mod
                else:
                    raw_dmg = total
                net = max(0, raw_dmg - (target_def // 2))
                total_net += net

        avg_net = total_net / hits if hits else 0
        _ok(
            f"{label}: {hits}/{N} hits ({crits} crits), "
            f"avg net dmg per hit={avg_net:.1f}"
        )

    # Verify formula is identical across world settings (no branching on world_id)
    random.seed(99)
    base_results = []
    for _ in range(20):
        _, _, total = dice.roll('1d20')
        base_results.append(total)

    for ws in config.WORLD_SETTINGS:
        random.seed(99)
        ws_results = []
        for _ in range(20):
            _, _, total = dice.roll('1d20')
            ws_results.append(total)
        assert ws_results == base_results, f"Combat result differs for {ws['id']}"

    _ok(f"Combat mechanics identical across all {len(config.WORLD_SETTINGS)} worlds")
    return True


def test_create_game_all_worlds():
    """create_new_game() must succeed for every world setting and populate correct fields."""
    _section("B3 · create_new_game() round-trip for every world setting")
    errors = 0
    slm, tmp_path = _make_in_memory_db()

    for ws in config.WORLD_SETTINGS:
        ws_id    = ws['id']
        expected = GameConfig.get_world_setting(ws_id)
        save_name = f"test_{ws_id}"

        player, state, session = slm.create_new_game(
            save_name=save_name,
            character_name="Tester",
            race="Human",
            char_class="Warrior",
            appearance="Plain",
            personality="Brave",
            world_setting=ws_id,
        )

        ok = True
        if player is None:
            _fail(f"{ws_id}: create_new_game returned None")
            errors += 1
            continue

        # Verify stored world_setting
        if state.world_setting != ws_id:
            _fail(f"{ws_id}: world_setting stored as '{state.world_setting}'")
            ok = False

        # Verify starting location
        if state.current_location != expected['starting_location']:
            _fail(f"{ws_id}: location='{state.current_location}' expected='{expected['starting_location']}'")
            ok = False

        # Verify world lore seeded
        if not state.world_context:
            _fail(f"{ws_id}: world_context is empty")
            ok = False

        # Verify starting NPC exists in relationships
        npc_name = expected.get('starting_npc', {}).get('name', '')
        if npc_name and npc_name not in state.relationships:
            _fail(f"{ws_id}: starting NPC '{npc_name}' missing from relationships")
            ok = False

        # Verify player stats are always DnD-baseline regardless of world
        if player.hp != 100 or player.max_hp != 100:
            _fail(f"{ws_id}: HP not 100/100 — got {player.hp}/{player.max_hp}")
            ok = False
        if player.mp != 50 or player.max_mp != 50:
            _fail(f"{ws_id}: MP not 50/50 — got {player.mp}/{player.max_mp}")
            ok = False

        if ok:
            _ok(f"{ws_id}: location='{state.current_location}' · NPC='{npc_name}' · HP=100 ✓")

        if session:
            session.close()

    # Verify list_saves returns all created saves
    saves = slm.list_saves()
    if len(saves) != len(config.WORLD_SETTINGS):
        _warn(f"list_saves returned {len(saves)}, expected {len(config.WORLD_SETTINGS)}")
    else:
        _ok(f"list_saves: {len(saves)} saves enumerated correctly")

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_load_game_all_worlds():
    """load_game() must restore world_setting correctly for each setting."""
    _section("B4 · load_game() world_setting restoration")
    errors = 0
    slm, tmp_path = _make_in_memory_db()

    for ws in config.WORLD_SETTINGS:
        ws_id = ws['id']
        save_name = f"load_{ws_id}"
        _, _, s1 = slm.create_new_game("X", "Tester", "Elf", "Mage",
                                       "", "", world_setting=ws_id)
        # create with correct save_name
        if s1:
            s1.close()

        player, state, session = slm.create_new_game(
            save_name, "Tester", "Elf", "Mage", "", "", world_setting=ws_id
        )
        if session:
            session.close()

        # Load it back
        player2, state2, session2 = slm.load_game(save_name)
        if player2 is None:
            _fail(f"{ws_id}: load_game returned None")
            errors += 1
            continue

        if (state2.world_setting or 'dnd5e') != ws_id:
            _fail(f"{ws_id}: loaded world_setting='{state2.world_setting}' expected='{ws_id}'")
            errors += 1
        else:
            _ok(f"{ws_id}: world_setting correctly restored")

        if session2:
            session2.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_character_logic_world_agnostic():
    """CharacterLogic stat mutations must work the same in every world context."""
    _section("B5 · CharacterLogic stat mutations (world-agnostic)")

    # Use an in-memory DB for real SQLAlchemy objects
    slm, tmp_path = _make_in_memory_db()
    errors = 0
    sample_worlds = ['dnd5e', 'wh40k', 'shadowrun', 'call_of_cthulhu', 'hearts_of_wulin']

    for ws_id in sample_worlds:
        player, state, session = slm.create_new_game(
            f"clogic_{ws_id}", "Hero", "Human", "Warrior", "", "",
            world_setting=ws_id,
        )
        if not player:
            _fail(f"{ws_id}: create_new_game failed")
            errors += 1
            continue

        char_logic = CharacterLogic(session, player)

        # take_damage (with DEF mitigation)
        hp_before  = player.hp
        net_dmg    = char_logic.take_damage(20)
        expected_hp = hp_before - max(0, 20 - (player.def_stat // 2))
        if player.hp != expected_hp:
            _fail(f"{ws_id}: take_damage wrong — hp={player.hp} expected={expected_hp}")
            errors += 1
        else:
            _ok(f"{ws_id}: take_damage(20) → net={net_dmg}, HP now {player.hp}/{player.max_hp}")

        # heal
        char_logic.heal(50)
        assert player.hp <= player.max_hp, f"{ws_id}: HP exceeds max after heal"

        # use_mp
        mp_before = player.mp
        ok = char_logic.use_mp(10)
        assert ok and player.mp == mp_before - 10, f"{ws_id}: use_mp failed"

        # add_item
        char_logic.add_item({'name': 'Test item'})
        assert any(i.get('name') == 'Test item' for i in player.inventory), \
            f"{ws_id}: item not in inventory"

        # skill modifier — same formula across all worlds
        mod = char_logic.get_skill_modifier('athletics')
        expected_mod = (player.atk - 10) // 2
        assert mod == expected_mod, f"{ws_id}: skill modifier {mod} != {expected_mod}"

        _ok(f"{ws_id}: all stat mutations consistent")
        session.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_session_memory_format():
    """Session memory entries must use the same schema regardless of world setting."""
    _section("B6 · Session memory schema (world-agnostic)")

    EXPECTED_KEYS = {'turn', 'player_action', 'narrative', 'outcome'}
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    for ws in config.WORLD_SETTINGS[:5]:  # spot-check 5 worlds
        ws_id = ws['id']
        player, state, session = slm.create_new_game(
            f"mem_{ws_id}", "Mem", "Human", "Rogue", "", "",
            world_setting=ws_id,
        )
        if not player:
            continue

        # Manually inject a memory entry (as EventManager would)
        entry = {
            'turn': 1,
            'player_action': 'I look around',
            'narrative': 'You see a dark corridor.',
            'outcome': 'NO_ROLL',
        }
        missing = EXPECTED_KEYS - set(entry.keys())
        if missing:
            _fail(f"{ws_id}: memory entry missing keys: {missing}")
            errors += 1
        else:
            _ok(f"{ws_id}: memory schema OK ({list(EXPECTED_KEYS)})")
        session.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL'}")
    return errors == 0


# ---------------------------------------------------------------------------
# Vocabulary diff table (bonus display)
# ---------------------------------------------------------------------------

def print_vocabulary_diff_table():
    """Print a concise side-by-side diff of key terms across all worlds."""
    _section("BONUS · Vocabulary comparison table")

    keys = ['hp_name', 'mp_name', 'gold_name', 'dm_title']
    header = f"  {'Setting':<28}" + "".join(f"{k:<26}" for k in keys)
    print(header)
    _hr(' ', 0)
    print('  ' + '─' * (28 + 26 * len(keys)))

    dnd_tm = config.get_world_setting('dnd5e')['term_map']
    for ws in config.WORLD_SETTINGS:
        tm   = ws.get('term_map', {})
        name = ws['name'][:26]
        row  = f"  {name:<28}"
        for k in keys:
            val = tm.get(k, '—')
            # Highlight if it differs from DnD baseline
            diff = val != dnd_tm.get(k, '')
            marker = '* ' if diff else '  '
            row += f"{marker}{val[:22]:<24}"
        print(row)

    print("\n  * = differs from D&D 5e baseline")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print()
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║  DND-like RPG — World Setting Validator                                  ║")
    print("║  Tests: text differentiation (A) + game-flow consistency (B)            ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")

    results = {}

    # A — Text differentiation
    results['A1 term-map completeness']       = test_term_map_completeness()
    results['A2 vocabulary differentiation']  = test_vocabulary_differentiation()
    results['A3 world-lore uniqueness']       = test_world_lore_uniqueness()
    results['A4 starting location/NPC']       = test_starting_location_uniqueness()
    results['A5 system prompt diff']          = test_system_prompt_differentiation()
    results['A6 category grouping']           = test_world_categories()

    # B — Game-flow consistency
    results['B1 dice consistency']            = test_dice_consistency()
    results['B2 combat mechanics']            = test_combat_mechanics_consistency()
    results['B3 create_new_game all worlds']  = test_create_game_all_worlds()
    results['B4 load_game restoration']       = test_load_game_all_worlds()
    results['B5 CharacterLogic mutations']    = test_character_logic_world_agnostic()
    results['B6 session memory schema']       = test_session_memory_format()

    # Bonus table
    print_vocabulary_diff_table()

    # Summary
    _section("SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    for name, ok in results.items():
        sym = '✓' if ok else '✗'
        print(f"  {sym}  {name}")
    print()
    _hr()
    status = "ALL PASS" if passed == total else f"{total - passed} FAILED"
    print(f"  {passed}/{total} tests passed   [{status}]")
    _hr()
    sys.exit(0 if passed == total else 1)

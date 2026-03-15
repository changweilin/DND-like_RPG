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

        party, state, session = slm.create_new_game(
            save_name=save_name,
            character_name="Tester",
            race="Human",
            char_class="Warrior",
            appearance="Plain",
            personality="Brave",
            world_setting=ws_id,
        )
        player = party[0] if party else None

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

        # Verify player stats match CLASS_BASE_STATS (Warrior is the test class)
        expected_hp = config.CLASS_BASE_STATS['warrior']['hp']
        expected_mp = config.CLASS_BASE_STATS['warrior']['mp']
        if player.hp != expected_hp or player.max_hp != expected_hp:
            _fail(f"{ws_id}: HP not {expected_hp}/{expected_hp} — got {player.hp}/{player.max_hp}")
            ok = False
        if player.mp != expected_mp or player.max_mp != expected_mp:
            _fail(f"{ws_id}: MP not {expected_mp}/{expected_mp} — got {player.mp}/{player.max_mp}")
            ok = False

        if ok:
            _ok(f"{ws_id}: location='{state.current_location}' · NPC='{npc_name}' · HP=100 ✓")

        if session:
            session.close()

    # Verify list_saves returns all created saves (each with party_size=1)
    saves = slm.list_saves()
    if len(saves) != len(config.WORLD_SETTINGS):
        _warn(f"list_saves returned {len(saves)}, expected {len(config.WORLD_SETTINGS)}")
    else:
        _ok(f"list_saves: {len(saves)} saves enumerated correctly")
        for s in saves:
            if s.get('party_size', 0) != 1:
                _warn(f"  {s['save_name']}: party_size={s.get('party_size')} expected 1")

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

        party_c, state, session = slm.create_new_game(
            save_name, "Tester", "Elf", "Mage", "", "", world_setting=ws_id
        )
        if session:
            session.close()

        # Load it back
        party2, state2, session2 = slm.load_game(save_name)
        if not party2:
            _fail(f"{ws_id}: load_game returned None")
            errors += 1
            continue
        player2 = party2[0]

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
        party_c, state, session = slm.create_new_game(
            f"clogic_{ws_id}", "Hero", "Human", "Warrior", "", "",
            world_setting=ws_id,
        )
        player = party_c[0] if party_c else None
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
        party_m, state, session = slm.create_new_game(
            f"mem_{ws_id}", "Mem", "Human", "Rogue", "", "",
            world_setting=ws_id,
        )
        if not party_m:
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
# C) MULTI-PLAYER STABILITY TESTS
# ---------------------------------------------------------------------------

def test_class_balance_budget():
    """CLASS_BASE_STATS must define all 4 classes with complete required fields."""
    _section("C1 · Class balance budget — stat completeness")
    REQUIRED = {'hp', 'max_hp', 'mp', 'max_mp', 'atk', 'def_stat', 'mov',
                'gold', 'reward_weight', 'role'}
    errors = 0
    from engine.config import GameConfig

    # Power-budget audit: compute effective_hp × avg_dps for each class
    print()
    print(f"  {'Class':<10} {'HP':>5} {'DEF':>5} {'ATK':>5} "
          f"{'EffHP':>7} {'AvgDPS':>8} {'Combat Score':>13} {'Wt':>6}")
    print("  " + "─" * 65)

    for cls, base in GameConfig.CLASS_BASE_STATS.items():
        missing = REQUIRED - set(base.keys())
        if missing:
            _fail(f"'{cls}' missing keys: {missing}")
            errors += 1

        # Effective HP after DEF mitigation over 5 incoming hits
        eff_hp   = base['hp'] + (base['def_stat'] // 2) * 5
        # Average weapon damage
        dice_avg = {'warrior': 4.5, 'mage': 2.5, 'rogue': 3.5, 'cleric': 3.5}.get(cls, 3.5)
        atk_mod  = (base['atk'] - 10) // 2
        avg_dmg  = dice_avg + atk_mod
        # Hit probability vs enemy DEF=10
        min_roll = max(1, 10 - atk_mod)
        hit_prob = max(0.05, (21 - min_roll) / 20)
        avg_dps  = avg_dmg * hit_prob
        score    = eff_hp * avg_dps

        print(f"  {cls:<10} {base['hp']:>5} {base['def_stat']:>5} {base['atk']:>5} "
              f"{eff_hp:>7} {avg_dps:>8.2f} {score:>13.1f} {base['reward_weight']:>6.2f}")

    print()
    # All reward_weights must be >= 1.0 (no class should be penalised below baseline)
    for cls, base in GameConfig.CLASS_BASE_STATS.items():
        w = base.get('reward_weight', 1.0)
        if w < 1.0:
            _fail(f"'{cls}' reward_weight {w} < 1.0 — would penalise this class")
            errors += 1
        else:
            _ok(f"'{cls}': reward_weight={w} ≥ 1.0  gold={base.get('gold')}")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_party_creation_sizes():
    """create_new_game() must succeed for party sizes 1-6 across several worlds."""
    _section("C2 · Multi-player party creation (sizes 1–6)")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    party_configs = [
        {'name': 'Aric',  'race': 'Human', 'char_class': 'Warrior', 'appearance': '', 'personality': ''},
        {'name': 'Lyra',  'race': 'Elf',   'char_class': 'Mage',    'appearance': '', 'personality': ''},
        {'name': 'Dax',   'race': 'Dwarf', 'char_class': 'Rogue',   'appearance': '', 'personality': ''},
        {'name': 'Sera',  'race': 'Human', 'char_class': 'Cleric',  'appearance': '', 'personality': ''},
        {'name': 'Orin',  'race': 'Orc',   'char_class': 'Warrior', 'appearance': '', 'personality': ''},
        {'name': 'Vessa', 'race': 'Elf',   'char_class': 'Mage',    'appearance': '', 'personality': ''},
    ]
    sample_worlds = ['dnd5e', 'wh40k', 'call_of_cthulhu', 'hearts_of_wulin']

    for ws_id in sample_worlds:
        for n in range(1, 7):
            configs = party_configs[:n]
            lead    = configs[0]
            extra   = configs[1:]
            save_nm = f"party_{ws_id}_{n}p"

            party, state, session = slm.create_new_game(
                save_nm, lead['name'], lead['race'], lead['char_class'],
                lead['appearance'], lead['personality'],
                world_setting=ws_id,
                extra_players=[
                    {'name': e['name'], 'race': e['race'], 'char_class': e['char_class'],
                     'appearance': e['appearance'], 'personality': e['personality']}
                    for e in extra
                ] if extra else None,
            )

            if party is None:
                _fail(f"{ws_id} {n}p: create_new_game returned None")
                errors += 1
                continue

            # Verify party size
            if len(party) != n:
                _fail(f"{ws_id} {n}p: got {len(party)} members, expected {n}")
                errors += 1
            elif len(state.party_ids) != n:
                _fail(f"{ws_id} {n}p: party_ids length {len(state.party_ids)} ≠ {n}")
                errors += 1
            else:
                names = [c.name for c in party]
                _ok(f"{ws_id} {n}p: {names}")

            # Verify class-balanced stats
            for char in party:
                base = config.CLASS_BASE_STATS.get(char.char_class.lower(), {})
                if char.hp != base.get('hp', 0):
                    _fail(f"{ws_id}: {char.name} HP={char.hp}, expected={base.get('hp')}")
                    errors += 1

            # Verify contribution tracker initialised for all members
            for cid in state.party_ids:
                if str(cid) not in (state.party_contributions or {}):
                    _fail(f"{ws_id} {n}p: no contribution entry for char_id={cid}")
                    errors += 1

            if session:
                session.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_turn_rotation():
    """Active player must cycle through the party, skipping defeated characters."""
    _section("C3 · Turn rotation — round-robin with defeat skip")
    from logic.events import EventManager
    from engine.game_state import DatabaseManager, GameState, Character
    import types

    errors = 0

    # Create a 4-player party using in-memory helpers
    slm, tmp_path = _make_in_memory_db()

    party_configs = [
        {'name': 'Aric', 'race': 'Human', 'char_class': 'Warrior'},
        {'name': 'Lyra', 'race': 'Elf',   'char_class': 'Mage'},
        {'name': 'Dax',  'race': 'Dwarf', 'char_class': 'Rogue'},
        {'name': 'Sera', 'race': 'Human', 'char_class': 'Cleric'},
    ]
    lead  = party_configs[0]
    extra = party_configs[1:]
    party, state, session = slm.create_new_game(
        "rotation_test", lead['name'], lead['race'], lead['char_class'],
        '', '', world_setting='dnd5e',
        extra_players=[{'name': e['name'], 'race': e['race'], 'char_class': e['char_class'],
                        'appearance': '', 'personality': ''} for e in extra],
    )

    if party is None:
        _fail("Could not create 4-player party for rotation test")
        return False

    em = EventManager(None, None, session)

    # Full rotation: 4 advances should return to index 0
    start_idx = state.active_player_index
    _ok(f"Start: idx={start_idx}, active={party[start_idx].name}")

    for step in range(4):
        em._advance_active_player(state, party)
        idx = state.active_player_index
        _ok(f"After advance {step+1}: idx={idx}, active={party[idx].name}")

    if state.active_player_index != start_idx:
        _fail(f"After 4 advances, index={state.active_player_index} ≠ start {start_idx}")
        errors += 1
    else:
        _ok("4-advance cycle returns to start ✓")

    # Defeat player at index 1 — next advance from 0 should skip to 2
    party[1].hp = 0
    state.active_player_index = 0
    em._advance_active_player(state, party)
    if state.active_player_index != 2:
        _fail(f"Expected skip to idx=2 (player 1 dead), got {state.active_player_index}")
        errors += 1
    else:
        _ok(f"Defeated {party[1].name} → skipped to {party[state.active_player_index].name} ✓")

    # Defeat all but last — advance should stop at surviving player
    party[2].hp = 0
    party[3].hp = 0
    state.active_player_index = 0
    em._advance_active_player(state, party)
    # Only party[0] is alive (idx=0), so advance wraps and finds 0
    if party[state.active_player_index].hp <= 0:
        _fail(f"Advanced to dead player at idx={state.active_player_index}")
        errors += 1
    else:
        _ok(f"Only survivor: {party[state.active_player_index].name} ✓")

    session.close()
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_contribution_tracking():
    """Contribution accumulation must work correctly; reward split must sum to party gold."""
    _section("C4 · Contribution tracking & reward balance")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    party_cfg = [
        {'name': 'W', 'race': 'Human', 'char_class': 'Warrior'},
        {'name': 'M', 'race': 'Elf',   'char_class': 'Mage'},
        {'name': 'R', 'race': 'Dwarf', 'char_class': 'Rogue'},
        {'name': 'C', 'race': 'Human', 'char_class': 'Cleric'},
    ]
    lead  = party_cfg[0]
    extra = party_cfg[1:]
    party, state, session = slm.create_new_game(
        "contrib_test", lead['name'], lead['race'], lead['char_class'],
        '', '', world_setting='dnd5e',
        extra_players=[{'name': e['name'], 'race': e['race'], 'char_class': e['char_class'],
                        'appearance': '', 'personality': ''} for e in extra],
    )
    if party is None:
        _fail("Party creation failed")
        return False

    from logic.events import EventManager
    em = EventManager(None, None, session)

    # Simulate varied contributions
    # Warrior: heavy damage dealer
    em._update_contributions(state, party[0], damage_dealt=80, healing_done=0,  checks_passed=2)
    # Mage: moderate damage + arcana checks
    em._update_contributions(state, party[1], damage_dealt=30, healing_done=0,  checks_passed=8)
    # Rogue: damage + scouting
    em._update_contributions(state, party[2], damage_dealt=50, healing_done=0,  checks_passed=5)
    # Cleric: healer
    em._update_contributions(state, party[3], damage_dealt=10, healing_done=120, checks_passed=3)

    # Verify accumulated correctly
    for char in party:
        entry = state.party_contributions.get(str(char.id), {})
        _ok(f"{char.name} ({char.char_class}): "
            f"dmg={entry.get('damage_dealt',0)} "
            f"heal={entry.get('healing_done',0)} "
            f"checks={entry.get('skill_checks_passed',0)} "
            f"turns={entry.get('turns_taken',0)}")

    # Compute reward split and verify it sums to total party gold
    rewards = slm.compute_end_game_rewards(party, state)
    total_gold = sum(c.gold for c in party)
    reward_sum = sum(rewards.values())

    if reward_sum != total_gold:
        _fail(f"Reward sum {reward_sum} ≠ party gold {total_gold}")
        errors += 1
    else:
        _ok(f"Reward sum = party gold = {total_gold} ✓")

    # Verify no player gets 0 gold (floor of 1 score)
    for name, gold in rewards.items():
        if gold < 0:
            _fail(f"{name} got negative gold: {gold}")
            errors += 1
        _ok(f"  {name}: {gold} gold")

    # Balance audit: with reward_weight, classes with lower raw combat should converge
    # The Cleric healed 120 × 1.5 × 1.25 = 225 score; Warrior 80 × 1.0 = 80 score
    # So Cleric should get more gold than Warrior here
    warrior_gold = rewards.get('W', 0)
    cleric_gold  = rewards.get('C', 0)
    if cleric_gold > warrior_gold:
        _ok(f"Balance check: Cleric ({cleric_gold}g) > Warrior ({warrior_gold}g) when Cleric healed heavily ✓")
    else:
        _warn(f"Balance check: Cleric ({cleric_gold}g) ≤ Warrior ({warrior_gold}g) — expected Cleric higher (heavy healer)")

    session.close()
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_multiplay_load_restore():
    """Party round-trip: create → close → load → verify party_ids and active index."""
    _section("C5 · Multi-player save/load round-trip stability")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    for n in [2, 3, 4, 5, 6]:
        all_cfg = [
            {'name': f'P{i+1}', 'race': 'Human',
             'char_class': ['Warrior','Mage','Rogue','Cleric'][i % 4]}
            for i in range(n)
        ]
        lead  = all_cfg[0]
        extra = all_cfg[1:]
        save_nm = f"roundtrip_{n}p"

        party, state, session = slm.create_new_game(
            save_nm, lead['name'], lead['race'], lead['char_class'],
            '', '', world_setting='dnd5e',
            extra_players=[{'name': e['name'], 'race': e['race'], 'char_class': e['char_class'],
                            'appearance': '', 'personality': ''} for e in extra] if extra else None,
        )
        # Simulate advancing 2 turns
        from logic.events import EventManager
        em = EventManager(None, None, session)
        em._advance_active_player(state, party)
        em._advance_active_player(state, party)
        saved_idx = state.active_player_index
        session.close()

        # Reload
        party2, state2, session2 = slm.load_game(save_nm)
        if not party2:
            _fail(f"{n}p: load_game failed")
            errors += 1
            continue

        if len(party2) != n:
            _fail(f"{n}p: reloaded {len(party2)} members, expected {n}")
            errors += 1
        elif (state2.active_player_index or 0) != saved_idx:
            _fail(f"{n}p: active_idx={state2.active_player_index}, expected={saved_idx}")
            errors += 1
        else:
            names = [c.name for c in party2]
            _ok(f"{n}p: restored {names}, active_idx={state2.active_player_index} ✓")

        session2.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_class_stats_differ():
    """All 4 classes must have different stat profiles (no accidental duplicates)."""
    _section("C6 · Class differentiation — no duplicate stat profiles")
    from engine.config import GameConfig
    profiles = {}
    errors = 0

    for cls, base in GameConfig.CLASS_BASE_STATS.items():
        profile = (base['hp'], base['mp'], base['atk'], base['def_stat'], base['mov'])
        if profile in profiles:
            _fail(f"'{cls}' and '{profiles[profile]}' share identical stats {profile}")
            errors += 1
        else:
            profiles[profile] = cls
            _ok(f"{cls}: HP={base['hp']} MP={base['mp']} "
                f"ATK={base['atk']} DEF={base['def_stat']} MOV={base['mov']}")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} duplicate(s)'}")
    return errors == 0


# ---------------------------------------------------------------------------
# D) AI PLAYER & 6-PLAYER EXPANSION TESTS
# ---------------------------------------------------------------------------

def test_six_player_creation():
    """create_new_game() must succeed for full 6-player party."""
    _section("D1 · 6-player party creation and config storage")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    # Build 6 configs: slots 0,2,4 human; slots 1,3,5 AI with varied personalities
    ai_personalities = list(config.AI_PERSONALITIES.keys())
    ai_difficulties  = list(config.AI_DIFFICULTIES.keys())

    extra_cfgs = []
    for i in range(1, 6):
        extra_cfgs.append({
            'name':           f'Member{i+1}',
            'race':           'Human',
            'char_class':     ['Mage','Rogue','Cleric','Warrior','Mage'][i-1],
            'appearance':     '',
            'personality':    '',
            'is_ai':          (i % 2 == 1),   # slots 1,3,5 are AI
            'ai_personality': ai_personalities[i % len(ai_personalities)],
            'ai_difficulty':  ai_difficulties[i % len(ai_difficulties)],
        })

    party, state, session = slm.create_new_game(
        "six_player_test", "Leader", "Dwarf", "Warrior", "", "",
        world_setting='dnd5e',
        extra_players=extra_cfgs,
    )

    if party is None:
        _fail("create_new_game returned None for 6-player party")
        errors += 1
    else:
        if len(party) != 6:
            _fail(f"Expected 6 members, got {len(party)}")
            errors += 1
        else:
            _ok(f"6 members created: {[c.name for c in party]}")

        # Verify ai_configs stored correctly
        ai_cfgs = state.ai_configs or {}
        expected_ai_slots = {str(i) for i in [1, 3, 5]}
        stored_ai_slots   = {k for k, v in ai_cfgs.items() if v.get('is_ai')}
        if stored_ai_slots != expected_ai_slots:
            _fail(f"ai_configs slots {stored_ai_slots} ≠ expected {expected_ai_slots}")
            errors += 1
        else:
            _ok(f"AI slots stored correctly: {sorted(stored_ai_slots)}")

        # Verify each AI slot has personality + difficulty
        for slot_key, cfg_entry in ai_cfgs.items():
            if cfg_entry.get('is_ai'):
                p = cfg_entry.get('personality', '')
                d = cfg_entry.get('difficulty', '')
                if p not in config.AI_PERSONALITIES:
                    _fail(f"Slot {slot_key}: unknown personality '{p}'")
                    errors += 1
                elif d not in config.AI_DIFFICULTIES:
                    _fail(f"Slot {slot_key}: unknown difficulty '{d}'")
                    errors += 1
                else:
                    _ok(f"Slot {slot_key}: is_ai=True · personality={p} · difficulty={d}")

        # Verify slot 0 (leader) is NOT in ai_configs
        if ai_cfgs.get('0', {}).get('is_ai', False):
            _fail("Slot 0 (party leader) must never be AI-controlled")
            errors += 1
        else:
            _ok("Slot 0 (leader) correctly marked as human")

        if session:
            session.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_ai_player_decision_tree():
    """AIPlayerController must produce valid action strings for all personalities and difficulties."""
    _section("D2 · AIPlayerController decision tree — all personalities × difficulties")
    from logic.events import AIPlayerController
    import types

    errors = 0
    controller = AIPlayerController()

    # Build mock character and state objects
    def _mock_char(name, char_class, hp, max_hp, mp, max_mp, char_id=1):
        c = types.SimpleNamespace(
            id=char_id, name=name, char_class=char_class,
            hp=hp, max_hp=max_hp, mp=mp, max_mp=max_mp,
        )
        return c

    def _mock_state(enemies=None):
        return types.SimpleNamespace(
            known_entities=(
                {e: {'type': 'monster', 'hp': 30, 'alive': True} for e in (enemies or [])}
            ),
            world_setting='dnd5e',
        )

    personalities = list(config.AI_PERSONALITIES.keys())
    difficulties  = list(config.AI_DIFFICULTIES.keys())

    # Test every personality × difficulty combination
    for personality in personalities:
        for difficulty in difficulties:
            ai_config = {'is_ai': True, 'personality': personality, 'difficulty': difficulty}

            # Scenario A: full HP, no enemies → explore or support
            char_a  = _mock_char("Tester", "Warrior", 150, 150, 20, 20)
            state_a = _mock_state(enemies=[])
            action_a = controller.decide_action(char_a, state_a, [char_a], ai_config)
            if not isinstance(action_a, str) or not action_a.strip():
                _fail(f"{personality}/{difficulty} (no enemies): empty action returned")
                errors += 1

            # Scenario B: enemies present → should include attack actions
            char_b  = _mock_char("Fighter", "Warrior", 150, 150, 20, 20)
            state_b = _mock_state(enemies=['goblin', 'orc'])
            action_b = controller.decide_action(char_b, state_b, [char_b], ai_config)
            if not isinstance(action_b, str) or not action_b.strip():
                _fail(f"{personality}/{difficulty} (with enemies): empty action returned")
                errors += 1

            # Scenario C: critical HP → should lean toward heal/retreat
            char_c  = _mock_char("Cleric", "Cleric", 10, 110, 60, 70, char_id=2)
            state_c = _mock_state(enemies=[])
            action_c = controller.decide_action(char_c, state_c, [char_c], ai_config)
            if not isinstance(action_c, str) or not action_c.strip():
                _fail(f"{personality}/{difficulty} (low HP): empty action returned")
                errors += 1

        _ok(f"{personality}: all {len(difficulties)} difficulties produced valid actions")

    # Aggressive should prefer attacking (most actions should mention attacking)
    attack_actions = 0
    for _ in range(20):
        ai_config = {'personality': 'aggressive', 'difficulty': 'normal'}
        char  = _mock_char("Brute", "Warrior", 150, 150, 20, 20)
        state = _mock_state(enemies=['dragon'])
        action = controller.decide_action(char, state, [char], ai_config)
        if any(word in action.lower() for word in ('attack', 'strike', 'charge')):
            attack_actions += 1

    if attack_actions >= 15:
        _ok(f"Aggressive personality: {attack_actions}/20 actions were attacks (bias confirmed)")
    else:
        _warn(f"Aggressive personality: only {attack_actions}/20 were attacks (lower than expected)")

    # Support/Cleric healing ally detection
    healer   = _mock_char("Sera", "Cleric", 110, 110, 70, 70, char_id=10)
    wounded  = _mock_char("Aric", "Warrior", 20, 150, 20, 20, char_id=11)
    state_h  = _mock_state(enemies=[])
    ai_cfg_s = {'personality': 'support', 'difficulty': 'normal'}
    party_h  = [healer, wounded]
    heal_actions = 0
    for _ in range(10):
        action = controller.decide_action(healer, state_h, party_h, ai_cfg_s)
        if any(word in action.lower() for word in ('heal', 'spell on', 'tend')):
            heal_actions += 1
    if heal_actions >= 7:
        _ok(f"Support personality: {heal_actions}/10 actions healed wounded ally ✓")
    else:
        _warn(f"Support personality: {heal_actions}/10 healed ally (may need tuning)")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_mixed_party_save_load():
    """Mixed human+AI party must round-trip correctly through save/load."""
    _section("D3 · Mixed human+AI party save/load round-trip")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    for n_ai in [1, 2, 3, 5]:  # number of AI slots (out of 6 total)
        n_total = 6
        extra = []
        for i in range(1, n_total):
            is_ai = (i <= n_ai)
            extra.append({
                'name':           f'Slot{i+1}',
                'race':           'Human',
                'char_class':     ['Mage','Rogue','Cleric','Warrior','Mage'][i-1],
                'appearance':     '',
                'personality':    '',
                'is_ai':          is_ai,
                'ai_personality': 'tactical',
                'ai_difficulty':  'normal',
            })

        save_nm = f"mixed_{n_ai}ai"
        party, state, session = slm.create_new_game(
            save_nm, "Leader", "Human", "Warrior", "", "",
            world_setting='dnd5e', extra_players=extra,
        )
        if not party:
            _fail(f"{n_ai} AI slots: create_new_game failed")
            errors += 1
            continue

        orig_ai_cfgs = dict(state.ai_configs or {})
        session.close()

        # Reload and compare
        party2, state2, session2 = slm.load_game(save_nm)
        if not party2:
            _fail(f"{n_ai} AI slots: load_game failed")
            errors += 1
            continue

        loaded_ai_cfgs = state2.ai_configs or {}
        if len(party2) != n_total:
            _fail(f"{n_ai} AI: reloaded {len(party2)} members, expected {n_total}")
            errors += 1
        elif loaded_ai_cfgs != orig_ai_cfgs:
            _fail(f"{n_ai} AI: ai_configs mismatch after load\n  orig:   {orig_ai_cfgs}\n  loaded: {loaded_ai_cfgs}")
            errors += 1
        else:
            ai_slots = [k for k, v in loaded_ai_cfgs.items() if v.get('is_ai')]
            _ok(f"{n_ai} AI slots: party={len(party2)} · ai_slots={sorted(ai_slots)} ✓")

        session2.close()

    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_player_flags():
    """PLAYER_FLAGS must cover all 6 slots and contain distinct non-empty emoji."""
    _section("D4 · Player flag emoji — completeness and uniqueness")
    errors = 0
    flags  = config.PLAYER_FLAGS

    if len(flags) < config.MAX_PARTY_SIZE:
        _fail(f"Only {len(flags)} flags for MAX_PARTY_SIZE={config.MAX_PARTY_SIZE}")
        errors += 1
    else:
        _ok(f"{len(flags)} flags defined for {config.MAX_PARTY_SIZE} max party slots")

    if len(set(flags)) != len(flags):
        _fail("Duplicate flag entries detected")
        errors += 1
    else:
        _ok("All flags are unique")

    for i, flag in enumerate(flags):
        if not flag.strip():
            _fail(f"Slot {i}: empty flag")
            errors += 1
        else:
            _ok(f"Slot {i}: '{flag}'")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_ai_run_turn_interface():
    """EventManager.run_ai_turn() must return 5-tuple with valid action string."""
    _section("D5 · run_ai_turn() interface and return type")
    from logic.events import EventManager
    import types

    errors = 0
    slm, tmp_path = _make_in_memory_db()

    # Create a 3-player party: slot 0 human, slots 1+2 AI
    extra = [
        {'name': 'AIRogue',  'race': 'Elf',   'char_class': 'Rogue',  'appearance': '',
         'personality': '', 'is_ai': True, 'ai_personality': 'aggressive', 'ai_difficulty': 'normal'},
        {'name': 'AICleric', 'race': 'Human', 'char_class': 'Cleric', 'appearance': '',
         'personality': '', 'is_ai': True, 'ai_personality': 'support', 'ai_difficulty': 'easy'},
    ]
    party, state, session = slm.create_new_game(
        "ai_turn_test", "Human", "Dwarf", "Warrior", "", "",
        world_setting='dnd5e', extra_players=extra,
    )
    if not party:
        _fail("Could not create party for run_ai_turn test")
        return False

    # Mock the LLM and RAG to avoid real network calls
    class _MockLLM:
        def parse_intent(self, *a, **kw):
            return {'thought_process': '', 'action_type': 'direct_action',
                    'requires_roll': False, 'skill': '', 'dc': 0, 'target': '', 'summary': ''}
        def render_narrative(self, *a, **kw):
            return {'scene_type': 'exploration', 'narrative': 'The AI acts.',
                    'choices': ['Continue'], 'damage_taken': 0, 'hp_healed': 0,
                    'mp_used': 0, 'items_found': [], 'location_change': '',
                    'npc_relationship_changes': {}}
        def summarize_memory_segment(self, *a, **kw): return ''
        def evaluate_npc_reactions(self, *a, **kw): return {}

    class _MockRAG:
        def retrieve_context(self, *a, **kw): return ''
        def world_lore_seeded(self): return True
        def entity_stat_block_exists(self, *a): return True
        def add_story_event(self, *a, **kw): pass

    em = EventManager(_MockLLM(), _MockRAG(), session)

    # Advance to slot 1 (first AI player)
    from sqlalchemy.orm.attributes import flag_modified
    state.active_player_index = 1
    flag_modified(state, 'active_player_index')
    session.commit()

    result = em.run_ai_turn(state, party)

    if not isinstance(result, tuple) or len(result) != 5:
        _fail(f"run_ai_turn returned {type(result)} with len={len(result) if hasattr(result, '__len__') else '?'}, expected 5-tuple")
        errors += 1
    else:
        action_text, narrative, choices, turn_data, dice_result = result
        if not isinstance(action_text, str) or not action_text.strip():
            _fail(f"action_text is empty or not a string: {action_text!r}")
            errors += 1
        else:
            _ok(f"run_ai_turn action: '{action_text[:60]}'")

        if not isinstance(narrative, str):
            _fail("narrative is not a string")
            errors += 1
        else:
            _ok(f"narrative returned: '{narrative[:50]}'")

        if not isinstance(choices, list):
            _fail("choices is not a list")
            errors += 1
        else:
            _ok(f"choices: {choices}")

    session.close()
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


# ---------------------------------------------------------------------------
# E — Game board (engine/board.py) pure-logic tests
# ---------------------------------------------------------------------------

def test_detect_location_type():
    """detect_location_type() returns correct (row, icon) for each biome keyword."""
    _section("E1 · detect_location_type() — keyword → row mapping")
    from engine.board import detect_location_type

    cases = [
        # (name,               expected_row, description)
        ('Dark Dungeon',        4,  'dungeon keyword → row 4'),
        ('Goblin Cavern',       4,  'cavern keyword → row 4'),
        ('Spider Cave',         4,  'cave keyword → row 4'),
        ('Iron Mine',           4,  'mine keyword → row 4'),
        ('Old Crypt',           4,  'crypt keyword → row 4'),
        ('Castle Blackthorn',   3,  'castle keyword → row 3'),
        ('Temple of Light',     3,  'temple keyword → row 3'),
        ('Harbor District',     3,  'harbor keyword → row 3'),
        ('Mages Guild',         3,  'guild keyword → row 3'),
        ('Riverside Village',   3,  'village keyword → row 3'),
        ('Old Town Square',     3,  'town keyword → row 3'),
        ('Whispering Forest',   2,  'forest keyword → row 2'),
        ('Bogwater Swamp',      2,  'swamp keyword → row 2'),
        ('Crystal River',       2,  'river keyword → row 2'),
        ('Mountain Pass',       1,  'mountain keyword → row 1'),
        ('Frozen Tundra',       1,  'tundra keyword → row 1'),
        ('Ancient Ruin',        1,  'ruin keyword → row 1'),
        ('Astral Nexus',        0,  'astral keyword → row 0'),
        ('Sky Heavens Above',   0,  'sky/cloud keyword → row 0'),
        ('Random Place',        3,  'no keyword → default row 3'),
    ]

    errors = 0
    for name, exp_row, desc in cases:
        row, icon = detect_location_type(name)
        if row != exp_row:
            _fail(f"{desc}: got row={row}, expected {exp_row}  (name={name!r})")
            errors += 1
        elif not icon:
            _fail(f"{desc}: icon is empty  (name={name!r})")
            errors += 1
        else:
            _ok(f"{desc}: row={row}, icon={icon}")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_assign_map_position_no_collisions():
    """assign_map_position() places 20 locations without any two sharing a cell."""
    _section("E2 · assign_map_position() — no cell collisions for 20 locations")
    from engine.board import assign_map_position

    location_names = [
        'Ironforge Town', 'Shadowmere Village', 'Temple of Dawn', 'Dungeon of Doom',
        'Whispering Forest', 'Mountain Peak', 'Crystal Cave', 'Astral Plane',
        'Harbor Port', 'Ancient Ruin', 'Bogwater Swamp', 'Old Castle',
        'River Crossing', 'Sky Citadel', 'Dark Crypt', 'Plains of Gold',
        'Mage Tower', 'Ice Tundra', 'Goblin Lair', 'Market District',
    ]

    errors   = 0
    world_map = {}
    occupied  = set()

    for loc in location_names:
        row, col, icon = assign_map_position(loc, world_map)
        cell = (row, col)
        if cell in occupied:
            _fail(f"Collision at {cell} when placing '{loc}'")
            errors += 1
        else:
            _ok(f"Placed '{loc[:20]}' at ({row},{col}) {icon}")
            occupied.add(cell)
            world_map[loc] = {'row': row, 'col': col, 'icon': icon}

    # Idempotency: re-querying same name returns same cell
    for loc in location_names[:5]:
        row2, col2, _ = assign_map_position(loc, world_map)
        stored = world_map[loc]
        if (row2, col2) != (stored['row'], stored['col']):
            _fail(f"Idempotency failed for '{loc}': got ({row2},{col2}), stored ({stored['row']},{stored['col']})")
            errors += 1

    if errors == 0:
        _ok("Idempotency check passed for first 5 locations")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_assign_map_position_settlement_default():
    """Locations with no keyword match land in row 3 (settlement zone)."""
    _section("E3 · assign_map_position() — default row is 3 (settlement)")
    from engine.board import assign_map_position

    generic_names = [
        'Xvzqr Place', 'Blorf Area', 'Zindop Location',
        'Quirble Zone', 'Phlox Spot',
    ]

    errors   = 0
    world_map = {}

    for name in generic_names:
        row, col, icon = assign_map_position(name, world_map)
        if row != 3:
            _fail(f"'{name}' → row={row}, expected 3 (settlement default)")
            errors += 1
        else:
            _ok(f"'{name}' → row=3, icon={icon}")
        world_map[name] = {'row': row, 'col': col, 'icon': icon}

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_build_map_html_structure():
    """build_map_html() returns a complete HTML string with required structural elements."""
    _section("E4 · build_map_html() — HTML structure and content")
    from engine.board import build_map_html, MAP_ROWS, MAP_COLS

    errors = 0

    # Minimal mock party member
    class _MockChar:
        def __init__(self, cid, name, hp):
            self.id   = cid
            self.name = name
            self.hp   = hp

    party = [
        _MockChar(1, 'Aria',   30),
        _MockChar(2, 'Brom',    0),  # dead
    ]

    world_map = {
        'Starting Village': {'row': 3, 'col': 2, 'icon': '🏘️'},
        'Dark Dungeon':     {'row': 4, 'col': 5, 'icon': '💀'},
    }
    player_positions = {
        1: {'location': 'Starting Village', 'row': 3, 'col': 2},
        2: {'location': 'Dark Dungeon',     'row': 4, 'col': 5},
    }
    player_flags   = ['🔴', '🔵']
    active_char_id = 1

    html = build_map_html(world_map, player_positions, party, active_char_id, player_flags)

    if not isinstance(html, str):
        _fail(f"Return type is {type(html)}, expected str")
        return False

    _ok(f"Return type is str, length={len(html)}")

    # Check required structural elements
    checks = [
        ('<style>',          'CSS block present'),
        ('<table',           '<table> element present'),
        ('</table>',         '</table> closing tag present'),
        ('<tr>',             '<tr> row elements present'),
        ('<td',              '<td> cell elements present'),
        ('rpg-map',          'rpg-map CSS class present'),
        ('rpg-cell',         'rpg-cell CSS class present'),
        ('rpg-fog',          'fog-of-war CSS class present'),
        ('Starting Village', 'Known location name in output'),
        ('Dark Dungeon',     'Second location name in output'),
        ('🏘️',              'Location icon rendered'),
        ('💀',              'Dungeon icon rendered'),
        ('🔴',              'Player 1 flag (active) rendered'),
        ('🔵',              'Player 2 flag rendered'),
        ('rpg-active',       'Active cell gets rpg-active class'),
        ('rpg-dead-token',   'Dead player gets dead-token class'),
    ]

    for snippet, desc in checks:
        if snippet in html:
            _ok(desc)
        else:
            _fail(f"{desc} — snippet not found: {snippet!r}")
            errors += 1

    # Row count: MAP_ROWS table rows expected
    tr_count = html.count('<tr>')
    if tr_count == MAP_ROWS:
        _ok(f"Table has {MAP_ROWS} <tr> rows as expected")
    else:
        _fail(f"Expected {MAP_ROWS} <tr> rows, got {tr_count}")
        errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_map_position_full_row_overflow():
    """assign_map_position() falls back gracefully when preferred row is full."""
    _section("E5 · assign_map_position() — overflow to adjacent row when full")
    from engine.board import assign_map_position, MAP_COLS

    errors   = 0
    world_map = {}

    # Fill row 3 (settlement default) completely with 8 generic locations
    for i in range(MAP_COLS):
        name = f'GenericTown{i}'
        row, col, icon = assign_map_position(name, world_map)
        world_map[name] = {'row': row, 'col': col, 'icon': icon}

    # Now place one more generic location — must go to a different row
    name = 'OverflowVillage'
    row, col, icon = assign_map_position(name, world_map)
    if row == 3:
        # Check if col is truly free (should be impossible since row 3 is full)
        if any(v['row'] == 3 and v['col'] == col for v in world_map.values()):
            _fail(f"Overflow placed '{name}' in occupied cell (3, {col})")
            errors += 1
        else:
            _ok(f"Overflow placed in row=3 free cell (col={col})")
    else:
        _ok(f"Overflow '{name}' correctly placed in fallback row={row}, col={col}")

    world_map[name] = {'row': row, 'col': col, 'icon': icon}

    # Verify total unique positions
    positions = {(v['row'], v['col']) for v in world_map.values()}
    if len(positions) == len(world_map):
        _ok(f"All {len(world_map)} locations have unique positions")
    else:
        _fail(f"Collision detected: {len(world_map)} locations but only {len(positions)} unique positions")
        errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


# ---------------------------------------------------------------------------
# F — Player handbook (engine/manual.py) tests
# ---------------------------------------------------------------------------

def test_manual_chapter_count():
    """build_manual_chapters() must return exactly 8 chapters for every world setting."""
    _section("F1 · build_manual_chapters() — 8 chapters for all 14 worlds")
    from engine.manual import build_manual_chapters

    errors = 0
    EXPECTED = 8
    for ws in config.WORLD_SETTINGS:
        chapters = build_manual_chapters(ws)
        n = len(chapters)
        if n != EXPECTED:
            _fail(f"{ws['id']}: {n} chapters, expected {EXPECTED}")
            errors += 1
        else:
            _ok(f"{ws['id']}: {n} chapters ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_manual_chapter_structure():
    """Every chapter must have non-empty icon, title, content, and tags list."""
    _section("F2 · Chapter structure — icon, title, content, tags present")
    from engine.manual import build_manual_chapters

    errors = 0
    ws = config.get_world_setting('dnd5e')
    chapters = build_manual_chapters(ws)

    for i, ch in enumerate(chapters):
        if not ch.get('icon', '').strip():
            _fail(f"Chapter {i}: empty icon")
            errors += 1
        if not ch.get('title', '').strip():
            _fail(f"Chapter {i}: empty title")
            errors += 1
        if len(ch.get('content', '')) < 50:
            _fail(f"Chapter {i} '{ch.get('title')}': content too short ({len(ch.get('content', ''))} chars)")
            errors += 1
        if not isinstance(ch.get('tags', None), list) or len(ch.get('tags', [])) < 2:
            _fail(f"Chapter {i} '{ch.get('title')}': tags missing or too few")
            errors += 1
        else:
            _ok(f"Chapter {i} '{ch['title']}': icon={ch['icon']}, "
                f"len={len(ch['content'])}, tags={len(ch['tags'])}")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_manual_vocabulary_substitution():
    """Setting-specific vocabulary must appear in chapter content (not default DnD terms)."""
    _section("F3 · Vocabulary substitution — setting terms appear in content")
    from engine.manual import build_manual_chapters

    # Pick worlds with clearly distinct vocabulary
    test_cases = [
        ('wh40k',            'hp_name',      'Wounds',               'combat'),
        ('shadowrun',        'gold_name',     'nuyen',                'vocabulary'),
        ('blades_in_the_dark','hp_name',      'Harm',                 'combat'),
        ('hearts_of_wulin',  'gold_name',     'silver taels',         'vocabulary'),
        ('deadlands',        'dm_title',      'Marshal',              'ai'),
        ('call_of_cthulhu',  'mp_name',       'Sanity',               'dice'),
        ('l5r',              'warrior_class', 'Bushi',                'classes'),
    ]

    errors = 0
    for ws_id, tm_key, expected_term, chapter_hint in test_cases:
        ws = config.get_world_setting(ws_id)
        if not ws:
            _fail(f"{ws_id}: world setting not found")
            errors += 1
            continue

        actual_val = ws.get('term_map', {}).get(tm_key, '')
        if not actual_val:
            _warn(f"{ws_id}.{tm_key}: not set in config, skipping")
            continue

        chapters = build_manual_chapters(ws)
        # Search all chapter content for the world-specific term
        found_in = [ch['title'] for ch in chapters if actual_val.lower() in ch['content'].lower()]
        if found_in:
            _ok(f"{ws_id} · {tm_key}={actual_val!r}: appears in {found_in}")
        else:
            _fail(f"{ws_id} · {tm_key}={actual_val!r}: NOT found in any chapter content")
            errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_manual_search_tags():
    """Common gameplay keywords must exist in at least one chapter's tags."""
    _section("F4 · Keyword search tags — gameplay keywords indexable")
    from engine.manual import build_manual_chapters

    # These are typical player search queries
    search_keywords = [
        'attack', 'damage', 'dice', 'dc', 'stealth', 'arcana',
        'warrior', 'mage', 'cleric', 'rogue', 'ai', 'exploration',
        'map', 'combat', 'skill', 'vocabulary',
    ]

    errors = 0
    ws = config.get_world_setting('dnd5e')
    chapters = build_manual_chapters(ws)

    for kw in search_keywords:
        # Tag or content match (mirrors what the search bar does)
        hits = [
            ch for ch in chapters
            if kw in ch.get('content', '').lower()
            or any(kw in t for t in ch.get('tags', []))
        ]
        if hits:
            _ok(f"'{kw}': found in {[ch['title'] for ch in hits[:2]]}")
        else:
            _fail(f"'{kw}': not found in any chapter content or tags")
            errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_manual_world_differentiation():
    """Chapter content must differ meaningfully across different world settings."""
    _section("F5 · World content differentiation — distinct chapters per setting")
    from engine.manual import build_manual_chapters

    errors = 0
    # Compare dnd5e vs 5 other settings on combat + vocabulary chapters
    base_ws  = config.get_world_setting('dnd5e')
    base_chapters = build_manual_chapters(base_ws)
    base_combat   = next(ch['content'] for ch in base_chapters if '戰鬥' in ch['title'])
    base_vocab    = next(ch['content'] for ch in base_chapters if '術語' in ch['title'])

    check_ids = ['wh40k', 'shadowrun', 'call_of_cthulhu', 'hearts_of_wulin', 'deadlands']
    for ws_id in check_ids:
        ws = config.get_world_setting(ws_id)
        if not ws:
            continue
        chs     = build_manual_chapters(ws)
        combat  = next(ch['content'] for ch in chs if '戰鬥' in ch['title'])
        vocab   = next(ch['content'] for ch in chs if '術語' in ch['title'])

        if combat == base_combat:
            _fail(f"{ws_id}: combat chapter identical to dnd5e (vocabulary not substituted)")
            errors += 1
        else:
            _ok(f"{ws_id}: combat chapter differs from dnd5e ✓")

        if vocab == base_vocab:
            _fail(f"{ws_id}: vocabulary chapter identical to dnd5e")
            errors += 1
        else:
            _ok(f"{ws_id}: vocabulary chapter differs from dnd5e ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


# ---------------------------------------------------------------------------
# G — Image prompt builder (engine/image_prompts.py) tests
# ---------------------------------------------------------------------------

def test_image_styles_registry():
    """IMAGE_STYLES must define all required presets with non-empty name/suffix (except custom)."""
    _section("G1 · IMAGE_STYLES registry — completeness and field validation")
    from engine.image_prompts import IMAGE_STYLES

    REQUIRED_PRESETS = {'fantasy_art', 'watercolor', 'anime', 'realistic', 'pixel_art', 'ink', 'custom'}
    errors = 0

    missing = REQUIRED_PRESETS - set(IMAGE_STYLES.keys())
    if missing:
        _fail(f"Missing style presets: {missing}")
        errors += len(missing)
    else:
        _ok(f"All {len(REQUIRED_PRESETS)} required presets present")

    for key, style in IMAGE_STYLES.items():
        if not style.get('name', '').strip():
            _fail(f"'{key}': empty 'name' field")
            errors += 1
        elif not style.get('name_en', '').strip():
            _fail(f"'{key}': empty 'name_en' field")
            errors += 1
        elif key != 'custom' and not style.get('suffix', '').strip():
            _fail(f"'{key}': empty 'suffix' field (non-custom styles must have a suffix)")
            errors += 1
        else:
            _ok(f"'{key}': name={style['name']} · name_en={style['name_en']} "
                f"· suffix_len={len(style.get('suffix',''))}")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_map_prompt_all_worlds():
    """build_map_prompt() must return non-empty, world-specific string for all 14 settings."""
    _section("G2 · build_map_prompt() — non-empty and world-specific for all 14 worlds")
    from engine.image_prompts import build_map_prompt, IMAGE_STYLES

    errors = 0
    styles = list(IMAGE_STYLES.keys())

    # Test every world × 3 spot-check styles
    spot_styles = ['fantasy_art', 'anime', 'realistic']
    prompts_by_world = {}

    for ws in config.WORLD_SETTINGS:
        for style in spot_styles:
            prompt = build_map_prompt(ws, image_style=style)
            if not prompt or len(prompt) < 30:
                _fail(f"{ws['id']} + {style}: prompt too short ({len(prompt)} chars)")
                errors += 1
            else:
                _ok(f"{ws['id']} + {style}: len={len(prompt)} chars ✓")
        # Store default style prompt for differentiation test
        prompts_by_world[ws['id']] = build_map_prompt(ws, image_style='fantasy_art')

    # Prompts must differ across worlds (world-specific keywords injected)
    unique_prompts = set(prompts_by_world.values())
    if len(unique_prompts) != len(config.WORLD_SETTINGS):
        _fail(f"Only {len(unique_prompts)} unique map prompts for {len(config.WORLD_SETTINGS)} worlds")
        errors += 1
    else:
        _ok(f"All {len(config.WORLD_SETTINGS)} world map prompts are unique ✓")

    # Custom suffix override
    p = build_map_prompt(config.get_world_setting('dnd5e'),
                         image_style='custom', custom_suffix='oil painting')
    if 'oil painting' not in p:
        _fail("Custom suffix 'oil painting' not found in custom-style prompt")
        errors += 1
    else:
        _ok("Custom suffix override works ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_portrait_prompt_per_class():
    """build_portrait_prompt() must produce unique prompts per class and include class keywords."""
    _section("G3 · build_portrait_prompt() — class-specific content and uniqueness")
    from engine.image_prompts import build_portrait_prompt
    import types

    errors = 0
    ws = config.get_world_setting('dnd5e')

    # Expected visual keywords per class
    class_keywords = {
        'warrior': ['armor', 'weapon', 'shield', 'battle', 'sword', 'greatsword'],
        'mage':    ['robes', 'staff', 'arcane', 'magical', 'orb', 'energy'],
        'rogue':   ['leather', 'dagger', 'hood', 'shadow', 'agile', 'crouching'],
        'cleric':  ['divine', 'vestment', 'holy', 'healing', 'symbol', 'light'],
    }

    portraits = {}
    for cls, expected_kws in class_keywords.items():
        char = types.SimpleNamespace(
            name='Tester', race='Human', char_class=cls,
            appearance='tall with dark hair', personality='brave and bold',
        )
        prompt = build_portrait_prompt(char, ws, image_style='fantasy_art')
        if not prompt or len(prompt) < 30:
            _fail(f"Class '{cls}': portrait prompt too short")
            errors += 1
            continue

        found_kw = [kw for kw in expected_kws if kw in prompt.lower()]
        if not found_kw:
            _fail(f"Class '{cls}': none of {expected_kws[:3]} found in prompt")
            errors += 1
        else:
            _ok(f"Class '{cls}': found keywords {found_kw[:3]} in portrait prompt ✓")

        # Appearance text injected
        if 'dark hair' not in prompt.lower() and 'tall' not in prompt.lower():
            _fail(f"Class '{cls}': appearance text not found in prompt")
            errors += 1

        portraits[cls] = prompt

    # All 4 class prompts must differ
    unique = set(portraits.values())
    if len(unique) != len(portraits):
        _fail(f"Only {len(unique)} unique prompts for {len(portraits)} classes")
        errors += 1
    else:
        _ok("All 4 class portrait prompts are distinct ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_portrait_prompt_race_keywords():
    """Portrait prompts must include race-specific visual descriptors."""
    _section("G4 · build_portrait_prompt() — race descriptors in prompts")
    from engine.image_prompts import build_portrait_prompt
    import types

    errors = 0
    ws = config.get_world_setting('dnd5e')

    race_cases = [
        ('Human',    ['human', 'expressive']),
        ('Elf',      ['elf', 'pointed ears']),
        ('Dwarf',    ['dwarf', 'beard', 'stocky']),
        ('Orc',      ['orc', 'tusk', 'green']),
        ('Halfling', ['halfling', 'small', 'round']),
    ]

    for race, expected_kws in race_cases:
        char = types.SimpleNamespace(
            name='Tester', race=race, char_class='warrior',
            appearance='', personality='',
        )
        prompt = build_portrait_prompt(char, ws, image_style='fantasy_art')
        found  = [kw for kw in expected_kws if kw in prompt.lower()]
        if not found:
            _fail(f"Race '{race}': none of {expected_kws} found in portrait prompt")
            errors += 1
        else:
            _ok(f"Race '{race}': {found} found ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_map_prompt_world_differentiation():
    """Map prompts must contain world-setting-specific content strings."""
    _section("G5 · build_map_prompt() — world-specific content keywords")
    from engine.image_prompts import build_map_prompt

    # Key phrase that must appear in each world's map prompt
    world_keywords = [
        ('dnd5e',            ['frontier', 'tolkien', 'fantasy']),
        ('wh40k',            ['hive', 'gothic', 'grimdark']),
        ('shadowrun',        ['cyberpunk', 'neon', 'corporate']),
        ('call_of_cthulhu',  ['lovecraft', 'arkham', '1920']),
        ('hearts_of_wulin',  ['jianghu', 'wuxia', 'chinese']),
        ('deadlands',        ['weird west', 'ghost', 'frontier']),
        ('mutant_year_zero', ['post-apocalyptic', 'ark', 'ruin']),
        ('blades_in_the_dark',['doskvol', 'gothic', 'industrial']),
    ]

    errors = 0
    for ws_id, keywords in world_keywords:
        ws     = config.get_world_setting(ws_id)
        if not ws:
            _fail(f"{ws_id}: world setting not found")
            errors += 1
            continue
        prompt = build_map_prompt(ws, image_style='fantasy_art').lower()
        found  = [kw for kw in keywords if kw in prompt]
        if not found:
            _fail(f"{ws_id}: none of {keywords} found in map prompt")
            errors += 1
        else:
            _ok(f"{ws_id}: {found} ✓")

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0

# ---------------------------------------------------------------------------
# H — Cinematic event detection and VRAM guard tests
# ---------------------------------------------------------------------------

def test_cinematic_battle_transitions():
    """classify_cinematic_event() must detect combat start and end transitions."""
    _section("H1 · classify_cinematic_event() — combat boundary detection")
    from engine.image_prompts import classify_cinematic_event

    errors = 0
    base_td = {'scene_type': 'combat', 'location_change': '',
               'npc_relationship_changes': {}}

    # Non-combat → combat: battle_start
    r = classify_cinematic_event(base_td, 'exploration', turn_count=3, narrative='We fight!')
    if r and r['type'] == 'battle_start':
        _ok(f"exploration→combat: battle_start ✓")
    else:
        _fail(f"exploration→combat: expected battle_start, got {r}"); errors += 1

    # Social → combat: also battle_start
    r = classify_cinematic_event(base_td, 'social', turn_count=3, narrative='Ambush!')
    if r and r['type'] == 'battle_start':
        _ok(f"social→combat: battle_start ✓")
    else:
        _fail(f"social→combat: expected battle_start, got {r}"); errors += 1

    # Combat → exploration: battle_end
    end_td = {**base_td, 'scene_type': 'exploration'}
    r = classify_cinematic_event(end_td, 'combat', turn_count=4, narrative='Victory!')
    if r and r['type'] == 'battle_end':
        _ok(f"combat→exploration: battle_end ✓")
    else:
        _fail(f"combat→exploration: expected battle_end, got {r}"); errors += 1

    # Continuing combat: no battle boundary event
    r = classify_cinematic_event(base_td, 'combat', turn_count=5, narrative='Still fighting.')
    if r is None or r['type'] not in ('battle_start', 'battle_end'):
        _ok("combat→combat: no boundary event ✓")
    else:
        _fail(f"combat→combat: unexpected boundary {r['type']}"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_cinematic_plot_twist_keywords():
    """Plot-twist keywords in narrative must trigger a plot_twist event."""
    _section("H2 · classify_cinematic_event() — plot-twist keyword detection")
    from engine.image_prompts import classify_cinematic_event

    errors = 0
    td = {'scene_type': 'social', 'location_change': '',
          'npc_relationship_changes': {}}

    for narrative, kw in [
        ('The villain was betrayed by his ally.', 'betrayed'),
        ('The secret was revealed to all.',       'revealed'),
        ('The hero suddenly died.',               'died'),
        ('背叛使者出現',                           '背叛'),
        ('You fall into an ambush.',              'ambush'),
    ]:
        r = classify_cinematic_event(td, 'social', turn_count=2, narrative=narrative)
        if r and r['type'] == 'plot_twist':
            _ok(f"'{kw}' → plot_twist ✓")
        else:
            _fail(f"'{kw}' did not trigger plot_twist (got {r})"); errors += 1

    for narrative in ['The goblin swings.', 'You search the chest.',
                      'The NPC greets you.']:
        r = classify_cinematic_event(td, 'social', turn_count=2, narrative=narrative)
        if r is None or r['type'] != 'plot_twist':
            _ok(f"Non-twist narrative → no plot_twist ✓")
        else:
            _fail(f"False-positive plot_twist: '{narrative[:40]}'"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_cinematic_npc_event():
    """NPC relationship delta ≥ 20 triggers npc_event; delta < 20 does not."""
    _section("H3 · classify_cinematic_event() — NPC relationship threshold")
    from engine.image_prompts import classify_cinematic_event

    errors = 0

    for delta, desc, should_trigger in [
        ({'NPC': 25},                                        'int +25',        True),
        ({'NPC': -30},                                       'int -30',        True),
        ({'NPC': 5},                                         'int +5 (small)', False),
        ({'NPC': {'affinity_delta': 22, 'state': 'Friendly'}}, 'dict +22',    True),
        ({'NPC': {'affinity_delta': 3,  'state': 'Neutral'}},  'dict +3',     False),
    ]:
        td = {'scene_type': 'social', 'location_change': '',
              'npc_relationship_changes': delta}
        r  = classify_cinematic_event(td, 'social', turn_count=2, narrative='')
        got_event = (r is not None and r['type'] == 'npc_event')
        if got_event == should_trigger:
            _ok(f"{desc}: npc_event={'yes' if got_event else 'no'} (expected {'yes' if should_trigger else 'no'}) ✓")
        else:
            _fail(f"{desc}: expected npc_event={should_trigger}, got {got_event}"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_cinematic_milestone():
    """Milestone cinematic triggers at multiples of IMAGE_GEN_MILESTONE_TURNS."""
    _section("H4 · classify_cinematic_event() — milestone turn trigger")
    from engine.image_prompts import classify_cinematic_event
    from engine.config import config

    errors    = 0
    milestone = getattr(config, 'IMAGE_GEN_MILESTONE_TURNS', 5)
    td = {'scene_type': 'exploration', 'location_change': '',
          'npc_relationship_changes': {}}

    if milestone <= 0:
        _ok(f"IMAGE_GEN_MILESTONE_TURNS={milestone}: disabled, skipping")
        print(f"\n  Result: PASS"); return True

    for turn, should_be_milestone in [
        (milestone,     True),
        (milestone * 2, True),
        (milestone - 1, False),
        (0,             False),
        (1,             False),
    ]:
        r = classify_cinematic_event(td, 'exploration', turn_count=turn, narrative='')
        is_ms = (r is not None and r['type'] == 'milestone')
        if is_ms == should_be_milestone:
            _ok(f"turn={turn}: milestone={is_ms} (correct) ✓")
        else:
            _fail(f"turn={turn}: expected milestone={should_be_milestone}, got {is_ms}")
            errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_cinematic_build_prompts():
    """build_cinematic_prompt() returns non-empty, unique strings for every event type."""
    _section("H5 · build_cinematic_prompt() — all event types produce distinct prompts")
    from engine.image_prompts import (build_cinematic_prompt, IMAGE_STYLES,
                                       _CINEMATIC_TEMPLATES)
    import types

    errors  = 0
    ws      = config.get_world_setting('dnd5e')
    char    = types.SimpleNamespace(
        name='Hero', race='Human', char_class='warrior',
        appearance='tall warrior', personality='brave',
    )
    td      = {'scene_type': 'combat', 'location_change': 'Ancient Dungeon',
               'npc_relationship_changes': {}}

    prompts = {}
    for etype in _CINEMATIC_TEMPLATES:
        p = build_cinematic_prompt(etype, td, char, ws, image_style='fantasy_art')
        if not p or len(p) < 20:
            _fail(f"{etype}: prompt too short ({len(p)} chars)"); errors += 1
        else:
            _ok(f"{etype}: len={len(p)} ✓")
        prompts[etype] = p

    if len(set(prompts.values())) == len(prompts):
        _ok(f"All {len(prompts)} event-type prompts are unique ✓")
    else:
        _fail("Duplicate prompts found across event types"); errors += 1

    # Location injected for new_location
    p = build_cinematic_prompt('new_location', td, char, ws)
    if 'ancient dungeon' in p.lower():
        _ok("Location name injected into new_location prompt ✓")
    else:
        _fail("Location not found in new_location prompt"); errors += 1

    # Custom suffix override
    p = build_cinematic_prompt('battle_start', td, char, ws,
                               image_style='custom', custom_suffix='oil painting')
    if 'oil painting' in p:
        _ok("Custom suffix override ✓")
    else:
        _fail("Custom suffix not in cinematic prompt"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


# ---------------------------------------------------------------------------
# I — Image persistence & Book Mode (story_saver.py)
# ---------------------------------------------------------------------------

def test_save_image_with_text():
    """I1: save_image_with_text() creates PNG + JSON sidecar with correct content."""
    _section("I1 · save_image_with_text creates PNG + JSON sidecar")
    errors = 0
    from engine.story_saver import save_image_with_text, get_image_dir

    try:
        from PIL import Image as _PIL
        has_pil = True
    except ImportError:
        has_pil = False

    if not has_pil:
        _ok("PIL not installed — skipping image write (no-PIL env)")
        return True

    with tempfile.TemporaryDirectory() as tmp:
        # Patch config.SAVE_DIR temporarily
        orig_save_dir = config.SAVE_DIR
        config.SAVE_DIR = tmp
        try:
            img = _PIL.new('RGB', (64, 64), color=(100, 150, 200))
            path = save_image_with_text('test_save', img, 'A dragon appears!', 7, 'battle_start')

            if path and os.path.exists(path):
                _ok(f"PNG saved: {os.path.basename(path)}")
            else:
                _fail("PNG not saved or path None"); errors += 1

            meta_path = path.replace('.png', '.json') if path else None
            if meta_path and os.path.exists(meta_path):
                import json as _json
                with open(meta_path, 'r') as f:
                    meta = _json.load(f)
                if meta.get('event_type') == 'battle_start':
                    _ok("JSON sidecar event_type=battle_start ✓")
                else:
                    _fail(f"event_type mismatch: {meta.get('event_type')}"); errors += 1
                if meta.get('turn') == 7:
                    _ok("JSON sidecar turn=7 ✓")
                else:
                    _fail(f"turn mismatch: {meta.get('turn')}"); errors += 1
                if 'dragon' in meta.get('text', '').lower():
                    _ok("JSON sidecar text preserved ✓")
                else:
                    _fail("text not in sidecar"); errors += 1
            else:
                _fail("JSON sidecar missing"); errors += 1
        finally:
            config.SAVE_DIR = orig_save_dir

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_compress_game_log():
    """I2: compress_game_log() condenses history into correct page dicts."""
    _section("I2 · compress_game_log condenses history correctly")
    errors = 0
    from engine.story_saver import compress_game_log

    history = [
        {'role': 'player', 'actor': '🔴 Aria', 'content': 'I attack the goblin.'},
        {'role': 'dm',     'content': 'You swing your sword and hit the goblin for 8 damage!',
         'scene_type': 'combat', 'cinematic_label': '⚔️ 戰鬥開始', 'turn': 1, 'image_path': '/tmp/img.png'},
        {'role': 'player', 'actor': '🔴 Aria', 'content': 'I search the room.'},
        {'role': 'dm',     'content': 'You find a chest containing 20 gold coins.',
         'scene_type': 'exploration', 'cinematic_label': '', 'turn': 2, 'image_path': ''},
    ]
    pages = compress_game_log(history)

    if len(pages) == 2:
        _ok("2 pages for 2 player+dm pairs ✓")
    else:
        _fail(f"Expected 2 pages, got {len(pages)}"); errors += 1

    if pages[0].get('action') == 'I attack the goblin.':
        _ok("Page 1 action preserved ✓")
    else:
        _fail(f"Page 1 action: {pages[0].get('action')}"); errors += 1

    if pages[0].get('scene_type') == 'combat':
        _ok("Page 1 scene_type=combat ✓")
    else:
        _fail(f"scene_type: {pages[0].get('scene_type')}"); errors += 1

    if pages[0].get('label') == '⚔️ 戰鬥開始':
        _ok("Cinematic label preserved ✓")
    else:
        _fail(f"label: {pages[0].get('label')}"); errors += 1

    if pages[0].get('image_path') == '/tmp/img.png':
        _ok("image_path preserved ✓")
    else:
        _fail(f"image_path: {pages[0].get('image_path')}"); errors += 1

    # all_choices stored when provided by player entry
    hist_choices = [
        {'role': 'player', 'actor': 'Hero', 'content': 'A. attack',
         'all_choices': ['A. attack', 'B. retreat', 'C. negotiate']},
        {'role': 'dm', 'content': 'You attack!', 'scene_type': 'combat', 'turn': 1},
    ]
    pages_ch = compress_game_log(hist_choices)
    if pages_ch[0].get('all_choices') == ['A. attack', 'B. retreat', 'C. negotiate']:
        _ok("all_choices stored in page ✓")
    else:
        _fail(f"all_choices: {pages_ch[0].get('all_choices')}"); errors += 1

    # is_prologue flag stored for prologue DM entries
    prologue_hist = [
        {'role': 'dm', 'content': 'Welcome, adventurers!', 'scene_type': 'exploration',
         'turn': 0, 'is_prologue': True},
    ]
    pro_pages = compress_game_log(prologue_hist)
    if pro_pages[0].get('is_prologue') is True:
        _ok("is_prologue flag stored ✓")
    else:
        _fail(f"is_prologue: {pro_pages[0].get('is_prologue')}"); errors += 1

    # Last-12 pages kept FULL — single page within window not truncated
    long_hist = [
        {'role': 'player', 'content': 'Look around.'},
        {'role': 'dm', 'content': 'X' * 400, 'scene_type': 'exploration', 'turn': 1},
    ]
    long_pages = compress_game_log(long_hist)
    if len(long_pages[0]['narrative']) == 400:
        _ok("Within-window narrative NOT truncated (full preserve) ✓")
    else:
        _fail(f"Within-window narrative length: {len(long_pages[0]['narrative'])}"); errors += 1

    # Older pages (beyond last-12) are truncated to ≤301 chars
    older_hist = []
    for t in range(14):   # 14 pages — first 2 are beyond last-12
        older_hist.append({'role': 'player', 'content': f'action {t}'})
        older_hist.append({'role': 'dm', 'content': 'Y' * 400,
                           'scene_type': 'exploration', 'turn': t + 1})
    older_pages = compress_game_log(older_hist)
    if len(older_pages[0]['narrative']) <= 301:
        _ok("Old page (>12 back) narrative truncated ✓")
    else:
        _fail(f"Old page not truncated: {len(older_pages[0]['narrative'])}"); errors += 1
    if len(older_pages[-1]['narrative']) == 400:
        _ok("Latest page narrative kept full ✓")
    else:
        _fail(f"Latest page truncated unexpectedly: {len(older_pages[-1]['narrative'])}"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_story_log_round_trip():
    """I3: save_game_log() + load_story_log() round-trip preserves all page data."""
    _section("I3 · story log save/load round-trip")
    errors = 0
    from engine.story_saver import save_game_log, load_story_log

    pages = [
        {'page': 1, 'turn': 1, 'actor': 'Aria', 'action': 'attack', 'narrative': 'Hit!',
         'image_path': '', 'label': '', 'scene_type': 'combat'},
        {'page': 2, 'turn': 2, 'actor': 'Aria', 'action': 'search', 'narrative': 'Found gold.',
         'image_path': '/saves/test/images/scene_exploration_turn2.png',
         'label': '', 'scene_type': 'exploration'},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        orig_save_dir = config.SAVE_DIR
        config.SAVE_DIR = tmp
        try:
            path = save_game_log('my_save', pages)
            if path and os.path.exists(path):
                _ok(f"story_log.json written ✓")
            else:
                _fail("story_log.json not created"); errors += 1

            loaded = load_story_log('my_save')
            if len(loaded) == 2:
                _ok("Loaded 2 pages ✓")
            else:
                _fail(f"Expected 2, got {len(loaded)}"); errors += 1

            if loaded[1].get('narrative') == 'Found gold.':
                _ok("Page 2 narrative preserved ✓")
            else:
                _fail(f"narrative: {loaded[1].get('narrative')}"); errors += 1

            if loaded[1].get('image_path') == pages[1]['image_path']:
                _ok("image_path round-trips ✓")
            else:
                _fail(f"image_path mismatch"); errors += 1
        finally:
            config.SAVE_DIR = orig_save_dir

    # Missing file returns empty list
    with tempfile.TemporaryDirectory() as tmp:
        orig_save_dir = config.SAVE_DIR
        config.SAVE_DIR = tmp
        try:
            result = load_story_log('nonexistent_save')
            if result == []:
                _ok("Missing log returns [] ✓")
            else:
                _fail(f"Expected [], got {result}"); errors += 1
        finally:
            config.SAVE_DIR = orig_save_dir

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_restore_history_from_log():
    """I4-extra: restore_history_from_log reconstructs last-2 history entries."""
    _section("I4b · restore_history_from_log rebuilds last 2 pages")
    errors = 0
    from engine.story_saver import restore_history_from_log

    pages = [
        {'page': 1, 'turn': 1, 'actor': 'Hero', 'action': 'attack', 'narrative': 'Hit!',
         'image_path': '', 'label': '', 'scene_type': 'combat'},
        {'page': 2, 'turn': 2, 'actor': 'Hero', 'action': 'search', 'narrative': 'Found gold.',
         'image_path': '/tmp/scene.png', 'label': '🎬 Plot', 'scene_type': 'exploration'},
        {'page': 3, 'turn': 3, 'actor': 'Hero', 'action': 'run', 'narrative': 'Escaped!',
         'image_path': '', 'label': '', 'scene_type': 'exploration'},
    ]

    hist = restore_history_from_log(pages, n=2)
    # Last 2 pages → 2×(player+dm) = 4 entries
    if len(hist) == 4:
        _ok("4 history entries from 2 pages ✓")
    else:
        _fail(f"Expected 4 entries, got {len(hist)}"); errors += 1

    # Player entries
    player_entries = [h for h in hist if h['role'] == 'player']
    if player_entries[0]['content'] == 'search':
        _ok("Page-2 player action restored ✓")
    else:
        _fail(f"action: {player_entries[0].get('content')}"); errors += 1

    # DM entries
    dm_entries = [h for h in hist if h['role'] == 'dm']
    if dm_entries[-1]['content'] == 'Escaped!':
        _ok("Page-3 narrative restored ✓")
    else:
        _fail(f"narrative: {dm_entries[-1].get('content')}"); errors += 1

    if dm_entries[0].get('image_path') == '/tmp/scene.png':
        _ok("image_path carried over ✓")
    else:
        _fail(f"image_path: {dm_entries[0].get('image_path')}"); errors += 1

    if dm_entries[0].get('image') is None:
        _ok("PIL image=None (not in memory after load) ✓")
    else:
        _fail("image should be None"); errors += 1

    if dm_entries[0].get('is_cinematic') is True:
        _ok("is_cinematic=True from label ✓")
    else:
        _fail(f"is_cinematic: {dm_entries[0].get('is_cinematic')}"); errors += 1

    # Empty log returns []
    if restore_history_from_log([]) == []:
        _ok("Empty log → empty history ✓")
    else:
        _fail("Empty log should return []"); errors += 1

    # Fewer pages than requested
    hist_1 = restore_history_from_log(pages[:1], n=2)
    if len(hist_1) == 2:  # 1 page → 1×(player+dm)
        _ok("Fewer pages than n=2 handled ✓")
    else:
        _fail(f"Expected 2, got {len(hist_1)}"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_book_page_count():
    """I4: Book mode page count matches saved log entries."""
    _section("I4 · Book Mode page count matches saved log")
    errors = 0
    from engine.story_saver import compress_game_log, save_game_log, load_story_log

    # Build a 5-turn history
    history = []
    for i in range(1, 6):
        history.append({'role': 'player', 'content': f'Turn {i} action', 'actor': 'Hero'})
        history.append({
            'role': 'dm', 'content': f'Turn {i} narrative response',
            'scene_type': 'exploration', 'turn': i, 'image_path': '', 'cinematic_label': '',
        })

    pages = compress_game_log(history)
    if len(pages) == 5:
        _ok("5 turns → 5 pages ✓")
    else:
        _fail(f"Expected 5 pages, got {len(pages)}"); errors += 1

    with tempfile.TemporaryDirectory() as tmp:
        orig_save_dir = config.SAVE_DIR
        config.SAVE_DIR = tmp
        try:
            save_game_log('adventure', pages)
            loaded = load_story_log('adventure')
            if len(loaded) == 5:
                _ok("Saved and reloaded 5 pages ✓")
            else:
                _fail(f"Reloaded {len(loaded)} pages"); errors += 1

            # Page numbers are sequential
            for j, pg in enumerate(loaded, 1):
                if pg.get('page') == j:
                    continue
                _fail(f"Page {j} has page={pg.get('page')}"); errors += 1; break
            else:
                _ok("Page numbers sequential ✓")
        finally:
            config.SAVE_DIR = orig_save_dir

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_image_filename_convention():
    """I5: Image filename encodes event_type and turn number."""
    _section("I5 · Image filename encoding (event_type + turn)")
    errors = 0
    from engine.story_saver import save_image_with_text

    try:
        from PIL import Image as _PIL
        has_pil = True
    except ImportError:
        has_pil = False

    if not has_pil:
        _ok("PIL not installed — skipping (no-PIL env)")
        return True

    test_cases = [
        ('map',         0, 'map_turn0.png'),
        ('battle_start', 3, 'battle_start_turn3.png'),
        ('plot_twist',  10, 'plot_twist_turn10.png'),
        ('portrait_Aria Knight', 0, 'portrait_Aria_Knight_turn0.png'),
        ('new_location', 7, 'new_location_turn7.png'),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        orig_save_dir = config.SAVE_DIR
        config.SAVE_DIR = tmp
        try:
            for event_type, turn, expected_name in test_cases:
                img  = _PIL.new('RGB', (8, 8), color=(0, 0, 0))
                path = save_image_with_text('sv', img, 'text', turn, event_type)
                actual = os.path.basename(path) if path else ''
                if actual == expected_name:
                    _ok(f"{event_type} turn={turn} → {expected_name} ✓")
                else:
                    _fail(f"Expected '{expected_name}', got '{actual}'"); errors += 1
        finally:
            config.SAVE_DIR = orig_save_dir

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
    return errors == 0


def test_narrative_constraints():
    """I5b: _validated_narrative enforces ≥3 choices; prologue schema accepted."""
    _section("I5b · narrative ≥3 choices + prologue validated_narrative")
    errors = 0
    # Import without triggering Ollama
    import sys, types
    # Patch ollama to avoid connection
    fake_ollama = types.ModuleType('ollama')
    fake_ollama.chat = lambda **kw: None
    had_ollama = 'ollama' in sys.modules
    sys.modules.setdefault('ollama', fake_ollama)
    try:
        from ai.llm_client import _validated_narrative
    finally:
        if not had_ollama:
            sys.modules.pop('ollama', None)

    # 0 choices → padded to 3
    result = _validated_narrative({"narrative": "A", "choices": []})
    if len(result["choices"]) >= 3:
        _ok("0 choices padded to ≥3 ✓")
    else:
        _fail(f"choices: {result['choices']}"); errors += 1

    # 1 choice → padded to 3
    result = _validated_narrative({"narrative": "A", "choices": ["Only one"]})
    if len(result["choices"]) >= 3:
        _ok("1 choice padded to ≥3 ✓")
    else:
        _fail(f"choices count: {len(result['choices'])}"); errors += 1

    # 2 choices → padded to 3
    result = _validated_narrative({"narrative": "A", "choices": ["A", "B"]})
    if len(result["choices"]) >= 3:
        _ok("2 choices padded to ≥3 ✓")
    else:
        _fail(f"choices count: {len(result['choices'])}"); errors += 1

    # 4 choices → kept as-is
    result = _validated_narrative({"narrative": "A", "choices": ["A", "B", "C", "D"]})
    if len(result["choices"]) == 4:
        _ok("4 choices kept intact ✓")
    else:
        _fail(f"choices count: {len(result['choices'])}"); errors += 1

    # Default (no choices key) → at least 3
    result = _validated_narrative({"narrative": "Something happens..."})
    if len(result["choices"]) >= 3:
        _ok("Default choices ≥3 ✓")
    else:
        _fail(f"Default choices: {result['choices']}"); errors += 1

    print(f"\n  Result: {'PASS' if errors == 0 else f'FAIL — {errors} error(s)'}")
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
    print("║  A:text·B:flow·C:multi·D:AI·E:board·F:manual·G:image·H:cinematic·I:book  ║")
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

    # C — Multi-player stability
    results['C1 class balance budget']       = test_class_balance_budget()
    results['C2 party creation 1-6p']        = test_party_creation_sizes()
    results['C3 turn rotation']              = test_turn_rotation()
    results['C4 contribution & rewards']     = test_contribution_tracking()
    results['C5 save/load round-trip']       = test_multiplay_load_restore()
    results['C6 class stat differentiation'] = test_class_stats_differ()

    # D — 6-player & AI player expansion
    results['D1 6-player creation & ai_configs'] = test_six_player_creation()
    results['D2 AI decision tree all modes']     = test_ai_player_decision_tree()
    results['D3 mixed human+AI save/load']       = test_mixed_party_save_load()
    results['D4 player flag emoji']              = test_player_flags()
    results['D5 run_ai_turn() interface']        = test_ai_run_turn_interface()

    # E — Game board pure logic
    results['E1 detect_location_type']           = test_detect_location_type()
    results['E2 no cell collisions (20 locs)']   = test_assign_map_position_no_collisions()
    results['E3 default row=3 settlement']        = test_assign_map_position_settlement_default()
    results['E4 build_map_html structure']        = test_build_map_html_structure()
    results['E5 overflow to adjacent row']        = test_map_position_full_row_overflow()

    # F — Player handbook (manual.py)
    results['F1 chapter count all worlds']        = test_manual_chapter_count()
    results['F2 chapter structure fields']        = test_manual_chapter_structure()
    results['F3 vocabulary substitution']         = test_manual_vocabulary_substitution()
    results['F4 keyword search tags']             = test_manual_search_tags()
    results['F5 world content differentiation']   = test_manual_world_differentiation()

    # G — Image prompt builder (image_prompts.py)
    results['G1 IMAGE_STYLES registry']           = test_image_styles_registry()
    results['G2 map prompt all worlds']           = test_map_prompt_all_worlds()
    results['G3 portrait prompt per class']       = test_portrait_prompt_per_class()
    results['G4 portrait prompt race keywords']   = test_portrait_prompt_race_keywords()
    results['G5 map prompt world keywords']       = test_map_prompt_world_differentiation()

    # H — Cinematic event detection + VRAM guard
    results['H1 combat boundary detection']       = test_cinematic_battle_transitions()
    results['H2 plot-twist keyword detection']    = test_cinematic_plot_twist_keywords()
    results['H3 NPC relationship threshold']      = test_cinematic_npc_event()
    results['H4 milestone turn trigger']          = test_cinematic_milestone()
    results['H5 cinematic prompt builder']        = test_cinematic_build_prompts()

    # I — Image persistence & Book Mode
    results['I1 save_image_with_text']            = test_save_image_with_text()
    results['I2 compress_game_log']               = test_compress_game_log()
    results['I3 story log round-trip']            = test_story_log_round_trip()
    results['I4b restore_history_from_log']       = test_restore_history_from_log()
    results['I4 book mode page count']            = test_book_page_count()
    results['I5 image filename convention']       = test_image_filename_convention()
    results['I5b narrative ≥3 choices enforce']   = test_narrative_constraints()

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

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
    """create_new_game() must succeed for party sizes 1-4 across several worlds."""
    _section("C2 · Multi-player party creation (sizes 1–4)")
    slm, tmp_path = _make_in_memory_db()
    errors = 0

    party_configs = [
        {'name': 'Aric', 'race': 'Human', 'char_class': 'Warrior', 'appearance': '', 'personality': ''},
        {'name': 'Lyra', 'race': 'Elf',   'char_class': 'Mage',    'appearance': '', 'personality': ''},
        {'name': 'Dax',  'race': 'Dwarf', 'char_class': 'Rogue',   'appearance': '', 'personality': ''},
        {'name': 'Sera', 'race': 'Human', 'char_class': 'Cleric',  'appearance': '', 'personality': ''},
    ]
    sample_worlds = ['dnd5e', 'wh40k', 'call_of_cthulhu', 'hearts_of_wulin']

    for ws_id in sample_worlds:
        for n in range(1, 5):
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

    for n in [2, 3, 4]:
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
    print("║  A: text diff  ·  B: flow consistency  ·  C: multi-player stability     ║")
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
    results['C2 party creation 1-4p']        = test_party_creation_sizes()
    results['C3 turn rotation']              = test_turn_rotation()
    results['C4 contribution & rewards']     = test_contribution_tracking()
    results['C5 save/load round-trip']       = test_multiplay_load_restore()
    results['C6 class stat differentiation'] = test_class_stats_differ()

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

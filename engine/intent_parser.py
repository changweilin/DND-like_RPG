import re
import random
from data.monsters import get_monster_by_name, MONSTER_ROSTER
from engine.combat import detect_class_ability

# ---------------------------------------------------------------------------
# Affinity delta table  (P3 — replaces LLM numeric affinity_delta)
# ---------------------------------------------------------------------------

# Normalise the outcome_label strings used in events.py to lowercase keys
_OUTCOME_NORM = {
    'CRITICAL SUCCESS': 'critical_success',
    'SUCCESS':          'success',
    'FAILURE':          'failure',
    'CRITICAL FAILURE': 'critical_failure',
    'NO_ROLL':          'no_roll',
}

# (action_type, outcome_key) → affinity delta applied to all present NPCs
_AFFINITY_RULES = {
    ('attack',        'critical_success'): -30,
    ('attack',        'success'):          -20,
    ('attack',        'failure'):          -10,
    ('attack',        'critical_failure'): -5,
    ('social',        'critical_success'): +20,
    ('social',        'success'):          +10,
    ('social',        'failure'):          -5,
    ('social',        'critical_failure'): -15,
    ('magic',         'critical_success'): +5,
    ('magic',         'success'):          +3,
    ('magic',         'failure'):          -3,
    ('magic',         'critical_failure'): -5,
    ('skill_check',   'critical_success'): +5,
    ('skill_check',   'success'):          +3,
    ('skill_check',   'failure'):          0,
    ('skill_check',   'critical_failure'): -3,
    ('explore',       'critical_success'): +3,
    ('explore',       'success'):          +1,
    ('explore',       'failure'):          0,
    ('explore',       'critical_failure'): -2,
    ('direct_action', 'no_roll'):          0,
}


def calculate_affinity_delta(action_type, outcome_label):
    """
    Return the rule-engine affinity delta for all NPCs present in the scene.

    outcome_label is the uppercase string used in events.py
    (e.g. 'SUCCESS', 'CRITICAL FAILURE', 'NO_ROLL').
    Returns 0 for unrecognised combinations.
    """
    outcome_key = _OUTCOME_NORM.get(outcome_label, 'no_roll')
    return _AFFINITY_RULES.get((action_type, outcome_key), 0)


# ---------------------------------------------------------------------------
# Entity stat tables  (P2 — replaces LLM numeric generation)
# ---------------------------------------------------------------------------

# Base stats by (entity_type, difficulty): (hp, atk, def_stat)
_ENTITY_STAT_TABLE = {
    'monster':  {'easy': (15, 8, 8),   'normal': (25, 12, 10), 'hard': (40, 16, 14), 'deadly': (60, 20, 18)},
    'boss':     {'easy': (40, 12, 12), 'normal': (60, 18, 16), 'hard': (100, 22, 20), 'deadly': (150, 28, 24)},
    'npc':      {'easy': (10, 5, 5),   'normal': (15, 8, 8),   'hard': (20, 12, 10),  'deadly': (25, 14, 12)},
    'guard':    {'easy': (12, 8, 10),  'normal': (20, 10, 12), 'hard': (30, 14, 16),  'deadly': (45, 18, 20)},
    'merchant': {'easy': (8, 4, 4),    'normal': (10, 5, 5),   'hard': (12, 6, 6),    'deadly': (15, 8, 8)},
}

_BOSS_RE     = re.compile(r'(boss|chief|king|queen|lord|dragon|demon|devil|大魔王|首領|王|魔王|頭目|領主)', re.I)
_GUARD_RE    = re.compile(r'(guard|soldier|warrior|knight|patrol|守衛|士兵|騎士|衛兵|哨兵)', re.I)
_MERCHANT_RE = re.compile(r'(merchant|trader|vendor|shopkeeper|peddler|商人|小販|攤販|商販)', re.I)


def detect_entity_type(entity_name, action_type):
    """
    Infer entity type from name keywords and the action that triggered the encounter.
    Checks the monster roster first, then falls back to regex heuristics.
    Returns one of: 'boss' | 'guard' | 'merchant' | 'monster' | 'npc'
    """
    # Check predefined roster first — most reliable signal
    roster_entry = get_monster_by_name(entity_name)
    if roster_entry:
        return roster_entry.get('type', 'monster')

    if _BOSS_RE.search(entity_name):
        return 'boss'
    if _GUARD_RE.search(entity_name):
        return 'guard'
    if _MERCHANT_RE.search(entity_name):
        return 'merchant'
    if action_type == 'attack':
        return 'monster'
    return 'npc'


# Difficulty multipliers relative to "normal" base stats
_DIFF_SCALE = {
    'easy':   0.60,
    'normal': 1.00,
    'hard':   1.60,
    'deadly': 2.40,
}


def get_entity_base_stats(entity_type, difficulty, entity_name=None,
                          player_level=1):
    """
    Return (hp, atk, def_stat) for the entity.

    Priority:
      1. Named monster in MONSTER_ROSTER (scaled by difficulty + player level).
      2. Generic lookup table with ±20 % variance (existing behaviour).

    entity_name is optional; pass it to enable roster lookup.
    player_level (1–10) adds a tier-gap multiplier so monsters always feel
    appropriately challenging:
      Lv 1-2  → expect Tier 1  (easy)
      Lv 3-5  → expect Tier 2  (normal)
      Lv 6-8  → expect Tier 3  (hard)
      Lv 9-10 → expect Tier 4  (deadly)
    If the monster's actual tier is below the player's expected tier, stats
    are boosted.  If above, they are slightly reduced to keep the fight
    survivable (not replaced — player freedom is preserved).
    """
    diff_key = (difficulty or 'normal').lower()
    vary = lambda v: max(1, int(v * random.uniform(0.8, 1.2)))

    # Map player level → expected tier
    if player_level <= 2:
        expected_tier = 1
    elif player_level <= 5:
        expected_tier = 2
    elif player_level <= 8:
        expected_tier = 3
    else:
        expected_tier = 4

    if entity_name:
        roster_entry = get_monster_by_name(entity_name)
        if roster_entry:
            scale = _DIFF_SCALE.get(diff_key, 1.0)
            monster_tier = roster_entry.get('tier', 2)
            # Tier gap correction: +15% per tier below expected, -10% per tier above
            tier_gap = expected_tier - monster_tier
            if tier_gap > 0:
                scale *= (1.0 + 0.15 * tier_gap)   # boost weak monsters
            elif tier_gap < 0:
                scale *= max(0.6, 1.0 + 0.10 * tier_gap)  # soften overleveled monsters
            hp       = vary(int(roster_entry['hp']       * scale))
            atk      = vary(int(roster_entry['atk']      * scale))
            def_stat = vary(int(roster_entry['def_stat'] * scale))
            return hp, atk, def_stat

    row = _ENTITY_STAT_TABLE.get(entity_type, _ENTITY_STAT_TABLE['npc'])
    hp, atk, def_stat = row.get(diff_key, row['normal'])
    return vary(hp), vary(atk), vary(def_stat)


# ---------------------------------------------------------------------------
# Pattern tables  (P1 — intent parsing)
# ---------------------------------------------------------------------------

_ATTACK_RE = re.compile(
    r'(攻擊|砍|刺|切|打|射|殺|衝|傷害|刺穿|揮砍|痛擊|刺殺|暗殺|重擊|踢|踹|打倒|斬|劈|'
    r'blast|attack|strike|hit|slash|stab|shoot|\bfire\b|'
    r'charge|swing|smash|pummel|punch|kick|thrust|cleave|'
    r'hurt|wound|assault|maul|batter|slay|dispatch|lunge|ram|gore|shred|'
    r'cut down|stab at|strike at|take down|beat up|beat down|tear apart)',
    re.I,
)
_MAGIC_RE = re.compile(
    r'(施法|召喚|咒語|法術|cast|spell|summon|invoke|enchant|fireball|lightning|'
    r'frost|arcane|hex|curse|魔法)',
    re.I,
)
_SOCIAL_RE = re.compile(
    r'(說話|交談|詢問|告訴|說服|勸說|威嚇|恐嚇|交涉|遊說|問|說|告知|'
    r'talk|speak|tell|ask|say|persuade|convince|negotiate|'
    r'intimidate|threaten|charm|bribe|deceive|lie|converse)',
    re.I,
)
_EXPLORE_RE = re.compile(
    r'(搜索|察看|調查|觀察|檢查|看看|找|偵測|查看|環顧|'
    r'search|examine|investigate|look|check|inspect|scout|survey|scan)',
    re.I,
)
_STEALTH_RE = re.compile(
    r'(偷偷|潛行|躲藏|隱身|悄悄|sneaky|sneak|stealth|hide|conceal|shadow|lurk)',
    re.I,
)
_REST_RE = re.compile(
    r'(休息|睡覺|紮營|冥想|恢復體力|rest|sleep|camp|meditate|take a break|recover)',
    re.I,
)
# Item use intent — "use potion", "drink potion", "throw bomb", "喝藥水", "使用"
_ITEM_USE_RE = re.compile(
    r'(使用|喝|服用|吞|投擲|丟|use\s+(?:a\s+|the\s+|my\s+)?|drink\s+|consume\s+|throw\s+|toss\s+)',
    re.I,
)
# Capture item name after the verb (greedy, up to end of string)
_ITEM_NAME_RE = re.compile(
    r'(?:使用|喝|服用|吞|投擲|丟|use(?:\s+(?:a|the|my))?|drink|consume|throw|toss)\s+(.+)',
    re.I,
)
# Shop buy/sell intents
_BUY_RE = re.compile(
    r'(購買|買|採購|buy|purchase|get\s+(?:a\s+|the\s+)?|acquire)',
    re.I,
)
_SELL_RE = re.compile(
    r'(賣|出售|販賣|sell|trade\s+(?:away)?|exchange)',
    re.I,
)
# Equip / unequip intents
_EQUIP_RE = re.compile(
    r'(裝備|佩戴|穿上|戴上|equip|wear|put\s+on|wield)',
    re.I,
)
_UNEQUIP_RE = re.compile(
    r'(卸下|脫下|取下|unequip|remove|take\s+off|unsheathe)',
    re.I,
)
# Generic item-name capture after equip/unequip verb
_EQUIP_NAME_RE = re.compile(
    r'(?:裝備|佩戴|穿上|戴上|卸下|脫下|取下|equip|wear|put\s+on|wield|remove|take\s+off|unequip)\s+(.+)',
    re.I,
)
# Item-name capture after buy/sell verb
_TRADE_NAME_RE = re.compile(
    r'(?:購買|買|採購|賣|出售|販賣|buy|purchase|sell|acquire)\s+(.+)',
    re.I,
)
# Spell name capture — "cast <spell>", "施展 <spell>", "use <spell> spell"
_CAST_NAME_RE = re.compile(
    r'(?:cast|施展|念|use\s+(?:a\s+)?|施放)\s+(.+?)(?:\s+(?:spell|術|咒)?)?$',
    re.I,
)

# Ordered: later entries with longer patterns must not shadow earlier ones
_SKILL_PATTERNS = [
    ('stealth',      re.compile(r'(偷偷|潛行|躲藏|隱身|悄悄|sneak|stealth|hide|conceal|lurk)', re.I)),
    ('persuasion',   re.compile(r'(說服|勸說|遊說|persuade|convince|negotiate|charm|bribe)', re.I)),
    ('intimidation', re.compile(r'(威嚇|恐嚇|威脅|intimidate|threaten|menace)', re.I)),
    ('athletics',    re.compile(r'(攀爬|跳躍|游泳|翻越|奔跑|climb|jump|leap|swim|sprint|vault|run)', re.I)),
    ('acrobatics',   re.compile(r'(翻滾|閃避|雜技|dodge|roll|tumble|flip|somersault)', re.I)),
    ('perception',   re.compile(r'(觀察|察覺|感知|偵測|look|notice|spot|listen|detect|perceive)', re.I)),
    ('medicine',     re.compile(r'(治療|急救|包紮|heal|treat|bandage|cure|mend)', re.I)),
    ('arcana',       re.compile(r'(魔法|法術|咒語|magic|arcane|spell|enchant)', re.I)),
]

_DC_TABLE = {'easy': 10, 'normal': 15, 'hard': 20, 'deadly': 25}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_target(player_action, known_entities):
    """Return the known_entities key whose name appears in player_action, or ''."""
    if not known_entities:
        return ''
    action_low = player_action.lower()
    # prefer longer names so "goblin king" beats "goblin"
    for name in sorted(known_entities.keys(), key=len, reverse=True):
        if name in action_low:
            return name
    return ''


def _detect_skill(player_action):
    """Return the first matching skill name, or ''."""
    for skill, pattern in _SKILL_PATTERNS:
        if pattern.search(player_action):
            return skill
    return ''


def _intent(action_type, requires_roll, skill, dc, target, player_action,
            class_ability=None):
    return {
        'thought_process': '',   # rule engine skips chain-of-thought
        'action_type':     action_type,
        'requires_roll':   requires_roll,
        'skill':           skill,
        'dc':              dc,
        'target':          target,
        'summary':         player_action,
        'class_ability':   class_ability,   # ability key or None
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_parse(player_action, known_entities, difficulty, char_class=None):
    """
    Rule-based intent parser — hybrid layer 1.

    Returns a validated intent dict when a pattern is matched with high
    confidence, or None to signal the caller to fall back to the LLM.

    Confident-match criteria:
      - action_type clearly identified by a dedicated keyword pattern
      - For skill_check / social with roll: at least one skill keyword matched

    Keeps thought_process empty (rule engine needs no chain-of-thought).
    char_class — optional character class string used to detect class abilities.
    """
    dc     = _DC_TABLE.get((difficulty or 'normal').lower(), 15)
    skill  = _detect_skill(player_action)
    target = _find_target(player_action, known_entities)

    # --- Class ability detection (checked before generic patterns) ---
    class_ability = None
    if char_class:
        class_ability = detect_class_ability(player_action, char_class)

    # --- Magic (checked before attack: "cast fireball" must not match attack's "fire") ---
    if _MAGIC_RE.search(player_action):
        magic_skill = skill if skill in ('arcana', 'medicine') else 'arcana'
        return _intent('magic', True, magic_skill, dc, target, player_action,
                       class_ability=class_ability)

    # --- Attack ---
    if _ATTACK_RE.search(player_action):
        # combat roll is handled by Step 5 (combat engine), so requires_roll=False here
        return _intent('attack', False, '', 0, target, player_action,
                       class_ability=class_ability)

    # --- Utility class ability (heal, shield, turn undead, evasion) ---
    # Routed as 'magic' action so MP cost is applied; no attack roll needed.
    if class_ability:
        from engine.combat import get_ability_definition
        adef = get_ability_definition(char_class, class_ability)
        if adef and (adef.get('heal_dice') or adef.get('def_bonus')
                     or adef.get('affects_undead_only') or adef.get('damage_reduction')):
            return _intent('magic', False, 'arcana', 0, target, player_action,
                           class_ability=class_ability)

    # --- Stealth (before generic social/explore to take priority) ---
    if _STEALTH_RE.search(player_action):
        return _intent('skill_check', True, 'stealth', dc, target, player_action,
                       class_ability=class_ability)

    # --- Social with a contested skill (persuasion / intimidation) → roll ---
    if _SOCIAL_RE.search(player_action) and skill in ('persuasion', 'intimidation'):
        return _intent('social', True, skill, dc, target, player_action,
                       class_ability=class_ability)

    # --- Social without a detectable contested skill → no roll (direct action) ---
    if _SOCIAL_RE.search(player_action):
        return _intent('social', False, '', 0, target, player_action,
                       class_ability=class_ability)

    # --- Physical skill checks (athletics / acrobatics) ---
    if skill in ('athletics', 'acrobatics'):
        return _intent('skill_check', True, skill, dc, target, player_action,
                       class_ability=class_ability)

    # --- Healing / medicine action ---
    if skill == 'medicine':
        return _intent('skill_check', True, 'medicine', dc, target, player_action,
                       class_ability=class_ability)

    # --- Explore / perception check ---
    if _EXPLORE_RE.search(player_action):
        has_perception = skill == 'perception'
        return _intent(
            'explore',
            has_perception,
            'perception' if has_perception else '',
            dc if has_perception else 0,
            target,
            player_action,
            class_ability=class_ability,
        )

    # --- Item use ---
    if _ITEM_USE_RE.search(player_action):
        m = _ITEM_NAME_RE.search(player_action)
        item_name = m.group(1).strip() if m else ''
        return _intent('item_use', False, '', 0, item_name, player_action,
                       class_ability=class_ability)

    # --- Rest (short or long) ---
    if _REST_RE.search(player_action):
        # Distinguish long rest from short rest by keyword
        is_long = bool(re.search(r'(long\s+rest|紮營|長時間休息|完全恢復|camp\s+(?:for\s+)?(?:the\s+)?night)', player_action, re.I))
        rest_type = 'long_rest' if is_long else 'short_rest'
        return _intent(rest_type, False, '', 0, '', player_action,
                       class_ability=class_ability)

    # --- Buy ---
    if _BUY_RE.search(player_action) and not _ATTACK_RE.search(player_action):
        m = _TRADE_NAME_RE.search(player_action)
        item_name = m.group(1).strip() if m else ''
        return _intent('buy', False, '', 0, item_name, player_action,
                       class_ability=class_ability)

    # --- Sell ---
    if _SELL_RE.search(player_action):
        m = _TRADE_NAME_RE.search(player_action)
        item_name = m.group(1).strip() if m else ''
        return _intent('sell', False, '', 0, item_name, player_action,
                       class_ability=class_ability)

    # --- Equip ---
    if _EQUIP_RE.search(player_action) and not _UNEQUIP_RE.search(player_action):
        m = _EQUIP_NAME_RE.search(player_action)
        item_name = m.group(1).strip() if m else ''
        return _intent('equip', False, '', 0, item_name, player_action,
                       class_ability=class_ability)

    # --- Unequip ---
    if _UNEQUIP_RE.search(player_action):
        m = _EQUIP_NAME_RE.search(player_action)
        item_name = m.group(1).strip() if m else ''
        return _intent('unequip', False, '', 0, item_name, player_action,
                       class_ability=class_ability)

    # Pattern not recognised with sufficient confidence → LLM fallback
    return None

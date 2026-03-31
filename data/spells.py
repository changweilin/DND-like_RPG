# Named spell compendium — single source of truth for deterministic spell resolution.
# Each entry: {mp_cost, damage_dice?, heal_dice?, status_apply?, aoe?, element?, dc?,
#              undead_only?, description}
SPELL_COMPENDIUM = {
    # ── Mage spells ──────────────────────────────────────────────────────────────
    'fireball':          {'mp_cost': 4, 'damage_dice': '3d6', 'aoe': True,
                          'element': 'fire', 'dc': 15,
                          'description': 'Hurl a ball of fire at all enemies'},
    'magic missile':     {'mp_cost': 2, 'damage_dice': '1d4+1',
                          'element': 'force',
                          'description': 'Unerring bolt of magical force'},
    'ice shard':         {'mp_cost': 2, 'damage_dice': '1d6',
                          'element': 'ice', 'status_apply': 'slowed',
                          'description': 'Shard of ice that slows the target'},
    'lightning bolt':    {'mp_cost': 5, 'damage_dice': '3d6',
                          'element': 'lightning',
                          'description': 'Devastating bolt of lightning'},
    'arcane bolt':       {'mp_cost': 1, 'damage_dice': '1d6',
                          'element': 'arcane',
                          'description': 'Basic arcane projectile'},
    'frost nova':        {'mp_cost': 4, 'damage_dice': '2d4', 'aoe': True,
                          'element': 'ice', 'status_apply': 'stunned',
                          'description': 'Burst of ice that stuns all nearby foes'},
    # Chinese aliases (mage)
    '火球術':            {'mp_cost': 4, 'damage_dice': '3d6', 'aoe': True, 'element': 'fire', 'dc': 15},
    '冰矢':              {'mp_cost': 2, 'damage_dice': '1d6', 'element': 'ice', 'status_apply': 'slowed'},
    '閃電術':            {'mp_cost': 5, 'damage_dice': '3d6', 'element': 'lightning'},
    '魔法飛彈':          {'mp_cost': 2, 'damage_dice': '1d4+1', 'element': 'force'},
    '奧術射線':          {'mp_cost': 1, 'damage_dice': '1d6', 'element': 'arcane'},
    '冰霜新星':          {'mp_cost': 4, 'damage_dice': '2d4', 'aoe': True, 'element': 'ice', 'status_apply': 'stunned'},

    # ── Cleric spells ─────────────────────────────────────────────────────────────
    'heal':              {'mp_cost': 3, 'heal_dice': '2d6+3',
                          'description': 'Restore health to yourself or an ally'},
    'mass heal':         {'mp_cost': 6, 'heal_dice': '2d4+2', 'aoe': True,
                          'description': 'Heal all party members'},
    'smite':             {'mp_cost': 2, 'damage_dice': '2d4',
                          'element': 'radiant',
                          'description': 'Channel divine power into a weapon strike'},
    'bless':             {'mp_cost': 2, 'status_apply': 'blessed',
                          'description': 'Bless an ally granting +2 to all rolls'},
    'holy fire':         {'mp_cost': 4, 'damage_dice': '2d6',
                          'element': 'radiant', 'status_apply': 'burning',
                          'description': 'Divine flames especially potent against undead'},
    'banish undead':     {'mp_cost': 3, 'damage_dice': '3d6',
                          'element': 'radiant', 'undead_only': True,
                          'description': 'Radiant burst that devastates undead'},
    # Chinese aliases (cleric)
    '治癒術':            {'mp_cost': 3, 'heal_dice': '2d6+3'},
    '群體治療':          {'mp_cost': 6, 'heal_dice': '2d4+2', 'aoe': True},
    '神聖打擊':          {'mp_cost': 2, 'damage_dice': '2d4', 'element': 'radiant'},
    '祝福':              {'mp_cost': 2, 'status_apply': 'blessed'},
    '神聖火焰':          {'mp_cost': 4, 'damage_dice': '2d6', 'element': 'radiant', 'status_apply': 'burning'},
    '驅除不死':          {'mp_cost': 3, 'damage_dice': '3d6', 'element': 'radiant', 'undead_only': True},

    # ── Rogue spells (limited) ─────────────────────────────────────────────────
    'shadow step':       {'mp_cost': 2, 'status_apply': 'invisible',
                          'description': 'Briefly meld into the shadows'},
    'poison blade':      {'mp_cost': 2, 'damage_dice': '1d4',
                          'status_apply': 'poisoned',
                          'description': 'Coat weapon with a magical poison'},
    # Chinese aliases (rogue)
    '暗影步':            {'mp_cost': 2, 'status_apply': 'invisible'},
    '毒刃':              {'mp_cost': 2, 'damage_dice': '1d4', 'status_apply': 'poisoned'},

    # ── Warrior battle-magic ──────────────────────────────────────────────────
    'battle cry':        {'mp_cost': 2, 'status_apply': 'strengthened',
                          'description': 'A war cry that sharpens your battle focus'},
    'shield bash':       {'mp_cost': 1, 'damage_dice': '1d4',
                          'status_apply': 'stunned',
                          'description': 'Strike with your shield to stun the foe'},
    # Chinese aliases (warrior)
    '戰吼':              {'mp_cost': 2, 'status_apply': 'strengthened'},
    '盾擊':              {'mp_cost': 1, 'damage_dice': '1d4', 'status_apply': 'stunned'},
}

# Spells available by class (for UI display — English canonical names)
CLASS_SPELLS = {
    'mage':    ['arcane bolt', 'magic missile', 'ice shard', 'fireball',
                'lightning bolt', 'frost nova'],
    'cleric':  ['heal', 'smite', 'bless', 'holy fire', 'mass heal', 'banish undead'],
    'rogue':   ['shadow step', 'poison blade'],
    'warrior': ['battle cry', 'shield bash'],
}


def get_spell(spell_name):
    """Return spell dict by name (case-insensitive) or None."""
    if not spell_name:
        return None
    return SPELL_COMPENDIUM.get(spell_name.lower()) or SPELL_COMPENDIUM.get(spell_name)

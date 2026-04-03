# Shop catalogue — items available from merchants.
# Each entry: {price, type, description, stat bonuses...}
# type ∈ {consumable, throwable, weapon, armor, accessory, tool, scroll, upgrade}
SHOP_CATALOGUE = {
    # ── Consumables ──────────────────────────────────────────────────────────
    'healing potion':          {'price': 50,  'type': 'consumable',
                                'description': 'Restores 2d4+2 HP'},
    'greater healing potion':  {'price': 120, 'type': 'consumable',
                                'description': 'Restores 4d4+4 HP'},
    'antidote':                {'price': 40,  'type': 'consumable',
                                'description': 'Cures poisoned status'},
    'mana potion':             {'price': 60,  'type': 'consumable',
                                'description': 'Restores 20 MP'},
    'stamina elixir':          {'price': 80,  'type': 'consumable',
                                'description': 'Restores 30 MP and cures stunned/slowed'},
    'rations':                 {'price': 20,  'type': 'consumable',
                                'description': 'Trail food — restores 1d4 HP, removes starving penalty'},
    'smelling salts':          {'price': 35,  'type': 'consumable',
                                'description': 'Instantly clears stunned status'},
    'bandages':                {'price': 25,  'type': 'consumable',
                                'description': 'Stops bleeding status'},

    # ── Throwables ───────────────────────────────────────────────────────────
    'poison vial':             {'price': 60,  'type': 'throwable',
                                'description': 'Throw to apply poisoned status'},
    'bomb':                    {'price': 80,  'type': 'throwable',
                                'description': 'AoE 2d6 explosive damage'},
    'torch':                   {'price': 10,  'type': 'throwable',
                                'description': '1d4 fire damage + burning status'},
    'flash powder':            {'price': 45,  'type': 'throwable',
                                'description': 'Blinds all enemies for 1 turn (no damage)'},
    'smoke bomb':              {'price': 55,  'type': 'throwable',
                                'description': 'Creates smoke — +4 to flee check this turn'},

    # ── Tools ─────────────────────────────────────────────────────────────────
    'lockpicks':               {'price': 60,  'type': 'tool',
                                'restricted_to': ['rogue'],
                                'description': '+2 to stealth and lockpicking checks'},
    'rope':                    {'price': 15,  'type': 'tool',
                                'description': 'Useful for climbing, binding, and escape'},
    'disguise kit':            {'price': 100, 'type': 'tool',
                                'description': '+2 to deception and persuasion checks'},
    'thieves tools':           {'price': 120, 'type': 'tool',
                                'restricted_to': ['rogue'],
                                'description': '+3 to all stealth and sleight of hand checks'},
    'lantern':                 {'price': 30,  'type': 'tool',
                                'description': 'Provides reliable light — removes darkness penalty'},
    'spyglass':                {'price': 200, 'type': 'tool',
                                'description': '+3 to perception checks at long range'},

    # ── Spell Scrolls ─────────────────────────────────────────────────────────
    'scroll of healing':       {'price': 150, 'type': 'scroll',
                                'spell_key': 'healing_word',
                                'description': 'One-use: cast Healing Word (4d4+4 HP restore)'},
    'scroll of fireball':      {'price': 300, 'type': 'scroll',
                                'spell_key': 'fireball',
                                'description': 'One-use: cast Fireball (3d6 fire damage, AoE)'},
    'scroll of protection':    {'price': 250, 'type': 'scroll',
                                'spell_key': 'shield_of_faith',
                                'description': 'One-use: cast Shield of Faith (+3 DEF for 2 turns)'},
    'scroll of restoration':   {'price': 200, 'type': 'scroll',
                                'spell_key': 'lesser_restoration',
                                'description': 'One-use: cure all status effects'},
    'scroll of lightning':     {'price': 350, 'type': 'scroll',
                                'spell_key': 'lightning_bolt',
                                'description': 'One-use: cast Lightning Bolt (2d8 lightning damage)'},

    # ── Upgrade Kits ──────────────────────────────────────────────────────────
    'weapon upgrade kit':      {'price': 250, 'type': 'upgrade',
                                'upgrade_stat': 'atk', 'upgrade_bonus': 1,
                                'description': 'Permanently +1 ATK to your equipped weapon'},
    'armor repair kit':        {'price': 180, 'type': 'upgrade',
                                'upgrade_stat': 'def_stat', 'upgrade_bonus': 1,
                                'description': 'Permanently +1 DEF to your equipped armor'},
    'enchanting stone':        {'price': 500, 'type': 'upgrade',
                                'upgrade_stat': 'atk', 'upgrade_bonus': 2,
                                'description': 'Permanently +2 ATK to equipped weapon (magical)'},
    'reinforcement rune':      {'price': 450, 'type': 'upgrade',
                                'upgrade_stat': 'def_stat', 'upgrade_bonus': 2,
                                'description': 'Permanently +2 DEF to equipped armor (magical)'},

    # ── Weapons ──────────────────────────────────────────────────────────────
    'iron sword':              {'price': 200, 'type': 'weapon', 'atk_bonus': 2,
                                'restricted_to': ['warrior', 'rogue'],
                                'description': 'A sturdy iron blade (+2 ATK)'},
    'steel sword':             {'price': 400, 'type': 'weapon', 'atk_bonus': 4,
                                'restricted_to': ['warrior', 'rogue'],
                                'description': 'Finely crafted steel blade (+4 ATK)'},
    'mage staff':              {'price': 250, 'type': 'weapon', 'atk_bonus': 1,
                                'mp_bonus': 10,
                                'restricted_to': ['mage'],
                                'description': 'Arcane focus (+1 ATK, +10 max MP)'},
    'arcane wand':             {'price': 320, 'type': 'weapon', 'atk_bonus': 2, 'mp_bonus': 15,
                                'restricted_to': ['mage'],
                                'description': 'Focused arcane wand (+2 ATK, +15 max MP)'},
    'longbow':                 {'price': 300, 'type': 'weapon', 'atk_bonus': 3,
                                'restricted_to': ['warrior', 'rogue'],
                                'description': "Ranger's longbow (+3 ATK)"},
    'holy mace':               {'price': 350, 'type': 'weapon', 'atk_bonus': 2,
                                'mp_bonus': 5,
                                'restricted_to': ['cleric'],
                                'description': 'Blessed mace (+2 ATK, +5 max MP)'},

    # ── Armor ─────────────────────────────────────────────────────────────────
    'leather armor':           {'price': 100, 'type': 'armor', 'def_bonus': 2,
                                'restricted_to': ['warrior', 'rogue', 'cleric'],
                                'description': 'Light leather armor (+2 DEF)'},
    'chainmail':               {'price': 300, 'type': 'armor', 'def_bonus': 5,
                                'restricted_to': ['warrior', 'cleric'],
                                'description': 'Interlocked rings (+5 DEF)'},
    'steel shield':            {'price': 150, 'type': 'armor', 'def_bonus': 3,
                                'restricted_to': ['warrior', 'cleric'],
                                'description': 'Reliable steel shield (+3 DEF)'},
    'plate armor':             {'price': 600, 'type': 'armor', 'def_bonus': 8,
                                'restricted_to': ['warrior'],
                                'description': 'Full plate protection (+8 DEF)'},

    # ── Accessories ───────────────────────────────────────────────────────────
    'elven boots':             {'price': 180, 'type': 'accessory', 'mov_bonus': 2,
                                'description': 'Light enchanted boots (+2 MOV)'},
    'holy symbol':             {'price': 220, 'type': 'accessory', 'mp_bonus': 10,
                                'restricted_to': ['cleric'],
                                'description': 'Divine focus (+10 max MP)'},
    'ring of protection':      {'price': 350, 'type': 'accessory', 'def_bonus': 2,
                                'description': 'Magical ring of warding (+2 DEF)'},
    'amulet of health':        {'price': 400, 'type': 'accessory', 'hp_bonus': 20,
                                'description': 'Grants extra vitality (+20 max HP)'},
    'cloak of shadows':        {'price': 300, 'type': 'accessory', 'mov_bonus': 1,
                                'restricted_to': ['rogue'],
                                'description': 'Partial invisibility (+1 MOV, +2 stealth)'},
    'mage tome':               {'price': 280, 'type': 'accessory', 'mp_bonus': 20,
                                'restricted_to': ['mage'],
                                'description': 'Spellbook of power (+20 max MP)'},
    'spell focus crystal':     {'price': 280, 'type': 'accessory', 'mp_bonus': 25,
                                'restricted_to': ['mage'],
                                'description': 'Amplifies spell power (+25 max MP)'},

    # ── Chinese name aliases ──────────────────────────────────────────────────
    '治療藥水':   {'price': 50,  'type': 'consumable', 'description': '恢復 2d4+2 HP'},
    '大治療藥水': {'price': 120, 'type': 'consumable', 'description': '恢復 4d4+4 HP'},
    '解毒劑':     {'price': 40,  'type': 'consumable', 'description': '治癒中毒狀態'},
    '法力藥水':   {'price': 60,  'type': 'consumable', 'description': '恢復 20 MP'},
    '體力精華':   {'price': 80,  'type': 'consumable', 'description': '恢復 30 MP 並治癒暈眩/緩速'},
    '乾糧':       {'price': 20,  'type': 'consumable', 'description': '恢復 1d4 HP'},
    '繃帶':       {'price': 25,  'type': 'consumable', 'description': '停止流血狀態'},
    '鐵劍':       {'price': 200, 'type': 'weapon',     'atk_bonus': 2},
    '鋼劍':       {'price': 400, 'type': 'weapon',     'atk_bonus': 4},
    '法師杖':     {'price': 250, 'type': 'weapon',     'atk_bonus': 1, 'mp_bonus': 10},
    '皮甲':       {'price': 100, 'type': 'armor',      'def_bonus': 2},
    '鎖甲':       {'price': 300, 'type': 'armor',      'def_bonus': 5},
    '鋼盾':       {'price': 150, 'type': 'armor',      'def_bonus': 3},
    '板甲':       {'price': 600, 'type': 'armor',      'def_bonus': 8},
    '精靈靴':     {'price': 180, 'type': 'accessory',  'mov_bonus': 2},
    '聖符':       {'price': 220, 'type': 'accessory',  'mp_bonus': 10},
    '護身符':     {'price': 350, 'type': 'accessory',  'def_bonus': 2},
    '鎖具工具':   {'price': 60,  'type': 'tool',       'description': '+2 潛行與開鎖檢定'},
    '繩索':       {'price': 15,  'type': 'tool',       'description': '攀爬、綁縛、逃脫用'},
    '武器升級套件': {'price': 250, 'type': 'upgrade',  'upgrade_stat': 'atk',      'upgrade_bonus': 1},
    '裝甲修復套件': {'price': 180, 'type': 'upgrade',  'upgrade_stat': 'def_stat', 'upgrade_bonus': 1},
    '附魔石':     {'price': 500, 'type': 'upgrade',    'upgrade_stat': 'atk',      'upgrade_bonus': 2},
    '強化符文':   {'price': 450, 'type': 'upgrade',    'upgrade_stat': 'def_stat', 'upgrade_bonus': 2},
    '治療捲軸':   {'price': 150, 'type': 'scroll',     'spell_key': 'healing_word'},
    '火球捲軸':   {'price': 300, 'type': 'scroll',     'spell_key': 'fireball'},
    '防護捲軸':   {'price': 250, 'type': 'scroll',     'spell_key': 'shield_of_faith'},
    '淨化捲軸':   {'price': 200, 'type': 'scroll',     'spell_key': 'lesser_restoration'},
    '奧術魔杖':   {'price': 320, 'type': 'weapon',     'atk_bonus': 2, 'mp_bonus': 15, 'restricted_to': ['mage']},
    '法術聚焦水晶': {'price': 280, 'type': 'accessory', 'mp_bonus': 25, 'restricted_to': ['mage']},
}

# Items sell for this fraction of their listed price
SELL_PRICE_RATIO = 0.5


def get_shop_item(item_name):
    """Return shop catalogue entry (case-insensitive) or None."""
    if not item_name:
        return None
    return SHOP_CATALOGUE.get(item_name.lower()) or SHOP_CATALOGUE.get(item_name)


def sell_price(item_name):
    """Return the gold a player receives for selling this item."""
    entry = get_shop_item(item_name)
    if entry:
        return max(1, int(entry['price'] * SELL_PRICE_RATIO))
    return 5  # default value for unrecognised items

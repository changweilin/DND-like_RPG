# Shop catalogue — items available from merchants.
# Each entry: {price, type, description, stat bonuses...}
# type ∈ {consumable, throwable, weapon, armor, accessory}
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
    # ── Throwables ───────────────────────────────────────────────────────────
    'poison vial':             {'price': 60,  'type': 'throwable',
                                'description': 'Throw to apply poisoned status'},
    'bomb':                    {'price': 80,  'type': 'throwable',
                                'description': 'AoE 2d6 explosive damage'},
    'torch':                   {'price': 10,  'type': 'throwable',
                                'description': '1d4 fire damage + burning status'},
    # ── Weapons ──────────────────────────────────────────────────────────────
    'iron sword':              {'price': 200, 'type': 'weapon', 'atk_bonus': 2,
                                'description': 'A sturdy iron blade (+2 ATK)'},
    'steel sword':             {'price': 400, 'type': 'weapon', 'atk_bonus': 4,
                                'description': 'Finely crafted steel blade (+4 ATK)'},
    'mage staff':              {'price': 250, 'type': 'weapon', 'atk_bonus': 1,
                                'mp_bonus': 10, 'description': 'Arcane focus (+1 ATK, +10 max MP)'},
    'longbow':                 {'price': 300, 'type': 'weapon', 'atk_bonus': 3,
                                'description': 'Ranger\'s longbow (+3 ATK)'},
    'holy mace':               {'price': 350, 'type': 'weapon', 'atk_bonus': 2,
                                'mp_bonus': 5, 'description': 'Blessed mace (+2 ATK, +5 max MP)'},
    # ── Armor ─────────────────────────────────────────────────────────────────
    'leather armor':           {'price': 100, 'type': 'armor', 'def_bonus': 2,
                                'description': 'Light leather armor (+2 DEF)'},
    'chainmail':               {'price': 300, 'type': 'armor', 'def_bonus': 5,
                                'description': 'Interlocked rings (+5 DEF)'},
    'steel shield':            {'price': 150, 'type': 'armor', 'def_bonus': 3,
                                'description': 'Reliable steel shield (+3 DEF)'},
    'plate armor':             {'price': 600, 'type': 'armor', 'def_bonus': 8,
                                'description': 'Full plate protection (+8 DEF)'},
    # ── Accessories ───────────────────────────────────────────────────────────
    'elven boots':             {'price': 180, 'type': 'accessory', 'mov_bonus': 2,
                                'description': 'Light enchanted boots (+2 MOV)'},
    'holy symbol':             {'price': 220, 'type': 'accessory', 'mp_bonus': 10,
                                'description': 'Divine focus (+10 max MP)'},
    'ring of protection':      {'price': 350, 'type': 'accessory', 'def_bonus': 2,
                                'description': 'Magical ring of warding (+2 DEF)'},
    'amulet of health':        {'price': 400, 'type': 'accessory', 'hp_bonus': 20,
                                'description': 'Grants extra vitality (+20 max HP)'},

    # ── Chinese name aliases ──────────────────────────────────────────────────
    '治療藥水':  {'price': 50,  'type': 'consumable', 'description': '恢復 2d4+2 HP'},
    '大治療藥水':{'price': 120, 'type': 'consumable', 'description': '恢復 4d4+4 HP'},
    '解毒劑':    {'price': 40,  'type': 'consumable', 'description': '治癒中毒狀態'},
    '法力藥水':  {'price': 60,  'type': 'consumable', 'description': '恢復 20 MP'},
    '鐵劍':      {'price': 200, 'type': 'weapon',     'atk_bonus': 2},
    '鋼劍':      {'price': 400, 'type': 'weapon',     'atk_bonus': 4},
    '法師杖':    {'price': 250, 'type': 'weapon',     'atk_bonus': 1, 'mp_bonus': 10},
    '皮甲':      {'price': 100, 'type': 'armor',      'def_bonus': 2},
    '鎖甲':      {'price': 300, 'type': 'armor',      'def_bonus': 5},
    '鋼盾':      {'price': 150, 'type': 'armor',      'def_bonus': 3},
    '板甲':      {'price': 600, 'type': 'armor',      'def_bonus': 8},
    '精靈靴':    {'price': 180, 'type': 'accessory',  'mov_bonus': 2},
    '聖符':      {'price': 220, 'type': 'accessory',  'mp_bonus': 10},
    '護身符':    {'price': 350, 'type': 'accessory',  'def_bonus': 2},
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

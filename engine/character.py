class CharacterLogic:
    # Maps TRPG skill names to the character stat that governs them.
    # Modifier formula (D&D-style): (stat_value - 10) // 2
    _SKILL_STAT_MAP = {
        'acrobatics': 'mov',
        'athletics':  'atk',
        'intimidation': 'atk',
        'arcana':     'mp',         # rough proxy for intelligence / magic aptitude
        'perception': 'def_stat',
        'stealth':    'mov',
        'persuasion': 'def_stat',
        'medicine':   'def_stat',
    }

    # Weapon damage dice by class (Section 3.3: deterministic damage roll).
    # ATK modifier = (atk - 10) // 2 is added on top.
    _CLASS_DAMAGE_MAP = {
        'warrior': '1d8',
        'mage':    '1d4',
        'rogue':   '1d6',
        'cleric':  '1d6',
    }

    def __init__(self, db_session, character_model):
        self.session = db_session
        self.model = character_model

    def take_damage(self, amount):
        actual_damage = max(0, amount - ((self.model.def_stat or 0) // 2))
        self.model.hp = max(0, (self.model.hp or 0) - actual_damage)
        self.session.commit()
        return actual_damage

    def heal(self, amount):
        self.model.hp = min(self.model.max_hp or 0, (self.model.hp or 0) + amount)
        self.session.commit()

    def use_mp(self, amount):
        if (self.model.mp or 0) >= amount:
            self.model.mp = (self.model.mp or 0) - amount
            self.session.commit()
            return True
        return False

    def add_item(self, item_dict):
        inventory = self.model.inventory.copy() if self.model.inventory else []
        inventory.append(item_dict)
        self.model.inventory = inventory
        self.session.commit()

    def remove_item(self, item_name):
        if not self.model.inventory:
            return False
        inventory = self.model.inventory.copy()
        for idx, item in enumerate(inventory):
            if item.get('name') == item_name:
                inventory.pop(idx)
                self.model.inventory = inventory
                self.session.commit()
                return True
        return False

    # Item effect lookup table — name fragment → effect dict
    _ITEM_EFFECTS = {
        'healing potion':  {'hp_healed_dice': '2d4+2', 'type': 'consumable'},
        'health potion':   {'hp_healed_dice': '2d4+2', 'type': 'consumable'},
        'potion of healing': {'hp_healed_dice': '2d4+2', 'type': 'consumable'},
        '藥水':            {'hp_healed_dice': '2d4+2', 'type': 'consumable'},
        '治療藥水':        {'hp_healed_dice': '2d4+2', 'type': 'consumable'},
        'greater healing':  {'hp_healed_dice': '4d4+4', 'type': 'consumable'},
        '大治療':          {'hp_healed_dice': '4d4+4', 'type': 'consumable'},
        'antidote':        {'cures_status': 'poisoned', 'type': 'consumable'},
        '解毒劑':          {'cures_status': 'poisoned', 'type': 'consumable'},
        'mana potion':     {'mp_restored': 20, 'type': 'consumable'},
        '法力藥水':        {'mp_restored': 20, 'type': 'consumable'},
        'stamina elixir':  {'mp_restored': 30, 'cures_status': 'stunned', 'type': 'consumable'},
        '體力精華':        {'mp_restored': 30, 'cures_status': 'stunned', 'type': 'consumable'},
        'rations':         {'hp_healed_dice': '1d4', 'type': 'consumable'},
        '乾糧':            {'hp_healed_dice': '1d4', 'type': 'consumable'},
        'smelling salts':  {'cures_status': 'stunned', 'type': 'consumable'},
        'bandages':        {'cures_status': 'bleeding', 'type': 'consumable'},
        '繃帶':            {'cures_status': 'bleeding', 'type': 'consumable'},
        'poison vial':     {'apply_status': 'poisoned', 'type': 'throwable'},
        'bomb':            {'damage_dice': '2d6', 'aoe': True, 'type': 'throwable'},
        '炸彈':            {'damage_dice': '2d6', 'aoe': True, 'type': 'throwable'},
        'torch':           {'damage_dice': '1d4', 'apply_status': 'burning', 'type': 'throwable'},
        'flash powder':    {'apply_status': 'stunned', 'aoe': True, 'type': 'throwable'},
        'smoke bomb':      {'smoke_escape': True, 'type': 'throwable'},
        # Spell scrolls — spell_key is resolved in events.py via data/spells.py
        'scroll of healing':    {'spell_key': 'healing_word', 'type': 'scroll'},
        '治療捲軸':             {'spell_key': 'healing_word', 'type': 'scroll'},
        'scroll of fireball':   {'spell_key': 'fireball',     'type': 'scroll'},
        '火球捲軸':             {'spell_key': 'fireball',     'type': 'scroll'},
        'scroll of protection': {'spell_key': 'shield_of_faith', 'type': 'scroll'},
        '防護捲軸':             {'spell_key': 'shield_of_faith', 'type': 'scroll'},
        'scroll of restoration':{'spell_key': 'lesser_restoration', 'type': 'scroll'},
        '淨化捲軸':             {'spell_key': 'lesser_restoration', 'type': 'scroll'},
        'scroll of lightning':  {'spell_key': 'lightning_bolt', 'type': 'scroll'},
    }

    def use_item(self, item_name, dice_roller=None):
        """
        Attempt to use a named item from inventory.

        Returns a dict:
          {'used': bool, 'item_name': str,
           'hp_healed': int, 'cures_status': str|None,
           'apply_status': str|None, 'damage_dice': str|None,
           'aoe': bool, 'type': str}
        If item not found → {'used': False}.
        """
        # Find item in inventory (case-insensitive substring match)
        item_name_lower = item_name.lower()
        found = None
        for item in (self.model.inventory or []):
            if item.get('name', '').lower() == item_name_lower:
                found = item
                break
        if found is None:
            # Try substring match
            for item in (self.model.inventory or []):
                if item_name_lower in item.get('name', '').lower():
                    found = item
                    break
        if found is None:
            return {'used': False, 'item_name': item_name}

        # Look up effect
        effect = {}
        name_lower = found.get('name', '').lower()
        for fragment, eff in self._ITEM_EFFECTS.items():
            if fragment in name_lower:
                effect = dict(eff)
                break
        # Default: unknown consumable heals a small amount
        if not effect:
            effect = {'hp_healed_dice': '1d4', 'type': 'consumable'}

        # Scrolls are dispatched back to events.py (need spells.py + combat engine)
        if effect.get('type') == 'scroll' and effect.get('spell_key'):
            self.remove_item(found['name'])
            return {
                'used':      True,
                'item_name': found['name'],
                'item_type': 'scroll',
                'spell_key': effect['spell_key'],
                'hp_healed': 0,
            }

        # Roll HP healing
        hp_healed = 0
        if effect.get('hp_healed_dice') and dice_roller:
            _, _, hp_healed = dice_roller.roll(effect['hp_healed_dice'])
            self.heal(hp_healed)

        # Restore MP
        mp_restored = 0
        if effect.get('mp_restored'):
            mp_restored = effect['mp_restored']
            self.model.mp = min(
                self.model.max_mp or 50,
                (self.model.mp or 0) + mp_restored,
            )

        # Remove from inventory
        self.remove_item(found['name'])

        return {
            'used':          True,
            'item_name':     found['name'],
            'hp_healed':     hp_healed,
            'mp_restored':   mp_restored,
            'cures_status':  effect.get('cures_status'),
            'apply_status':  effect.get('apply_status'),
            'damage_dice':   effect.get('damage_dice'),
            'smoke_escape':  effect.get('smoke_escape', False),
            'aoe':           effect.get('aoe', False),
            'item_type':     effect.get('type', 'consumable'),
        }

    # ── Equipment upgrade ─────────────────────────────────────────────────────

    # Maps upgrade kit name/key → (stat_attr, bonus_increment)
    _UPGRADE_KIT_MAP = {
        'weapon upgrade kit': ('atk',      1),
        'armor repair kit':   ('def_stat', 1),
        'enchanting stone':   ('atk',      2),
        'reinforcement rune': ('def_stat', 2),
        'weapon_upgrade':     ('atk',      1),
        'armor_upgrade':      ('def_stat', 1),
        '武器升級套件':       ('atk',      1),
        '裝甲修復套件':       ('def_stat', 1),
        '附魔石':             ('atk',      2),
        '強化符文':           ('def_stat', 2),
    }

    def apply_upgrade(self, kit_name):
        """
        Consume an upgrade kit from inventory and permanently raise a character stat.
        kit_name — the item name or an alias key like 'weapon_upgrade'.
        Returns {'upgraded': bool, 'stat': str, 'bonus': int, 'reason': str|None}.
        """
        mapping = self._UPGRADE_KIT_MAP.get(kit_name.lower())
        if mapping is None:
            return {'upgraded': False, 'stat': '', 'bonus': 0, 'reason': 'unknown_kit'}
        stat_attr, bonus = mapping

        # Resolve the actual inventory item name
        kit_name_lower = kit_name.lower()
        found_name = None
        for it in (self.model.inventory or []):
            n = (it.get('name', '') if isinstance(it, dict) else str(it)).lower()
            if n == kit_name_lower or self._UPGRADE_KIT_MAP.get(n) == mapping:
                found_name = it.get('name', it) if isinstance(it, dict) else str(it)
                break
        if found_name is None:
            return {'upgraded': False, 'stat': stat_attr, 'bonus': bonus, 'reason': 'not_in_inventory'}

        # Apply permanent stat increase
        current = getattr(self.model, stat_attr, 0) or 0
        setattr(self.model, stat_attr, current + bonus)
        self.remove_item(found_name)   # commits stat change + inventory removal atomically
        return {'upgraded': True, 'stat': stat_attr, 'bonus': bonus, 'reason': None}

    # ── Rest ─────────────────────────────────────────────────────────────────

    def short_rest(self, dice_roller):
        """Roll 1d8 and restore that many HP (cannot exceed max_hp)."""
        _, _, healed = dice_roller.roll('1d8')
        self.heal(healed)
        return healed

    def long_rest(self):
        """Fully restore HP and MP (only valid outside combat)."""
        self.model.hp = self.model.max_hp or 100
        self.model.mp = self.model.max_mp or 50
        self.session.commit()
        return {'hp_restored': self.model.hp, 'mp_restored': self.model.mp}

    # ── Equipment ─────────────────────────────────────────────────────────────

    def equip(self, item_name):
        """
        Move an item from inventory into the matching equipment slot.
        Returns the slot name ('weapon'|'armor'|'accessory') or '' on failure.
        """
        from data.shop import get_shop_item
        item_name_lower = item_name.lower()
        found = None
        for item in (self.model.inventory or []):
            if item.get('name', '').lower() == item_name_lower:
                found = item
                break
        if found is None:
            for item in (self.model.inventory or []):
                if item_name_lower in item.get('name', '').lower():
                    found = item
                    break
        if found is None:
            return ''

        # Determine equipment slot from item type
        item_type = found.get('type', '')
        shop_entry = get_shop_item(found.get('name', ''))
        if shop_entry:
            item_type = shop_entry.get('type', item_type)

        slot_map = {'weapon': 'weapon', 'armor': 'armor', 'accessory': 'accessory'}
        slot = slot_map.get(item_type, '')
        if not slot:
            # Infer from name keywords
            nl = found.get('name', '').lower()
            if any(w in nl for w in ('sword', 'staff', 'bow', 'dagger', 'axe', 'mace', 'spear', '劍', '杖', '弓', '刀')):
                slot = 'weapon'
            elif any(w in nl for w in ('shield', 'mail', 'armor', 'robe', 'plate', '盾', '甲', '袍', '鎧')):
                slot = 'armor'
            else:
                slot = 'accessory'

        equipment = dict(self.model.equipment or {})
        old_item = equipment.get(slot)

        # Unequip previous item — remove its stat bonuses and return to inventory
        if old_item:
            old_entry = get_shop_item(old_item.get('name', ''))
            if old_entry:
                self._apply_equipment_stats(old_entry, sign=-1)
            inv = list(self.model.inventory or [])
            inv.append(old_item)
            self.model.inventory = inv

        # Apply new item's stat bonuses
        if shop_entry:
            self._apply_equipment_stats(shop_entry, sign=+1)

        # Move from inventory to equipment slot
        self.remove_item(found['name'])
        equipment[slot] = found
        self.model.equipment = equipment
        self.session.commit()
        return slot

    def unequip(self, slot):
        """
        Remove the item from an equipment slot and return it to inventory.
        Returns the item name or '' if the slot was empty.
        """
        from data.shop import get_shop_item
        equipment = dict(self.model.equipment or {})
        item = equipment.get(slot)
        if not item:
            return ''

        shop_entry = get_shop_item(item.get('name', ''))
        if shop_entry:
            self._apply_equipment_stats(shop_entry, sign=-1)

        inv = list(self.model.inventory or [])
        inv.append(item)
        self.model.inventory = inv
        del equipment[slot]
        self.model.equipment = equipment
        self.session.commit()
        return item.get('name', '')

    def _apply_equipment_stats(self, shop_entry, sign):
        """Apply or remove stat bonuses from an equipment shop entry (sign = +1 or -1)."""
        if shop_entry.get('atk_bonus'):
            self.model.atk = max(1, (self.model.atk or 10) + sign * shop_entry['atk_bonus'])
        if shop_entry.get('def_bonus'):
            self.model.def_stat = max(1, (self.model.def_stat or 10) + sign * shop_entry['def_bonus'])
        if shop_entry.get('mov_bonus'):
            self.model.mov = max(1, (self.model.mov or 5) + sign * shop_entry['mov_bonus'])
        if shop_entry.get('mp_bonus'):
            self.model.max_mp = max(10, (self.model.max_mp or 50) + sign * shop_entry['mp_bonus'])
            self.model.mp = min(self.model.mp or 0, self.model.max_mp)
        if shop_entry.get('hp_bonus'):
            self.model.max_hp = max(10, (self.model.max_hp or 100) + sign * shop_entry['hp_bonus'])
            self.model.hp = min(self.model.hp or 0, self.model.max_hp)

    # ── Shop ──────────────────────────────────────────────────────────────────

    def buy_item(self, item_name, price=None):
        """
        Deduct gold and add item to inventory.
        Returns {'bought': bool, 'price': int, 'reason': str|None}.
        """
        from data.shop import get_shop_item
        entry = get_shop_item(item_name)
        if entry is None:
            return {'bought': False, 'reason': 'not_found', 'price': 0}
        actual_price = price if price is not None else entry['price']
        if (self.model.gold or 0) < actual_price:
            return {'bought': False, 'reason': 'insufficient_gold', 'price': actual_price}
        self.model.gold = (self.model.gold or 0) - actual_price
        self.add_item({'name': item_name, 'type': entry.get('type', 'item')})
        self.session.commit()
        return {'bought': True, 'price': actual_price, 'item_name': item_name}

    def sell_item(self, item_name, price_mult=1.0):
        """
        Remove item from inventory and add gold.
        price_mult — faction reputation modifier (e.g. 0.9 = merchant pays 10% more).
        Returns {'sold': bool, 'gold': int, 'base_gold': int, 'reason': str|None}.
        """
        from data.shop import sell_price
        # Cannot sell equipped items
        for slot, item in (self.model.equipment or {}).items():
            if item and item.get('name', '').lower() == item_name.lower():
                return {'sold': False, 'gold': 0, 'base_gold': 0, 'reason': 'equipped'}
        # Verify item exists before touching gold
        item_name_lower = item_name.lower()
        found = any(
            (it.get('name', '') if isinstance(it, dict) else str(it)).lower() == item_name_lower
            for it in (self.model.inventory or [])
        )
        if not found:
            return {'sold': False, 'gold': 0, 'base_gold': 0, 'reason': 'not_found'}
        # Apply faction modifier to sell price (friendly merchants buy at better rates)
        base_gold = sell_price(item_name)
        gold = max(1, int(base_gold * price_mult))
        # Add gold first so both mutations commit together inside remove_item()
        self.model.gold = (self.model.gold or 0) + gold
        self.remove_item(item_name)   # commits gold + inventory removal atomically
        return {'sold': True, 'gold': gold, 'base_gold': base_gold}

    # ── Level-up stat allocation ──────────────────────────────────────────────

    # Stat-point allocation: maps choice key → (attribute_name, increment)
    _STAT_POINT_MAP = {
        'max_hp':    ('max_hp',    10),
        'max_mp':    ('max_mp',    10),
        'atk':       ('atk',        2),
        'def_stat':  ('def_stat',   2),
        'mov':       ('mov',        1),
    }

    def spend_stat_point(self, stat_key):
        """
        Spend one pending stat point on the chosen stat.
        Returns True on success, False if no points remain or key is invalid.
        """
        pending = self.model.pending_stat_points or 0
        if pending <= 0:
            return False
        entry = self._STAT_POINT_MAP.get(stat_key)
        if entry is None:
            return False
        attr, increment = entry
        current = getattr(self.model, attr, 0) or 0
        setattr(self.model, attr, current + increment)
        # Also raise current HP/MP to reflect the new maximum
        if attr == 'max_hp':
            self.model.hp = min((self.model.hp or 0) + increment, self.model.max_hp)
        if attr == 'max_mp':
            self.model.mp = min((self.model.mp or 0) + increment, self.model.max_mp)
        self.model.pending_stat_points = pending - 1
        self.session.commit()
        return True

    # Tool/item bonuses applied to skill checks when item is in inventory or equipped.
    # Key = item name fragment (lowercase), value = {skill_name: bonus_int}
    _TOOL_SKILL_BONUSES = {
        'lockpicks':        {'stealth': 2},
        'thieves tools':    {'stealth': 3, 'acrobatics': 1},
        'disguise kit':     {'persuasion': 2, 'intimidation': 1},
        'spyglass':         {'perception': 3},
        'lantern':          {'perception': 1},
        'rope':             {'athletics': 2},
        'cloak of shadows': {'stealth': 2, 'acrobatics': 1},
    }

    def get_skill_modifier(self, skill_name):
        """
        Return the integer modifier for a skill check.
        Combines base stat modifier (D&D-style) with bonuses from tools in inventory.
        The rule engine passes this to DiceRoller so the LLM never touches dice math.
        """
        skill_lower = skill_name.lower().strip()
        stat_name = self._SKILL_STAT_MAP.get(skill_lower)
        base_mod = 0
        if stat_name:
            stat_val = getattr(self.model, stat_name, 10) or 10
            base_mod = (stat_val - 10) // 2

        # Sum bonuses from all tools currently in inventory or equipment slots
        tool_bonus = 0
        all_items = list(self.model.inventory or [])
        for slot_item in (self.model.equipment or {}).values():
            if slot_item:
                all_items.append(slot_item)
        for item in all_items:
            item_name = (item.get('name', '') if isinstance(item, dict) else str(item)).lower()
            for tool_key, bonuses in self._TOOL_SKILL_BONUSES.items():
                if tool_key in item_name:
                    tool_bonus += bonuses.get(skill_lower, 0)

        return base_mod + tool_bonus

    def get_weapon_damage_notation(self):
        # Return the damage notation string for this character's class.
        # Used by the combat rule engine in EventManager._resolve_combat().
        char_class = (self.model.char_class or 'warrior').lower().strip()
        base_dice = self._CLASS_DAMAGE_MAP.get(char_class, '1d6')
        atk_mod = (self.model.atk - 10) // 2
        if atk_mod >= 0:
            return f"{base_dice}+{atk_mod}"
        return f"{base_dice}{atk_mod}"   # atk_mod is already negative

    def get_class_abilities(self):
        """Return the ability definitions available to this character's class."""
        from engine.combat import CLASS_ABILITIES
        char_class = (self.model.char_class or 'warrior').lower().strip()
        return CLASS_ABILITIES.get(char_class, {})

    def can_use_ability(self, ability_key, used_abilities=None):
        """
        Check whether this character can use the named ability.
        used_abilities — set of ability keys already used this encounter
        (once_per_combat restriction).
        """
        from engine.combat import CLASS_ABILITIES
        char_class = (self.model.char_class or 'warrior').lower().strip()
        ability = CLASS_ABILITIES.get(char_class, {}).get(ability_key)
        if ability is None:
            return False
        if ability.get('once_per_combat') and used_abilities and ability_key in used_abilities:
            return False
        if ability.get('mp_cost', 0) > self.model.mp:
            return False
        return True

    def apply_def_bonus(self, bonus, current_state):
        """
        Store a temporary DEF bonus (e.g. Arcane Shield) in the player's
        status buffer so it can be consumed on the next incoming hit.
        """
        known = dict(current_state.known_entities or {})
        buffs = list(known.get('_player_buffs', []))
        # Remove any existing arcane_shield buff before adding a fresh one
        buffs = [b for b in buffs if b.get('key') != '_arcane_shield']
        buffs.append({'key': '_arcane_shield', 'def_bonus': bonus, 'turns_remaining': 1})
        known['_player_buffs'] = buffs
        current_state.known_entities = known

    def consume_def_bonus(self, current_state):
        """
        Pop the temporary DEF bonus (Arcane Shield) if present.
        Returns the bonus value (0 if none).
        """
        known = dict(current_state.known_entities or {})
        buffs = list(known.get('_player_buffs', []))
        bonus = 0
        new_buffs = []
        for b in buffs:
            if b.get('key') == '_arcane_shield':
                bonus += b.get('def_bonus', 0)
            else:
                new_buffs.append(b)
        known['_player_buffs'] = new_buffs
        current_state.known_entities = known
        return bonus

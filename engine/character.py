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

    # Maps item type → equipment slot key (9-slot system).
    _TYPE_TO_SLOT = {
        'weapon':     'main_hand',
        'two_handed': 'main_hand',
        'shield':     'off_hand',
        'off_hand':   'off_hand',
        'helmet':     'head',
        'armor':      'body',
        'gloves':     'hands',
        'boots':      'feet',
        'necklace':   'necklace',
        'ring':       'ring',
        'earring':    'earring',
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
        # Skill books — consuming grants a permanent proficiency bonus
        'tome of athletics':      {'type': 'skillbook', 'skill_granted': 'athletics',    'bonus': 1},
        'tome of intimidation':   {'type': 'skillbook', 'skill_granted': 'intimidation', 'bonus': 1},
        'tome of acrobatics':     {'type': 'skillbook', 'skill_granted': 'acrobatics',   'bonus': 1},
        'tome of stealth':        {'type': 'skillbook', 'skill_granted': 'stealth',      'bonus': 1},
        'tome of perception':     {'type': 'skillbook', 'skill_granted': 'perception',   'bonus': 1},
        'tome of persuasion':     {'type': 'skillbook', 'skill_granted': 'persuasion',   'bonus': 1},
        'tome of medicine':       {'type': 'skillbook', 'skill_granted': 'medicine',     'bonus': 1},
        'tome of arcana':         {'type': 'skillbook', 'skill_granted': 'arcana',       'bonus': 1},
        '體能之書':               {'type': 'skillbook', 'skill_granted': 'athletics',    'bonus': 1},
        '威嚇之書':               {'type': 'skillbook', 'skill_granted': 'intimidation', 'bonus': 1},
        '特技之書':               {'type': 'skillbook', 'skill_granted': 'acrobatics',   'bonus': 1},
        '潛行之書':               {'type': 'skillbook', 'skill_granted': 'stealth',      'bonus': 1},
        '察覺之書':               {'type': 'skillbook', 'skill_granted': 'perception',   'bonus': 1},
        '說服之書':               {'type': 'skillbook', 'skill_granted': 'persuasion',   'bonus': 1},
        '醫療之書':               {'type': 'skillbook', 'skill_granted': 'medicine',     'bonus': 1},
        '奧術之書':               {'type': 'skillbook', 'skill_granted': 'arcana',       'bonus': 1},
        # Advanced skill books (Tier 2: +2 bonus)
        'advanced stealth tome':    {'type': 'skillbook', 'skill_granted': 'stealth',    'bonus': 2},
        'advanced athletics tome':  {'type': 'skillbook', 'skill_granted': 'athletics',  'bonus': 2},
        'advanced acrobatics tome': {'type': 'skillbook', 'skill_granted': 'acrobatics', 'bonus': 2},
        'advanced perception tome': {'type': 'skillbook', 'skill_granted': 'perception', 'bonus': 2},
        'advanced persuasion tome': {'type': 'skillbook', 'skill_granted': 'persuasion', 'bonus': 2},
        'advanced arcana tome':     {'type': 'skillbook', 'skill_granted': 'arcana',     'bonus': 2},
        '進階潛行典籍':             {'type': 'skillbook', 'skill_granted': 'stealth',    'bonus': 2},
        '進階體能典籍':             {'type': 'skillbook', 'skill_granted': 'athletics',  'bonus': 2},
        '進階奧術典籍':             {'type': 'skillbook', 'skill_granted': 'arcana',     'bonus': 2},
        # Elixirs — temporary stat buffs; buff data returned to events.py for game_state storage
        'strength potion':        {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 2}], 'buff_duration': 3},
        'iron skin potion':       {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 2}], 'buff_duration': 3},
        'swiftness potion':       {'type': 'elixir', 'buffs': [{'stat': 'mov', 'value': 1}], 'buff_duration': 3},
        'berserker elixir':       {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 4}, {'stat': 'def_stat', 'value': -2}], 'buff_duration': 4},
        'stone skin elixir':      {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 4}, {'stat': 'mov', 'value': -1}], 'buff_duration': 4},
        'mana surge elixir':      {'type': 'elixir', 'buffs': [{'stat': 'mp', 'value': 30}], 'buff_duration': 3},
        'battle focus elixir':    {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 3}, {'stat': 'def_stat', 'value': 2}], 'buff_duration': 3},
        'warlord draught':        {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 6}, {'stat': 'def_stat', 'value': 3}], 'buff_duration': 4},
        'divine blessing elixir': {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 6}, {'stat': 'mp', 'value': 40}], 'buff_duration': 5},
        'arcane surge elixir':    {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 5}, {'stat': 'mp', 'value': 50}], 'buff_duration': 4},
        '力量藥水':    {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 2}], 'buff_duration': 3},
        '鐵皮藥水':    {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 2}], 'buff_duration': 3},
        '敏捷藥水':    {'type': 'elixir', 'buffs': [{'stat': 'mov', 'value': 1}], 'buff_duration': 3},
        '狂戰士藥劑':  {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 4}, {'stat': 'def_stat', 'value': -2}], 'buff_duration': 4},
        '岩膚藥劑':    {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 4}, {'stat': 'mov', 'value': -1}], 'buff_duration': 4},
        '法力湧動藥劑': {'type': 'elixir', 'buffs': [{'stat': 'mp', 'value': 30}], 'buff_duration': 3},
        '戰鬥精華':    {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 3}, {'stat': 'def_stat', 'value': 2}], 'buff_duration': 3},
        '戰王藥劑':    {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 6}, {'stat': 'def_stat', 'value': 3}], 'buff_duration': 4},
        '神佑藥劑':    {'type': 'elixir', 'buffs': [{'stat': 'def_stat', 'value': 6}, {'stat': 'mp', 'value': 40}], 'buff_duration': 5},
        '奧術爆發藥劑': {'type': 'elixir', 'buffs': [{'stat': 'atk', 'value': 5}, {'stat': 'mp', 'value': 50}], 'buff_duration': 4},
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

        # Skill books — permanently add proficiency bonus to Character.skills
        if effect.get('type') == 'skillbook' and effect.get('skill_granted'):
            skill_name = effect['skill_granted']
            bonus = effect.get('bonus', 1)
            skills = list(self.model.skills or [])
            existing = next((s for s in skills if isinstance(s, dict) and s.get('skill') == skill_name), None)
            if existing:
                existing['bonus'] = existing.get('bonus', 0) + bonus
                self.model.skills = skills
            else:
                skills.append({'skill': skill_name, 'bonus': bonus})
                self.model.skills = skills
            self.remove_item(found['name'])   # commits skills + inventory removal atomically
            return {
                'used':          True,
                'item_name':     found['name'],
                'item_type':     'skillbook',
                'skill_granted': skill_name,
                'bonus':         bonus,
                'hp_healed':     0,
            }

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

        # Elixirs — return buff data; events.py applies them to game_state.active_buffs
        if effect.get('type') == 'elixir' and effect.get('buffs'):
            self.remove_item(found['name'])
            return {
                'used':         True,
                'item_name':    found['name'],
                'item_type':    'elixir',
                'buffs':        effect['buffs'],
                'buff_duration': effect.get('buff_duration', 3),
                'hp_healed':    0,
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

    # ── Trainer NPC skill training ────────────────────────────────────────────

    # Gold cost to gain the next proficiency tier (1st +1, 2nd +1 stacked, etc.)
    _TRAINING_COST = {1: 200, 2: 350, 3: 500}

    def train_skill(self, skill_name):
        skill_lower = (skill_name or '').lower().strip()
        if skill_lower not in self._SKILL_STAT_MAP:
            return {'trained': False, 'skill': skill_lower, 'bonus_gained': 0,
                    'gold_spent': 0, 'reason': 'unknown_skill'}

        skills = list(self.model.skills or [])
        existing = next((s for s in skills if isinstance(s, dict) and s.get('skill') == skill_lower), None)
        current_bonus = existing.get('bonus', 0) if existing else 0
        next_tier = current_bonus + 1
        cost = self._TRAINING_COST.get(next_tier, 500)

        if (self.model.gold or 0) < cost:
            return {'trained': False, 'skill': skill_lower, 'bonus_gained': 0,
                    'gold_spent': 0, 'reason': 'insufficient_gold'}

        self.model.gold = (self.model.gold or 0) - cost
        if existing:
            existing['bonus'] = current_bonus + 1
            self.model.skills = skills
        else:
            skills.append({'skill': skill_lower, 'bonus': 1})
            self.model.skills = skills
        self.session.commit()
        return {'trained': True, 'skill': skill_lower, 'bonus_gained': 1,
                'gold_spent': cost, 'reason': None}

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

    # ── Temporary buffs (elixirs) ─────────────────────────────────────────────

    @staticmethod
    def tick_buffs(game_state, db_session):
        """Decrement active buff turn counters; remove expired buffs. Call at turn start."""
        from sqlalchemy.orm.attributes import flag_modified
        buffs = list(game_state.active_buffs or [])
        expired = []
        remaining = []
        for b in buffs:
            b = dict(b)
            b['turns_left'] = b.get('turns_left', 1) - 1
            if b['turns_left'] > 0:
                remaining.append(b)
            else:
                expired.append(b)
        game_state.active_buffs = remaining
        flag_modified(game_state, 'active_buffs')
        db_session.commit()
        return expired  # list of buffs that just wore off

    @staticmethod
    def apply_buffs(game_state, buffs, buff_duration, source, db_session):
        """Append new buff entries to game_state.active_buffs."""
        from sqlalchemy.orm.attributes import flag_modified
        active = list(game_state.active_buffs or [])
        for b in buffs:
            active.append({
                'stat':       b['stat'],
                'value':      b['value'],
                'turns_left': buff_duration,
                'source':     source,
            })
        game_state.active_buffs = active
        flag_modified(game_state, 'active_buffs')
        db_session.commit()

    @staticmethod
    def get_buff_modifier(game_state, stat):
        """Return the total active buff modifier for a given stat string."""
        return sum(b.get('value', 0) for b in (game_state.active_buffs or []) if b.get('stat') == stat)

    # ── Equipment ─────────────────────────────────────────────────────────────

    def equip(self, item_name):
        # Move an item from inventory into the matching equipment slot.
        # Returns the slot name or '' on failure.
        from data.shop import get_shop_item
        equipment = dict(self.model.equipment or {})
        # Migrate legacy 3-slot keys before any logic
        self._migrate_old_equipment_slots(equipment)

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

        # Enforce class equipment restriction
        char_class = (self.model.char_class or '').lower().strip()
        item_lower = found.get('name', '').lower()
        allowed = self._CLASS_EQUIP_RULES.get(item_lower)
        if allowed and char_class not in allowed:
            return ''

        # Determine equipment slot from item type
        item_type = found.get('type', '')
        shop_entry = get_shop_item(found.get('name', ''))
        if shop_entry:
            item_type = shop_entry.get('type', item_type)

        slot = self._TYPE_TO_SLOT.get(item_type, '')
        if not slot:
            # Infer from name keywords for items not in the shop catalogue
            nl = found.get('name', '').lower()
            if any(w in nl for w in ('sword', 'staff', 'bow', 'dagger', 'axe', 'mace', 'spear', '劍', '杖', '弓', '刀')):
                slot = 'main_hand'
            elif any(w in nl for w in ('mail', 'armor', 'robe', 'plate', '甲', '袍', '鎧')):
                slot = 'body'
            else:
                slot = 'necklace'

        # Two-handed weapon: clear off_hand sentinel/item first
        if item_type == 'two_handed':
            old_oh = equipment.get('off_hand')
            if old_oh and not isinstance(old_oh, dict) or (isinstance(old_oh, dict) and '_two_hand_ref' not in old_oh):
                # Real item in off_hand — return it to inventory
                if old_oh:
                    oh_entry = get_shop_item(old_oh.get('name', '')) if isinstance(old_oh, dict) else None
                    if oh_entry:
                        self._apply_equipment_stats(oh_entry, sign=-1)
                    inv = list(self.model.inventory or [])
                    inv.append(old_oh)
                    self.model.inventory = inv

        # Off_hand item while main_hand holds a two-handed weapon: unequip the weapon first
        if slot == 'off_hand':
            mh = equipment.get('main_hand')
            mh_entry = get_shop_item(mh.get('name', '')) if isinstance(mh, dict) and 'name' in mh else None
            if mh_entry and mh_entry.get('type') == 'two_handed':
                self.unequip('main_hand')
                equipment = dict(self.model.equipment or {})

        old_item = equipment.get(slot)

        # Unequip previous item — remove its stat bonuses and return to inventory
        if old_item and isinstance(old_item, dict) and '_two_hand_ref' not in old_item:
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
        # Place two-hand sentinel in off_hand to block that slot
        if item_type == 'two_handed':
            equipment['off_hand'] = {'_two_hand_ref': found.get('name', '')}
        self.model.equipment = equipment
        self.session.commit()
        return slot

    def unequip(self, slot):
        # Remove the item from an equipment slot and return it to inventory.
        # Returns the item name or '' if the slot was empty.
        from data.shop import get_shop_item
        equipment = dict(self.model.equipment or {})
        # Migrate legacy 3-slot keys before any logic
        self._migrate_old_equipment_slots(equipment)

        item = equipment.get(slot)
        if not item:
            return ''

        # If off_hand contains the two-hand sentinel, redirect to main_hand unequip
        if slot == 'off_hand' and isinstance(item, dict) and '_two_hand_ref' in item:
            slot = 'main_hand'
            item = equipment.get('main_hand')
            if not item:
                # Sentinel present but main_hand already gone — just clear sentinel
                del equipment['off_hand']
                self.model.equipment = equipment
                self.session.commit()
                return ''

        # Skip sentinel dicts — should not reach inventory
        if isinstance(item, dict) and '_two_hand_ref' in item:
            del equipment[slot]
            self.model.equipment = equipment
            self.session.commit()
            return ''

        shop_entry = get_shop_item(item.get('name', '')) if isinstance(item, dict) else None
        if shop_entry:
            self._apply_equipment_stats(shop_entry, sign=-1)

        inv = list(self.model.inventory or [])
        inv.append(item)
        self.model.inventory = inv
        del equipment[slot]

        # If we just unequipped a two-handed weapon, also clear the off_hand sentinel
        if shop_entry and shop_entry.get('type') == 'two_handed':
            equipment.pop('off_hand', None)

        self.model.equipment = equipment
        self.session.commit()
        return item.get('name', '') if isinstance(item, dict) else ''

    @staticmethod
    def _migrate_old_equipment_slots(equipment):
        # In-place migrate legacy 3-slot keys to 9-slot keys. Returns modified dict.
        renames = {'weapon': 'main_hand', 'armor': 'body'}
        for old, new in renames.items():
            if old in equipment and new not in equipment:
                equipment[new] = equipment.pop(old)
        # 'accessory' → infer from item type
        if 'accessory' in equipment:
            item = equipment.pop('accessory')
            if isinstance(item, dict):
                from data.shop import get_shop_item
                entry = get_shop_item(item.get('name', '')) or {}
                itype = entry.get('type', item.get('type', ''))
                _TYPE_TO_SLOT_LOCAL = {
                    'boots': 'feet', 'helmet': 'head', 'gloves': 'hands',
                    'ring': 'ring', 'earring': 'earring', 'necklace': 'necklace',
                    'shield': 'off_hand', 'off_hand': 'off_hand',
                }
                slot = _TYPE_TO_SLOT_LOCAL.get(itype, 'necklace')
                if slot not in equipment:
                    equipment[slot] = item
        return equipment

    def _apply_equipment_stats(self, shop_entry, sign):
        # Skip two-hand sentinel dicts — they carry no stats
        if isinstance(shop_entry, dict) and '_two_hand_ref' in shop_entry:
            return
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

    # Maps specific item names (lowercase) → set of classes that may equip them.
    # Items not in this table are equippable by all classes.
    _CLASS_EQUIP_RULES = {
        # Single-hand weapons
        'iron sword':        {'warrior', 'rogue'},
        'steel sword':       {'warrior', 'rogue'},
        'mage staff':        {'mage'},
        'arcane wand':       {'mage'},
        'holy mace':         {'cleric'},
        # Two-handed weapons
        'longbow':           {'warrior', 'rogue'},
        'great sword':       {'warrior'},
        'war hammer':        {'warrior', 'cleric'},
        'battle staff':      {'mage'},
        # Shields / off-hand
        'steel shield':      {'warrior', 'cleric'},
        'buckler':           {'warrior', 'cleric', 'rogue'},
        'tower shield':      {'warrior'},
        'focus orb':         {'mage'},
        'holy tome':         {'cleric'},
        'mage tome':         {'mage'},
        'spell focus crystal': {'mage'},
        # Helmets
        'iron helm':         {'warrior', 'cleric'},
        'mage hood':         {'mage'},
        'rogue hood':        {'rogue'},
        # Body armor
        'leather armor':     {'warrior', 'rogue', 'cleric'},
        'cloak of shadows':  {'rogue'},
        'chainmail':         {'warrior', 'cleric'},
        'plate armor':       {'warrior'},
        # Gloves
        'steel gauntlets':   {'warrior', 'cleric'},
        'arcane gloves':     {'mage'},
        'rogue gloves':      {'rogue'},
        # Boots
        'iron boots':        {'warrior', 'cleric'},
        'arcane boots':      {'mage'},
        # Necklaces
        'holy symbol':       {'cleric'},
        'arcane pendant':    {'mage'},
        'warrior pendant':   {'warrior'},
        # Rings
        'mana ring':         {'mage', 'cleric'},
        'strength ring':     {'warrior', 'rogue'},
        # Earrings
        'moonstone earring': {'mage', 'cleric'},
        'rogue earring':     {'rogue'},
        'warrior earring':   {'warrior'},
        # Chinese aliases
        '鐵劍':     {'warrior', 'rogue'},
        '鋼劍':     {'warrior', 'rogue'},
        '法師杖':   {'mage'},
        '聖錘':     {'cleric'},
        '雙手劍':   {'warrior'},
        '戰鎚':     {'warrior', 'cleric'},
        '戰鬥法杖': {'mage'},
        '長弓':     {'warrior', 'rogue'},
        '鋼盾':     {'warrior', 'cleric'},
        '塔盾':     {'warrior'},
        '皮甲':     {'warrior', 'rogue', 'cleric'},
        '鎖甲':     {'warrior', 'cleric'},
        '板甲':     {'warrior'},
        '鐵盔':     {'warrior', 'cleric'},
        '鋼鐵護手': {'warrior', 'cleric'},
        '鐵靴':     {'warrior', 'cleric'},
        '聖符':     {'cleric'},
        '法術聚焦水晶': {'mage'},
        '法力戒指': {'mage', 'cleric'},
        '月長石耳環': {'mage', 'cleric'},
    }

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

        # Sum proficiency bonuses stored in Character.skills (dict entries from skill books / training)
        proficiency_bonus = 0
        for entry in (self.model.skills or []):
            if isinstance(entry, dict) and entry.get('skill', '').lower() == skill_lower:
                proficiency_bonus += entry.get('bonus', 0)

        return base_mod + tool_bonus + proficiency_bonus

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

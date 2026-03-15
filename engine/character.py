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
        if self.model.mp >= amount:
            self.model.mp -= amount
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

    def get_skill_modifier(self, skill_name):
        """
        Return the integer modifier for a skill check.
        The rule engine passes this to DiceRoller so the LLM never touches dice math.
        Returns 0 for unrecognised skills (no modifier applied).
        """
        stat_name = self._SKILL_STAT_MAP.get(skill_name.lower().strip())
        if stat_name is None:
            return 0
        stat_val = getattr(self.model, stat_name, 10) or 10
        return (stat_val - 10) // 2

    def get_weapon_damage_notation(self):
        # Return the damage notation string for this character's class.
        # Used by the combat rule engine in EventManager._resolve_combat().
        char_class = (self.model.char_class or 'warrior').lower().strip()
        base_dice = self._CLASS_DAMAGE_MAP.get(char_class, '1d6')
        atk_mod = (self.model.atk - 10) // 2
        if atk_mod >= 0:
            return f"{base_dice}+{atk_mod}"
        return f"{base_dice}{atk_mod}"   # atk_mod is already negative

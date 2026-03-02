class CharacterLogic:
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
        if not self.model.inventory: return False
        inventory = self.model.inventory.copy()
        for idx, item in enumerate(inventory):
            if item.get('name') == item_name:
                inventory.pop(idx)
                self.model.inventory = inventory
                self.session.commit()
                return True
        return False

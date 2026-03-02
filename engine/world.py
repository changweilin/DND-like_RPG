import json
from engine.game_state import GameState

class WorldManager:
    def __init__(self, db_session, state_model: GameState):
        self.session = db_session
        self.state = state_model

    def update_location(self, new_location):
        self.state.current_location = new_location
        self.session.commit()

    def update_world_context(self, context_str):
        self.state.world_context = context_str
        self.session.commit()

    def update_relationship(self, entity_name, change_amount):
        """Update reputation or relationship with an entity/faction."""
        rels = self.state.relationships.copy() if self.state.relationships else {}
        current_val = rels.get(entity_name, 0)
        rels[entity_name] = current_val + change_amount
        self.state.relationships = rels
        self.session.commit()

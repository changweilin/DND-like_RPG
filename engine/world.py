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

    def update_relationship(self, entity_name, affinity_delta, state=None, goal=None):
        """
        Update an NPC / faction relationship entry.

        Relationships are stored as:
            {name: {"affinity": int, "state": str, "goal": str}}

        "affinity" is a signed integer (-100 = hostile, 0 = neutral, +100 = devoted).
        "state"    is a short mood label (e.g. "Friendly", "Suspicious", "Fearful").
        "goal"     is the NPC's current short-term objective (free text).

        Legacy flat-integer entries ({"Village Elder": 10}) are silently migrated.
        """
        rels = dict(self.state.relationships or {})
        existing = rels.get(entity_name, {})

        # Migrate legacy format
        if isinstance(existing, (int, float)):
            existing = {"affinity": int(existing), "state": "Neutral", "goal": ""}

        existing["affinity"] = existing.get("affinity", 0) + affinity_delta
        if state is not None:
            existing["state"] = state
        if goal is not None:
            existing["goal"] = goal

        rels[entity_name] = existing
        self.state.relationships = rels
        self.session.commit()

    def get_relationship(self, entity_name):
        """Return the relationship dict for an NPC, or neutral defaults if not tracked."""
        rels = self.state.relationships or {}
        entry = rels.get(entity_name, {"affinity": 0, "state": "Neutral", "goal": ""})
        if isinstance(entry, (int, float)):
            return {"affinity": int(entry), "state": "Neutral", "goal": ""}
        return entry

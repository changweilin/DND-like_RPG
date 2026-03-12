from sqlalchemy.orm.attributes import flag_modified
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

    def update_relationship(self, entity_name, affinity_delta, state=None, goal=None,
                            emotion=None, action=None, health=None,
                            proper_name=None, aliases=None,
                            biography=None, personality=None, traits=None):
        """
        Update an NPC / faction relationship entry.

        Core fields (always available):
          affinity — signed integer (-100 = hostile, 0 = neutral, +100 = devoted)
          state    — short mood label (Friendly / Suspicious / Fearful / …)
          goal     — NPC's current short-term objective

        Rich profile fields (populated on first encounter, then stable):
          proper_name  — proper given name (may differ from the dict key if key is a title)
          aliases      — list of titles, honorifics, nicknames
          biography    — 2-3 sentence life history
          personality  — MBTI type + description
          traits       — appearance, build, intelligence, physique (text)

        Scene-volatile fields (updated each turn, cleared when NPC leaves):
          emotion — current emotional state (set only when NPC is in scene)
          action  — what the NPC is visibly doing right now
          health  — health status (Healthy / Wounded / …)

        Legacy flat-integer entries ({"Village Elder": 10}) are silently migrated.
        """
        rels = dict(self.state.relationships or {})
        existing = rels.get(entity_name, {})

        # Migrate legacy format
        if isinstance(existing, (int, float)):
            existing = {"affinity": int(existing), "state": "Neutral", "goal": ""}

        existing["affinity"] = max(-100, min(100, existing.get("affinity", 0) + affinity_delta))
        if state is not None:
            existing["state"] = state
        if goal is not None:
            existing["goal"] = goal
        if emotion is not None:
            existing["emotion"] = emotion
        if action is not None:
            existing["action"] = action
        if health is not None:
            existing["health"] = health
        if proper_name is not None:
            existing["proper_name"] = proper_name
        if aliases is not None:
            existing["aliases"] = aliases
        if biography is not None:
            existing["biography"] = biography
        if personality is not None:
            existing["personality"] = personality
        if traits is not None:
            existing["traits"] = traits

        rels[entity_name] = existing
        self.state.relationships = rels
        # flag_modified is required: SQLAlchemy cannot detect in-place mutations
        # to JSON columns without an explicit signal
        flag_modified(self.state, 'relationships')
        self.session.commit()

    def register_npc(self, display_name, profile):
        """
        Register a new NPC entry in relationships with a full generated profile.

        If display_name is already tracked, only fills in missing profile fields
        (never overwrites existing data — avoids clobbering turn-by-turn state).
        profile is a dict returned by LLMClient.generate_npc_profile().
        """
        rels = dict(self.state.relationships or {})
        if display_name in rels:
            # Already tracked — only back-fill missing profile fields
            existing = rels[display_name]
            if isinstance(existing, (int, float)):
                existing = {"affinity": int(existing), "state": "Neutral", "goal": ""}
            for field in ('proper_name', 'aliases', 'biography', 'personality', 'traits', 'health', 'action'):
                if not existing.get(field) and profile.get(field):
                    existing[field] = profile[field]
            if not existing.get('goal') and profile.get('goal'):
                existing['goal'] = profile['goal']
            rels[display_name] = existing
        else:
            rels[display_name] = {
                "affinity":    0,
                "state":       "Neutral",
                "goal":        profile.get('goal', ''),
                "proper_name": profile.get('proper_name', display_name),
                "aliases":     profile.get('aliases', []),
                "biography":   profile.get('biography', ''),
                "personality": profile.get('personality', ''),
                "traits":      profile.get('traits', ''),
                "health":      profile.get('health', 'Healthy'),
                "action":      profile.get('action', ''),
                "emotion":     "",   # emotion is empty until NPC enters an active scene
            }
        self.state.relationships = rels
        flag_modified(self.state, 'relationships')
        self.session.commit()

    def get_relationship(self, entity_name):
        """Return the relationship dict for an NPC, or neutral defaults if not tracked."""
        rels = self.state.relationships or {}
        entry = rels.get(entity_name, {"affinity": 0, "state": "Neutral", "goal": ""})
        if isinstance(entry, (int, float)):
            return {"affinity": int(entry), "state": "Neutral", "goal": ""}
        return entry

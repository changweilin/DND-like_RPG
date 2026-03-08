import os
from engine.game_state import DatabaseManager, GameState, Character
from engine.config import config

class SaveLoadManager:
    """Handles creating, saving, and loading game sessions."""

    def __init__(self, db_manager=None):
        # Dependency injection: accept an external DatabaseManager so tests and
        # the UI can provide their own DB path without touching config.
        if db_manager is None:
            db_manager = DatabaseManager(config.get_db_path())
        self.db_manager = db_manager

    def create_new_game(self, save_name, character_name, race, char_class,
                        appearance, personality,
                        difficulty="Normal", language="English",
                        world_context="The world is a blank slate, waiting for heroes."):
        session = self.db_manager.get_session()

        existing = session.query(GameState).filter_by(save_name=save_name).first()
        if existing:
            session.close()
            return None, None, None

        player = Character(
            name=character_name,
            race=race,
            char_class=char_class,
            appearance=appearance,
            personality=personality,
            hp=100, max_hp=100,
            mp=50, max_mp=50,
            atk=10, def_stat=10, mov=5,
            gold=100,
            inventory=[],
            skills=[],
        )
        session.add(player)
        session.commit()

        game_state = GameState(
            save_name=save_name,
            current_location="Starting Village",
            world_context=world_context,
            difficulty=difficulty,
            language=language,
            player_id=player.id,
            turn_count=0,
            # NPC entity format: {name: {affinity, state, goal}}
            relationships={
                "Village Elder": {"affinity": 10, "state": "Friendly", "goal": "Protect the village"}
            },
            session_memory=[],
            # Dynamically generated entity stat blocks for live HP tracking
            known_entities={},
        )
        session.add(game_state)
        session.commit()

        return player, game_state, session

    def load_game(self, save_name):
        session = self.db_manager.get_session()
        game_state = session.query(GameState).filter_by(save_name=save_name).first()
        if not game_state:
            session.close()
            return None, None, None

        player = session.query(Character).filter_by(id=game_state.player_id).first()
        return player, game_state, session

    def list_saves(self):
        session = self.db_manager.get_session()
        saves = session.query(
            GameState.save_name, GameState.current_location, GameState.turn_count
        ).all()
        session.close()
        return [{"save_name": s[0], "location": s[1], "turns": s[2] or 0} for s in saves]

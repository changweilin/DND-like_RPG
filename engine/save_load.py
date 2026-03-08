import os
from engine.game_state import DatabaseManager, GameState, Character
from engine.config import config, GameConfig

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
                        world_context="",
                        world_setting="dnd5e"):
        session = self.db_manager.get_session()

        existing = session.query(GameState).filter_by(save_name=save_name).first()
        if existing:
            session.close()
            return None, None, None

        # Pull world-setting defaults (starting location, NPC, lore)
        ws = GameConfig.get_world_setting(world_setting)
        starting_location = ws['starting_location']
        starting_npc      = ws.get('starting_npc', {
            "name": "Village Elder", "affinity": 10, "state": "Friendly",
            "goal": "Protect the settlement",
        })
        # Use caller-supplied world_context; fall back to the world's built-in lore
        effective_world_context = world_context.strip() or ws.get('world_lore', 'A world waiting to be explored.')

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

        npc_name = starting_npc['name']
        game_state = GameState(
            save_name=save_name,
            current_location=starting_location,
            world_context=effective_world_context,
            difficulty=difficulty,
            language=language,
            world_setting=world_setting,
            player_id=player.id,
            turn_count=0,
            relationships={
                npc_name: {
                    "affinity": starting_npc.get('affinity', 0),
                    "state":    starting_npc.get('state', 'Neutral'),
                    "goal":     starting_npc.get('goal', ''),
                }
            },
            session_memory=[],
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

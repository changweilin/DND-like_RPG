import os
from engine.game_state import DatabaseManager, GameState, Character
from engine.config import config

class SaveLoadManager:
    """Handles creating, saving, and loading game sessions."""
    def __init__(self):
        self.db_manager = DatabaseManager(config.get_db_path())
        
    def create_new_game(self, save_name, player_name, race, char_class, appearance, personality, difficulty="Normal", language="English"):
        session = self.db_manager.get_session()
        
        # Check if save exists
        existing = session.query(GameState).filter_by(save_name=save_name).first()
        if existing:
            session.close()
            return False, "Save name already exists."
            
        # Create Player
        player = Character(
            name=player_name,
            race=race,
            char_class=char_class,
            appearance=appearance,
            personality=personality,
            # Base stats could be derived from race/class later
            hp=100, max_hp=100,
            mp=50, max_mp=50,
            atk=10, def_stat=10, mov=5,
            gold=100
        )
        session.add(player)
        session.commit() # Commit to get player ID
        
        # Create Game State
        game_state = GameState(
            save_name=save_name,
            current_location="Starting Village",
            world_context="The world is a blank slate, waiting for heroes.",
            difficulty=difficulty,
            language=language,
            player_id=player.id,
            relationships={"Village Elder": 10}
        )
        session.add(game_state)
        session.commit()
        
        session.close()
        return True, "Game created."

    def load_game(self, save_name):
        session = self.db_manager.get_session()
        game_state = session.query(GameState).filter_by(save_name=save_name).first()
        if not game_state:
            session.close()
            return None, None, None
            
        player = session.query(Character).filter_by(id=game_state.player_id).first()
        return session, game_state, player
        
    def list_saves(self):
        session = self.db_manager.get_session()
        saves = session.query(GameState.save_name, GameState.current_location).all()
        session.close()
        return [{"name": s[0], "location": s[1]} for s in saves]

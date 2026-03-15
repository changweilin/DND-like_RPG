import json
import os
from engine.config import GameConfig

class PersistenceManager:
    """Handles saving and loading of user preferences and UI state."""
    
    PREFS_FILE = os.path.join(GameConfig.SAVE_DIR, "user_prefs.json")

    @classmethod
    def save_prefs(cls, prefs):
        """Save a dictionary of preferences to a JSON file."""
        os.makedirs(GameConfig.SAVE_DIR, exist_ok=True)
        try:
            with open(cls.PREFS_FILE, "w", encoding="utf-8") as f:
                json.dump(prefs, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    @classmethod
    def load_prefs(cls):
        """Load preferences from the JSON file. Returns empty dict if not found."""
        if not os.path.exists(cls.PREFS_FILE):
            return {}
        try:
            with open(cls.PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading preferences: {e}")
            return {}

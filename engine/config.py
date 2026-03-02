import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class GameConfig:
    # Model preferences
    USER_VRAM_GB = 12
    LLM_MODEL_NAME = "llama3" # Default quantization in ollama
    IMAGE_MODEL_NAME = "stabilityai/sdxl-turbo" # good for fast, lower VRAM usage

    # Fallback policies (User chosen, matching Options A and B)
    # "A": Skip Image Gen completely if low VRAM
    # "B": Swap models (Unload LLM -> Load Image Gen -> Generate -> Unload Image Gen -> Load LLM)
    VRAM_STRATEGY = "B"

    # Paths (absolute, relative to project root)
    SAVE_DIR = os.path.join(_PROJECT_ROOT, "saves")
    DB_NAME = "savegame.db"
    CHROMA_DB_DIR = os.path.join(_PROJECT_ROOT, "chroma_data")

    @classmethod
    def get_db_path(cls):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        return os.path.join(cls.SAVE_DIR, cls.DB_NAME)
        
config = GameConfig()

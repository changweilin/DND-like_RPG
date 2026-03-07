import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class GameConfig:
    # --- Model preferences ---
    # RTX 3060 (12 GB VRAM): "jcai/breeze-7b-instruct-v1_0-gguf" (Q6_K) or "llama3"
    # RTX 4090 (24 GB VRAM): "qwen2.5:32b" (Q4_K_M) or "qwen2.5:32b" (Q5_K_M)
    USER_VRAM_GB = 12
    LLM_MODEL_NAME = "llama3"
    IMAGE_MODEL_NAME = "stabilityai/sdxl-turbo"

    # Fallback policies
    # "A": Skip image gen entirely if VRAM is too low
    # "B": Swap models (unload LLM -> load image model -> generate -> unload -> reload LLM)
    VRAM_STRATEGY = "B"

    # --- Memory & context window ---
    # Sliding window: number of past turns kept in session memory (passed to LLM each turn)
    SESSION_MEMORY_WINDOW = 15
    # Target context window in tokens — should match your deployed model's context limit
    CONTEXT_WINDOW_SIZE = 8192

    # --- Paths (absolute, relative to project root) ---
    SAVE_DIR = os.path.join(_PROJECT_ROOT, "saves")
    DB_NAME = "savegame.db"
    CHROMA_DB_DIR = os.path.join(_PROJECT_ROOT, "chroma_data")

    @classmethod
    def get_db_path(cls):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        return os.path.join(cls.SAVE_DIR, cls.DB_NAME)

config = GameConfig()

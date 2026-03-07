import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class GameConfig:
    # --- Model preferences ---
    # RTX 3060 (12 GB VRAM): "jcai/breeze-7b-instruct-v1_0-gguf" (Q6_K) or "llama3"
    #   Breeze-7B has an expanded Traditional Chinese tokenizer — ~2× faster Chinese TPS.
    # RTX 4090 (24 GB VRAM): "qwen2.5:32b" (Q4_K_M / Q5_K_M) — top-tier bilingual,
    #   or "ms3.2-24b" (Magnum Diamond) for literary RP quality.
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
    # At 8K context: RAG chunks + session memory + system prompt consume ~4–5K tokens.
    # Raise to 16384 or 32768 if running Qwen3-32B / MS3.2-24B on a 4090.
    CONTEXT_WINDOW_SIZE = 8192

    # --- RAG / Embedding ---
    # ChromaDB default embedding: all-MiniLM-L6-v2 (sentence-transformers, ~80 MB).
    # For better D&D terminology recall, fine-tune a BGE embedding model on the
    # Datapizza AI Lab D&D 5e SRD QA dataset and set this path to the local model dir.
    # Leave empty to use ChromaDB's built-in default.
    EMBEDDING_MODEL = ""

    # --- Paths (absolute, relative to project root) ---
    SAVE_DIR = os.path.join(_PROJECT_ROOT, "saves")
    DB_NAME = "savegame.db"
    CHROMA_DB_DIR = os.path.join(_PROJECT_ROOT, "chroma_data")
    # Directory where SRD JSON files (soryy708/dnd5-srd format) are stored.
    # Run tools/seed_srd.py after placing JSON files here to seed game_rules RAG.
    SRD_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "srd")
    # Directory where generated LoRA training data is written by tools/gen_lora_data.py
    LORA_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "lora_training")

    @classmethod
    def get_db_path(cls):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        return os.path.join(cls.SAVE_DIR, cls.DB_NAME)

config = GameConfig()

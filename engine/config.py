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

    # ---------------------------------------------------------------------------
    # Model preset registry
    # Each entry drives the UI selector and the LLMClient provider routing.
    # Fields:
    #   id          — model identifier passed to the provider SDK
    #   name        — display name shown in the UI
    #   provider    — "ollama" | "anthropic" | "openai" | "google"
    #   category    — group header shown in the UI selector
    #   description — one-line summary shown as tooltip / sub-caption
    #   pros        — comma-separated positive points
    #   cons        — comma-separated caveats / requirements
    #   env_key     — environment variable name for the API key (cloud only)
    #   base_url    — override API base URL (e.g. xAI Grok uses OpenAI-compat API)
    #   vram_gb     — approximate VRAM requirement (local models only)
    #   tags        — freeform labels for filtering / recommendation logic
    # ---------------------------------------------------------------------------
    MODEL_PRESETS = [
        # -----------------------------------------------------------------------
        # Local models — run via Ollama, no API key required
        # -----------------------------------------------------------------------
        {
            "id":          "llama3",
            "name":        "Llama 3 8B",
            "provider":    "ollama",
            "category":    "Local · Ollama",
            "description": "Meta Llama 3 8B — solid English narration, entry-level local model.",
            "pros":        "Free · Offline · No API key · ~6 GB VRAM",
            "cons":        "Limited Chinese · Weaker reasoning than 32B+ models",
            "vram_gb":     6,
            "tags":        ["english", "entry-level"],
        },
        {
            "id":          "jcai/breeze-7b-instruct-v1_0-gguf",
            "name":        "Breeze-7B 繁中 (Q6_K)",
            "provider":    "ollama",
            "category":    "Local · Ollama",
            "description": "MediaTek Research Breeze-7B — expanded 繁體中文 tokenizer, 32K context.",
            "pros":        "2× Chinese TPS · 32K context · Offline · ~8 GB VRAM",
            "cons":        "Primarily 繁中-optimized · Pure-English slightly slower",
            "vram_gb":     8,
            "tags":        ["chinese", "recommended-3060"],
        },
        {
            "id":          "qwen2.5:14b",
            "name":        "Qwen 2.5 14B",
            "provider":    "ollama",
            "category":    "Local · Ollama",
            "description": "Alibaba Qwen 2.5 14B — strong bilingual reasoning, fits 3060 at Q4.",
            "pros":        "Strong zh/en · 128K context · Good JSON adherence · Offline",
            "cons":        "~10 GB VRAM at Q4_K_M",
            "vram_gb":     10,
            "tags":        ["chinese", "english", "recommended-3060"],
        },
        {
            "id":          "qwen2.5:32b",
            "name":        "Qwen 2.5 32B",
            "provider":    "ollama",
            "category":    "Local · Ollama",
            "description": "Alibaba Qwen 2.5 32B — top-tier bilingual, strict JSON, needs 4090.",
            "pros":        "Best open-source zh/en · Long context · Strict JSON output",
            "cons":        "~20 GB VRAM — requires RTX 4090 or better",
            "vram_gb":     20,
            "tags":        ["chinese", "english", "recommended-4090"],
        },
        {
            "id":          "qwen3:32b",
            "name":        "Qwen 3 32B (Thinking)",
            "provider":    "ollama",
            "category":    "Local · Ollama",
            "description": "Alibaba Qwen 3 32B — thinking mode, latest generation, best reasoning.",
            "pros":        "Thinking mode · Best open-source reasoning · zh/en · Offline",
            "cons":        "~22 GB VRAM · Slower due to thinking steps",
            "vram_gb":     22,
            "tags":        ["chinese", "english", "thinking", "recommended-4090"],
        },
        # -----------------------------------------------------------------------
        # Cloud models — Anthropic Claude
        # -----------------------------------------------------------------------
        {
            "id":          "claude-sonnet-4-6",
            "name":        "Claude Sonnet 4.6",
            "provider":    "anthropic",
            "category":    "Cloud · Anthropic",
            "description": "Anthropic Claude Sonnet 4.6 — fast, strong reasoning and narration.",
            "pros":        "Excellent DM narrative · Fast · Long context · Safe outputs",
            "cons":        "Requires ANTHROPIC_API_KEY · Paid · Internet connection",
            "env_key":     "ANTHROPIC_API_KEY",
            "tags":        ["cloud", "paid", "narrative"],
        },
        {
            "id":          "claude-opus-4-6",
            "name":        "Claude Opus 4.6",
            "provider":    "anthropic",
            "category":    "Cloud · Anthropic",
            "description": "Anthropic Claude Opus 4.6 — highest narrative quality and reasoning.",
            "pros":        "Best narrative quality · Deep reasoning · Long context",
            "cons":        "Requires ANTHROPIC_API_KEY · Higher cost than Sonnet",
            "env_key":     "ANTHROPIC_API_KEY",
            "tags":        ["cloud", "paid", "narrative", "best"],
        },
        # -----------------------------------------------------------------------
        # Cloud models — Google Gemini
        # -----------------------------------------------------------------------
        {
            "id":          "gemini-2.0-flash",
            "name":        "Gemini 2.0 Flash",
            "provider":    "google",
            "category":    "Cloud · Google",
            "description": "Google Gemini 2.0 Flash — very fast, free tier available.",
            "pros":        "Fast · Free tier (rate-limited) · 1M token context",
            "cons":        "Requires GOOGLE_API_KEY · Internet · Weaker roleplay personality",
            "env_key":     "GOOGLE_API_KEY",
            "tags":        ["cloud", "free-tier", "fast"],
        },
        {
            "id":          "gemini-2.5-pro",
            "name":        "Gemini 2.5 Pro",
            "provider":    "google",
            "category":    "Cloud · Google",
            "description": "Google Gemini 2.5 Pro — thinking mode, 1M context, top reasoning.",
            "pros":        "1M context window · Thinking mode · Top Google reasoning",
            "cons":        "Requires GOOGLE_API_KEY · Paid · Slower than Flash",
            "env_key":     "GOOGLE_API_KEY",
            "tags":        ["cloud", "paid", "thinking", "long-context"],
        },
        # -----------------------------------------------------------------------
        # Cloud models — OpenAI GPT
        # -----------------------------------------------------------------------
        {
            "id":          "gpt-4o",
            "name":        "GPT-4o",
            "provider":    "openai",
            "category":    "Cloud · OpenAI",
            "description": "OpenAI GPT-4o — strong general intelligence, reliable JSON output.",
            "pros":        "Reliable JSON · Strong reasoning · Multimodal · Fast",
            "cons":        "Requires OPENAI_API_KEY · Paid · Internet connection",
            "env_key":     "OPENAI_API_KEY",
            "tags":        ["cloud", "paid"],
        },
        {
            "id":          "gpt-4o-mini",
            "name":        "GPT-4o Mini",
            "provider":    "openai",
            "category":    "Cloud · OpenAI",
            "description": "OpenAI GPT-4o Mini — cheap, fast, solid JSON for structured tasks.",
            "pros":        "Low cost · Fast · Good JSON adherence",
            "cons":        "Requires OPENAI_API_KEY · Less creative than full GPT-4o",
            "env_key":     "OPENAI_API_KEY",
            "tags":        ["cloud", "paid", "cheap"],
        },
        # -----------------------------------------------------------------------
        # Cloud models — xAI Grok (OpenAI-compatible API)
        # -----------------------------------------------------------------------
        {
            "id":          "grok-3",
            "name":        "Grok 3",
            "provider":    "openai",       # xAI uses OpenAI-compatible REST API
            "category":    "Cloud · xAI",
            "description": "xAI Grok 3 — real-time web access, 131K context, strong reasoning.",
            "pros":        "Real-time web info · 131K context · Strong narrative",
            "cons":        "Requires XAI_API_KEY · Paid · Internet connection",
            "env_key":     "XAI_API_KEY",
            "base_url":    "https://api.x.ai/v1",
            "tags":        ["cloud", "paid", "realtime"],
        },
        {
            "id":          "grok-3-mini",
            "name":        "Grok 3 Mini",
            "provider":    "openai",
            "category":    "Cloud · xAI",
            "description": "xAI Grok 3 Mini — fast, cheap, reasoning-focused variant.",
            "pros":        "Fast · Lower cost · Thinking mode · Web access",
            "cons":        "Requires XAI_API_KEY · Weaker narrative than full Grok 3",
            "env_key":     "XAI_API_KEY",
            "base_url":    "https://api.x.ai/v1",
            "tags":        ["cloud", "paid", "cheap", "thinking"],
        },
    ]

    @classmethod
    def get_db_path(cls):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        return os.path.join(cls.SAVE_DIR, cls.DB_NAME)

config = GameConfig()

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

    # Minimum free VRAM (GB) required before attempting an image generation call.
    # SDXL-Turbo fp16 needs ~4 GB.  Raise if you see OOM with other image models.
    IMAGE_VRAM_REQUIRED_GB = 4.0

    # Auto-disable image generation after this many consecutive OOM / failures.
    # Set to 0 to never auto-disable (useful for debugging; risk of repeated OOM).
    IMAGE_GEN_MAX_FAILURES = 3

    # Generate a cinematic scene image every N turns regardless of other triggers.
    # Set to 0 to disable milestone images (only explicit cinematic events will trigger).
    IMAGE_GEN_MILESTONE_TURNS = 5

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
    # Multi-player party configuration
    # ---------------------------------------------------------------------------

    MAX_PARTY_SIZE = 5
    MIN_PARTY_SIZE = 1

    # Per-player flag emoji (index 0-5 → player slot 0-5).
    # Shown in sidebar and game prompts to visually distinguish each party member.
    PLAYER_FLAGS = ['🔴', '🔵', '🟢', '🟡', '🟣', '🟠']

    # ---------------------------------------------------------------------------
    # AI Player configuration
    # ---------------------------------------------------------------------------

    # Personality archetypes — define action bias and decision thresholds.
    # These are used by AIPlayerController in logic/events.py.
    AI_PERSONALITIES = {
        'aggressive': {
            'name':            'Aggressive',
            'description':     'Prefers direct attacks; ignores self-preservation; targets strongest enemies.',
            'action_bias':     'combat',
            'heal_threshold':  0.10,   # only heals when nearly dead
            'attack_first':    True,
        },
        'cautious': {
            'name':            'Cautious',
            'description':     'Defensive fighter; retreats when HP < 50%; avoids unnecessary risk.',
            'action_bias':     'defense',
            'heal_threshold':  0.50,
            'attack_first':    False,
        },
        'support': {
            'name':            'Support',
            'description':     'Prioritises healing and protecting teammates over personal glory.',
            'action_bias':     'healing',
            'heal_threshold':  0.70,   # heals aggressively
            'attack_first':    False,
        },
        'chaotic': {
            'name':            'Chaotic',
            'description':     'Unpredictable — chooses actions randomly; anything can happen.',
            'action_bias':     'random',
            'heal_threshold':  0.30,
            'attack_first':    None,
        },
        'tactical': {
            'name':            'Tactical',
            'description':     'Analyses party composition and enemy state; makes context-aware optimal decisions.',
            'action_bias':     'optimal',
            'heal_threshold':  0.35,
            'attack_first':    None,
        },
    }

    # Difficulty levels — control decision-tree depth and LLM involvement.
    AI_DIFFICULTIES = {
        'easy': {
            'name':              'Easy',
            'description':       'Picks actions randomly from a safe pool — no real strategy.',
            'use_decision_tree': False,
            'use_llm':           False,
        },
        'normal': {
            'name':              'Normal',
            'description':       'Rule-based decision tree: attacks when healthy, heals when hurt.',
            'use_decision_tree': True,
            'use_llm':           False,
        },
        'hard': {
            'name':              'Hard',
            'description':       'Extended decision tree with multi-scenario evaluation.',
            'use_decision_tree': True,
            'use_llm':           False,
        },
        'deadly': {
            'name':              'Deadly',
            'description':       'Decision tree + LLM contextual refinement for optimal actions.',
            'use_decision_tree': True,
            'use_llm':           True,
        },
    }

    # Balanced base stats per class.
    # Design philosophy: each class has the same "power budget" but distributed
    # across different combat/support roles so all contribute equally at end-game.
    #
    # Power-budget formula used for tuning:
    #   effective_hp  = HP + (DEF // 2) × 5          (DEF mitigation over ~5 hits)
    #   avg_dps       = avg_damage × hit_probability  (vs enemy DEF 10 baseline)
    #   combat_score  = effective_hp × avg_dps
    #
    # Non-combat compensation:
    #   reward_weight — multiplier on contribution score to equalise end-game gold:
    #     1.00 = Warrior baseline (best sustained combat)
    #     1.15 = Rogue (good scout/skill checks, slightly lower combat)
    #     1.25 = Cleric (heals party; healing × 1.5 weights improve raw score)
    #     1.30 = Mage (arcana-check heavy; lower raw ATK offset by spell burst)
    #
    # contribution_score per player = (damage_dealt × 1.0
    #                                + healing_done  × 1.5
    #                                + checks_passed × 20) × reward_weight
    # End-game gold per player      = party_gold × (player_score / total_score)
    CLASS_BASE_STATS = {
        'warrior': {
            'hp': 150, 'max_hp': 150, 'mp': 20,  'max_mp': 20,
            'atk': 16, 'def_stat': 14, 'mov': 5,
            'gold': 80,
            'reward_weight': 1.00,
            'role': 'Tank — highest HP/DEF, sustained melee damage (1d8)',
        },
        'mage': {
            'hp': 70,  'max_hp': 70,  'mp': 100, 'max_mp': 100,
            'atk': 12, 'def_stat': 8,  'mov': 5,
            'gold': 80,
            'reward_weight': 1.30,
            'role': 'Burst — lowest HP, huge MP pool for spell-based arcana checks',
        },
        'rogue': {
            'hp': 90,  'max_hp': 90,  'mp': 50,  'max_mp': 50,
            'atk': 14, 'def_stat': 10, 'mov': 8,
            'gold': 80,
            'reward_weight': 1.15,
            'role': 'Scout — high MOV (acrobatics/stealth bonus), good damage (1d6)',
        },
        'cleric': {
            'hp': 110, 'max_hp': 110, 'mp': 70,  'max_mp': 70,
            'atk': 10, 'def_stat': 13, 'mov': 5,
            'gold': 80,
            'reward_weight': 1.25,
            'role': 'Healer — party sustain; healing is worth 1.5× in score calc',
        },
    }

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

    # ---------------------------------------------------------------------------
    # Image generation model registry
    #
    # Fields:
    #   id          — HuggingFace repo_id (diffusers) or logical id (cloud)
    #   name        — display name in the UI
    #   provider    — "diffusers" | "openai" | "stability"
    #   vram_gb     — approximate VRAM required (0 for cloud models)
    #   description — one-line summary shown in the selector
    #   steps       — default inference steps (diffusers only)
    #   guidance    — guidance_scale (diffusers; 0.0 for turbo/flow models)
    #   env_key     — environment variable for API key (cloud only)
    #   tags        — freeform labels
    # ---------------------------------------------------------------------------
    IMAGE_MODEL_PRESETS = [
        {
            "id":          "stabilityai/sdxl-turbo",
            "name":        "SDXL-Turbo",
            "provider":    "diffusers",
            "vram_gb":     4.0,
            "description": "1-step ultra-fast generation. Low VRAM. Default choice.",
            "steps":       1,
            "guidance":    0.0,
            "tags":        ["fast", "low-vram", "local"],
        },
        {
            "id":          "Lykon/dreamshaper-8",
            "name":        "DreamShaper 8",
            "provider":    "diffusers",
            "vram_gb":     4.0,
            "description": "Fantasy & portrait fine-tune of SD1.5. Great for RPG scenes.",
            "steps":       20,
            "guidance":    7.5,
            "tags":        ["fantasy", "portrait", "local"],
        },
        {
            "id":          "runwayml/stable-diffusion-v1-5",
            "name":        "SD v1.5",
            "provider":    "diffusers",
            "vram_gb":     4.0,
            "description": "Classic Stable Diffusion 1.5. Wide LoRA ecosystem.",
            "steps":       20,
            "guidance":    7.5,
            "tags":        ["classic", "local"],
        },
        {
            "id":          "stabilityai/stable-diffusion-xl-base-1.0",
            "name":        "SDXL Base 1.0",
            "provider":    "diffusers",
            "vram_gb":     6.0,
            "description": "Higher quality than Turbo. Needs ~6 GB VRAM.",
            "steps":       20,
            "guidance":    7.5,
            "tags":        ["quality", "local"],
        },
        {
            "id":          "black-forest-labs/FLUX.1-schnell",
            "name":        "FLUX.1-schnell",
            "provider":    "diffusers",
            "vram_gb":     8.0,
            "description": "State-of-art 4-step model. Best local quality. Needs ~8 GB VRAM.",
            "steps":       4,
            "guidance":    0.0,
            "tags":        ["sota", "quality", "local"],
        },
        {
            "id":          "dalle3",
            "name":        "DALL·E 3",
            "provider":    "openai",
            "vram_gb":     0,
            "description": "OpenAI cloud API. Best quality. Requires OPENAI_API_KEY.",
            "env_key":     "OPENAI_API_KEY",
            "tags":        ["cloud", "premium"],
        },
        {
            "id":          "stability-core",
            "name":        "Stability AI Core",
            "provider":    "stability",
            "vram_gb":     0,
            "description": "Stability AI cloud REST API. Requires STABILITY_API_KEY.",
            "env_key":     "STABILITY_API_KEY",
            "tags":        ["cloud"],
        },
    ]

    @classmethod
    def get_image_preset(cls, model_id):
        """Return IMAGE_MODEL_PRESETS entry for model_id, or the first preset as default."""
        for p in cls.IMAGE_MODEL_PRESETS:
            if p["id"] == model_id:
                return p
        return cls.IMAGE_MODEL_PRESETS[0]

    # ---------------------------------------------------------------------------
    # World setting registry
    # Each entry defines a switchable TRPG universe. Game mechanics are always
    # DnD-based under the hood; term_map provides vocabulary substitutions that
    # are injected into every LLM prompt so the AI speaks the right flavour.
    #
    # Fields:
    #   id               — unique identifier stored in GameState.world_setting
    #   name             — display name in the UI
    #   category         — group header (language/era of the setting)
    #   description      — one-line summary
    #   tone             — atmospheric guidance injected into the system prompt
    #   starting_location — default location name for new games
    #   starting_npc     — {name, affinity, state, goal} for the initial NPC
    #   world_lore       — seed paragraph stored in world_lore RAG on game start
    #   term_map         — DnD term → setting-specific vocabulary overrides
    #     hp_name        — what Hit Points are called
    #     mp_name        — what Magic Points / spell resource is called
    #     gold_name      — currency name
    #     warrior_class  — fighter archetype label
    #     mage_class     — magic-user archetype label
    #     rogue_class    — sneaky archetype label
    #     cleric_class   — healer/support archetype label
    #     dm_title       — what the game master is called
    #     skill_check    — how ability checks are described
    #     starting_area  — flavour term for the initial zone type
    # ---------------------------------------------------------------------------
    WORLD_SETTINGS = [
        # -------------------------------------------------------------------
        # 正統奇幻 · Classic Fantasy
        # -------------------------------------------------------------------
        {
            "id":               "dnd5e",
            "name":             "D&D 5e — Forgotten Realms",
            "category":         "正統奇幻 Classic Fantasy",
            "description":      "Classic high fantasy — taverns, dragons, dungeons and heroic quests.",
            "tone":             "Heroic high fantasy. Wondrous magic, ancient ruins, noble quests, and fearsome monsters. The world teems with adventurers seeking glory and gold.",
            "starting_location":"Phandalin — a small frontier town",
            "starting_npc":     {"name": "Sildar Hallwinter", "affinity": 10, "state": "Friendly", "goal": "Restore order to the Triboar Trail"},
            "world_lore":       "The Forgotten Realms is a world of high fantasy where magic suffuses the land. Adventurers travel from city-states to wild frontiers, battling monsters in ancient dungeons and earning coin at local taverns. The goddess Tymora smiles on bold heroes.",
            "term_map": {
                "hp_name":        "HP",
                "mp_name":        "Spell Slots / MP",
                "gold_name":      "gold pieces (gp)",
                "warrior_class":  "Fighter",
                "mage_class":     "Wizard",
                "rogue_class":    "Rogue",
                "cleric_class":   "Cleric",
                "dm_title":       "Dungeon Master",
                "skill_check":    "ability check",
                "starting_area":  "frontier town",
            },
        },
        {
            "id":               "pathfinder",
            "name":             "Pathfinder — Golarion",
            "category":         "正統奇幻 Classic Fantasy",
            "description":      "Richly detailed world with intense political intrigue, multiple continents, and vast pantheons.",
            "tone":             "Epic political fantasy. Golarion is a world of intricate nation-states, ancient god-wars, and diverse cultures. Heroes navigate scheming factions as much as dungeons.",
            "starting_location":"Absalom — City at the Center of the World",
            "starting_npc":     {"name": "Venture-Captain Drandle Dreng", "affinity": 10, "state": "Friendly", "goal": "Assign Pathfinder Society missions"},
            "world_lore":       "Golarion is a world rich with history and strife. The Pathfinder Society sends agents across the Inner Sea region to recover lost knowledge, negotiate with powerful factions, and battle the forces of the Whispering Tyrant. Magic is structured by the schools of Korvosa and Absalom.",
            "term_map": {
                "hp_name":        "HP",
                "mp_name":        "Spell Slots",
                "gold_name":      "gold pieces (gp)",
                "warrior_class":  "Fighter",
                "mage_class":     "Wizard",
                "rogue_class":    "Rogue",
                "cleric_class":   "Cleric",
                "dm_title":       "Game Master",
                "skill_check":    "skill check",
                "starting_area":  "city district",
            },
        },
        # -------------------------------------------------------------------
        # 黑暗奇幻 · Dark Fantasy
        # -------------------------------------------------------------------
        {
            "id":               "warhammer_fantasy",
            "name":             "Warhammer Fantasy — The Old World",
            "category":         "黑暗奇幻 Dark Fantasy",
            "description":      "Grim, perilous world of Empire soldiers, mutating Chaos taint, and desperate survival.",
            "tone":             "Grimdark low fantasy. Chaos corruption spreads from the north. Common folk live in fear. Heroes are gritty survivors, not shining paladins. Black humour and brutal consequences define every encounter.",
            "starting_location":"Bögenhafen — a prosperous river trading town",
            "starting_npc":     {"name": "Magistrate Kastor Lieberung", "affinity": 0, "state": "Suspicious", "goal": "Maintain order during the Schaffenfest festival"},
            "world_lore":       "The Old World is a place of crumbling empires, religious witch-hunters, and the ever-present taint of Chaos. The Empire of Man clings to order while rat-men stir beneath the cities and greenskins rampage on the borders. Magic is feared and practitioners called Wizards walk a fine line between power and corruption.",
            "term_map": {
                "hp_name":        "Wounds",
                "mp_name":        "Wind Points",
                "gold_name":      "gold crowns",
                "warrior_class":  "Soldier",
                "mage_class":     "Wizard",
                "rogue_class":    "Thief",
                "cleric_class":   "Priest of Sigmar",
                "dm_title":       "Game Master",
                "skill_check":    "characteristic test",
                "starting_area":  "river trading town",
            },
        },
        # -------------------------------------------------------------------
        # 科幻 · Sci-Fi
        # -------------------------------------------------------------------
        {
            "id":               "wh40k",
            "name":             "Warhammer 40,000 — Dark Imperium",
            "category":         "科幻 Sci-Fi",
            "description":      "Far-future galaxy-spanning war — Space Marines, xenos, Chaos gods, and relentless holy war.",
            "tone":             "Brutal science-fantasy grimdark. In the grim darkness of the far future, there is only war. The Imperium of Man fights xenos, heretics, and Chaos with fanatical zeal. Every victory is pyrrhic. Hope is a sin punished by the Inquisition.",
            "starting_location":"Hive Primus — underhive slums, Necromunda",
            "starting_npc":     {"name": "Arbitrator Harkon Vess", "affinity": 0, "state": "Suspicious", "goal": "Root out heresy in the underhive"},
            "world_lore":       "It is the 41st Millennium. The Emperor of Mankind sits, entombed but undying, upon the Golden Throne. His armies of Space Marines and Imperial Guard fight across ten thousand worlds. Psykers channel dangerous warp energies under the watchful eye of the Inquisition. Every xenos race is an existential threat. Every citizen is either a soldier or a servant.",
            "term_map": {
                "hp_name":        "Wounds",
                "mp_name":        "Psy Rating",
                "gold_name":      "thrones (Θ)",
                "warrior_class":  "Imperial Guardsman",
                "mage_class":     "Sanctioned Psyker",
                "rogue_class":    "Scum / Desperado",
                "cleric_class":   "Ministorum Priest",
                "dm_title":       "Game Master",
                "skill_check":    "characteristic test",
                "starting_area":  "underhive sector",
            },
        },
        {
            "id":               "shadowrun",
            "name":             "Shadowrun — Sixth World",
            "category":         "科幻 Sci-Fi · 賽博龐克 Cyberpunk",
            "description":      "Cyberpunk meets urban fantasy — megacorp CEOs are dragons, hackers surf the Matrix.",
            "tone":             "Neon-soaked corporate dystopia with street magic. Massive megacorporations rule nation-states. Elves sling fireballs in back alleys, trolls work as street muscle, and deckers jack into the Matrix to steal corp secrets. Everyone has a price.",
            "starting_location":"Redmond Barrens — the sprawl, Seattle",
            "starting_npc":     {"name": "Mr. Johnson", "affinity": 0, "state": "Professional", "goal": "Deliver the team's latest contract briefing"},
            "world_lore":       "It is 2080. Magic returned to the world in 2011, awakening dragons, elves, and orks from the human population. Meanwhile, megacorporations like Aztechnology and Saeder-Krupp eclipsed nation-states in power. Shadowrunners — deniable freelancers — operate in the grey zones between corporate law and SINless street life, taking jobs that never officially existed.",
            "term_map": {
                "hp_name":        "Physical Condition Monitor",
                "mp_name":        "Essence / Drain",
                "gold_name":      "nuyen (¥)",
                "warrior_class":  "Street Samurai",
                "mage_class":     "Mage / Shaman",
                "rogue_class":    "Decker / Face",
                "cleric_class":   "Shaman / Healer",
                "dm_title":       "Game Master",
                "skill_check":    "dice pool test",
                "starting_area":  "sprawl barrens",
            },
        },
        # -------------------------------------------------------------------
        # 現代暗黑 · Modern Dark & Cosmic Horror
        # -------------------------------------------------------------------
        {
            "id":               "world_of_darkness",
            "name":             "World of Darkness — Vampire: The Masquerade",
            "category":         "現代暗黑 Modern Dark",
            "description":      "Vampires, werewolves and mages hide among humanity in a Gothic-punk modern world.",
            "tone":             "Gothic-punk political horror. Vampires rule the nights from opulent Elysiums and ruined havens. The Masquerade hides supernatural society from mortals. Power is measured in Disciplines, Blood, and ancient political debts. Every alliance breeds betrayal.",
            "starting_location":"Elysium — neutral ground, an upscale nightclub",
            "starting_npc":     {"name": "The Prince", "affinity": -10, "state": "Imperious", "goal": "Maintain the Masquerade and political dominance"},
            "world_lore":       "The World of Darkness mirrors our own, but shadows hide vampiric clans, werewolf packs, and mage cabals. Vampire society is divided between the Camarilla (preserve the Masquerade), the Anarchs (demand freedom), and the Sabbat (embrace monstrosity). The thin-blooded newest vampires scramble for survival in a world where ancients called Elders pull every string.",
            "term_map": {
                "hp_name":        "Health Levels",
                "mp_name":        "Blood Pool / Willpower",
                "gold_name":      "dollars ($)",
                "warrior_class":  "Brujah / Gangrel",
                "mage_class":     "Tremere (blood mage)",
                "rogue_class":    "Nosferatu / Malkavian",
                "cleric_class":   "Ventrue / Toreador",
                "dm_title":       "Storyteller",
                "skill_check":    "dice pool roll",
                "starting_area":  "Elysium (vampire neutral ground)",
            },
        },
        {
            "id":               "call_of_cthulhu",
            "name":             "Call of Cthulhu — 1920s Lovecraftian",
            "category":         "宇宙恐怖 Cosmic Horror",
            "description":      "1920s investigators uncover forbidden knowledge; sanity is the real resource.",
            "tone":             "Cosmic horror and existential dread. The investigators are ordinary humans who stumble upon truths mankind was not meant to know. Ancient gods slumber beneath the earth and seas. Every revelation erodes Sanity. Victory means surviving with your mind intact — not slaying the monster.",
            "starting_location":"Arkham, Massachusetts — Miskatonic University campus",
            "starting_npc":     {"name": "Prof. Armitage", "affinity": 20, "state": "Worried", "goal": "Prevent the awakening of a Great Old One"},
            "world_lore":       "It is 1923. Beneath the veneer of Jazz Age prosperity lurk cults, forbidden tomes, and entities whose mere comprehension shatters the human mind. The Necronomicon, the King in Yellow, and the Dunwich Horror are not myths. Miskatonic University's Restricted Library holds the clues — and the dangers. Investigators trade Sanity for knowledge in a race against madness.",
            "term_map": {
                "hp_name":        "HP",
                "mp_name":        "Sanity (SAN)",
                "gold_name":      "dollars ($)",
                "warrior_class":  "Private Investigator",
                "mage_class":     "Occultist",
                "rogue_class":    "Thief / Journalist",
                "cleric_class":   "Doctor / Archaeologist",
                "dm_title":       "Keeper of Arcane Lore",
                "skill_check":    "skill roll",
                "starting_area":  "university town",
            },
        },
        # -------------------------------------------------------------------
        # 蒸氣龐克 · Steampunk & Gaslamp
        # -------------------------------------------------------------------
        {
            "id":               "iron_kingdoms",
            "name":             "Iron Kingdoms — Full Metal Fantasy",
            "category":         "蒸氣龐克 Steampunk",
            "description":      "Steam-powered warjacks and warcasters clash in a world of endless national warfare.",
            "tone":             "Industrial military fantasy. Nations field colossal steam-powered warjacks controlled by warcasters who channel arcane energy. Mercenaries, mechaniks, and alchemists thrive in a world perpetually at war between Iron Kingdoms vying for resources and dominance.",
            "starting_location":"Corvis — city of rivers, western Immoren",
            "starting_npc":     {"name": "Captain Darius Vor", "affinity": 10, "state": "Friendly", "goal": "Recruit capable mercenaries for an urgent contract"},
            "world_lore":       "Western Immoren is divided among rival nations: the Cygnaran kingdom of steam science, the Khadoran empire of iron might, the Protectorate of Menoth's zealous theocracy, and the elven Retribution of Scyrah. Warcasters bond mentally with warjacks — massive steam-and-arcane constructs — to wage war. Mechaniks keep the machines running. Alchemists brew volatile concoctions. Magic and industry are inseparable.",
            "term_map": {
                "hp_name":        "HP",
                "mp_name":        "Focus / Fury",
                "gold_name":      "gold crowns",
                "warrior_class":  "Man-at-Arms / Mercenary",
                "mage_class":     "Warcaster",
                "rogue_class":    "Gun Mage / Spy",
                "cleric_class":   "Priest of Morrow / Mechanik",
                "dm_title":       "Game Master",
                "skill_check":    "skill roll",
                "starting_area":  "river city",
            },
        },
        {
            "id":               "blades_in_the_dark",
            "name":             "Blades in the Dark — Doskvol",
            "category":         "蒸氣龐克 Gaslamp Fantasy",
            "description":      "Gang life in an eternally dark city powered by electroplasmic ghost-blood.",
            "tone":             "Gaslit criminal underworld. Doskvol is a walled city lit by crackling arc lights, surrounded by a demon-haunted darkened world. Player characters run a criminal crew — scoundrels, smugglers, cultists. Stress replaces fear; Harm replaces injury. Every job leaves scars. Faction clocks tick toward war.",
            "starting_location":"Doskvol — Crow's Foot district, a contested gang territory",
            "starting_npc":     {"name": "Roric, the Cutter", "affinity": 0, "state": "Neutral", "goal": "Maintain uneasy peace between gangs"},
            "world_lore":       "The city of Doskvol — called Duskwall by locals — clings to the Void Sea shore. Electroplasmic lightning towers ring the walls, keeping the hungry ghosts and demons of the Deathlands at bay. The city is powered by the blood of leviathans. Twelve ruling noble houses control the docks, factories, and Bluecoat police. Below them, a dozen criminal factions clash for turf, coin, and leverage.",
            "term_map": {
                "hp_name":        "Harm",
                "mp_name":        "Stress",
                "gold_name":      "coin",
                "warrior_class":  "Cutter (muscle)",
                "mage_class":     "Whisper (ghost-touched occultist)",
                "rogue_class":    "Slide (face) / Lurk (thief)",
                "cleric_class":   "Leech (alchemist/medic)",
                "dm_title":       "Game Master",
                "skill_check":    "action roll",
                "starting_area":  "gang territory district",
            },
        },
        # -------------------------------------------------------------------
        # 武俠 · Wuxia & Eastern Fantasy
        # -------------------------------------------------------------------
        {
            "id":               "hearts_of_wulin",
            "name":             "Hearts of Wulin — Jianghu",
            "category":         "武俠 Wuxia & Eastern Fantasy",
            "description":      "Wuxia drama — honour, betrayal, forbidden love, and martial arts rivalries.",
            "tone":             "Melodramatic wuxia epic. Heroes of the Jianghu navigate tangled webs of honour, sworn brotherhood, romantic entanglements, and blood feuds. Internal martial arts (neigong) fuel superhuman feats. Face and reputation matter as much as blade skill. Tragedy and catharsis drive the story.",
            "starting_location":"Luoyang — bustling martial world crossroads",
            "starting_npc":     {"name": "Elder Su Mengmei", "affinity": 10, "state": "Respected", "goal": "Preserve harmony of the Five Peaks Alliance"},
            "world_lore":       "The Jianghu — 'rivers and lakes' — is the hidden world of martial artists, wandering heroes, and secret sects that exists alongside the Imperial court. The Five Great Sects maintain an uneasy alliance. Rogue masters sell their swords. Forbidden manuals of lost arts are worth dying for. Every hero carries obligations: shifu's teaching, sworn brothers' loyalty, impossible loves. The martial world remembers everything.",
            "term_map": {
                "hp_name":        "Vitality (Chi)",
                "mp_name":        "Inner Force (Neigong)",
                "gold_name":      "silver taels (兩)",
                "warrior_class":  "External Stylist (外家功夫)",
                "mage_class":     "Inner Force Cultivator (內功)",
                "rogue_class":    "Shadow Walker / Assassin",
                "cleric_class":   "Healer / Taoist Priest",
                "dm_title":       "Game Master",
                "skill_check":    "martial test",
                "starting_area":  "jianghu crossroads town",
            },
        },
        {
            "id":               "l5r",
            "name":             "Legend of the Five Rings — Rokugan",
            "category":         "武俠 Wuxia & Eastern Fantasy",
            "description":      "Feudal samurai empire — clan politics and court intrigue are as deadly as the battlefield.",
            "tone":             "Honorable samurai epic. In Rokugan, a samurai's greatest weapon is their reputation. Seven Great Clans compete for Imperial favour through war, diplomacy, and assassination. Shugenja pray to the elemental Kami. The shadowlands corrupt the southern border with supernatural evil. Death in battle is honourable; betrayal is eternal shame.",
            "starting_location":"Otosan Uchi — the Imperial capital, outer rings",
            "starting_npc":     {"name": "Bayushi Kachiko", "affinity": -10, "state": "Calculating", "goal": "Advance the Scorpion Clan's court position"},
            "world_lore":       "Rokugan is a feudal realm modelled on samurai Japan, ruled by the divine Emperor and governed by the seven Great Clans: Lion (honour), Crane (art), Dragon (wisdom), Phoenix (magic), Scorpion (subterfuge), Unicorn (cavalry), and Crab (defence). Shugenja commune with elemental spirits. The Shadowlands beyond the Crab Wall breed demons and corruption. Court intrigue in the Imperial capital can be as deadly as any war.",
            "term_map": {
                "hp_name":        "Wounds (水/火/地/風/空)",
                "mp_name":        "Void Points",
                "gold_name":      "koku (石)",
                "warrior_class":  "Bushi (武士)",
                "mage_class":     "Shugenja (祈禱師)",
                "rogue_class":    "Shinobi / Courtier",
                "cleric_class":   "Monk (Togashi Order)",
                "dm_title":       "Game Master",
                "skill_check":    "ring/skill roll",
                "starting_area":  "Imperial capital district",
            },
        },
        # -------------------------------------------------------------------
        # 廢土 · Post-Apocalyptic & Weird West
        # -------------------------------------------------------------------
        {
            "id":               "deadlands",
            "name":             "Deadlands — Weird West",
            "category":         "廢土 Weird West",
            "description":      "1870s America gone wrong — ghost rock energy, undead outlaws, and Lovecraftian horror.",
            "tone":             "Weird Western horror comedy. The American West of 1876 has been twisted by the Reckoning. Ghost rock — a miraculous mineral — powers mad science gadgets. Hucksters bargain with demons for hexes. Indian shamans battle manitous. Harrowed undead walk again. The Weird West is wild, dangerous, and darkly funny.",
            "starting_location":"Deadwood, Dakota Territory — a booming ghost-rock town",
            "starting_npc":     {"name": "Sheriff Bullock", "affinity": 10, "state": "Wary", "goal": "Keep Deadwood from tearing itself apart"},
            "world_lore":       "The Reckoning of 1863 changed everything. The dead began rising. Ghost rock — a strange black mineral — exploded with unnatural force and became the West's most valuable resource. Mad scientists (called 'Gadgeteers') build impossible steam-and-ghost-rock contraptions. Hucksters gamble with demons for magical hexes. The Great Maze flooded California into an archipelago. Fear itself feeds the Reckoners — ancient evil spirits reshaping the world.",
            "term_map": {
                "hp_name":        "Wounds",
                "mp_name":        "Power Points",
                "gold_name":      "dollars ($) / ghost rock",
                "warrior_class":  "Gunslinger / Cowboy",
                "mage_class":     "Huckster / Shaman",
                "rogue_class":    "Outlaw / Scout",
                "cleric_class":   "Blessed / Doc",
                "dm_title":       "Marshal",
                "skill_check":    "trait roll",
                "starting_area":  "frontier boom town",
            },
        },
        {
            "id":               "mutant_year_zero",
            "name":             "Mutant: Year Zero — The Ark",
            "category":         "廢土 Post-Apocalyptic",
            "description":      "Post-apocalyptic mutants explore the Zone from their sanctuary Ark base.",
            "tone":             "Melancholy post-apocalyptic survival. The world ended. Mutants — humans warped by radiation — survive in the Ark, a fragile sanctuary. They venture into the Zone — ruins, monsters, other survivor groups — to find artefacts, food, and answers about the before-times. Every resource matters. The Ark must be improved or everyone dies.",
            "starting_location":"The Ark — a ruined shopping mall turned mutant sanctuary",
            "starting_npc":     {"name": "The Boss", "affinity": 10, "state": "Authoritative", "goal": "Keep the Ark fed and defended"},
            "world_lore":       "Year Zero: the bombs fell, the old world died, and from the ruins came the mutants. Warped by radiation and whatever experiments were done before the end, mutants possess strange powers — but also rot (Rot Points erode their health). The Zone outside the Ark is a labyrinth of collapsed buildings, hostile fauna, and pockets of the old world's deadly technology. The People of the Ark are all that remains of humanity's future.",
            "term_map": {
                "hp_name":        "Strength",
                "mp_name":        "Mutation Points",
                "gold_name":      "grub / bullets (barter)",
                "warrior_class":  "Enforcer (muscle)",
                "mage_class":     "Mutant (special powers)",
                "rogue_class":    "Stalker (Zone scout)",
                "cleric_class":   "Fixer (medic/engineer)",
                "dm_title":       "Game Master",
                "skill_check":    "push roll",
                "starting_area":  "Ark sanctuary",
            },
        },
        {
            "id":               "gloomhaven",
            "name":             "Gloomhaven — City of Lions",
            "category":         "廢土 Dark Fantasy Campaign",
            "description":      "Mercenary-driven legacy dungeon crawl in a dark, morally ambiguous world.",
            "tone":             "Grim mercenary fantasy. Gloomhaven is a city of desperate ambition on the edge of the world. Mercenaries take contracts to delve ancient ruins and clear monster-infested tunnels — not for glory, but for gold. Every choice has lasting consequences. The city's prosperity or decline reflects player decisions over dozens of linked scenarios.",
            "starting_location":"Gloomhaven — the Sleeping Lion tavern, dockside",
            "starting_npc":     {"name": "Jekserah", "affinity": -5, "state": "Calculating", "goal": "Hire mercenaries for a secretive 'warehouse job'"},
            "world_lore":       "Gloomhaven sits at the edge of the Misty Sea, a grim city of ambitious merchants, exiled criminals, and desperate mercenaries. Ancient ruins of the Valrath, Quatryl, and Aesther empires dot the surrounding wilderness, filled with monsters, treasure, and forgotten magic. The Merchant's Guild controls city politics. Mercenary guilds sell their services — and their morals — to whoever pays.",
            "term_map": {
                "hp_name":        "HP",
                "mp_name":        "Cards (hand)",
                "gold_name":      "gold",
                "warrior_class":  "Brute / Cragheart",
                "mage_class":     "Spellweaver / Mindthief",
                "rogue_class":    "Scoundrel / Nightshroud",
                "cleric_class":   "Tinkerer / Plagueherald",
                "dm_title":       "Game Master",
                "skill_check":    "ability card play",
                "starting_area":  "dockside tavern",
            },
        },
    ]

    @classmethod
    def get_db_path(cls):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        return os.path.join(cls.SAVE_DIR, cls.DB_NAME)

    @classmethod
    def get_world_setting(cls, world_id):
        """Return the world setting dict for the given id, or DnD 5e as default."""
        for ws in cls.WORLD_SETTINGS:
            if ws['id'] == world_id:
                return ws
        return cls.WORLD_SETTINGS[0]

config = GameConfig()

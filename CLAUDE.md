# CLAUDE.md — AI Assistant Guide for DND-like RPG

This file provides essential context for AI assistants (Claude, Copilot, etc.)
working on this codebase. Read this before making any changes.

---

## Project Overview

A turn-based, text-driven RPG engine powered by local AI models. The player
creates a character, then interacts with an AI Game Master that generates
narrative, choices, and mechanical outcomes each turn. Scene images are
optionally rendered via a local diffusion model.

**Tech stack:**
- Python 3 (no build step — interpreted directly)
- Streamlit — web UI
- Ollama + llama3 — local LLM for narrative generation
- ChromaDB — vector store for RAG-based game memory
- SQLAlchemy + SQLite — game state and character persistence
- Diffusers (SDXL-Turbo) — optional scene image generation
- PyTorch — underlies the diffusion pipeline

---

## Running the Application

```bash
# From the repository root
python run.py
```

`run.py` sets `PYTHONPATH` to the project root, then calls:
```bash
streamlit run ui/app.py
```

All imports use project-root-relative paths (e.g., `from engine.config import
Config`). Never use relative imports.

---

## Repository Layout

```
DND-like_RPG/
├── run.py                  # Entry point — sets PYTHONPATH and launches Streamlit
├── ai/
│   ├── llm_client.py       # Ollama wrapper — generates narrative JSON per turn
│   ├── rag_system.py       # ChromaDB RAG — stores/retrieves story context
│   ├── image_gen.py        # SDXL-Turbo pipeline — optional scene images
│   └── audio_gen.py        # Stub — placeholder for future audio generation
├── engine/
│   ├── config.py           # Central constants (model names, paths, VRAM budget)
│   ├── game_state.py       # SQLAlchemy ORM: Character + GameState tables
│   ├── character.py        # Character logic (damage, healing, MP, inventory)
│   ├── save_load.py        # New game creation and save/load via SQLite
│   └── world.py            # WorldManager — updates location and relationships
├── logic/
│   └── events.py           # EventManager — orchestrates one full game turn
├── ui/
│   └── app.py              # Streamlit frontend — menu, game loop, sidebar
├── chroma_data/            # Persistent ChromaDB vector store (git-ignored data)
├── saves/
│   └── savegame.db         # SQLite save file (git-ignored data)
└── .claude/
    └── settings.local.json # Claude Code shell permissions
```

---

## Architecture & Data Flow

### Layered design

```
ui/app.py           ← Streamlit session state, rendering
    └── logic/events.py     ← Orchestrates one full turn
            ├── ai/llm_client.py    ← LLM call (Ollama)
            ├── ai/rag_system.py    ← Context retrieval + event storage
            ├── engine/character.py ← Applies stat changes
            ├── engine/world.py     ← Updates location/relationships
            └── engine/game_state.py ← SQLAlchemy ORM / DB sessions
```

### One full turn (EventManager.process_turn)

1. `RAGSystem.retrieve_context()` — semantic search for relevant past events
2. Format system prompt with character sheet + game context
3. `LLMClient.generate_turn()` → returns JSON:
   ```json
   {
     "narrative": "...",
     "choices": ["...", "...", "..."],
     "mechanics": {"damage": 0, "healing": 0, "items_gained": [], ...}
   }
   ```
4. `CharacterLogic` applies mechanical effects (HP, MP, inventory)
5. `WorldManager` updates location and NPC relationships
6. `RAGSystem.add_story_event()` persists the event to ChromaDB
7. `ImageGenerator.generate()` optionally renders a scene image
8. Return narrative + choices + updated stats to UI

---

## Key Classes & Responsibilities

| Class | File | Purpose |
|---|---|---|
| `Config` | `engine/config.py` | Central constants — edit here for model/path changes |
| `DatabaseManager` | `engine/game_state.py` | SQLAlchemy session factory and table creation |
| `Character` | `engine/game_state.py` | ORM model: name, race, class, HP/MP/ATK/DEF/MOV |
| `GameState` | `engine/game_state.py` | ORM model: location, context, difficulty, language |
| `CharacterLogic` | `engine/character.py` | Stat mutations (damage, heal, MP, inventory) |
| `SaveLoadManager` | `engine/save_load.py` | Create new game or load existing save |
| `WorldManager` | `engine/world.py` | Location and relationship updates |
| `LLMClient` | `ai/llm_client.py` | Ollama API — structured JSON generation |
| `RAGSystem` | `ai/rag_system.py` | ChromaDB collections: world_lore, story_events |
| `ImageGenerator` | `ai/image_gen.py` | SDXL-Turbo pipeline with VRAM load/unload |
| `AudioGenerator` | `ai/audio_gen.py` | Stub — no real implementation yet |
| `EventManager` | `logic/events.py` | Coordinates all systems for one game turn |

---

## Configuration

All tunable constants live in **`engine/config.py`**. Change models, paths, and
VRAM limits there — never hard-code them elsewhere.

| Constant | Default | Purpose |
|---|---|---|
| `VRAM_GB` | `12` | Total GPU VRAM budget |
| `LLM_MODEL` | `"llama3"` | Ollama model tag |
| `IMAGE_MODEL` | `"stabilityai/sdxl-turbo"` | Diffusers model ID |
| `VRAM_STRATEGY` | `"B"` | `"A"` = skip images; `"B"` = swap models |
| `SAVE_PATH` | `"saves/savegame.db"` | SQLite save file location |
| `CHROMA_PATH` | `"chroma_data"` | ChromaDB persistence directory |

**VRAM strategies:**
- **Strategy A** — If VRAM is too low, skip image generation entirely.
- **Strategy B** — Unload the LLM from VRAM before loading the image model,
  then reload the LLM afterward. Slower but enables both on constrained GPUs.

---

## Database Schema

Two SQLite tables managed by SQLAlchemy (auto-created on first run):

### `characters`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| name | String | Player character name |
| race | String | e.g., Human, Elf, Dwarf |
| character_class | String | e.g., Warrior, Mage |
| appearance | Text | Free-text description |
| personality | Text | Free-text description |
| hp / max_hp | Integer | Current and maximum hit points |
| mp / max_mp | Integer | Current and maximum magic points |
| atk / def / mov | Integer | Attack, defense, movement stats |
| inventory | Text | JSON-serialized list of item strings |
| skills | Text | JSON-serialized list of skill strings |

### `game_state`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| character_id | Integer FK | Links to `characters.id` |
| current_location | String | Current in-game location name |
| world_context | Text | Narrative world description |
| turn_count | Integer | Number of turns played |
| difficulty | String | easy / normal / hard |
| language | String | Narrative language (e.g., "English") |
| npc_relationships | Text | JSON dict of NPC name → relationship value |

---

## ChromaDB RAG Collections

Stored in `chroma_data/` (persistent client):

| Collection | Content | Usage |
|---|---|---|
| `world_lore` | Static world background added at game creation | Retrieved to ground LLM in world rules |
| `story_events` | Dynamic events stored after every turn | Retrieved to give LLM recent story context |

Call `RAGSystem.retrieve_context(query, n_results=5)` to get the most
semantically relevant context strings before prompting the LLM.

---

## LLM Integration

`LLMClient` wraps the Ollama Python SDK. The model must be running locally:

```bash
# Ensure Ollama is running and the model is pulled
ollama pull llama3
ollama serve
```

`generate_turn()` sends a single prompt and expects a **JSON response** with
keys `narrative`, `choices` (list), and `mechanics` (dict). The prompt
enforces this schema — do not remove the JSON instruction.

---

## Image Generation

`ImageGenerator` uses the Hugging Face `diffusers` library with SDXL-Turbo.

- **Load:** `ImageGenerator.load_model()` — moves pipeline to GPU
- **Generate:** `ImageGenerator.generate(prompt)` → returns a PIL `Image`
- **Unload:** `ImageGenerator.unload_model()` — frees VRAM

When using Strategy B, `EventManager` calls unload/load around LLM and image
steps to stay within the VRAM budget. Always unload before loading the other
model.

---

## Streamlit UI Conventions

- All mutable game state lives in **`st.session_state`** (not global variables).
- Key session state keys: `game_active`, `character`, `game_state`,
  `chat_history`, `current_narrative`, `current_choices`, `current_image`.
- Never call `st.rerun()` inside a callback — set a state flag and let
  Streamlit's natural rerun handle it.
- The sidebar displays the live character sheet; the main column shows the
  narrative chat history.

---

## Coding Conventions

- **No type annotations** in the existing codebase — do not add them unless the
  file already uses them.
- **No docstrings** on methods — use inline comments only where logic is
  non-obvious.
- **No tests exist** — do not add test infrastructure unless explicitly asked.
- Class constructors do dependency injection: pass `DatabaseManager`,
  `LLMClient`, etc. as constructor arguments rather than instantiating them
  inside.
- JSON fields from the LLM (inventory, skills, npc_relationships) are stored as
  plain JSON strings in SQLite — always use `json.loads` / `json.dumps` when
  reading or writing them.
- Keep each module focused on its layer. UI code belongs in `ui/`, game rules
  in `engine/` or `logic/`, AI integrations in `ai/`.

---

## Common Tasks

### Add a new character stat
1. Add the column to `Character` in `engine/game_state.py`.
2. Add a mutation method to `CharacterLogic` in `engine/character.py`.
3. Initialize the default value in `SaveLoadManager.create_new_game()`.
4. Display it in the sidebar in `ui/app.py`.

### Add a new LLM output field
1. Update the prompt in `LLMClient.generate_turn()` to request the new field.
2. Parse it from the JSON response in `EventManager.process_turn()`.
3. Apply any mechanical effects and return the field to the UI.

### Change the local LLM model
Edit `Config.LLM_MODEL` in `engine/config.py`, then pull the new model:
```bash
ollama pull <new-model-name>
```

### Add world lore at game start
Call `RAGSystem.add_world_lore(lore_text)` inside `SaveLoadManager.create_new_game()`
after the RAGSystem is initialized.

---

## What Does Not Exist Yet

- **No tests** — no pytest, unittest, or test fixtures.
- **No requirements.txt / pyproject.toml** — dependencies are implicit.
- **No CI/CD** — no GitHub Actions or similar pipelines.
- **No audio** — `AudioGenerator` is a stub with placeholder methods only.
- **No README** — `CLAUDE.md` is the primary documentation.

When adding any of the above, follow the existing style conventions and keep
changes minimal and focused.

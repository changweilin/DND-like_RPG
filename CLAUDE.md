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
config`). Never use relative imports.

---

## Repository Layout

```
DND-like_RPG/
├── run.py                  # Entry point — sets PYTHONPATH and launches Streamlit
├── ai/
│   ├── llm_client.py       # Ollama wrapper — two-phase: parse_intent + render_narrative
│   ├── rag_system.py       # ChromaDB RAG — world_lore / story_events / game_rules
│   ├── image_gen.py        # SDXL-Turbo pipeline — optional scene images
│   └── audio_gen.py        # Stub — placeholder for future audio generation
├── engine/
│   ├── config.py           # Central constants (model names, paths, VRAM, memory window)
│   ├── game_state.py       # SQLAlchemy ORM: Character + GameState tables
│   ├── character.py        # CharacterLogic: damage, healing, MP, inventory, skill modifiers
│   ├── dice.py             # DiceRoller — authoritative random numbers (never the LLM)
│   ├── save_load.py        # New game creation and save/load via SQLite
│   └── world.py            # WorldManager — location and NPC entity tracking
├── logic/
│   └── events.py           # EventManager — 4-step neuro-symbolic turn orchestration
├── ui/
│   └── app.py              # Streamlit frontend — menu, game loop, dice banner, sidebar
├── chroma_data/            # Persistent ChromaDB vector store (git-ignored data)
├── saves/
│   └── savegame.db         # SQLite save file (git-ignored data)
└── .claude/
    └── settings.local.json # Claude Code shell permissions
```

---

## Architecture & Data Flow

### Neuro-Symbolic Design Principle

The LLM is **stateless** — it cannot reliably maintain game state across turns.
All ground truth (HP, inventory, NPC states, turn count) is stored in SQLite
and injected into every prompt as structured facts.

The LLM fills two narrow roles only:
1. **Intent Parser** — converts player natural language to structured JSON
2. **Narrative Generator** — converts rule-engine output to story prose

All dice rolling, stat mutations, and rule adjudication happen in deterministic
Python code, never inside the LLM.

### Layered design

```
ui/app.py                   ← Streamlit session state, rendering, dice banners
    └── logic/events.py     ← 4-step neuro-symbolic turn orchestrator
            ├── ai/llm_client.py    ← Phase 1: parse_intent()
            ├── engine/dice.py      ← Deterministic DiceRoller
            │                         (rule engine: dice + DC → outcome)
            ├── ai/llm_client.py    ← Phase 2: render_narrative()
            ├── ai/rag_system.py    ← Context retrieval + event storage
            ├── engine/character.py ← Applies stat changes + skill modifiers
            ├── engine/world.py     ← Updates location / NPC entity states
            └── engine/game_state.py ← SQLAlchemy ORM / DB sessions
```

### One full turn (EventManager.process_turn) — 8 steps

1. **RAG retrieval** — semantic search across `story_events`, `world_lore`,
   and `game_rules` collections
2. **Intent parsing** — `LLMClient.parse_intent()` → structured intent:
   ```json
   {
     "action_type": "skill_check",
     "requires_roll": true,
     "skill": "acrobatics",
     "dc": 15,
     "target": "lava trench",
     "summary": "Player attempts to leap over the lava trench"
   }
   ```
3. **Dice roll** (if `requires_roll`) — `DiceRoller.roll_skill_check(dc, modifier)`
   returns `{raw_roll, modifier, total, dc, outcome, notation}`
4. **Narrative rendering** — `LLMClient.render_narrative()` receives the
   dice result as hard facts and produces:
   ```json
   {
     "narrative": "...",
     "choices": ["...", "..."],
     "damage_taken": 0, "hp_healed": 0, "mp_used": 0,
     "items_found": [], "location_change": "",
     "npc_relationship_changes": {}
   }
   ```
5. **Apply mechanics** — `CharacterLogic` mutates HP, MP, inventory;
   `WorldManager` updates location and NPC states
6. **Session memory update** — append turn to sliding window, trim to
   `SESSION_MEMORY_WINDOW` (default 15), persist to SQLite
7. **RAG persistence** — `RAGSystem.add_story_event()` stores the turn
   in ChromaDB long-term memory
8. Return `(narrative, choices, turn_data, dice_result)` to UI

---

## Key Classes & Responsibilities

| Class | File | Purpose |
|---|---|---|
| `GameConfig` | `engine/config.py` | Central constants — edit here for model/path/memory changes |
| `DatabaseManager` | `engine/game_state.py` | SQLAlchemy session factory and table creation |
| `Character` | `engine/game_state.py` | ORM model: name, race, class, HP/MP/ATK/DEF/MOV |
| `GameState` | `engine/game_state.py` | ORM model: location, context, difficulty, language, session_memory |
| `DiceRoller` | `engine/dice.py` | Authoritative TRPG dice roller — the only source of randomness |
| `CharacterLogic` | `engine/character.py` | Stat mutations + skill modifier calculation |
| `SaveLoadManager` | `engine/save_load.py` | Create new game or load existing save |
| `WorldManager` | `engine/world.py` | Location updates + rich NPC entity tracking |
| `LLMClient` | `ai/llm_client.py` | Ollama wrapper: `parse_intent()` + `render_narrative()` |
| `RAGSystem` | `ai/rag_system.py` | ChromaDB: world_lore / story_events / game_rules collections |
| `ImageGenerator` | `ai/image_gen.py` | SDXL-Turbo pipeline with VRAM load/unload |
| `AudioGenerator` | `ai/audio_gen.py` | Stub — no real implementation yet |
| `EventManager` | `logic/events.py` | 4-step neuro-symbolic turn orchestrator |

---

## Configuration

All tunable constants live in **`engine/config.py`**. Change models, paths, and
VRAM limits there — never hard-code them elsewhere.

| Constant | Default | Purpose |
|---|---|---|
| `USER_VRAM_GB` | `12` | Total GPU VRAM budget |
| `LLM_MODEL_NAME` | `"llama3"` | Ollama model tag |
| `IMAGE_MODEL_NAME` | `"stabilityai/sdxl-turbo"` | Diffusers model ID |
| `VRAM_STRATEGY` | `"B"` | `"A"` = skip images; `"B"` = swap models |
| `SESSION_MEMORY_WINDOW` | `15` | Number of past turns kept in the session sliding window |
| `CONTEXT_WINDOW_SIZE` | `8192` | Target token budget — match your model's context limit |
| `SAVE_DIR` | `"saves/"` | SQLite save directory |
| `CHROMA_DB_DIR` | `"chroma_data/"` | ChromaDB persistence directory |

**Recommended models by hardware:**
- **RTX 3060 (12 GB):** `jcai/breeze-7b-instruct-v1_0-gguf` Q6_K, or
  `llama3` — Breeze-7B has an expanded Traditional Chinese tokenizer
  (2× faster Chinese TPS than base Mistral/Llama).
- **RTX 4090 (24 GB):** `qwen2.5:32b` Q4_K_M — top-tier bilingual
  reasoning; or `MS3.2-24B` (Magnum Diamond) for literary RP quality.

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
| char_class | String | e.g., Warrior, Mage |
| appearance | Text | Free-text description |
| personality | Text | Free-text description |
| hp / max_hp | Integer | Current and maximum hit points |
| mp / max_mp | Integer | Current and maximum magic points |
| atk / def_stat / mov | Integer | Attack, defense, movement stats |
| gold | Integer | Currency |
| inventory | JSON | List of item dicts |
| skills | JSON | List of skill strings |

### `game_state`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| save_name | String unique | Human-readable save identifier |
| player_id | Integer FK | Links to `characters.id` |
| current_location | String | Current in-game location name |
| world_context | Text | Narrative world description |
| turn_count | Integer | Number of turns played |
| difficulty | String | Easy / Normal / Hard |
| language | String | Narrative language (e.g., "繁體中文") |
| relationships | JSON | `{npc: {affinity, state, goal}}` — see NPC Entity Tracking |
| session_memory | JSON | Sliding window of last N turns — see Session Memory |

---

## NPC Entity Tracking

`GameState.relationships` uses an enriched format:

```json
{
  "Village Elder": {
    "affinity": 10,
    "state":    "Friendly",
    "goal":     "Protect the village"
  }
}
```

- **affinity** — signed integer, −100 (hostile) to +100 (devoted)
- **state** — short mood label (Friendly, Suspicious, Fearful, Hostile, etc.)
- **goal** — NPC's current short-term objective (free text)

`WorldManager.update_relationship(name, affinity_delta, state=None, goal=None)`
writes this dict and transparently migrates legacy flat-integer entries.
`WorldManager.get_relationship(name)` reads it back.

---

## Session Memory (Sliding Window)

`GameState.session_memory` is a JSON list of the last `SESSION_MEMORY_WINDOW`
turns, persisted in SQLite so memory survives page reloads:

```json
[
  {"turn": 3, "player_action": "I attack the goblin", "narrative": "You swing...", "outcome": "SUCCESS"},
  {"turn": 4, "player_action": "I search the room",   "narrative": "You find...", "outcome": "NO_ROLL"}
]
```

`EventManager._format_session_memory()` converts this to a text block injected
into the narrative prompt — this is *context engineering*, not just prompt
engineering.

---

## Dice Rolling

`engine/dice.py` is the **only** source of random numbers in the game.
The LLM is never asked to simulate rolls — it tends toward sycophantic,
narratively convenient results.

```python
dice = DiceRoller()

# Freeform notation
rolls, modifier, total = dice.roll('2d6+3')

# Skill check against a DC
result = dice.roll_skill_check(dc=15, modifier=2)
# result = {raw_roll, modifier, total, dc, outcome, notation}
# outcome in: critical_success | success | failure | critical_failure

# Damage
damage = dice.roll_damage('1d8+4')
```

The EventManager passes the `dice_result` dict to `render_narrative()` as
hard facts. The LLM reads the outcome and writes prose around it — never
overriding it.

---

## ChromaDB RAG Collections

Stored in `chroma_data/` (persistent client):

| Collection | Content | Usage |
|---|---|---|
| `world_lore` | Static world background added at game creation | Grounds LLM in world rules |
| `story_events` | Dynamic events stored after every turn | Gives LLM recent story context |
| `game_rules` | Spell descriptions, monster stat blocks, skill DC tables | Prevents hallucinated mechanics |

```python
rag = RAGSystem()
rag.add_game_rule("Fireball: 8d6 fire damage, 20-ft radius, DC 15 Dex save for half.", "srd_fireball")
context = rag.retrieve_context("player casts fireball", n_results=3)
```

---

## LLM Integration

`LLMClient` wraps the Ollama Python SDK. The model must be running locally:

```bash
ollama pull llama3
ollama serve
```

### Two-phase turn generation

**Phase 1 — Intent Parsing:**
```python
intent = llm.parse_intent(player_action, game_context_summary)
# intent = {action_type, requires_roll, skill, dc, target, summary}
```

**Phase 2 — Narrative Rendering:**
```python
turn_data = llm.render_narrative(system_prompt, outcome_context, rag_context)
# turn_data = {narrative, choices, damage_taken, hp_healed, mp_used,
#              items_found, location_change, npc_relationship_changes}
```

Both methods include JSON repair (`_repair_json`) and key-defaulting
(`_validated_intent`, `_validated_narrative`) so a malformed response never
crashes the game loop.

---

## Image Generation

`ImageGenerator` uses the Hugging Face `diffusers` library with SDXL-Turbo.

- **Load:** `load_model()` — moves pipeline to GPU
- **Generate:** `generate_image(prompt)` → returns a PIL `Image`
- **Unload:** `unload_model()` — frees VRAM

Strategy B unloads/reloads around LLM and image steps to stay within the VRAM
budget. Always unload before loading the other model.

---

## Streamlit UI Conventions

- All mutable game state lives in **`st.session_state`** (not global variables).
- Key session state keys: `current_session`, `game_state`, `player`,
  `event_manager`, `history`, `llm`, `rag`, `img_gen`, `save_manager`.
- Never call `st.rerun()` inside a callback — set a state flag and let
  Streamlit's natural rerun handle it.
- The sidebar shows character sheet + turn count + inventory.
- `_render_dice_result(dice_result)` displays a colour-coded banner before
  each DM response when a skill check occurred.

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
- JSON fields (inventory, skills, relationships, session_memory) are stored as
  plain JSON in SQLite — always use SQLAlchemy's JSON column type (not manual
  `json.loads`/`json.dumps`).
- Keep each module focused on its layer. UI code belongs in `ui/`, game rules
  in `engine/` or `logic/`, AI integrations in `ai/`.

---

## Common Tasks

### Add a new character stat
1. Add the column to `Character` in `engine/game_state.py`.
2. Add a mutation method to `CharacterLogic` in `engine/character.py`.
3. If the stat governs a skill check, add it to `_SKILL_STAT_MAP`.
4. Initialize the default in `SaveLoadManager.create_new_game()`.
5. Display it in the sidebar in `ui/app.py`.

### Add a new LLM output field
1. Add the key to `_validated_narrative()` defaults in `ai/llm_client.py`.
2. Update the JSON schema string in `render_narrative()`.
3. Parse and apply the field in `EventManager.process_turn()`.

### Add a new dice roll type
Use `DiceRoller` directly — it accepts any `NdM±K` notation:
```python
from engine.dice import DiceRoller
result = DiceRoller().roll('3d6')
```

### Add a new RAG collection
```python
# In ai/rag_system.py:
self.my_collection = self.client.get_or_create_collection(name="my_collection")
# Add add_X() and query it in retrieve_context()
```

### Change the local LLM model
Edit `GameConfig.LLM_MODEL_NAME` in `engine/config.py`, then pull the model:
```bash
ollama pull <new-model-name>
```

### Add world lore at game start
Call `RAGSystem.add_world_lore(lore_text, lore_id)` inside
`SaveLoadManager.create_new_game()` after the RAGSystem is initialized.

---

## What Does Not Exist Yet

- **No tests** — no pytest, unittest, or test fixtures.
- **No requirements.txt / pyproject.toml** — dependencies are implicit.
- **No CI/CD** — no GitHub Actions or similar pipelines.
- **No audio** — `AudioGenerator` is a stub with placeholder methods only.
- **No D&D 5e SRD data** — `game_rules` RAG collection exists but is empty;
  seed it with the soryy708/dnd5-srd JSON database converted to markdown.
- **No LoRA fine-tuning** — the model is used as-is; LoRA adapters for TRPG
  JSON output style and narrative tone are a future enhancement.

When adding any of the above, follow the existing style conventions and keep
changes minimal and focused.

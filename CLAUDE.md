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

### One full turn (EventManager.process_turn) — 10 steps

1. **RAG retrieval** — semantic search across `story_events`, `world_lore`,
   and `game_rules` collections
2. **World lore seeding** — on turn 0, chunk `world_context` into sentences
   and seed `world_lore` RAG for future semantic retrieval (TaskingAI style)
3. **Intent parsing** — `LLMClient.parse_intent()` → structured intent with
   `thought_process` FIRST (Guided Thinking / One Trillion and One Nights):
   ```json
   {
     "thought_process": "Player wants to leap — Acrobatics, medium difficulty",
     "action_type": "skill_check",
     "requires_roll": true,
     "skill": "acrobatics",
     "dc": 15,
     "target": "lava trench",
     "summary": "Player attempts to leap over the lava trench"
   }
   ```
4. **Dynamic entity stat block** — on first encounter with a named target,
   generate a TRPG-compliant stat block via LLM, cache in `game_rules` RAG
   and `known_entities` DB column (Infinite Monster Engine)
5. **Combat rule engine** (if `action_type == 'attack'`) — fully deterministic:
   attack roll `1d20 + ATK modifier` vs `target DEF`; on hit, roll weapon
   damage (class-based dice + ATK modifier); critical (raw 20) doubles dice;
   net damage after `DEF // 2` reduction; entity HP decremented in `known_entities`
6. **Skill check dice roll** (if `requires_roll` and not combat) —
   `DiceRoller.roll_skill_check(dc, modifier)` → `{raw_roll, modifier, total, dc, outcome, notation}`
7. **Narrative rendering** — `LLMClient.render_narrative()` receives all
   mechanical facts as structured text and produces:
   ```json
   {
     "scene_type": "combat",
     "narrative": "...",
     "choices": ["...", "..."],
     "damage_taken": 0, "hp_healed": 0, "mp_used": 0,
     "items_found": [], "location_change": "",
     "npc_relationship_changes": {}
   }
   ```
8. **Apply mechanics** — `CharacterLogic` mutates HP, MP, inventory;
   `WorldManager` updates location and NPC states
9. **NPC generative agent reactions** (social/NPC turns only) —
   `LLMClient.evaluate_npc_reactions()` lets each NPC independently update
   their goal and emotional state (Generative Agents, Park et al. 2023)
10. **Session memory update + RAG persistence** — append turn to sliding
    window, trim to `SESSION_MEMORY_WINDOW`; overflowed turns are summarized
    via LLM and stored as chapter summaries in `world_lore` RAG; current
    turn stored in `story_events` RAG; return to UI

---

## Key Classes & Responsibilities

| Class | File | Purpose |
|---|---|---|
| `GameConfig` | `engine/config.py` | Central constants — edit here for model/path/memory changes |
| `DatabaseManager` | `engine/game_state.py` | SQLAlchemy session factory and table creation |
| `Character` | `engine/game_state.py` | ORM model: name, race, class, HP/MP/ATK/DEF/MOV |
| `GameState` | `engine/game_state.py` | ORM model: location, context, difficulty, language, session_memory, known_entities |
| `DiceRoller` | `engine/dice.py` | Authoritative TRPG dice roller — the only source of randomness |
| `CharacterLogic` | `engine/character.py` | Stat mutations + skill modifiers + weapon damage notation |
| `SaveLoadManager` | `engine/save_load.py` | Create new game or load existing save |
| `WorldManager` | `engine/world.py` | Location updates + rich NPC entity tracking |
| `LLMClient` | `ai/llm_client.py` | Ollama wrapper: `parse_intent()`, `render_narrative()`, `evaluate_npc_reactions()`, `summarize_memory_segment()` |
| `RAGSystem` | `ai/rag_system.py` | ChromaDB: world_lore / story_events / game_rules collections |
| `ImageGenerator` | `ai/image_gen.py` | SDXL-Turbo pipeline with VRAM load/unload |
| `AudioGenerator` | `ai/audio_gen.py` | Stub — no real implementation yet |
| `EventManager` | `logic/events.py` | 10-step neuro-symbolic turn orchestrator |

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
| known_entities | JSON | `{name_lower: {type, hp, max_hp, atk, def_stat, alive, …}}` — live combat HP tracking |

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
  {
    "turn": 3,
    "player_action": "I attack the goblin",
    "narrative": "You swing...",
    "outcome": "SUCCESS",
    "location": "Dark Forest",
    "scene_type": "combat",
    "characters_present":      ["npc:goblin shaman", "npc:village elder"],
    "organizations_mentioned": ["org:iron vanguard"],
    "offered_choices":    ["Pursue", "Loot", "Retreat"],
    "unchosen_choices":   ["Retreat"]
  }
]
```

**Entity key format** (indexed references, not raw name strings):
- `characters_present` — `"npc:{name.lower()}"` for NPCs; max **6** entries.
  Party members are always implicitly present and excluded from this list.
- `organizations_mentioned` — `"org:{name.lower()}"` for organizations
  mentioned in the turn's narrative; max **3** entries.

`EventManager._format_session_memory()` resolves these keys back to display
names via `relationships` and `organizations` dicts before injecting into the
prompt — this is *context engineering*, not just prompt engineering.

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
# intent = {thought_process, action_type, requires_roll, skill, dc, target, summary}
```

**Phase 2 — Narrative Rendering:**
```python
turn_data = llm.render_narrative(system_prompt, outcome_context, rag_context)
# turn_data = {scene_type, narrative, choices, damage_taken, hp_healed, mp_used,
#              items_found, location_change, npc_relationship_changes}
```

**NPC Generative Agent Reactions (social turns):**
```python
reactions = llm.evaluate_npc_reactions(event_summary, npc_states, language)
# reactions = {npc_name: {affinity_delta, state, goal}}  — only changed NPCs
```

**Memory Summarization (overflow turns):**
```python
summary = llm.summarize_memory_segment(turns, language)
# summary = plain-text paragraph — stored in world_lore RAG as chapter summary
```

All methods include JSON repair (`_repair_json`) and key-defaulting
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

### Seed D&D 5e SRD rules into game_rules RAG (Section 6.1)
```bash
# 1. Download the SRD JSON files (soryy708/dnd5-srd on GitHub)
git clone https://github.com/soryy708/dnd5-srd /tmp/dnd5-srd
cp /tmp/dnd5-srd/src/5e-SRD-*.json data/srd/

# 2. Run the seeder (idempotent — safe to re-run)
python tools/seed_srd.py

# 3. Optionally limit to specific categories
python tools/seed_srd.py --categories monsters spells
```

`tools/seed_srd.py` calls `RAGSystem.seed_from_srd_json()`, which converts
each SRD entry (monster stat blocks, spell descriptions, equipment details)
to a text chunk and upserts it into the `game_rules` ChromaDB collection.

### Generate LoRA fine-tuning data (Section 5.2 + 6.2)
```bash
# Generate 200 scenarios with default model (Alpaca format):
python tools/gen_lora_data.py --samples 200

# Use a stronger model on a 4090 for higher-quality training data:
python tools/gen_lora_data.py --model qwen2.5:32b --samples 1000 --format chatml

# Output: data/lora_training/trpg_train.jsonl  (2 records per scenario)
```

Each generated scenario pairs a randomised character + action with:
1. A labelled `parse_intent` training record (Guided Thinking JSON output)
2. A labelled `render_narrative` training record (scene-typed Narrative Event)

Fine-tune with Unsloth, LLaMA-Factory, or Axolotl on the output JSONL to
teach a smaller local model the engine's exact JSON schema and DM voice.

---

## Combat Rule Engine (Section 3.3)

All combat is deterministic — the LLM only narrates; never adjudicates.

```
Attack roll: 1d20 + (ATK - 10) // 2  vs  target.def_stat
  Critical (raw 20): doubles dice component of damage
  Hit: roll weapon damage notation (class-based, see _CLASS_DAMAGE_MAP)
  Net damage: raw_damage - (target.def_stat // 2), minimum 0
Entity HP is tracked in known_entities[name_lower]['hp']
  On defeat: known_entities[name_lower]['alive'] = False
```

`CharacterLogic.get_weapon_damage_notation()` returns the full notation
string including ATK modifier, e.g. `"1d8+2"` for a Warrior with ATK 14.

`CharacterLogic._CLASS_DAMAGE_MAP`:
| Class | Dice |
|---|---|
| warrior | 1d8 |
| mage | 1d4 |
| rogue | 1d6 |
| cleric | 1d6 |

---

## NPC Generative Agent Behavior (Section 3.5)

After social scenes or turns with NPC relationship changes,
`EventManager._evaluate_npc_reactions()` calls
`LLMClient.evaluate_npc_reactions()`.

Each NPC is treated as an autonomous agent with a persistent goal and
emotional state (inspired by Park et al. 2023 "Generative Agents").
Only NPCs whose state changes are returned — unchanged NPCs are omitted.

The returned deltas are applied via `WorldManager.update_relationship()`.

---

## Memory Summarization (Section 3.1)

When `session_memory` overflows (exceeds `SESSION_MEMORY_WINDOW`), the
discarded turns are summarized via `LLMClient.summarize_memory_segment()`
and stored as a chapter summary in the `world_lore` RAG collection:

```
[Chapter Summary — turns 1–15] Earlier in the adventure, ...
```

This ensures long-term story continuity even after the sliding window moves
past early events.

---

## RAG + LoRA Hybrid Strategy (Section 5.2)

These two techniques solve different problems and are complementary:

| Technique | Purpose | TRPG application |
|---|---|---|
| **RAG** | External, dynamic, exact knowledge retrieval | D&D 5e SRD rules (monsters, spells), player history, item details |
| **LoRA** | Teach output format, DM tone, domain reasoning | Strict JSON schema, fantasy DM voice, TRPG action classification |

**Recommended workflow:**
1. Seed `game_rules` RAG with the D&D 5e SRD JSON (`tools/seed_srd.py`)
2. Generate synthetic training data (`tools/gen_lora_data.py`)
3. Fine-tune with LoRA (Unsloth / LLaMA-Factory / Axolotl)
4. Deploy the LoRA-adapted model via Ollama; RAG provides the dynamic content

**Dataset sources for LoRA fine-tuning (Section 6.2):**
- `hieunguyenminh/roleplay` — roleplay dialogue, multiple character archetypes
- `LimaRP` / `PIPPA` — long-form multi-turn RP with emotion and action descriptions
- `Smoltalk-chinese` (OpenCSG) — Chinese instruction-following for bilingual DMs
- Synthetic data from `tools/gen_lora_data.py` — generated using this engine's own schema

**Embedding model fine-tuning (Section 6.1):**
The Datapizza AI Lab RAG Evaluation Dataset (D&D 5e SRD QA pairs, JSON/Parquet)
can be used to fine-tune a BGE-family embedding model for better D&D terminology
retrieval. Point `GameConfig.EMBEDDING_MODEL` to the fine-tuned model directory.

---

## Repository Layout (updated)

```
DND-like_RPG/
├── tools/
│   ├── seed_srd.py         # D&D 5e SRD JSON → game_rules RAG (Section 6.1)
│   └── gen_lora_data.py    # Synthetic LoRA training data generator (Section 6.2)
├── data/
│   ├── srd/                # Place 5e-SRD-*.json files here for seed_srd.py
│   └── lora_training/      # gen_lora_data.py writes JSONL here
```

---

## What Does Not Exist Yet

- **No tests** — no pytest, unittest, or test fixtures.
- **No requirements.txt / pyproject.toml** — dependencies are implicit.
- **No CI/CD** — no GitHub Actions or similar pipelines.
- **No audio** — `AudioGenerator` is a stub with placeholder methods only.
- **No D&D 5e SRD data** — download JSON from soryy708/dnd5-srd and run
  `python tools/seed_srd.py` to populate the `game_rules` RAG collection.
- **No LoRA adapters** — run `tools/gen_lora_data.py` to generate training
  data, then fine-tune with Unsloth/LLaMA-Factory on the output JSONL.
- **No custom embedding model** — default ChromaDB embedding (MiniLM-L6-v2)
  is used. Set `GameConfig.EMBEDDING_MODEL` after fine-tuning a BGE model on
  the Datapizza D&D SRD QA dataset for better rule retrieval accuracy.

When adding any of the above, follow the existing style conventions and keep
changes minimal and focused.

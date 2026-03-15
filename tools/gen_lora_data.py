#!/usr/bin/env python3
"""
tools/gen_lora_data.py — Synthetic LoRA training data generator
================================================================

Section 5.2 + 6.2 recommendation: generate a LoRA fine-tuning dataset
using a high-capability model so that a smaller local model can be
taught the specific JSON output format, DM speaking style, and TRPG
reasoning patterns of this engine.

Design rationale (PDF Section 5.2):
  - RAG tells the model WHAT to think about (external, dynamic knowledge).
  - LoRA teaches the model HOW to think (format, tone, domain reasoning).
  - These two techniques are complementary; both should be used.

Section 6.2 recommends using EDG4LLM or any high-capability model
(GPT-4o, GLM-4-Flash, or locally a strong 32B model) to generate
thousands of structured dialogue samples that match the engine's
Pydantic-like JSON schema.

Output formats
--------------
  --format alpaca   → Alpaca instruction format (default)
                       {"instruction": ..., "input": ..., "output": ...}
  --format chatml   → ChatML multi-turn format
                       {"messages": [{"role": ..., "content": ...}, ...]}

Usage
-----
    # Generate 200 samples using the configured Ollama model, save as JSONL:
    python tools/gen_lora_data.py --samples 200 --output data/lora_training/trpg_train.jsonl

    # Generate in ChatML format for models like Qwen / Mistral:
    python tools/gen_lora_data.py --samples 500 --format chatml

    # Use a specific, more capable model (recommended: qwen2.5:32b on 4090):
    python tools/gen_lora_data.py --model qwen2.5:32b --samples 1000

After generation, fine-tune your target model with a LoRA trainer
(e.g. Unsloth, LLaMA-Factory, or Axolotl) on the generated JSONL.
"""

import sys
import os
import json
import random
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import config

# Try importing ollama; show a clear error if not installed
try:
    import ollama
except ImportError:
    print("[ERROR] ollama Python package not found. Install with: pip install ollama")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Scenario seed bank — varied enough to produce diverse training samples
# ---------------------------------------------------------------------------

_RACES    = ["Human", "Elf", "Dwarf", "Half-Orc", "Gnome", "Tiefling", "Dragonborn"]
_CLASSES  = ["Warrior", "Mage", "Rogue", "Cleric"]
_LOCS     = [
    "a goblin cave", "a haunted forest", "a crumbling wizard's tower",
    "a merchant's quarter", "an ancient temple", "a misty swamp",
    "a dragon's lair", "a city tavern", "an abandoned fortress",
    "a sea cliff overlooking a pirate cove",
]
_ACTIONS  = [
    "I try to sneak past the sleeping guard.",
    "I cast a fireball at the enemies.",
    "I persuade the merchant to lower his price.",
    "I attack the goblin chieftain with my sword.",
    "I search the room for hidden traps.",
    "I attempt to climb the crumbling tower wall.",
    "I try to intimidate the bandits into surrendering.",
    "I identify the strange magical rune on the door.",
    "I try to pick the lock on the iron chest.",
    "I leap across the lava trench.",
    "I treat the wounded soldier's injuries.",
    "I dive into the river to retrieve the sunken artifact.",
    "I listen at the door for sounds of movement.",
    "I negotiate with the dragon for safe passage.",
    "I attempt to break free from the chains.",
]
_DIFFICULTIES = ["Easy", "Normal", "Hard"]
_LANGUAGES    = ["English", "繁體中文"]


def _make_scenario():
    """Return a dict of randomised scenario fields for one training sample."""
    return {
        "race":       random.choice(_RACES),
        "char_class": random.choice(_CLASSES),
        "hp":         random.randint(8, 40),
        "max_hp":     40,
        "mp":         random.randint(0, 20),
        "max_mp":     20,
        "atk":        random.randint(8, 16),
        "def_stat":   random.randint(8, 14),
        "location":   random.choice(_LOCS),
        "action":     random.choice(_ACTIONS),
        "difficulty": random.choice(_DIFFICULTIES),
        "language":   random.choice(_LANGUAGES),
    }


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_INTENT_SYSTEM = """\
You are a TRPG intent parser. Given a player action in natural language, output a JSON object.
Think through the action BEFORE classifying it (put your reasoning in "thought_process" first).

JSON schema (output ONLY valid JSON, no markdown fences):
{
  "thought_process": "<brief chain-of-thought reasoning about the action>",
  "action_type": "<attack|skill_check|social|exploration|magic|direct_action>",
  "requires_roll": <true|false>,
  "skill": "<acrobatics|athletics|arcana|perception|stealth|persuasion|medicine|intimidation|>",
  "dc": <integer 5-30 or 0 if no roll>,
  "target": "<target of the action or empty string>",
  "summary": "<one-sentence English summary of what the player intends>"
}"""

_NARRATIVE_SYSTEM = """\
You are a creative Dungeon Master for a text RPG. Given a structured outcome, write a vivid
narrative response. Do NOT invent dice rolls or stat changes — those are hard facts given to you.

JSON schema (output ONLY valid JSON, no markdown fences):
{
  "scene_type": "<combat|social|exploration|puzzle|rest>",
  "narrative": "<2-4 sentences of vivid DM prose>",
  "choices": ["<choice 1>", "<choice 2>", "<choice 3>"],
  "damage_taken": <integer>,
  "hp_healed": <integer>,
  "mp_used": <integer>,
  "items_found": [],
  "location_change": "<new location name or empty string>",
  "npc_relationship_changes": {}
}"""


def _build_intent_user(sc):
    return (
        f"Character: {sc['race']} {sc['char_class']}. "
        f"HP: {sc['hp']}/{sc['max_hp']}, MP: {sc['mp']}/{sc['max_mp']}. "
        f"Location: {sc['location']}. "
        f"Difficulty: {sc['difficulty']}.\n\n"
        f"Player action: {sc['action']}"
    )


def _build_narrative_user(sc, intent):
    # Simulate a dice roll outcome for training variety
    raw_roll = random.randint(1, 20)
    mod      = (sc['atk'] - 10) // 2
    dc       = intent.get('dc', 0) or 0
    total    = raw_roll + mod
    outcome  = (
        "CRITICAL SUCCESS" if raw_roll == 20 else
        "CRITICAL FAILURE" if raw_roll == 1  else
        "SUCCESS"          if total >= dc    else
        "FAILURE"
    )
    skill    = intent.get('skill', 'general')
    roll_str = (
        f"Skill checked: {skill} vs DC {dc}. "
        f"Roll: 1d20={raw_roll} + {mod} = {total} → {outcome}."
        if intent.get('requires_roll') and dc > 0 else "No dice roll required."
    )
    return (
        f"Player: {sc['action']}\n"
        f"Intent analysis: {intent.get('thought_process', '')}\n"
        f"{roll_str}\n"
        f"Write the narrative in {sc['language']}."
    )


# ---------------------------------------------------------------------------
# Generation loop
# ---------------------------------------------------------------------------

def _call_llm(model, system, user, repair_attempts=2):
    """Call Ollama and attempt basic JSON repair if the response is malformed."""
    for attempt in range(repair_attempts + 1):
        try:
            resp = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                format='json',
            )
            raw = resp.message.content.strip()
            # Strip markdown fences if present
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as e:
            if attempt == repair_attempts:
                raise
            time.sleep(1)


def _to_alpaca(sc, intent_user, intent_output, narr_user, narr_output):
    """Return two Alpaca-format dicts: one for intent parsing, one for narrative."""
    return [
        {
            "instruction": _INTENT_SYSTEM,
            "input":        intent_user,
            "output":       json.dumps(intent_output, ensure_ascii=False),
        },
        {
            "instruction": _NARRATIVE_SYSTEM,
            "input":        narr_user,
            "output":       json.dumps(narr_output, ensure_ascii=False),
        },
    ]


def _to_chatml(sc, intent_user, intent_output, narr_user, narr_output):
    """Return two ChatML-format dicts."""
    return [
        {
            "messages": [
                {"role": "system",    "content": _INTENT_SYSTEM},
                {"role": "user",      "content": intent_user},
                {"role": "assistant", "content": json.dumps(intent_output, ensure_ascii=False)},
            ]
        },
        {
            "messages": [
                {"role": "system",    "content": _NARRATIVE_SYSTEM},
                {"role": "user",      "content": narr_user},
                {"role": "assistant", "content": json.dumps(narr_output, ensure_ascii=False)},
            ]
        },
    ]


def generate(n_samples, model, output_path, fmt):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    written   = 0
    failures  = 0

    print(f"Generating {n_samples} samples using model '{model}' → {output_path}")
    print(f"Format: {fmt}\n")

    with open(output_path, 'w', encoding='utf-8') as fout:
        for i in range(n_samples):
            sc = _make_scenario()
            intent_user = _build_intent_user(sc)
            try:
                intent = _call_llm(model, _INTENT_SYSTEM, intent_user)
            except Exception as e:
                print(f"  [{i+1}/{n_samples}] intent FAILED: {e}")
                failures += 1
                continue

            narr_user = _build_narrative_user(sc, intent)
            try:
                narr = _call_llm(model, _NARRATIVE_SYSTEM, narr_user)
            except Exception as e:
                print(f"  [{i+1}/{n_samples}] narrative FAILED: {e}")
                failures += 1
                continue

            if fmt == 'chatml':
                records = _to_chatml(sc, intent_user, intent, narr_user, narr)
            else:
                records = _to_alpaca(sc, intent_user, intent, narr_user, narr)

            for record in records:
                fout.write(json.dumps(record, ensure_ascii=False) + '\n')
            written += 1

            if (i + 1) % 10 == 0 or (i + 1) == n_samples:
                print(f"  [{i+1}/{n_samples}] {written} scenarios written, {failures} failed")

    total_lines = written * 2  # 2 records per scenario (intent + narrative)
    print(f"\nDone. {total_lines} training records written to {output_path}")
    if failures:
        print(f"  ({failures} scenarios skipped due to LLM errors)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic LoRA training data for the TRPG engine"
    )
    parser.add_argument(
        '--samples', type=int, default=200,
        help="Number of unique TRPG scenarios to generate (each yields 2 training records)",
    )
    parser.add_argument(
        '--model', default=config.LLM_MODEL_NAME,
        help=f"Ollama model to use for generation (default: {config.LLM_MODEL_NAME}). "
             "Recommended: qwen2.5:32b on 4090 for highest quality.",
    )
    parser.add_argument(
        '--output',
        default=os.path.join(config.LORA_DATA_DIR, 'trpg_train.jsonl'),
        help="Output JSONL path (default: data/lora_training/trpg_train.jsonl)",
    )
    parser.add_argument(
        '--format', dest='fmt', choices=['alpaca', 'chatml'], default='alpaca',
        help="Training data format: 'alpaca' (default) or 'chatml'",
    )
    args = parser.parse_args()
    generate(args.samples, args.model, args.output, args.fmt)


if __name__ == '__main__':
    main()

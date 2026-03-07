#!/usr/bin/env python3
"""
tools/seed_srd.py — D&D 5e SRD JSON → game_rules RAG seeder
=============================================================

Section 6.1 recommendation: convert the D&D 5e SRD JSON database
(github.com/soryy708/dnd5-srd) to retrievable text chunks and import
them into the ChromaDB game_rules collection so the narrative engine
retrieves exact rules instead of hallucinating them.

Usage
-----
1. Clone the SRD repository or download individual JSON files:
       git clone https://github.com/soryy708/dnd5-srd
       cp -r dnd5-srd/src/5e-SRD-*.json data/srd/

2. Run this script from the repository root:
       python tools/seed_srd.py

3. Optionally filter categories:
       python tools/seed_srd.py --categories monsters spells

The script is idempotent — already-seeded entries are skipped silently.

Supported SRD categories
------------------------
monsters, spells, equipment, magic-items, classes, races, conditions,
damage-types, features, traits, skills, ability-scores, alignments
"""

import sys
import os
import json
import argparse

# Allow running from the repository root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import config
from ai.rag_system import RAGSystem

# Maps the SRD JSON filename fragment → RAG category label.
# Only these categories produce meaningful, RAG-retrievable text for TRPG play.
_SRD_CATEGORIES = {
    "Monsters":          "monsters",
    "Spells":            "spells",
    "Equipment":         "equipment",
    "Magic-Items":       "magic-items",
    "Classes":           "classes",
    "Races":             "races",
    "Conditions":        "conditions",
    "Damage-Types":      "damage-types",
    "Features":          "features",
    "Traits":            "traits",
    "Skills":            "skills",
    "Ability-Scores":    "ability-scores",
}


def _find_srd_files(srd_dir):
    """Yield (category_label, filepath) pairs for every SRD JSON found in srd_dir."""
    if not os.path.isdir(srd_dir):
        print(f"[ERROR] SRD data directory not found: {srd_dir}")
        print("Place soryy708/dnd5-srd JSON files in that directory and retry.")
        return

    for filename in sorted(os.listdir(srd_dir)):
        if not filename.endswith('.json'):
            continue
        # Filename pattern from soryy708/dnd5-srd: "5e-SRD-Monsters.json"
        stem = filename.replace('5e-SRD-', '').replace('.json', '')
        category = _SRD_CATEGORIES.get(stem)
        if category is None:
            print(f"  [SKIP] {filename} (no matching category handler)")
            continue
        yield category, os.path.join(srd_dir, filename)


def seed(srd_dir=None, categories=None):
    if srd_dir is None:
        srd_dir = config.SRD_DATA_DIR

    rag = RAGSystem()
    total_seeded = 0
    total_skipped = 0

    for category, filepath in _find_srd_files(srd_dir):
        if categories and category not in categories:
            continue

        if rag.srd_category_seeded(category):
            print(f"  [SKIP] {category} — already seeded")
            continue

        print(f"  [SEED] {category} from {os.path.basename(filepath)} …", end=' ', flush=True)
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"FAILED to load ({e})")
            continue

        # The SRD JSON root is either a list or a dict with a 'results' key
        entries = data if isinstance(data, list) else data.get('results', [])
        seeded, skipped = rag.seed_from_srd_json(entries, category=category)
        total_seeded  += seeded
        total_skipped += skipped
        print(f"{seeded} entries seeded, {skipped} skipped")

    print(f"\nDone. Total: {total_seeded} seeded, {total_skipped} skipped.")
    return total_seeded


def main():
    parser = argparse.ArgumentParser(description="Seed D&D 5e SRD data into ChromaDB game_rules RAG")
    parser.add_argument(
        '--srd-dir',
        default=config.SRD_DATA_DIR,
        help=f"Directory containing 5e-SRD-*.json files (default: {config.SRD_DATA_DIR})",
    )
    parser.add_argument(
        '--categories',
        nargs='*',
        choices=list(_SRD_CATEGORIES.values()),
        help="Limit to specific categories (default: all found)",
    )
    args = parser.parse_args()
    seed(srd_dir=args.srd_dir, categories=args.categories)


if __name__ == '__main__':
    main()

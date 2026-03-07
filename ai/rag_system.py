import os
import chromadb
from engine.config import config

# Basic TRPG rules seeded into game_rules on first startup.
# Inspired by TaskingAI's approach: break the rulebook into retrievable chunks
# so the LLM can look up the exact rule instead of hallucinating it.
_BASIC_RULES = [
    ("Skill Check DCs — Trivial: DC 5 | Easy: DC 10 | Medium: DC 15 | Hard: DC 20 | Very Hard: DC 25 | Nearly Impossible: DC 30.",
     "dc_table"),
    ("Acrobatics (MOV stat): Jumping, balancing, tumbling, dodging, and navigating obstacles gracefully.",
     "skill_acrobatics"),
    ("Athletics (ATK stat): Climbing, swimming, grappling, lifting, throwing, and feats of raw strength.",
     "skill_athletics"),
    ("Arcana (MP stat): Identifying magical phenomena, recalling arcane lore, understanding spells and rituals.",
     "skill_arcana"),
    ("Perception (DEF stat): Noticing hidden objects, detecting ambushes, spotting subtle clues.",
     "skill_perception"),
    ("Stealth (MOV stat): Moving silently, hiding in shadows, avoiding detection by guards or monsters.",
     "skill_stealth"),
    ("Persuasion (DEF stat): Convincing NPCs, negotiating prices, making favourable social impressions.",
     "skill_persuasion"),
    ("Medicine (DEF stat): Stabilising wounds, diagnosing illness, identifying poisons, treating injuries.",
     "skill_medicine"),
    ("Intimidation (ATK stat): Threatening NPCs into compliance, breaking enemy morale, forcing confessions.",
     "skill_intimidation"),
    ("Critical Success (natural 20): Exceptional outcome — maximum possible benefit; may grant an extra bonus effect.",
     "crit_success"),
    ("Critical Failure (natural 1): Catastrophic outcome — worst possible result; may trigger a negative side effect.",
     "crit_failure"),
    ("Basic Combat: Attacker rolls 1d20 + ATK modifier vs defender DEF. On hit, roll weapon damage. Miss = no damage.",
     "combat_basic"),
    ("Damage reduction: Incoming damage is reduced by DEF // 2 (rounded down). Minimum damage taken is 0.",
     "damage_reduction"),
    ("Rest rules: Short rest (1 hour) recovers 1d6 + DEF HP. Long rest (8 hours) restores full HP and MP.",
     "rest_rules"),
    ("Magic: Spells cost MP equal to their power level. Casting a spell when MP = 0 automatically fails.",
     "magic_rules"),
    ("Exploration: Searching a room, picking a lock, or reading ancient text is often a Perception/Arcana DC 10–15 check.",
     "exploration_rules"),
    ("Social encounters: Persuasion DC depends on NPC's current affinity. Friendly NPC: DC 10. Suspicious: DC 15. Hostile: DC 20+.",
     "social_rules"),
]

class RAGSystem:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = config.CHROMA_DB_DIR
            os.makedirs(db_path, exist_ok=True)

        self.client = chromadb.PersistentClient(path=db_path)

        # Long-term semantic memory — three collections, each with distinct labels.
        self.lore_collection  = self.client.get_or_create_collection(name="world_lore")
        self.story_collection = self.client.get_or_create_collection(name="story_events")
        # Game rules: skill DCs, combat, spells, entity stat blocks, etc.
        # Seed with D&D 5e SRD JSON converted to markdown for best retrieval.
        self.rules_collection = self.client.get_or_create_collection(name="game_rules")

        # Seed basic rules on first run (TaskingAI-inspired: retrieve exact rule at use time)
        self._seed_basic_rules_if_empty()

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def add_story_event(self, event_text, event_id, metadata=None):
        """Persist a completed turn's player action + narrative to long-term memory."""
        self.story_collection.add(
            documents=[event_text],
            ids=[str(event_id)],
            metadatas=[metadata] if metadata else [{"type": "event"}],
        )

    def add_world_lore(self, lore_text, lore_id, metadata=None):
        """Add static world background (geography, factions, history) to RAG."""
        self.lore_collection.add(
            documents=[lore_text],
            ids=[str(lore_id)],
            metadatas=[metadata] if metadata else [{"type": "lore"}],
        )

    def add_game_rule(self, rule_text, rule_id, metadata=None):
        """
        Store a game rule for retrieval during play.
        Retrieving the exact rule at the point of use prevents hallucinated mechanics.
        """
        self.rules_collection.add(
            documents=[rule_text],
            ids=[str(rule_id)],
            metadatas=[metadata] if metadata else [{"type": "rule"}],
        )

    def add_entity_stat_block(self, entity_name, stat_block_text):
        """
        Store a dynamically generated entity stat block in the game_rules collection.
        The entity_id is derived from the name so lookups are deterministic.
        """
        entity_id = _entity_id(entity_name)
        try:
            self.rules_collection.add(
                documents=[stat_block_text],
                ids=[entity_id],
                metadatas=[{"type": "entity_stat_block", "entity": entity_name}],
            )
        except Exception as e:
            # Duplicate ID = stat block already exists; safe to ignore
            print(f"Stat block for {entity_name!r} already stored ({e})")

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def entity_stat_block_exists(self, entity_name):
        """Return True if a stat block for this entity is already in game_rules."""
        try:
            result = self.rules_collection.get(ids=[_entity_id(entity_name)])
            return len(result['ids']) > 0
        except Exception:
            return False

    def world_lore_seeded(self):
        """Return True if the world_lore collection already has at least one entry."""
        return self.lore_collection.count() > 0

    def retrieve_context(self, current_prompt, n_results=3):
        """
        Semantic search across all three collections.
        Returns a single labelled string block for injection into the LLM prompt.
        """
        context = []

        story_count = self.story_collection.count()
        if story_count > 0:
            res = self.story_collection.query(
                query_texts=[current_prompt],
                n_results=min(n_results, story_count),
            )
            if res and res['documents'] and res['documents'][0]:
                context.extend(["[Past Event] " + doc for doc in res['documents'][0]])

        lore_count = self.lore_collection.count()
        if lore_count > 0:
            res = self.lore_collection.query(
                query_texts=[current_prompt],
                n_results=min(n_results, lore_count),
            )
            if res and res['documents'] and res['documents'][0]:
                context.extend(["[World Lore] " + doc for doc in res['documents'][0]])

        rules_count = self.rules_collection.count()
        if rules_count > 0:
            res = self.rules_collection.query(
                query_texts=[current_prompt],
                n_results=min(2, rules_count),
            )
            if res and res['documents'] and res['documents'][0]:
                context.extend(["[Game Rule] " + doc for doc in res['documents'][0]])

        return "\n".join(context)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _seed_basic_rules_if_empty(self):
        """
        Populate game_rules with core TRPG skill/combat rules on first startup.
        Idempotent — skips if rules already exist.

        Mirrors the TaskingAI D&D Game Master approach: chunking the rulebook
        into retrievable pieces so the LLM never has to guess at DCs or
        mechanics — it reads the exact rule from its own memory.
        """
        if self.rules_collection.count() > 0:
            return
        for rule_text, rule_id in _BASIC_RULES:
            self.rules_collection.add(
                documents=[rule_text],
                ids=[rule_id],
                metadatas=[{"type": "rule", "source": "basic_rules"}],
            )

def _entity_id(entity_name):
    """Canonical ChromaDB ID for an entity stat block entry."""
    return "entity_" + entity_name.lower().strip().replace(' ', '_')

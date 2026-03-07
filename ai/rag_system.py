import os
import chromadb
from engine.config import config

class RAGSystem:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = config.CHROMA_DB_DIR
            os.makedirs(db_path, exist_ok=True)

        self.client = chromadb.PersistentClient(path=db_path)

        # Long-term semantic memory — three separate collections so retrieval
        # can be weighted and labelled distinctly.
        self.lore_collection  = self.client.get_or_create_collection(name="world_lore")
        self.story_collection = self.client.get_or_create_collection(name="story_events")
        # Game rules: spells, monster stat blocks, skill DC tables, equipment data, etc.
        # Seed with D&D 5e SRD JSON converted to markdown for best retrieval.
        self.rules_collection = self.client.get_or_create_collection(name="game_rules")

    def add_story_event(self, event_text, event_id, metadata=None):
        """Persist a completed turn's player action + narrative to long-term memory."""
        self.story_collection.add(
            documents=[event_text],
            ids=[str(event_id)],
            metadatas=[metadata] if metadata else [{"type": "event"}],
        )

    def add_world_lore(self, lore_text, lore_id, metadata=None):
        """Add static world background (geography, factions, history) at game creation."""
        self.lore_collection.add(
            documents=[lore_text],
            ids=[str(lore_id)],
            metadatas=[metadata] if metadata else [{"type": "lore"}],
        )

    def add_game_rule(self, rule_text, rule_id, metadata=None):
        """
        Store a game rule for retrieval during play.

        Examples: spell descriptions, monster stat blocks, skill DC tables,
        equipment properties from the D&D 5e SRD JSON database.
        Retrieving the exact rule at the point of use avoids hallucinated mechanics.
        """
        self.rules_collection.add(
            documents=[rule_text],
            ids=[str(rule_id)],
            metadatas=[metadata] if metadata else [{"type": "rule"}],
        )

    def retrieve_context(self, current_prompt, n_results=3):
        """
        Semantic search across all three collections.
        Returns a single string block labelled by source type.
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

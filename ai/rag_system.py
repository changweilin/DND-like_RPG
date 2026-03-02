import os
import chromadb
from engine.config import config

class RAGSystem:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = config.CHROMA_DB_DIR
            os.makedirs(db_path, exist_ok=True)
            
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Collections for different types of memory
        self.lore_collection = self.client.get_or_create_collection(name="world_lore")
        self.story_collection = self.client.get_or_create_collection(name="story_events")
        
    def add_story_event(self, event_text, event_id, metadata=None):
        """Adds a played story event to the vector db."""
        self.story_collection.add(
            documents=[event_text],
            ids=[str(event_id)],
            metadatas=[metadata] if metadata else [{"type": "event"}]
        )
        
    def add_world_lore(self, lore_text, lore_id, metadata=None):
        """Adds general world/character lore to the vector db."""
        self.lore_collection.add(
            documents=[lore_text],
            ids=[str(lore_id)],
            metadatas=[metadata] if metadata else [{"type": "lore"}]
        )

    def retrieve_context(self, current_prompt, n_results=3):
        """Retrieves relevant past events and lore for the current prompt."""
        context = []

        story_count = self.story_collection.count()
        if story_count > 0:
            story_results = self.story_collection.query(
                query_texts=[current_prompt],
                n_results=min(n_results, story_count)
            )
            if story_results and story_results['documents'] and story_results['documents'][0]:
                context.extend(["Past Event: " + doc for doc in story_results['documents'][0]])

        lore_count = self.lore_collection.count()
        if lore_count > 0:
            lore_results = self.lore_collection.query(
                query_texts=[current_prompt],
                n_results=min(n_results, lore_count)
            )
            if lore_results and lore_results['documents'] and lore_results['documents'][0]:
                context.extend(["World Lore: " + doc for doc in lore_results['documents'][0]])

        return "\n".join(context)

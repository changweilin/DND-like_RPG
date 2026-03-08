import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, JSON
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Character(Base):
    __tablename__ = 'characters'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    race = Column(String, nullable=False)
    char_class = Column(String, nullable=False)
    appearance = Column(Text)       # Physical description for image generation
    personality = Column(Text)      # Personality traits for LLM prompts

    # Core stats
    hp = Column(Integer, default=100)
    max_hp = Column(Integer, default=100)
    mp = Column(Integer, default=50)
    max_mp = Column(Integer, default=50)
    atk = Column(Integer, default=10)
    def_stat = Column(Integer, default=10)
    mov = Column(Integer, default=5)

    gold = Column(Integer, default=0)
    inventory = Column(JSON, default=lambda: [])   # List of item dicts
    skills = Column(JSON, default=lambda: [])       # List of skill strings

class GameState(Base):
    """Stores global world state, current location, and configuration."""
    __tablename__ = 'game_state'

    id = Column(Integer, primary_key=True)
    save_name = Column(String, unique=True, nullable=False)
    current_location = Column(String, nullable=False)
    world_context = Column(Text, nullable=False)
    difficulty = Column(String, default='Normal')
    language = Column(String, default='English')
    turn_count = Column(Integer, default=0)

    player_id = Column(Integer, ForeignKey('characters.id'))
    player = relationship("Character")

    # NPC / faction entity tracking.
    # Format: {name: {"affinity": int, "state": str, "goal": str}}
    # "affinity" — signed integer (-100 hostile … +100 devoted)
    # "state"    — short mood label, e.g. "Friendly", "Suspicious", "Fearful"
    # "goal"     — NPC's current short-term objective
    relationships = Column(JSON, default=lambda: {})

    # Sliding-window session memory — last N turns persisted in SQLite so the
    # game survives page reloads without losing short-term context.
    # Each entry: {"turn": int, "player_action": str, "narrative": str, "outcome": str}
    session_memory = Column(JSON, default=lambda: [])

    # Dynamically generated entity stat blocks, keyed by entity name (lower).
    # Format: {name: {type, hp, max_hp, atk, def_stat, skills, special_ability,
    #                 description, loot, alive}}
    # Written on first encounter; HP updated live during combat; alive=False when dead.
    known_entities = Column(JSON, default=lambda: {})

    # World setting id — matches a key in GameConfig.WORLD_SETTINGS.
    # Controls vocabulary, tone, starting location, and world lore seeded into RAG.
    # Defaults to 'dnd5e' for classic Forgotten Realms experience.
    world_setting = Column(String, default='dnd5e')

    # Multi-player party support (1-6 players).
    # party_ids: ordered list of Character.id values — index 0 is the party leader.
    #   Single-player games: party_ids = [player_id]
    # active_player_index: which slot is currently taking their turn (0-based).
    # party_contributions: per-player scoring for balanced end-game reward split.
    #   {str(char_id): {damage_dealt, healing_done, skill_checks_passed, turns_taken}}
    # ai_configs: per-slot AI configuration for AI-controlled party members.
    #   {str(slot_index): {is_ai: bool, personality: str, difficulty: str}}
    #   Slot 0 (party leader) is always human; slots 1-5 may be AI-controlled.
    party_ids             = Column(JSON, default=lambda: [])
    active_player_index   = Column(Integer, default=0)
    party_contributions   = Column(JSON, default=lambda: {})
    ai_configs            = Column(JSON, default=lambda: {})

class DatabaseManager:
    def __init__(self, db_path="savegame.db"):
        self.engine = create_engine(f'sqlite:///{db_path}')
        Base.metadata.create_all(self.engine)
        self._migrate(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _migrate(self, engine):
        """Add columns introduced after initial schema creation (non-destructive)."""
        _text = __import__('sqlalchemy').text
        migrations = [
            "ALTER TABLE game_state ADD COLUMN world_setting VARCHAR DEFAULT 'dnd5e'",
            "ALTER TABLE game_state ADD COLUMN party_ids TEXT DEFAULT '[]'",
            "ALTER TABLE game_state ADD COLUMN active_player_index INTEGER DEFAULT 0",
            "ALTER TABLE game_state ADD COLUMN party_contributions TEXT DEFAULT '{}'",
            "ALTER TABLE game_state ADD COLUMN ai_configs TEXT DEFAULT '{}'",
        ]
        with engine.connect() as conn:
            for sql in migrations:
                try:
                    conn.execute(_text(sql))
                    conn.commit()
                except Exception:
                    pass  # column already exists — safe to ignore

    def get_session(self):
        return self.Session()

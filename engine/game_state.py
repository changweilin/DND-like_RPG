import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Character(Base):
    __tablename__ = 'characters'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    race = Column(String, nullable=False)
    char_class = Column(String, nullable=False)
    gender = Column(String, default='')  # Inferred or chosen gender
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
    # Equipped items by slot: {weapon, armor, accessory} → item dict or null
    equipment = Column(JSON, default=lambda: {})

    # Progression: XP accumulated and current level (1-10).
    # Level thresholds follow D&D 5e milestones (scaled down for short campaigns).
    xp    = Column(Integer, default=0)
    level = Column(Integer, default=1)

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
    # Format: {display_name: {core_fields, profile_fields, scene_fields}}
    #
    # Core fields (always present):
    #   "affinity"    — signed integer (-100 hostile … +100 devoted)
    #   "state"       — short mood label, e.g. "Friendly", "Suspicious", "Fearful"
    #   "goal"        — NPC's current short-term objective
    #
    # Rich profile fields (generated on first encounter, stable thereafter):
    #   "proper_name" — proper given name (may differ from key if key is a title)
    #   "aliases"     — list of titles, honorifics, nicknames
    #   "biography"   — 2-3 sentence life history
    #   "personality" — MBTI type + description
    #   "traits"      — appearance, build, intelligence, physique (text)
    #
    # Scene-volatile fields (set when NPC enters scene, cleared when they leave):
    #   "emotion"     — current emotional state (only populated when NPC is present)
    #   "action"      — what the NPC is visibly doing right now
    #   "health"      — health status (Healthy / Wounded / Exhausted / …)
    relationships = Column(JSON, default=lambda: {})

    # Sliding-window session memory — last N turns persisted in SQLite so the
    # game survives page reloads without losing short-term context.
    # Each entry: {
    #   "turn": int, "player_action": str, "narrative": str, "outcome": str,
    #   "choices": list[str],             — all options offered this turn (incl. unchosen)
    #   "location": str,                  — location at time of turn
    #   "characters_present": list[str],  — NPCs / characters who appeared in this scene
    # }
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

    # Whether the custom action text input is available during gameplay.
    # Set at character creation; 1 = shown, 0 = hidden.
    allow_custom_action   = Column(Integer, default=1)

    # Organizations encountered in the story.
    # Format: {org_name_lower: {
    #   "name":            display name,
    #   "type":            category (government / army / guild / cult / academy / …),
    #   "founder":         name of the founder,
    #   "history":         2-3 sentence founding and key events,
    #   "member_count":    rough member count (text, e.g. "~500 soldiers"),
    #   "current_leader":  name of the current leader,
    #   "headquarters":    main base / location,
    #   "alignment":       moral alignment hint (e.g. "Lawful Neutral"),
    #   "description":     1-2 sentence flavour description,
    #   "first_seen_turn": turn number when first mentioned,
    # }}
    organizations = Column(JSON, default=lambda: {})

    # True while at least one living enemy is tracked in known_entities.
    # Cleared when all enemies die or player flees.  Used by UI and combat engine.
    in_combat = Column(Integer, default=0)   # stored as 0/1 for SQLite compat

    # Dungeon map: room/corridor graph generated by WorldManager.generate_dungeon().
    # {room_id: {name, description, connections:[room_id], enemies:[], loot:[], visited:bool}}
    dungeon_map = Column(JSON, default=lambda: {})

    # Active quest journal.
    # {quest_id: {name, description, status: active|completed|failed,
    #             objectives: [{text, done}], reward_xp, reward_gold,
    #             given_turn, completed_turn}}
    quests = Column(JSON, default=lambda: {})

class EntityRelation(Base):
    """
    Directed edge in the entity relationship graph.

    Both endpoints are identified by (entity_type, entity_key):
      entity_type  — 'org' | 'char' | 'npc'
      entity_key   — org  → name.lower()
                     char → str(Character.id)
                     npc  → name.lower()

    The relation is directional (source → target) but most queries will
    search both directions.  Use relation_type to distinguish semantics;
    strength encodes the signed intensity (-100 hostile … +100 devoted).

    Relation type vocabulary (not enforced — free text):
      org→org  : ally / rival / enemy / vassal / trade_partner / neutral
      org→char : employs / hunts / protects / founded_by
      char→org : member / leader / enemy / contractor / patron
      char→char: friend / rival / romantic / family / mentor / enemy
    """
    __tablename__ = 'entity_relations'
    __table_args__ = (
        # Prevent duplicate edges for the same (save, src, tgt, type) tuple.
        UniqueConstraint(
            'game_state_id', 'source_type', 'source_key',
            'target_type', 'target_key', 'relation_type',
            name='uq_entity_relation',
        ),
    )

    id             = Column(Integer, primary_key=True)
    game_state_id  = Column(Integer, ForeignKey('game_state.id'), nullable=False)

    source_type    = Column(String, nullable=False)   # 'org' | 'char' | 'npc'
    source_key     = Column(String, nullable=False)
    target_type    = Column(String, nullable=False)
    target_key     = Column(String, nullable=False)

    relation_type  = Column(String, nullable=False)   # e.g. 'ally', 'member'
    strength       = Column(Integer, default=0)        # -100 … +100
    description    = Column(Text, default='')          # one-sentence flavour note
    since_turn     = Column(Integer, default=0)        # turn when established


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
            "ALTER TABLE game_state ADD COLUMN turn_count INTEGER DEFAULT 0",
            "ALTER TABLE game_state ADD COLUMN session_memory TEXT DEFAULT '[]'",
            "ALTER TABLE game_state ADD COLUMN known_entities TEXT DEFAULT '{}'",
            "ALTER TABLE game_state ADD COLUMN allow_custom_action INTEGER DEFAULT 1",
            "ALTER TABLE game_state ADD COLUMN organizations TEXT DEFAULT '{}'",
            "ALTER TABLE characters ADD COLUMN gender VARCHAR DEFAULT ''",
            "ALTER TABLE characters ADD COLUMN xp INTEGER DEFAULT 0",
            "ALTER TABLE characters ADD COLUMN level INTEGER DEFAULT 1",
            "ALTER TABLE game_state ADD COLUMN in_combat INTEGER DEFAULT 0",
            "ALTER TABLE game_state ADD COLUMN dungeon_map TEXT DEFAULT '{}'",
            "ALTER TABLE characters ADD COLUMN equipment TEXT DEFAULT '{}'",
            "ALTER TABLE game_state ADD COLUMN quests TEXT DEFAULT '{}'",
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

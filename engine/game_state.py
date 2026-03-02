import os
import sqlite3
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, Text, JSON
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Character(Base):
    __tablename__ = 'characters'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    race = Column(String, nullable=False)
    char_class = Column(String, nullable=False)
    appearance = Column(Text) # Physical description for image generation
    personality = Column(Text) # Personality traits for LLM
    
    # Core Stats
    hp = Column(Integer, default=100)
    max_hp = Column(Integer, default=100)
    mp = Column(Integer, default=50)
    max_mp = Column(Integer, default=50)
    atk = Column(Integer, default=10)
    def_stat = Column(Integer, default=10)
    mov = Column(Integer, default=5)
    
    # Meta
    gold = Column(Integer, default=0)
    inventory = Column(JSON, default=lambda: []) # List of dictionaries defining items
    skills = Column(JSON, default=lambda: []) # List of skills/spells

class GameState(Base):
    """Stores global world state, current location, and configuration."""
    __tablename__ = 'game_state'
    
    id = Column(Integer, primary_key=True)
    save_name = Column(String, unique=True, nullable=False)
    current_location = Column(String, nullable=False)
    world_context = Column(Text, nullable=False) # E.g., Geography, politics
    difficulty = Column(String, default='Normal')
    language = Column(String, default='English') # Added language for prompts
    
    player_id = Column(Integer, ForeignKey('characters.id'))
    player = relationship("Character")
    
    # Global relationship states (e.g. Faction Reputations)
    relationships = Column(JSON, default=lambda: {})

class DatabaseManager:
    def __init__(self, db_path="savegame.db"):
        self.engine = create_engine(f'sqlite:///{db_path}')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.Session()

import streamlit as st
import os

from engine.save_load import SaveLoadManager
from engine.config import config
from ai.llm_client import LLMClient
from ai.image_gen import ImageGenerator
from ai.rag_system import RAGSystem
from logic.events import EventManager

st.set_page_config(page_title="AI RPG Engine", layout="wide")

# Initialize Systems
if 'save_manager' not in st.session_state:
    st.session_state.save_manager = SaveLoadManager()
    st.session_state.llm = LLMClient()
    st.session_state.rag = RAGSystem()
    st.session_state.img_gen = ImageGenerator()
    
    st.session_state.current_session = None
    st.session_state.game_state = None
    st.session_state.player = None
    st.session_state.event_manager = None

def main_menu():
    st.title("D&D AI RPG Engine")
    
    col1, col2 = st.columns(2)
    with col1:
        st.header("New Game")
        with st.form("new_game_form"):
            save_name = st.text_input("Save Name")
            player_name = st.text_input("Character Name")
            race = st.selectbox("Race", ["Human", "Elf", "Dwarf", "Orc", "Halfling"])
            char_class = st.selectbox("Class", ["Warrior", "Mage", "Rogue", "Cleric"])
            appearance = st.text_area("Appearance (For Image Gen)", "A brave adventurer.")
            personality = st.text_area("Personality", "Courageous and kind.")
            difficulty = st.selectbox("Difficulty", ["Easy", "Normal", "Hard"])
            language = st.selectbox("Language", ["English", "繁體中文", "日本語", "Español"])
            
            submitted = st.form_submit_button("Start Adventure")
            if submitted:
                if not save_name or not player_name:
                    st.error("Save Name and Character Name are required.")
                else:
                    success, msg = st.session_state.save_manager.create_new_game(
                        save_name, player_name, race, char_class, appearance, personality, difficulty, language
                    )
                    if success:
                        st.success("Save created! Please load it to play.")
                    else:
                        st.error(msg)
                    
    with col2:
        st.header("Load Game")
        saves = st.session_state.save_manager.list_saves()
        if not saves:
            st.info("No saves found.")
        else:
            save_options = [s['name'] for s in saves]
            selected_save = st.selectbox("Select Save", save_options, key="load_select")
            
            if st.button("Load"):
                # Load logic
                session, game_state, player = st.session_state.save_manager.load_game(selected_save)
                if session and game_state and player:
                    st.session_state.current_session = session
                    st.session_state.game_state = game_state
                    st.session_state.player = player
                    st.session_state.history = []
                    st.session_state.event_manager = EventManager(
                        st.session_state.llm, st.session_state.rag, session
                    )
                    st.success(f"Loaded {player.name}'s adventure!")
                    st.rerun()
                else:
                    st.error("Failed to load save file.")

def game_loop():
    player = st.session_state.player
    state = st.session_state.game_state
    
    st.sidebar.title("Character Sheet")
    st.sidebar.write(f"**Name:** {player.name} ({player.race} {player.char_class})")
    st.sidebar.write(f"**HP:** {player.hp}/{player.max_hp} | **MP:** {player.mp}/{player.max_mp}")
    st.sidebar.write(f"**Gold:** {player.gold}")
    
    if st.sidebar.button("Save & Quit"):
        st.session_state.current_session.commit()
        st.session_state.current_session.close()
        st.session_state.current_session = None
        st.session_state.game_state = None
        st.session_state.player = None
        st.session_state.event_manager = None
        st.session_state.history = []
        st.rerun()
        
    st.title(f"Location: {state.current_location}")
    
    # Simple Chat History
    if 'history' not in st.session_state:
        st.session_state.history = []
        
    # Render history
    for item in st.session_state.history:
        if item['role'] == 'player':
            st.markdown(f"**You**: {item['content']}")
        else:
            st.markdown(f"**DM**: {item['content']}")
            if 'image' in item and item['image']:
                st.image(item['image'], caption="Scene visualization")
            
        
    # Input Area - Render Choices or Free Actions
    st.markdown("### What do you do next?")
    
    # Check if we have pending choices from the last DM response
    current_choices = []
    if st.session_state.history and st.session_state.history[-1]['role'] == 'dm':
        current_choices = st.session_state.history[-1].get('choices', [])
        
    action_taken = None
    
    if current_choices:
        # Render choices as buttons
        for idx, choice in enumerate(current_choices):
            if st.button(choice, key=f"choice_{len(st.session_state.history)}_{idx}"):
                action_taken = choice
                
        # Fallback manual input just in case
        with st.expander("Or do something else..."):
            with st.form("custom_action_form", clear_on_submit=True):
                custom_action = st.text_input("Custom Action:")
                if st.form_submit_button("Do it") and custom_action:
                    action_taken = custom_action
    else:
        # Initial turn or no choices provided
        with st.form("action_form", clear_on_submit=True):
            col_input, col_submit = st.columns([4, 1])
            with col_input:
                action_taken = st.text_input("I choose to...", key="action_input")
            with col_submit:
                st.write("")
                st.write("")
                st.form_submit_button("Action")
        
    if action_taken:
        st.session_state.history.append({"role": "player", "content": action_taken})
        with st.spinner("The DM is rolling the dice and typing..."):
            response, choices, stats = st.session_state.event_manager.process_turn(
                action_taken, state, player
            )
            
            # Check for critical scene to generate image
            scene_image = None
            if "look" in action_taken.lower() or len(st.session_state.history) % 6 == 0:
                try:
                    prompt_for_image = f"A fantasy scene. {state.current_location}. {response[:100]}"
                    scene_image = st.session_state.img_gen.generate_image(prompt_for_image)
                except Exception as e:
                    print(f"Image gen failed: {e}")
            
            st.session_state.history.append({
                "role": "dm", 
                "content": response,
                "choices": choices,
                "image": scene_image
            })
        st.rerun()

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

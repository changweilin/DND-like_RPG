import streamlit as st
import os
import datetime

from engine.save_load import SaveLoadManager
from engine.config import config
from ai.llm_client import LLMClient
from ai.image_gen import ImageGenerator
from ai.rag_system import RAGSystem
from logic.events import EventManager

st.set_page_config(page_title="AI RPG Engine", layout="wide")

# Initialize shared systems once per browser session
if 'save_manager' not in st.session_state:
    st.session_state.save_manager    = SaveLoadManager()
    st.session_state.llm             = LLMClient()
    st.session_state.rag             = RAGSystem()
    st.session_state.img_gen         = ImageGenerator()

    st.session_state.current_session = None
    st.session_state.game_state      = None
    st.session_state.player          = None
    st.session_state.event_manager   = None
    st.session_state.history         = []

    # Model switcher state
    st.session_state.active_model_id  = config.LLM_MODEL_NAME
    st.session_state.last_model_check = ""   # ISO date string

# ---------------------------------------------------------------------------
# Scene type styling (Waidrin-inspired Narrative Event labelling)
# ---------------------------------------------------------------------------
_SCENE_ICONS = {
    'combat':      '⚔️',
    'social':      '💬',
    'exploration': '🗺️',
    'puzzle':      '🧩',
    'rest':        '🏕️',
}

_SCENE_COLOURS = {
    'combat':      '#8B0000',
    'social':      '#1a3a5c',
    'exploration': '#1a4a1a',
    'puzzle':      '#4a3a00',
    'rest':        '#2a2a4a',
}

# ---------------------------------------------------------------------------
# Daily model update check (Ollama local models only)
# ---------------------------------------------------------------------------

def _check_model_updates():
    """On the first load of each calendar day, check for new/updated Ollama models."""
    today = datetime.date.today().isoformat()
    if st.session_state.last_model_check == today:
        return
    st.session_state.last_model_check = today

    try:
        import ollama
        installed = ollama.list()
        installed_ids = {m.model.split(':')[0] + ':' + m.model.split(':')[1]
                         if ':' in m.model else m.model
                         for m in installed.models}
        preset_ids = {p['id'] for p in config.MODEL_PRESETS if p.get('provider') == 'ollama'}
        extras = installed_ids - preset_ids
        if extras:
            st.sidebar.info(
                f"🔄 Ollama models not in preset list detected:\n"
                + "\n".join(f"• `{m}`" for m in sorted(extras))
            )
    except Exception:
        pass  # Ollama not running — silently skip

# ---------------------------------------------------------------------------
# Model switcher sidebar panel
# ---------------------------------------------------------------------------

def _render_model_switcher():
    """Sidebar expander: select model, view pros/cons, switch live."""
    with st.sidebar.expander("⚙️ Model", expanded=False):
        # Build display labels grouped by category
        preset_labels = [
            f"[{p['category']}] {p['name']}" for p in config.MODEL_PRESETS
        ]
        preset_ids = [p['id'] for p in config.MODEL_PRESETS]

        # Find current selection index
        try:
            current_idx = preset_ids.index(st.session_state.active_model_id)
        except ValueError:
            current_idx = 0

        selected_idx = st.selectbox(
            "LLM Model",
            range(len(config.MODEL_PRESETS)),
            index=current_idx,
            format_func=lambda i: preset_labels[i],
            key="model_selector",
        )

        preset = config.MODEL_PRESETS[selected_idx]

        # Description + pros/cons shown immediately below selector
        st.caption(preset.get('description', ''))
        if preset.get('pros'):
            st.markdown(f"✅ **Pros:** {preset['pros']}")
        if preset.get('cons'):
            st.markdown(f"⚠️ **Cons:** {preset['cons']}")

        # API key status for cloud models
        env_key = preset.get('env_key')
        if env_key:
            if os.environ.get(env_key):
                st.success(f"🔑 `{env_key}` is set", icon=None)
            else:
                st.warning(f"🔑 `{env_key}` not found in environment")

        # VRAM indicator for local models
        vram = preset.get('vram_gb')
        if vram:
            st.caption(f"💾 VRAM: ~{vram} GB")

        if st.button("Switch Model", key="switch_model_btn"):
            new_id = preset['id']
            st.session_state.llm.switch_model(new_id)
            st.session_state.active_model_id = new_id
            st.success(f"Switched to **{preset['name']}**")

# ---------------------------------------------------------------------------
# Main Menu
# ---------------------------------------------------------------------------

def main_menu():
    _check_model_updates()
    _render_model_switcher()

    st.title("D&D AI RPG Engine")

    col1, col2 = st.columns(2)

    with col1:
        st.header("New Game")
        with st.form("new_game_form"):
            save_name      = st.text_input("Save Name")
            character_name = st.text_input("Character Name")
            race           = st.selectbox("Race", ["Human", "Elf", "Dwarf", "Orc", "Halfling"])
            char_class     = st.selectbox("Class", ["Warrior", "Mage", "Rogue", "Cleric"])
            appearance     = st.text_area("Appearance (For Image Gen)", "A brave adventurer.")
            personality    = st.text_area("Personality", "Courageous and kind.")
            difficulty     = st.selectbox("Difficulty", ["Easy", "Normal", "Hard"])
            language       = st.selectbox("Language", ["English", "繁體中文", "日本語", "Español"])

            if st.form_submit_button("Start Adventure"):
                if not save_name or not character_name:
                    st.error("Save Name and Character Name are required.")
                else:
                    player, game_state, session = st.session_state.save_manager.create_new_game(
                        save_name, character_name, race, char_class,
                        appearance, personality, difficulty, language,
                    )
                    if player is not None:
                        st.success("Save created! Please load it to play.")
                    else:
                        st.error(f"Save name '{save_name}' already exists. Choose a different name.")

    with col2:
        st.header("Load Game")
        saves = st.session_state.save_manager.list_saves()
        if not saves:
            st.info("No saves found.")
        else:
            save_labels  = [
                f"{s['save_name']} — {s['location']} (turn {s['turns']})" for s in saves
            ]
            save_names   = [s['save_name'] for s in saves]
            selected_idx = st.selectbox(
                "Select Save", range(len(saves)),
                format_func=lambda i: save_labels[i],
                key="load_select",
            )

            if st.button("Load"):
                selected_save = save_names[selected_idx]
                player, game_state, session = st.session_state.save_manager.load_game(selected_save)
                if player and game_state and session:
                    st.session_state.current_session = session
                    st.session_state.game_state      = game_state
                    st.session_state.player          = player
                    st.session_state.history         = []
                    st.session_state.event_manager   = EventManager(
                        st.session_state.llm, st.session_state.rag, session
                    )
                    st.success(f"Loaded {player.name}'s adventure!")
                    st.rerun()
                else:
                    st.error("Failed to load save file.")

# ---------------------------------------------------------------------------
# Game Loop helpers
# ---------------------------------------------------------------------------

def _render_dice_result(dice_result):
    """Colour-coded dice roll banner shown before DM narrative on skill checks."""
    if dice_result is None:
        return
    outcome = dice_result['outcome']
    icon_map = {
        'critical_success': '🟡',
        'success':          '🟢',
        'failure':          '🔴',
        'critical_failure': '💀',
    }
    icon  = icon_map.get(outcome, '🎲')
    label = outcome.replace('_', ' ').upper()
    st.info(
        f"{icon} **Dice Roll:** {dice_result['notation']} = "
        f"{dice_result['raw_roll']} + {dice_result['modifier']} "
        f"= **{dice_result['total']}** vs DC {dice_result['dc']} — **{label}**"
    )

def _render_scene_label(scene_type):
    """Small scene-type badge before DM narrative (Waidrin Narrative Event tagging)."""
    icon  = _SCENE_ICONS.get(scene_type, '🗺️')
    label = scene_type.capitalize()
    st.caption(f"{icon} *{label} scene*")

def _render_npc_tracker(state):
    """Sidebar widget: show NPC affinity, mood, and goal for all tracked relationships."""
    rels = state.relationships or {}
    if not rels:
        return
    st.sidebar.markdown("---")
    st.sidebar.write("**NPCs & Factions**")
    for name, data in rels.items():
        if isinstance(data, dict):
            affinity = data.get('affinity', 0)
            mood     = data.get('state', 'Neutral')
            goal     = data.get('goal', '')
        else:
            # legacy flat integer
            affinity, mood, goal = int(data), 'Neutral', ''

        bar = _affinity_bar(affinity)
        st.sidebar.write(f"**{name}**")
        st.sidebar.write(f"  {bar} {affinity:+d} · {mood}")
        if goal:
            st.sidebar.caption(f"  Goal: {goal}")

def _affinity_bar(affinity):
    """ASCII affinity bar: ████░░░░ style, centred on zero."""
    clamped = max(-100, min(100, affinity))
    filled  = round((clamped + 100) / 200 * 10)
    empty   = 10 - filled
    return '█' * filled + '░' * empty

# ---------------------------------------------------------------------------
# Game Loop
# ---------------------------------------------------------------------------

def game_loop():
    player = st.session_state.player
    state  = st.session_state.game_state

    # --- Sidebar: character sheet ---
    st.sidebar.title("Character Sheet")
    st.sidebar.write(f"**Name:** {player.name} ({player.race} {player.char_class})")

    hp_pct = int((player.hp / max(player.max_hp, 1)) * 100)
    mp_pct = int((player.mp / max(player.max_mp, 1)) * 100)
    st.sidebar.write(f"**HP** {player.hp}/{player.max_hp}")
    st.sidebar.progress(hp_pct)
    st.sidebar.write(f"**MP** {player.mp}/{player.max_mp}")
    st.sidebar.progress(mp_pct)

    st.sidebar.write(f"**ATK:** {player.atk} | **DEF:** {player.def_stat} | **MOV:** {player.mov}")
    st.sidebar.write(f"**Gold:** {player.gold}")
    st.sidebar.write(f"**Turn:** {state.turn_count or 0}  "
                     f"*(memory: last {config.SESSION_MEMORY_WINDOW})*")

    if player.inventory:
        st.sidebar.markdown("---")
        st.sidebar.write("**Inventory**")
        for item in player.inventory:
            name = item.get('name', item) if isinstance(item, dict) else item
            st.sidebar.write(f"  • {name}")

    # NPC / faction tracker (enriched relationship data)
    _render_npc_tracker(state)

    # Model switcher available in-game too
    _render_model_switcher()

    if st.sidebar.button("Save & Quit"):
        st.session_state.current_session.commit()
        st.session_state.current_session.close()
        st.session_state.current_session = None
        st.session_state.game_state      = None
        st.session_state.player          = None
        st.session_state.event_manager   = None
        st.session_state.history         = []
        st.rerun()

    st.title(f"Location: {state.current_location}")

    # Active model badge
    active_preset = next(
        (p for p in config.MODEL_PRESETS if p['id'] == st.session_state.active_model_id),
        None,
    )
    if active_preset:
        st.caption(f"🤖 DM powered by **{active_preset['name']}** ({active_preset['category']})")

    # --- Chat history ---
    if 'history' not in st.session_state:
        st.session_state.history = []

    for item in st.session_state.history:
        if item['role'] == 'player':
            st.markdown(f"**You:** {item['content']}")
        else:
            # Scene type badge (Waidrin Narrative Event style)
            scene_type = item.get('scene_type', 'exploration')
            _render_scene_label(scene_type)
            # Dice result banner (if a skill check occurred)
            _render_dice_result(item.get('dice_result'))
            st.markdown(f"**DM:** {item['content']}")
            if item.get('image'):
                st.image(item['image'], caption="Scene visualization")

    # --- Input area ---
    st.markdown("### What do you do next?")

    current_choices = []
    if st.session_state.history and st.session_state.history[-1]['role'] == 'dm':
        current_choices = st.session_state.history[-1].get('choices', [])

    action_taken = None

    if current_choices:
        for idx, choice in enumerate(current_choices):
            if st.button(choice, key=f"choice_{len(st.session_state.history)}_{idx}"):
                action_taken = choice

        with st.expander("Or do something else..."):
            with st.form("custom_action_form", clear_on_submit=True):
                custom_action = st.text_input("Custom Action:")
                if st.form_submit_button("Do it") and custom_action:
                    action_taken = custom_action
    else:
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
        with st.spinner("The DM is thinking..."):
            response, choices, turn_data, dice_result = (
                st.session_state.event_manager.process_turn(action_taken, state, player)
            )

            scene_image = None
            if "look" in action_taken.lower() or len(st.session_state.history) % 6 == 0:
                try:
                    prompt_for_image = (
                        f"A fantasy scene. {state.current_location}. {response[:100]}"
                    )
                    scene_image = st.session_state.img_gen.generate_image(prompt_for_image)
                except Exception as e:
                    print(f"Image gen failed: {e}")

            st.session_state.history.append({
                "role":        "dm",
                "content":     response,
                "choices":     choices,
                "scene_type":  turn_data.get('scene_type', 'exploration'),
                "dice_result": dice_result,
                "image":       scene_image,
            })
        st.rerun()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

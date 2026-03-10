import streamlit as st
import os
import datetime

from engine.save_load import SaveLoadManager
from engine.config import config
from engine.dice import DiceRoller
from engine.persistence import PersistenceManager
from engine.story_saver import (
    save_image_with_text, compress_game_log,
    save_game_log, load_story_log, restore_history_from_log,
)
from engine.board import (
    assign_map_position, detect_location_type, build_map_html,
    MAP_ROWS, MAP_COLS,
)
from engine.image_prompts import (
    IMAGE_STYLES,
    build_map_prompt, build_portrait_prompt,
    get_map_negative_prompt, get_portrait_negative_prompt,
    classify_cinematic_event, build_cinematic_prompt,
)
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
    st.session_state.player          = None   # active character (backward compat)
    st.session_state.party           = []     # list[Character] — all party members
    st.session_state.event_manager   = None
    st.session_state.history         = []

    # Model switcher state
    st.session_state.active_model_id  = config.LLM_MODEL_NAME
    st.session_state.last_model_check = ""   # ISO date string

    # Board state (world map + player token positions + manual dice)
    st.session_state.world_map        = {}   # loc_name → {row, col, icon}
    st.session_state.player_positions = {}   # char_id → {location, row, col}
    st.session_state.manual_dice      = {}   # dice_type → last_result (int)

    # Image generation state
    st.session_state.image_style      = 'fantasy_art'   # key in IMAGE_STYLES
    st.session_state.custom_img_suffix = ''             # user override suffix
    st.session_state.continent_map    = None            # PIL Image | None
    st.session_state.portraits        = {}              # {char_id: PIL Image}

    # Load persistent user preferences
    prefs = PersistenceManager.load_prefs()
    if prefs.get('active_model_id'):
        st.session_state.active_model_id = prefs['active_model_id']
    
    # Defaults for new game fields from prefs
    st.session_state.pref_difficulty = prefs.get('difficulty', 'Normal')
    st.session_state.pref_language   = prefs.get('language', 'English')
    st.session_state.pref_world_idx  = prefs.get('world_idx', 0)
    st.session_state.pref_img_style  = prefs.get('img_style', 0)
    st.session_state.pref_num_players = prefs.get('num_players', 1)

    # State for duplicate save name handling
    st.session_state.duplicate_save_pending = None # {save_name, lead_fields, difficulty, language, lore, world_idx, style_idx, custom_img, extra_players}

# ---------------------------------------------------------------------------
# Scene-type styling (Waidrin-style Narrative Event labels)
# ---------------------------------------------------------------------------
_SCENE_ICONS = {
    'combat':      '⚔️',
    'social':      '💬',
    'exploration': '🗺️',
    'puzzle':      '🧩',
    'rest':        '🏕️',
}

# ---------------------------------------------------------------------------
# Daily model update check (Ollama local models only)
# ---------------------------------------------------------------------------

def _check_model_updates():
    """On the first load of each calendar day, check for new Ollama models."""
    today = datetime.date.today().isoformat()
    if st.session_state.last_model_check == today:
        return
    st.session_state.last_model_check = today
    try:
        import ollama
        installed    = ollama.list()
        installed_ids = {
            m.model.split(':')[0] + ':' + m.model.split(':')[1]
            if ':' in m.model else m.model
            for m in installed.models
        }
        preset_ids = {p['id'] for p in config.MODEL_PRESETS if p.get('provider') == 'ollama'}
        extras = installed_ids - preset_ids
        if extras:
            st.sidebar.info(
                "🔄 Ollama models not in preset list:\n"
                + "\n".join(f"• `{m}`" for m in sorted(extras))
            )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Model switcher sidebar panel
# ---------------------------------------------------------------------------

def _render_model_switcher():
    """Sidebar expander: select model, view pros/cons, switch live."""
    with st.sidebar.expander("⚙️ Model", expanded=False):
        preset_labels = [f"[{p['category']}] {p['name']}" for p in config.MODEL_PRESETS]
        preset_ids    = [p['id'] for p in config.MODEL_PRESETS]
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
        st.caption(preset.get('description', ''))
        if preset.get('pros'):
            st.markdown(f"✅ **Pros:** {preset['pros']}")
        if preset.get('cons'):
            st.markdown(f"⚠️ **Cons:** {preset['cons']}")

        env_key = preset.get('env_key')
        if env_key:
            if os.environ.get(env_key):
                st.success(f"🔑 `{env_key}` is set")
            else:
                st.warning(f"🔑 `{env_key}` not found in environment")

        vram = preset.get('vram_gb')
        if vram:
            st.caption(f"💾 VRAM: ~{vram} GB")

        if st.button("Switch Model", key="switch_model_btn"):
            new_id = preset['id']
            st.session_state.llm.switch_model(new_id)
            st.session_state.active_model_id = new_id
            # Save model choice
            prefs = PersistenceManager.load_prefs()
            prefs['active_model_id'] = new_id
            PersistenceManager.save_prefs(prefs)
            st.success(f"Switched to **{preset['name']}**")

# ---------------------------------------------------------------------------
# Main Menu helpers
# ---------------------------------------------------------------------------

def _player_config_fields(idx, key_prefix):
    """
    Render config fields for one party member (inside a form).

    Slot 0 is always human (party leader). Slots 1-5 may be AI-controlled.
    Returns (name, race, char_class, appearance, personality,
             is_ai, ai_personality, ai_difficulty).
    """
    flag  = config.PLAYER_FLAGS[idx] if idx < len(config.PLAYER_FLAGS) else '👤'
    label = (f"{flag} Player 1 (Party Leader)" if idx == 0
             else f"{flag} Player {idx + 1}")
    st.markdown(f"**{label}**")

    is_ai          = False
    ai_personality = 'tactical'
    ai_difficulty  = 'normal'
    if idx > 0:
        is_ai = st.checkbox("🤖 AI-controlled", key=f"{key_prefix}_is_ai_{idx}", value=False)

    cols = st.columns([2, 1, 1])
    name       = cols[0].text_input("Name",  key=f"{key_prefix}_name_{idx}")
    race       = cols[1].selectbox("Race", ["Human", "Elf", "Dwarf", "Orc", "Halfling"],
                                   key=f"{key_prefix}_race_{idx}")
    char_class = cols[2].selectbox("Class", ["Warrior", "Mage", "Rogue", "Cleric"],
                                   key=f"{key_prefix}_class_{idx}")

    base = config.CLASS_BASE_STATS.get(char_class.lower(), {})
    st.caption(
        f"HP {base.get('max_hp','?')} · MP {base.get('max_mp','?')} · "
        f"ATK {base.get('atk','?')} · DEF {base.get('def_stat','?')} · "
        f"MOV {base.get('mov','?')} · "
        f"⚖ reward×{base.get('reward_weight',1.0):.2f} — *{base.get('role','')}*"
    )

    if is_ai:
        ai_cols = st.columns(2)
        personalities      = list(config.AI_PERSONALITIES.keys())
        personality_labels = [config.AI_PERSONALITIES[p]['name'] for p in personalities]
        ai_p_idx = ai_cols[0].selectbox(
            "AI Personality", range(len(personalities)),
            format_func=lambda i: personality_labels[i],
            key=f"{key_prefix}_ai_pers_{idx}",
        )
        ai_personality = personalities[ai_p_idx]

        difficulties      = list(config.AI_DIFFICULTIES.keys())
        difficulty_labels = [config.AI_DIFFICULTIES[d]['name'] for d in difficulties]
        ai_d_idx = ai_cols[1].selectbox(
            "AI Difficulty", range(len(difficulties)),
            format_func=lambda i: difficulty_labels[i],
            key=f"{key_prefix}_ai_diff_{idx}",
        )
        ai_difficulty = difficulties[ai_d_idx]

        p_desc = config.AI_PERSONALITIES.get(ai_personality, {}).get('description', '')
        d_desc = config.AI_DIFFICULTIES.get(ai_difficulty, {}).get('description', '')
        st.caption(f"🧠 {p_desc}  ·  ⚡ {d_desc}")
        appearance       = ""
        personality_text = ""
    else:
        appearance       = st.text_input("Appearance",  key=f"{key_prefix}_app_{idx}",
                                         placeholder="A brave adventurer.")
        personality_text = st.text_input("Personality", key=f"{key_prefix}_per_{idx}",
                                         placeholder="Courageous and kind.")

    return name, race, char_class, appearance, personality_text, is_ai, ai_personality, ai_difficulty


def main_menu():
    _check_model_updates()
    _render_model_switcher()

    st.title("D&D AI RPG Engine")

    col1, col2 = st.columns(2)

    with col1:
        st.header("New Game")
        with st.form("new_game_form"):
            save_name  = st.text_input("Save Name")
            difficulty = st.selectbox("Difficulty", ["Easy", "Normal", "Hard"],
                                      index=["Easy", "Normal", "Hard"].index(st.session_state.pref_difficulty))
            language   = st.selectbox("Language", ["English", "繁體中文", "日本語", "Español"],
                                      index=["English", "繁體中文", "日本語", "Español"].index(st.session_state.pref_language))

            st.markdown("**World Setting**")
            ws_labels = [f"[{ws['category']}] {ws['name']}" for ws in config.WORLD_SETTINGS]
            ws_ids    = [ws['id'] for ws in config.WORLD_SETTINGS]
            ws_idx    = st.selectbox(
                "Universe", range(len(config.WORLD_SETTINGS)),
                format_func=lambda i: ws_labels[i],
                index=st.session_state.pref_world_idx,
                key="new_game_ws_select",
            )
            ws = config.WORLD_SETTINGS[ws_idx]
            tm = ws.get('term_map', {})
            st.caption(
                f"**{ws['name']}** — {ws['description']}  \n"
                f"{tm.get('hp_name','HP')}·{tm.get('mp_name','MP')}·"
                f"{tm.get('gold_name','gold')}·GM={tm.get('dm_title','GM')}"
            )
            custom_lore = st.text_area(
                "Custom World Lore (optional)",
                placeholder=ws.get('world_lore', '')[:150] + "...",
                height=60, key="new_game_lore",
            )

            st.markdown("---")
            st.markdown("**🎨 影像風格 (Image Style)**")
            _style_keys   = list(IMAGE_STYLES.keys())
            _style_labels = [
                f"{IMAGE_STYLES[k]['name']} — {IMAGE_STYLES[k]['name_en']}"
                for k in _style_keys
            ]
            img_style_idx = st.selectbox(
                "Art Style",
                range(len(_style_keys)),
                format_func=lambda i: _style_labels[i],
                index=st.session_state.pref_img_style,
                key="new_game_img_style",
            )
            custom_img_suffix = st.text_input(
                "自定義風格後綴 (Custom style suffix, optional)",
                key="new_game_custom_img",
                placeholder="e.g. 'oil painting, baroque style, rich colors'",
            )
            st.caption(
                "🗺️ 開始遊戲後可在遊戲板生成大陸地圖，在角色頁籤生成角色肖像。"
            )

            st.markdown("---")
            st.markdown("**Party (1-6 players)**")
            num_players = st.selectbox(
                "Number of players", list(range(1, 7)), 
                index=st.session_state.pref_num_players - 1,
                key="new_game_num_players"
            )

            player_fields = []
            for i in range(num_players):
                player_fields.append(_player_config_fields(i, key_prefix="ng"))
                if i < num_players - 1:
                    st.markdown("---")

            if st.form_submit_button("Start Adventure"):
                lead = player_fields[0]
                if not save_name or not lead[0]:
                    st.error("Save Name and Player 1 Name are required.")
                else:
                    extra = []
                    for name, race, char_class, app, per, is_ai, ai_pers, ai_diff in player_fields[1:]:
                        extra.append({
                            'name': name or f'Adventurer {len(extra)+2}',
                            'race': race, 'char_class': char_class,
                            'appearance': app, 'personality': per,
                            'is_ai': is_ai,
                            'ai_personality': ai_pers, 'ai_difficulty': ai_diff,
                        })
                    party, game_state, session = (
                        st.session_state.save_manager.create_new_game(
                            save_name, lead[0], lead[1], lead[2], lead[3], lead[4],
                            difficulty, language,
                            world_context=custom_lore,
                            world_setting=ws_ids[ws_idx],
                            extra_players=extra or None,
                        )
                    )
                    if party is not None:
                        names    = ", ".join(c.name for c in party)
                        ai_count = sum(1 for e in extra if e.get('is_ai'))
                        suffix   = f" ({ai_count} AI)" if ai_count else ""
                        # Store image style selection for this session
                        st.session_state.image_style       = _style_keys[img_style_idx]
                        st.session_state.custom_img_suffix = custom_img_suffix.strip()
                        st.session_state.continent_map     = None
                        st.session_state.portraits         = {}
                        
                        # Save preferences
                        PersistenceManager.save_prefs({
                            'active_model_id': st.session_state.active_model_id,
                            'difficulty':  difficulty,
                            'language':    language,
                            'world_idx':   ws_idx,
                            'img_style':   img_style_idx,
                            'num_players': num_players
                        })
                        
                        st.success(
                            f"Party [{names}]{suffix} created in **{ws['name']}**! Load it to play."
                        )
                    else:
                        # Duplicate name handle
                        st.session_state.duplicate_save_pending = {
                            'save_name': save_name,
                            'lead_fields': lead,
                            'difficulty': difficulty,
                            'language': language,
                            'world_context': custom_lore,
                            'world_setting': ws_ids[ws_idx],
                            'extra_players': extra,
                            'img_style': _style_keys[img_style_idx],
                            'custom_img_suffix': custom_img_suffix.strip()
                        }
                        st.rerun()

    with col2:
        st.header("Load Game")
        saves = st.session_state.save_manager.list_saves()
        if not saves:
            st.info("No saves found.")
        else:
            save_labels = [
                f"{s['save_name']} — {s['location']} ({s['party_size']}p · turn {s['turns']})"
                for s in saves
            ]
            save_names   = [s['save_name'] for s in saves]
            selected_idx = st.selectbox(
                "Select Save", range(len(saves)),
                format_func=lambda i: save_labels[i],
                key="load_select",
            )
            
            l_col, d_col = st.columns(2)
            if l_col.button("Load", use_container_width=True):
                selected_save = save_names[selected_idx]
                party, game_state, session = st.session_state.save_manager.load_game(selected_save)
                if party and game_state and session:
                    active_idx  = game_state.active_player_index or 0
                    active_char = party[active_idx % len(party)]
                    # Restore last 2 story pages as history so the player
                    # can immediately see where the session left off
                    prior_log  = load_story_log(selected_save)
                    prior_hist = restore_history_from_log(prior_log, n=2)

                    st.session_state.current_session = session
                    st.session_state.game_state      = game_state
                    st.session_state.party           = party
                    st.session_state.player          = active_char
                    st.session_state.history         = prior_hist
                    st.session_state.event_manager   = EventManager(
                        st.session_state.llm, st.session_state.rag, session
                    )
                    # Reset board state for fresh session
                    st.session_state.world_map        = {}
                    st.session_state.player_positions = {}
                    st.session_state.manual_dice      = {}
                    st.session_state.continent_map    = None
                    st.session_state.portraits        = {}
                    # Open book at last page on load
                    st.session_state.book_page_idx   = max(0, len(prior_log) - 1)
                    names = ", ".join(c.name for c in party)
                    st.success(f"Loaded party [{names}]!")
                    st.rerun()
                else:
                    st.error("Failed to load save file.")
            
            if d_col.button("🗑️ Delete", use_container_width=True):
                selected_save = save_names[selected_idx]
                if st.session_state.save_manager.delete_game(selected_save):
                    st.success(f"Deleted save '{selected_save}'.")
                    st.rerun()
                else:
                    st.error(f"Failed to delete save '{selected_save}'.")

    # ---- Duplicate Save Dialog ----
    if st.session_state.duplicate_save_pending:
        pending = st.session_state.duplicate_save_pending
        st.markdown("---")
        st.warning(f"⚠️ Save name '**{pending['save_name']}**' already exists. What would you like to do?")
        c1, c2, c3 = st.columns([1, 1, 2])
        
        if c1.button("Overwrite"):
            # Delete and then create
            st.session_state.save_manager.delete_game(pending['save_name'])
            lead = pending['lead_fields']
            party, game_state, session = st.session_state.save_manager.create_new_game(
                pending['save_name'], lead[0], lead[1], lead[2], lead[3], lead[4],
                pending['difficulty'], pending['language'],
                world_context=pending['world_context'],
                world_setting=pending['world_setting'],
                extra_players=pending['extra_players'] or None,
            )
            if party:
                st.session_state.image_style       = pending['img_style']
                st.session_state.custom_img_suffix = pending['custom_img_suffix']
                st.session_state.duplicate_save_pending = None
                st.success(f"Overwrite successful for '{pending['save_name']}'!")
                st.rerun()
            else:
                st.error("Failed to overwrite.")
        
        if c2.button("Inherit"):
            # Just load the existing one
            party, game_state, session = st.session_state.save_manager.load_game(pending['save_name'])
            if party:
                # Same logic as "Load"
                prior_log  = load_story_log(pending['save_name'])
                prior_hist = restore_history_from_log(prior_log, n=2)
                st.session_state.current_session = session
                st.session_state.game_state      = game_state
                st.session_state.party           = party
                st.session_state.player          = party[game_state.active_player_index or 0]
                st.session_state.history         = prior_hist
                st.session_state.event_manager   = EventManager(st.session_state.llm, st.session_state.rag, session)
                st.session_state.duplicate_save_pending = None
                st.success(f"Inherited save '{pending['save_name']}'!")
                st.rerun()
            else:
                st.error("Failed to inherit.")

        if c3.button("Cancel"):
            st.session_state.duplicate_save_pending = None
            st.rerun()

# ---------------------------------------------------------------------------
# Board state helpers (world map + player token positions)
# ---------------------------------------------------------------------------

def _init_board_state(party, state):
    """
    Ensure every party member has an initial map position.
    Called once at the start of each game_loop() render.
    """
    if not party or not state:
        return
    starting_loc = state.current_location or "Starting Area"
    for char in party:
        if char.id not in st.session_state.player_positions:
            row, col, icon = assign_map_position(
                starting_loc, st.session_state.world_map
            )
            st.session_state.world_map[starting_loc] = {'row': row, 'col': col, 'icon': icon}
            st.session_state.player_positions[char.id] = {
                'location': starting_loc, 'row': row, 'col': col,
            }


def _move_player_on_map(char, new_location):
    """
    Assign (or retrieve) a grid position for new_location and update the
    player's token to that cell.
    """
    row, col, icon = assign_map_position(
        new_location, st.session_state.world_map
    )
    st.session_state.world_map[new_location] = {'row': row, 'col': col, 'icon': icon}
    st.session_state.player_positions[char.id] = {
        'location': new_location, 'row': row, 'col': col,
    }

# ---------------------------------------------------------------------------
# Sidebar helpers
# ---------------------------------------------------------------------------

def _render_party_sidebar(party, state, active_char):
    """Sidebar: compact card per member — flag, AI badge, pulsing active highlight."""
    ws_id   = getattr(state, 'world_setting', None) or 'dnd5e'
    tm      = config.get_world_setting(ws_id)['term_map']
    hp_lbl  = tm.get('hp_name', 'HP')
    mp_lbl  = tm.get('mp_name', 'MP')
    ai_cfgs = getattr(state, 'ai_configs', None) or {}

    st.sidebar.title("Party")

    # Inject pulse-animation CSS once
    st.sidebar.markdown(
        "<style>"
        "@keyframes pulse-active{0%,100%{background:#1a4a1a;}50%{background:#2d6b2d;}}"
        ".active-slot{animation:pulse-active 1.8s ease-in-out infinite;"
        " border-left:4px solid #4caf50;padding:4px 6px;border-radius:4px;margin:2px 0;}"
        ".dead-slot{border-left:4px solid #8b0000;padding:4px 6px;border-radius:4px;"
        " margin:2px 0;background:#2a0a0a;}"
        "</style>",
        unsafe_allow_html=True,
    )

    for i, char in enumerate(party):
        flag      = config.PLAYER_FLAGS[i] if i < len(config.PLAYER_FLAGS) else '👤'
        is_active = (char.id == active_char.id)
        is_dead   = (char.hp <= 0)
        ai_cfg    = ai_cfgs.get(str(i), {})
        is_ai     = ai_cfg.get('is_ai', False)
        ai_badge  = " 🤖" if is_ai else ""

        if is_dead:
            st.sidebar.markdown(
                f"<div class='dead-slot'>"
                f"<b>{flag}{ai_badge} {char.name}</b> ☠<br/>"
                f"<i style='font-size:0.85em'>{char.race} {char.char_class}</i>"
                f"</div>",
                unsafe_allow_html=True,
            )
        elif is_active:
            personality_label = ""
            if is_ai:
                p = ai_cfg.get('personality', '')
                personality_label = (
                    f" · {config.AI_PERSONALITIES.get(p, {}).get('name', p)}"
                )
            st.sidebar.markdown(
                f"<div class='active-slot'>"
                f"<b>{flag}{ai_badge} {char.name}</b>{personality_label}"
                f" <span style='color:#4caf50'>▶ ACTIVE</span><br/>"
                f"<i style='font-size:0.85em'>{char.race} {char.char_class}</i>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.sidebar.markdown(
                f"**{flag}{ai_badge} {char.name}**  *{char.race} {char.char_class}*"
            )

        if not is_dead:
            hp_pct = int(char.hp / max(char.max_hp, 1) * 100)
            mp_pct = int(char.mp / max(char.max_mp, 1) * 100)
            st.sidebar.write(f"{hp_lbl} {char.hp}/{char.max_hp}")
            st.sidebar.progress(hp_pct)
            st.sidebar.write(f"{mp_lbl} {char.mp}/{char.max_mp}")
            st.sidebar.progress(mp_pct)
            st.sidebar.caption(
                f"ATK {char.atk} · DEF {char.def_stat} · MOV {char.mov} · "
                f"{tm.get('gold_name','gold')}: {char.gold}"
            )

        if char.inventory:
            inv_names = [
                it.get('name', it) if isinstance(it, dict) else it
                for it in char.inventory
            ]
            st.sidebar.caption("Inventory: " + ", ".join(inv_names))
        st.sidebar.markdown("---")


def _render_npc_tracker(state):
    """Sidebar: NPC affinity bars, mood, and goals."""
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
            affinity, mood, goal = int(data), 'Neutral', ''

        bar = _affinity_bar(affinity)
        st.sidebar.write(f"**{name}**")
        st.sidebar.write(f"  {bar} {affinity:+d} · {mood}")
        if goal:
            st.sidebar.caption(f"  Goal: {goal}")


def _affinity_bar(affinity):
    clamped = max(-100, min(100, affinity))
    filled  = round((clamped + 100) / 200 * 10)
    return '█' * filled + '░' * (10 - filled)

# ---------------------------------------------------------------------------
# Board tab — world map, dice roller, score board
# ---------------------------------------------------------------------------

def _render_dice_result(dice_result):
    """Colour-coded dice roll banner before DM narrative (skill checks only)."""
    if dice_result is None:
        return
    outcome  = dice_result['outcome']
    icon_map = {
        'critical_success': '🟡', 'success': '🟢',
        'failure': '🔴',          'critical_failure': '💀',
    }
    icon  = icon_map.get(outcome, '🎲')
    label = outcome.replace('_', ' ').upper()
    st.info(
        f"{icon} **Dice Roll:** {dice_result['notation']} = "
        f"{dice_result['raw_roll']} + {dice_result['modifier']} "
        f"= **{dice_result['total']}** vs DC {dice_result['dc']} — **{label}**"
    )


def _render_scene_label(scene_type):
    icon  = _SCENE_ICONS.get(scene_type, '🗺️')
    label = scene_type.capitalize()
    st.caption(f"{icon} *{label} scene*")


def _render_world_map_widget(party, active_char):
    """Render the HTML world-map grid into the board tab."""
    html = build_map_html(
        world_map        = st.session_state.world_map,
        player_positions = st.session_state.player_positions,
        party            = party,
        active_char_id   = active_char.id,
        player_flags     = config.PLAYER_FLAGS,
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_manual_dice_roller():
    """Row of clickable dice buttons with large result display."""
    st.markdown("#### 🎲 手動擲骰")
    dice_types = [('d4', 4), ('d6', 6), ('d8', 8), ('d10', 10), ('d12', 12), ('d20', 20)]
    dice       = DiceRoller()
    cols       = st.columns(len(dice_types))

    for i, (d_name, d_sides) in enumerate(dice_types):
        with cols[i]:
            if st.button(f"**{d_name}**", key=f"manual_{d_name}", use_container_width=True):
                result = dice.roll(f'1{d_name}')[2]
                st.session_state.manual_dice[d_name] = result

            result  = st.session_state.manual_dice.get(d_name, '—')
            is_max  = isinstance(result, int) and result == d_sides
            is_min  = isinstance(result, int) and result == 1
            color   = '#ffd700' if is_max else ('#ff4444' if is_min else '#e0e0e0')
            st.markdown(
                f"<div style='text-align:center;font-size:2.2em;font-weight:bold;"
                f"color:{color};line-height:1.1;margin-top:4px'>{result}</div>",
                unsafe_allow_html=True,
            )
    st.caption("🟡 最大值  🔴 最小值  — 普通結果")


def _render_score_board(party, state):
    """Contribution score board: damage, healing, skill checks, gold."""
    st.markdown("#### 🏆 得分板")
    contribs = state.party_contributions or {}
    ai_cfgs  = getattr(state, 'ai_configs', None) or {}

    for i, char in enumerate(party):
        flag    = config.PLAYER_FLAGS[i] if i < len(config.PLAYER_FLAGS) else '👤'
        entry   = contribs.get(str(char.id), {})
        dmg     = entry.get('damage_dealt', 0)
        heal    = entry.get('healing_done', 0)
        chks    = entry.get('skill_checks_passed', 0)
        turns   = entry.get('turns_taken', 0)
        wt      = config.CLASS_BASE_STATS.get(char.char_class.lower(), {}).get('reward_weight', 1.0)
        score   = (dmg * 1.0 + heal * 1.5 + chks * 20) * wt
        is_ai   = ai_cfgs.get(str(i), {}).get('is_ai', False)
        ai_tag  = " 🤖" if is_ai else ""
        dead    = " ☠" if char.hp <= 0 else ""

        st.markdown(
            f"**{flag}{ai_tag} {char.name}**{dead}  "
            f"<small style='color:#aaa'>({char.char_class})</small>  `{score:.0f}pt`",
            unsafe_allow_html=True,
        )
        st.caption(f"⚔ {dmg}dmg  💚 {heal}heal  🎯 {chks}chk  ↩ {turns}t  💰 {char.gold}g")


def _render_game_board_tab(party, state, active_char, active_idx):
    """Tab 1 — 遊戲板: continent image + grid map + score board + dice roller."""
    flag    = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else '👤'
    ai_cfgs = getattr(state, 'ai_configs', None) or {}
    is_ai   = ai_cfgs.get(str(active_idx), {}).get('is_ai', False)
    ws_id   = getattr(state, 'world_setting', None) or 'dnd5e'
    ws      = config.get_world_setting(ws_id)

    # Turn indicator
    if active_char.hp > 0:
        if is_ai:
            st.info(f"🤖 {flag} **{active_char.name}** (AI) 正在行動…  "
                    f"切換至 📖 故事 頁籤查看。")
        else:
            st.info(f"{flag} **{active_char.name}** 的回合！  "
                    f"切換至 📖 故事 頁籤輸入行動。")

    # ── Continent map image ────────────────────────────────────────────────
    st.markdown("#### 🌍 大陸地圖 (Continent Map)")
    continent_img = st.session_state.get('continent_map')
    img_style     = st.session_state.get('image_style', 'fantasy_art')
    style_name    = IMAGE_STYLES.get(img_style, {}).get('name', img_style)

    if continent_img is not None:
        st.image(continent_img, caption=f"🌍 {ws['name']}  ·  風格: {style_name}",
                 use_container_width=True)
        if st.button("🔄 重新生成地圖", key="regen_map_btn"):
            st.session_state.continent_map = None
            _generate_continent_map(ws)
    else:
        gen_col, _ = st.columns([2, 3])
        if gen_col.button("🎨 生成大陸地圖", key="gen_map_btn", use_container_width=True):
            _generate_continent_map(ws)
        else:
            st.caption(
                f"點擊「生成大陸地圖」以 **{style_name}** 風格生成 {ws['name']} 的世界地圖。  "
                f"可在側欄「🎨 影像風格」更換畫風。"
            )

    st.divider()

    # ── Token grid map ─────────────────────────────────────────────────────
    col_map, col_right = st.columns([3, 1])

    with col_map:
        st.markdown("#### 🗺️ 位置追蹤地圖")
        if st.session_state.world_map:
            _render_world_map_widget(party, active_char)
            st.caption(
                "● ACTIVE = 當前行動玩家  ·  ❓ = 未探索區域  ·  "
                + "  ".join(
                    f"{config.PLAYER_FLAGS[i]} = {char.name}"
                    for i, char in enumerate(party)
                    if i < len(config.PLAYER_FLAGS)
                )
            )
        else:
            st.info("位置地圖將在遊戲開始後顯示（切換至 📖 故事 頁籤開始第一個回合）。")

    with col_right:
        _render_score_board(party, state)
        st.divider()
        _render_manual_dice_roller()

# ---------------------------------------------------------------------------
# Story tab — narrative history + branching choices + action input
# ---------------------------------------------------------------------------

def _render_story_tab(party, state, active_char, active_idx, ws_id):
    """Tab 2 — 故事: narrative + branching choices + action input."""
    ws     = config.get_world_setting(ws_id)
    tm     = ws.get('term_map', {})
    dm_lbl = tm.get('dm_title', 'DM')
    flag   = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else ''

    # World setting flavour bar
    st.markdown(
        f"<div style='background:#111;border-left:3px solid #4a3a6a;"
        f"padding:6px 10px;border-radius:4px;margin-bottom:8px;"
        f"font-size:0.85em;color:#aaa'>"
        f"📍 <b>{state.current_location}</b>  ·  "
        f"🌍 {ws['name']}  ·  🎭 {ws.get('tone','')[:60]}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Chat history
    for item in st.session_state.history:
        if item['role'] == 'player':
            actor  = item.get('actor', '')
            prefix = f"**{actor}:**" if actor else "**You:**"
            st.markdown(f"{prefix} {item['content']}")
            # Show unchosen branching choices with strikethrough
            all_ch = item.get('all_choices', [])
            if len(all_ch) > 1:
                chosen = item.get('content', '')
                parts  = []
                for c in all_ch:
                    if c == chosen:
                        parts.append(f"✅ **{c}**")
                    else:
                        parts.append(f"~~{c}~~")
                st.caption("🔀 " + "  ·  ".join(parts))
        else:
            scene_type = item.get('scene_type', 'exploration')
            if item.get('is_prologue'):
                st.markdown(
                    "<div style='background:#0a0a1a;border-left:4px solid #4a6aaa;"
                    "padding:8px 12px;border-radius:6px;margin-bottom:8px;"
                    "font-size:0.82em;color:#8898cc'>📜 開場白 · Turn 0</div>",
                    unsafe_allow_html=True,
                )
            else:
                _render_scene_label(scene_type)
            _render_dice_result(item.get('dice_result'))
            label = f"**{dm_lbl}:**" if not item.get('is_prologue') else f"**{dm_lbl} 開場白:**"
            st.markdown(f"{label} {item['content']}")
            if item.get('image'):
                if item.get('is_cinematic') and item.get('cinematic_label'):
                    st.markdown(
                        f"<div style='background:#1a0a2a;border-left:3px solid #9b59b6;"
                        f"padding:3px 10px;margin:4px 0 2px;border-radius:3px;"
                        f"font-size:0.85em;color:#c39bd3'>"
                        f"🎬 {item['cinematic_label']}</div>",
                        unsafe_allow_html=True,
                    )
                st.image(item['image'],
                         caption=item.get('cinematic_label') or "Scene",
                         use_container_width=True)

    # ---- Action input ----
    current_choices = []
    if st.session_state.history and st.session_state.history[-1]['role'] == 'dm':
        current_choices = st.session_state.history[-1].get('choices', [])

    action_taken = None

    if active_char.hp <= 0:
        st.warning(f"**{active_char.name}** 已倒下！等待下一位玩家…")

    elif current_choices:
        # Branching narrative choices — display prominently (≥3 choices, 3-col layout)
        st.markdown("---")
        st.markdown(
            f"<div style='font-size:1.1em;font-weight:bold;color:#c0c0ff;"
            f"margin-bottom:6px'>🔀 {flag} {active_char.name}，選擇你的行動:</div>",
            unsafe_allow_html=True,
        )
        n_cols      = min(max(len(current_choices), 3), 3)
        choice_cols = st.columns(n_cols)
        for idx, choice in enumerate(current_choices):
            letter = 'ABCDE'[idx] if idx < 5 else str(idx + 1)
            if choice_cols[idx % n_cols].button(
                f"**{letter}.** {choice}",
                key=f"choice_{len(st.session_state.history)}_{idx}",
                use_container_width=True,
            ):
                action_taken = choice

        with st.expander("✏️ 或自訂行動…"):
            with st.form("custom_action_form", clear_on_submit=True):
                custom = st.text_input("輸入其他行動:")
                if st.form_submit_button("執行") and custom:
                    action_taken = custom
    else:
        # Free-text action input
        with st.form("action_form", clear_on_submit=True):
            prompt_text = (
                f"{flag} {active_char.name} 的行動…" if len(party) > 1
                else "輸入你的行動…"
            )
            col_in, col_btn = st.columns([4, 1])
            action_taken = col_in.text_input(prompt_text, key="action_input")
            col_btn.write("")
            col_btn.write("")
            col_btn.form_submit_button("⚔️")

    # Process action
    if action_taken and active_char.hp > 0:
        st.session_state.history.append({
            "role":        "player",
            "actor":       f"{flag} {active_char.name}" if len(party) > 1 else "",
            "content":     action_taken,
            "all_choices": list(current_choices),   # records all branch options for strikethrough
        })
        with st.spinner(f"📖 {dm_lbl} 正在思考…"):
            response, choices, turn_data, dice_result = (
                st.session_state.event_manager.process_turn(
                    action_taken, state, active_char, party=party
                )
            )
            # Update world map if player moved
            if turn_data.get('location_change'):
                _move_player_on_map(active_char, turn_data['location_change'])

            # ── Cinematic / scene image decision ──────────────────────────
            scene_image      = None
            scene_image_path = ''
            is_cinematic     = False
            cinematic_label  = None
            img_gen          = st.session_state.img_gen
            img_style        = st.session_state.get('image_style', 'fantasy_art')
            custom_suf       = st.session_state.get('custom_img_suffix', '')

            if not img_gen.is_disabled():
                # Determine previous scene type from history for transition detection
                prev_scene = 'exploration'
                for _h in reversed(st.session_state.history):
                    if _h.get('role') == 'dm':
                        prev_scene = _h.get('scene_type', 'exploration')
                        break

                cinematic = classify_cinematic_event(
                    turn_data, prev_scene,
                    state.turn_count or 0,
                    response,
                )

                save_name  = getattr(state, 'save_name', None)
                turn_count = state.turn_count or 0

                if cinematic and img_gen.can_generate_safely():
                    # Priority: cinematic event — build tailored prompt
                    try:
                        cprompt = build_cinematic_prompt(
                            cinematic['type'], turn_data, active_char, ws,
                            img_style, custom_suf,
                        )
                        scene_image     = img_gen.generate_image(cprompt)
                        is_cinematic    = scene_image is not None
                        cinematic_label = cinematic['label'] if is_cinematic else None
                        if scene_image and save_name:
                            img_path = save_image_with_text(
                                save_name, scene_image,
                                response[:300],
                                turn_count, cinematic['type'],
                            )
                            scene_image_path = img_path or ''
                    except Exception as _e:
                        print(f"[Cinematic] {_e}")

                elif not cinematic and "look" in action_taken.lower():
                    # Non-cinematic: only generate on explicit look actions
                    if img_gen.can_generate_safely():
                        try:
                            _suf = IMAGE_STYLES.get(img_style, {}).get('suffix', '')
                            scene_prompt = (
                                f"{state.current_location}, "
                                f"{ws.get('tone','fantasy')[:60]}, "
                                f"{response[:80]}, scene illustration, {_suf}"
                            )
                            scene_image = img_gen.generate_image(scene_prompt)
                            if scene_image and save_name:
                                img_path = save_image_with_text(
                                    save_name, scene_image,
                                    response[:300],
                                    turn_count, 'scene',
                                )
                                scene_image_path = img_path or ''
                        except Exception as _e:
                            print(f"[SceneImg] {_e}")

                # Notify once when generation just got auto-disabled
                if img_gen.is_disabled():
                    st.warning(
                        "⚠️ 影像生成已自動停用（VRAM 不足）。"
                        "可在側欄「🎨 影像風格」重新啟用。"
                    )

        st.session_state.history.append({
            "role":            "dm",
            "content":         response,
            "choices":         choices,
            "scene_type":      turn_data.get('scene_type', 'exploration'),
            "dice_result":     dice_result,
            "image":           scene_image,
            "image_path":      scene_image_path,
            "is_cinematic":    is_cinematic,
            "cinematic_label": cinematic_label,
            "turn":            state.turn_count or 0,
        })

        # Persist compressed story log after every turn
        save_name = getattr(state, 'save_name', None)
        if save_name:
            save_game_log(save_name, compress_game_log(st.session_state.history))

        st.rerun()

# ---------------------------------------------------------------------------
# Characters tab — all players' detailed stats
# ---------------------------------------------------------------------------

def _render_characters_tab(party, state, active_char):
    """Tab 3 — 角色: expanded stat cards for every party member."""
    ws_id   = getattr(state, 'world_setting', None) or 'dnd5e'
    ws      = config.get_world_setting(ws_id)
    tm      = ws.get('term_map', {})
    ai_cfgs = getattr(state, 'ai_configs', None) or {}

    st.subheader("👥 隊伍成員 — 詳細數值")

    class_icons = {'warrior': '⚔️', 'mage': '🔮', 'rogue': '🗡️', 'cleric': '✨'}

    for i, char in enumerate(party):
        flag      = config.PLAYER_FLAGS[i] if i < len(config.PLAYER_FLAGS) else '👤'
        is_active = (char.id == active_char.id)
        is_dead   = (char.hp <= 0)
        ai_cfg    = ai_cfgs.get(str(i), {})
        is_ai     = ai_cfg.get('is_ai', False)

        dead_tag   = " ☠ 已倒下"   if is_dead   else ""
        active_tag = " ◀ 行動中"   if is_active else ""
        if is_ai:
            p = ai_cfg.get('personality', '')
            d = ai_cfg.get('difficulty', '')
            pn = config.AI_PERSONALITIES.get(p, {}).get('name', p)
            dn = config.AI_DIFFICULTIES.get(d, {}).get('name', d)
            ai_tag = f" 🤖 [{pn} · {dn}]"
        else:
            ai_tag = ""

        cls_icon = class_icons.get(char.char_class.lower(), '👤')
        header   = f"{flag} {cls_icon} {char.name} — {char.race} {char.char_class}{ai_tag}{active_tag}{dead_tag}"

        with st.expander(header, expanded=is_active):
            # ── Portrait ────────────────────────────────────────────────────
            portrait = st.session_state.portraits.get(char.id)
            por_col, stat_col = st.columns([1, 3])
            with por_col:
                if portrait is not None:
                    st.image(portrait, caption=char.name, use_container_width=True)
                    if st.button("🔄", key=f"regen_por_{char.id}",
                                 help=f"重新生成 {char.name} 肖像"):
                        del st.session_state.portraits[char.id]
                        _generate_portrait(char, ws)
                else:
                    st.markdown(
                        "<div style='background:#0d1117;border:1px dashed #333;"
                        "height:150px;display:flex;align-items:center;"
                        "justify-content:center;border-radius:6px;"
                        "color:#555;font-size:0.8em'>🖼️ 尚未生成</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"🎨 生成肖像", key=f"gen_por_{char.id}",
                                 use_container_width=True):
                        _generate_portrait(char, ws)

            with stat_col:
                c1, c2, c3 = st.columns(3)

                with c1:
                    hp_pct = char.hp / max(char.max_hp, 1)
                    hp_clr = '#4caf50' if hp_pct > 0.6 else ('#ff9800' if hp_pct > 0.3 else '#f44336')
                    st.markdown(f"**{tm.get('hp_name','HP')}**")
                    st.markdown(
                        f"<div style='font-size:1.6em;color:{hp_clr};font-weight:bold'>"
                        f"{char.hp} / {char.max_hp}</div>",
                        unsafe_allow_html=True,
                    )
                    st.progress(int(hp_pct * 100))

                    mp_pct = char.mp / max(char.max_mp, 1)
                    st.markdown(f"**{tm.get('mp_name','MP')}**")
                    st.markdown(
                        f"<div style='font-size:1.4em;color:#6a9bff'>"
                        f"{char.mp} / {char.max_mp}</div>",
                        unsafe_allow_html=True,
                    )
                    st.progress(int(mp_pct * 100))

                with c2:
                    for label, val in [
                        ('ATK', char.atk), ('DEF', char.def_stat),
                        ('MOV', char.mov), (tm.get('gold_name', 'Gold'), char.gold),
                    ]:
                        st.metric(label, val)

                with c3:
                    if char.skills:
                        st.markdown("**技能:**")
                        for skill in char.skills:
                            st.write(f"  • {skill}")
                    if char.inventory:
                        st.markdown("**背包:**")
                        for item in char.inventory:
                            nm = item.get('name', item) if isinstance(item, dict) else item
                            st.write(f"  • {nm}")
                    if char.appearance:
                        st.caption(f"外觀: {char.appearance}")
                    if char.personality:
                        st.caption(f"性格: {char.personality}")

# ---------------------------------------------------------------------------
# Rules tab — full world-aware player handbook with chapter navigation + search
# ---------------------------------------------------------------------------

def _render_rules_tab(state):
    """Tab 4 — 📜 規則: world-aware player handbook with search and chapter nav."""
    from engine.manual import build_manual_chapters

    ws_id = getattr(state, 'world_setting', None) or 'dnd5e'
    ws    = config.get_world_setting(ws_id)
    chapters = build_manual_chapters(ws)

    # ── Keyword search ──────────────────────────────────────────────────────
    search = st.text_input(
        "🔍 搜尋手冊關鍵字",
        key="manual_search",
        placeholder="輸入關鍵字，例如：attack、骰子、stealth…",
    )

    if search.strip():
        query = search.lower()
        matched = [
            (i, ch) for i, ch in enumerate(chapters)
            if query in ch['content'].lower()
            or query in ch['title'].lower()
            or any(query in t for t in ch.get('tags', []))
        ]
        if matched:
            st.success(f"找到 **{len(matched)}** 個章節包含「{search}」")
            for _, ch in matched:
                with st.expander(f"{ch['icon']} {ch['title']}", expanded=True):
                    st.markdown(ch['content'])
        else:
            st.warning(f"未找到包含「{search}」的章節。請嘗試其他關鍵字。")
        return

    # ── Chapter navigation ──────────────────────────────────────────────────
    if 'manual_chapter_idx' not in st.session_state:
        st.session_state.manual_chapter_idx = 0

    idx = min(st.session_state.manual_chapter_idx, len(chapters) - 1)
    n   = len(chapters)
    chapter_labels = [f"{ch['icon']} {ch['title']}" for ch in chapters]

    # Nav bar: ◀  chapter selector  ▶
    col_prev, col_sel, col_next = st.columns([1, 7, 1])
    with col_prev:
        if st.button("◀", key="manual_prev", disabled=(idx == 0),
                     use_container_width=True):
            st.session_state.manual_chapter_idx = idx - 1
            st.rerun()
    with col_sel:
        sel = st.selectbox(
            "章節",
            range(n),
            index=idx,
            format_func=lambda i: chapter_labels[i],
            key="manual_chapter_sel",
            label_visibility="collapsed",
        )
        if sel != idx:
            st.session_state.manual_chapter_idx = sel
            st.rerun()
    with col_next:
        if st.button("▶", key="manual_next", disabled=(idx == n - 1),
                     use_container_width=True):
            st.session_state.manual_chapter_idx = idx + 1
            st.rerun()

    # Progress dots  ● ○ ○ ...
    dots = ''.join('● ' if i == idx else '○ ' for i in range(n))
    st.caption(f"第 {idx + 1} / {n} 章　{dots.strip()}")
    st.divider()

    # Chapter content
    ch = chapters[idx]
    st.markdown(ch['content'])

    st.divider()

    # ── Table of Contents (collapsible) ────────────────────────────────────
    with st.expander("📋 目錄 — 點擊快速跳章", expanded=False):
        toc_cols = st.columns(2)
        for i, c in enumerate(chapters):
            btn_label = f"{c['icon']} {c['title']}"
            active    = "**" if i == idx else ""
            if toc_cols[i % 2].button(
                f"{active}{btn_label}{active}",
                key=f"toc_btn_{i}",
                use_container_width=True,
            ):
                st.session_state.manual_chapter_idx = i
                st.rerun()

# ---------------------------------------------------------------------------
# Book Mode tab — page-flip reader for saved story records
# ---------------------------------------------------------------------------

def _book_page_image(page):
    """Try to load the PIL Image for a page from its image_path. Returns None if unavailable."""
    img_path = page.get('image_path', '')
    if img_path and os.path.exists(img_path):
        try:
            from PIL import Image as _PILImage
            return _PILImage.open(img_path)
        except Exception:
            pass
    return None


def _book_render_page_content(page, container=None):
    """Render image + action + narrative for a single page into a container (or st)."""
    ctx = container if container is not None else st
    scene_icons = {
        'combat': '⚔️', 'social': '💬',
        'exploration': '🗺️', 'puzzle': '🧩', 'rest': '🏕️',
    }
    scene_icon = scene_icons.get(page.get('scene_type', 'exploration'), '🗺️')

    # Cinematic label + image
    pil_img = _book_page_image(page)
    if pil_img:
        if page.get('label'):
            ctx.markdown(
                f"<div style='background:#1a0a2a;border-left:3px solid #9b59b6;"
                f"padding:3px 10px;margin-bottom:4px;border-radius:3px;"
                f"font-size:0.85em;color:#c39bd3'>🎬 {page['label']}</div>",
                unsafe_allow_html=True,
            )
        ctx.image(pil_img,
                  caption=page.get('label') or f"Turn {page['turn']}",
                  use_container_width=True)

    # Prologue badge
    if page.get('is_prologue'):
        ctx.markdown(
            "<div style='background:#0a0a1a;border-left:4px solid #4a6aaa;"
            "padding:4px 10px;border-radius:4px;margin-bottom:6px;"
            "font-size:0.78em;color:#8898cc'>📜 開場白 · Turn 0</div>",
            unsafe_allow_html=True,
        )

    # Player action + unchosen choices with strikethrough
    actor  = page.get('actor', '')
    action = page.get('action', '')
    if actor or action:
        actor_str = f"**{actor}:** " if actor else ""
        ctx.markdown(
            f"<div class='book-action'>🗣 {actor_str}{action}</div>",
            unsafe_allow_html=True,
        )
        all_ch = page.get('all_choices', [])
        if len(all_ch) > 1:
            parts = []
            for c in all_ch:
                parts.append(f"✅ **{c}**" if c == action else f"~~{c}~~")
            ctx.caption("🔀 " + "  ·  ".join(parts))

    # Narrative body
    ctx.markdown(
        f"<div class='book-page'>"
        f"<div class='book-narrative'>{page.get('narrative','')}</div>"
        f"<div class='book-scene'>{scene_icon} "
        f"{page.get('scene_type','exploration').capitalize()} · Turn {page.get('turn',0)}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_book_tab(save_name):
    """Tab 5 — 📕 書本: page-flip reader for the saved story log."""
    pages = load_story_log(save_name) if save_name else []

    # Book CSS
    st.markdown(
        "<style>"
        ".book-page{background:#0d0d1a;border:1px solid #2a2a4a;"
        " border-radius:8px;padding:18px 22px;margin:8px 0;"
        " box-shadow:0 2px 12px #000a;}"
        ".book-action{font-size:0.82em;color:#8888bb;font-style:italic;"
        " margin-bottom:6px;}"
        ".book-narrative{font-size:0.96em;color:#d0d0e8;line-height:1.7;}"
        ".book-label{font-size:0.78em;color:#9b59b6;margin-top:6px;}"
        ".book-scene{font-size:0.75em;color:#5a5a7a;margin-top:2px;}"
        ".book-latest{border:1px solid #3a2a5a;border-radius:8px;"
        " padding:12px 16px;margin:6px 0;background:#0e0a1a;}"
        "</style>",
        unsafe_allow_html=True,
    )

    if not pages:
        st.info(
            "📕 尚無故事紀錄。  \n"
            "遊戲進行後，每回合會自動儲存故事與圖片，在此閱讀完整冒險記錄。"
        )
        return

    n = len(pages)
    st.caption(f"📕 共 **{n}** 頁故事記錄  ·  存檔：`{save_name}`")

    # ── 📌 最新記錄 — always show last 2 pages at top ──────────────────────
    with st.expander("📌 最新記錄（最近 2 頁）", expanded=True):
        recent_pages = pages[-2:]
        if len(recent_pages) == 2:
            col_a, col_b = st.columns(2)
            with col_a:
                st.caption(f"第 {n-1} 頁 · Turn {recent_pages[0]['turn']}")
                _book_render_page_content(recent_pages[0])
            with col_b:
                st.caption(f"第 {n} 頁 · Turn {recent_pages[1]['turn']}")
                _book_render_page_content(recent_pages[1])
        else:
            # Only 1 page so far
            st.caption(f"第 {n} 頁 · Turn {recent_pages[0]['turn']}")
            _book_render_page_content(recent_pages[0])

    st.divider()
    st.markdown("#### 📖 翻頁閱讀")

    # Page index state (default to last page)
    if 'book_page_idx' not in st.session_state:
        st.session_state.book_page_idx = n - 1
    idx = max(0, min(st.session_state.book_page_idx, n - 1))

    # Navigation bar
    col_first, col_prev, col_mid, col_next, col_last = st.columns([1, 1, 4, 1, 1])
    with col_first:
        if st.button("⏮", key="book_first", disabled=(idx == 0),
                     use_container_width=True):
            st.session_state.book_page_idx = 0
            st.rerun()
    with col_prev:
        if st.button("◀", key="book_prev", disabled=(idx == 0),
                     use_container_width=True):
            st.session_state.book_page_idx = idx - 1
            st.rerun()
    with col_mid:
        sel = st.selectbox(
            "頁碼",
            range(n),
            index=idx,
            format_func=lambda i: (
                f"★ 第 {i+1} 頁 (Turn {pages[i]['turn']})"
                if i >= n - 2 else
                f"第 {i+1} 頁  (Turn {pages[i]['turn']})"
            ),
            key="book_page_sel",
            label_visibility="collapsed",
        )
        if sel != idx:
            st.session_state.book_page_idx = sel
            st.rerun()
    with col_next:
        if st.button("▶", key="book_next", disabled=(idx == n - 1),
                     use_container_width=True):
            st.session_state.book_page_idx = idx + 1
            st.rerun()
    with col_last:
        if st.button("⏭", key="book_last", disabled=(idx == n - 1),
                     use_container_width=True):
            st.session_state.book_page_idx = n - 1
            st.rerun()

    # "Latest" badge for last 2 pages
    is_latest = idx >= n - 2
    if is_latest:
        st.markdown(
            "<div style='background:#1a0a2a;border-left:3px solid #9b59b6;"
            "padding:3px 10px;margin:4px 0;border-radius:3px;"
            "font-size:0.82em;color:#c39bd3'>📌 最新記錄</div>",
            unsafe_allow_html=True,
        )

    _book_render_page_content(pages[idx])

    # Progress dots
    dots_per_row = 20
    dot_rows = []
    for start in range(0, n, dots_per_row):
        chunk = range(start, min(start + dots_per_row, n))
        # mark last-2 with ★, current with ●, rest with ○
        dot_rows.append(
            ''.join(
                '● ' if i == idx else ('★ ' if i >= n - 2 else '○ ')
                for i in chunk
            ).strip()
        )
    st.caption("  \n".join(dot_rows) + f"  　第 {idx+1} / {n} 頁  ★=最新")


# ---------------------------------------------------------------------------
# Image style sidebar switcher
# ---------------------------------------------------------------------------

def _render_image_style_switcher():
    """Sidebar expander: switch image art style and regenerate map/portraits."""
    with st.sidebar.expander("🎨 影像風格", expanded=False):
        style_keys   = list(IMAGE_STYLES.keys())
        style_labels = [
            f"{IMAGE_STYLES[k]['name']} ({IMAGE_STYLES[k]['name_en']})"
            for k in style_keys
        ]
        try:
            cur_idx = style_keys.index(st.session_state.get('image_style', 'fantasy_art'))
        except ValueError:
            cur_idx = 0

        new_idx = st.selectbox(
            "風格",
            range(len(style_keys)),
            index=cur_idx,
            format_func=lambda i: style_labels[i],
            key="sidebar_img_style",
        )
        new_custom = st.text_input(
            "自定義後綴",
            value=st.session_state.get('custom_img_suffix', ''),
            key="sidebar_custom_img",
            placeholder="oil painting, baroque…",
        )

        style_changed  = (new_idx != cur_idx)
        custom_changed = (new_custom != st.session_state.get('custom_img_suffix', ''))
        if style_changed or custom_changed:
            st.session_state.image_style       = style_keys[new_idx]
            st.session_state.custom_img_suffix = new_custom

        if st.button("🔄 重新生成所有影像", use_container_width=True,
                     key="sidebar_regen_images"):
            st.session_state.continent_map = None
            st.session_state.portraits     = {}
            st.rerun()

        cur_style = IMAGE_STYLES.get(st.session_state.get('image_style', 'fantasy_art'), {})
        st.caption(f"當前: **{cur_style.get('name','')}** — {cur_style.get('name_en','')}")

        # VRAM status
        img_gen = st.session_state.get('img_gen')
        if img_gen:
            if img_gen.is_disabled():
                st.error("⚠️ 影像生成已自動停用（連續 VRAM 不足）")
                if st.button("🔄 重新啟用", key="reenable_img_gen",
                             use_container_width=True):
                    img_gen.reset_disabled()
                    st.rerun()
            elif not img_gen.can_generate_safely():
                st.warning("⚡ VRAM 可能不足，部分場景圖可能跳過生成")
            else:
                st.success("✅ 影像生成就緒")


# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------

def _generate_continent_map(ws):
    """Generate and cache the continent map for the current world setting."""
    prompt = build_map_prompt(
        ws,
        st.session_state.get('image_style', 'fantasy_art'),
        st.session_state.get('custom_img_suffix', ''),
    )
    with st.spinner(f"🎨 繪製 {ws['name']} 大陸地圖…"):
        img = st.session_state.img_gen.generate_image(prompt)
    if img:
        st.session_state.continent_map = img
        state     = st.session_state.get('game_state')
        save_name = getattr(state, 'save_name', None)
        if save_name:
            save_image_with_text(save_name, img, ws.get('name', ''), 0, 'map')
        st.rerun()
    else:
        st.warning("影像生成失敗 (Strategy A 或 GPU 未就緒)")


def _generate_portrait(char, ws):
    """Generate and cache a portrait for a single character."""
    prompt = build_portrait_prompt(
        char, ws,
        st.session_state.get('image_style', 'fantasy_art'),
        st.session_state.get('custom_img_suffix', ''),
    )
    with st.spinner(f"🎨 繪製 {char.name} 肖像…"):
        img = st.session_state.img_gen.generate_image(prompt)
    if img:
        st.session_state.portraits[char.id] = img
        state     = st.session_state.get('game_state')
        save_name = getattr(state, 'save_name', None)
        if save_name:
            safe_name = ''.join(c if c.isalnum() else '_' for c in char.name)
            save_image_with_text(
                save_name, img, char.appearance or char.name,
                0, f"portrait_{safe_name}",
            )
        st.rerun()
    else:
        st.warning(f"{char.name} 肖像生成失敗")


# ---------------------------------------------------------------------------
# Main game loop (tabbed layout)
# ---------------------------------------------------------------------------

def game_loop():
    party  = st.session_state.party or [st.session_state.player]
    state  = st.session_state.game_state

    # Determine active player
    active_idx  = (state.active_player_index or 0) % max(len(party), 1)
    active_char = party[active_idx]
    st.session_state.player = active_char  # backward-compat

    # Ensure board positions exist for all party members
    _init_board_state(party, state)

    # ---- Sidebar ----
    _render_party_sidebar(party, state, active_char)
    st.sidebar.write(
        f"**Turn:** {state.turn_count or 0}  "
        f"*(memory: last {config.SESSION_MEMORY_WINDOW})*"
    )
    _render_npc_tracker(state)
    _render_model_switcher()
    _render_image_style_switcher()

    if st.sidebar.button("Save & Quit"):
        st.session_state.current_session.commit()
        st.session_state.current_session.close()
        for key in ('current_session', 'game_state', 'player', 'event_manager'):
            st.session_state[key] = None
        st.session_state.party             = []
        st.session_state.history           = []
        st.session_state.world_map         = {}
        st.session_state.player_positions  = {}
        st.session_state.manual_dice       = {}
        st.session_state.continent_map     = None
        st.session_state.portraits         = {}
        st.rerun()

    # ---- Header ----
    ws_id = getattr(state, 'world_setting', None) or 'dnd5e'
    ws    = config.get_world_setting(ws_id)
    st.title(f"🌍 {ws['name']}")

    active_preset = next(
        (p for p in config.MODEL_PRESETS if p['id'] == st.session_state.active_model_id),
        None,
    )
    badge_parts = [f"🌍 **{ws['name']}**"]
    if active_preset:
        badge_parts.append(f"🤖 {active_preset['name']}")
    if len(party) > 1:
        ai_cfgs_h  = getattr(state, 'ai_configs', None) or {}
        ai_count   = sum(1 for i in range(len(party))
                         if ai_cfgs_h.get(str(i), {}).get('is_ai', False))
        human_cnt  = len(party) - ai_count
        party_badge = f"👥 {human_cnt}H"
        if ai_count:
            party_badge += f"+{ai_count}🤖"
        badge_parts.append(party_badge)
    st.caption("  ·  ".join(badge_parts))

    # ---- Generate prologue on Turn 0 ----
    if (state.turn_count or 0) == 0 and not st.session_state.history:
        ws_id_p = getattr(state, 'world_setting', None) or 'dnd5e'
        tm_p    = config.get_world_setting(ws_id_p).get('term_map', {})
        dm_lbl_p = tm_p.get('dm_title', 'GM')
        with st.spinner(f"📖 {dm_lbl_p} 正在書寫開場白…"):
            pro_narrative, pro_choices, pro_data = (
                st.session_state.event_manager.generate_prologue(state, party)
            )
        st.session_state.history.append({
            "role":            "dm",
            "content":         pro_narrative,
            "choices":         pro_choices,
            "scene_type":      pro_data.get('scene_type', 'exploration'),
            "dice_result":     None,
            "image":           None,
            "image_path":      '',
            "is_cinematic":    False,
            "cinematic_label": None,
            "turn":            0,
            "is_prologue":     True,
        })
        save_name_p = getattr(state, 'save_name', None)
        if save_name_p:
            save_game_log(save_name_p, compress_game_log(st.session_state.history))
        st.rerun()

    # ---- Auto-run AI turns (before tab rendering) ----
    ai_cfgs   = getattr(state, 'ai_configs', None) or {}
    active_ai = ai_cfgs.get(str(active_idx), {})
    if active_ai.get('is_ai', False) and active_char.hp > 0:
        flag        = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else '🤖'
        personality = active_ai.get('personality', 'tactical')
        p_name      = config.AI_PERSONALITIES.get(personality, {}).get('name', personality.title())
        with st.spinner(f"🤖 {flag} {active_char.name} ({p_name}) is deciding…"):
            action_text, response, choices, turn_data, dice_result = (
                st.session_state.event_manager.run_ai_turn(state, party)
            )
        if turn_data.get('location_change'):
            _move_player_on_map(active_char, turn_data['location_change'])
        st.session_state.history.append({
            "role":       "player",
            "actor":      f"{flag} 🤖 {active_char.name}",
            "content":    action_text,
            "all_choices": [],
        })
        st.session_state.history.append({
            "role":        "dm",
            "content":     response,
            "choices":     choices,
            "scene_type":  turn_data.get('scene_type', 'exploration'),
            "dice_result": dice_result,
            "image":       None,
            "image_path":  '',
        })
        st.rerun()

    # ---- Tabs ----
    tab_board, tab_story, tab_chars, tab_rules, tab_book = st.tabs(
        ["🗺️ 遊戲板", "📖 故事", "👥 角色", "📜 規則", "📕 書本"]
    )

    with tab_board:
        _render_game_board_tab(party, state, active_char, active_idx)

    with tab_story:
        _render_story_tab(party, state, active_char, active_idx, ws_id)

    with tab_chars:
        _render_characters_tab(party, state, active_char)

    with tab_rules:
        _render_rules_tab(state)

    with tab_book:
        _render_book_tab(getattr(state, 'save_name', None))

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

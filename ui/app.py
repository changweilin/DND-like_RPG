import streamlit as st
import os
import datetime

from engine.save_load import SaveLoadManager
from engine.config import config
from engine.dice import DiceRoller
from engine.board import (
    assign_map_position, detect_location_type, build_map_html,
    MAP_ROWS, MAP_COLS,
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
            difficulty = st.selectbox("Difficulty", ["Easy", "Normal", "Hard"])
            language   = st.selectbox("Language", ["English", "繁體中文", "日本語", "Español"])

            st.markdown("**World Setting**")
            ws_labels = [f"[{ws['category']}] {ws['name']}" for ws in config.WORLD_SETTINGS]
            ws_ids    = [ws['id'] for ws in config.WORLD_SETTINGS]
            ws_idx    = st.selectbox(
                "Universe", range(len(config.WORLD_SETTINGS)),
                format_func=lambda i: ws_labels[i],
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
            st.markdown("**Party (1-6 players)**")
            num_players = st.selectbox(
                "Number of players", list(range(1, 7)), key="new_game_num_players"
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
                        st.success(
                            f"Party [{names}]{suffix} created in **{ws['name']}**! Load it to play."
                        )
                    else:
                        st.error(f"Save name '{save_name}' already exists.")

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
            if st.button("Load"):
                selected_save = save_names[selected_idx]
                party, game_state, session = st.session_state.save_manager.load_game(selected_save)
                if party and game_state and session:
                    active_idx  = game_state.active_player_index or 0
                    active_char = party[active_idx % len(party)]
                    st.session_state.current_session = session
                    st.session_state.game_state      = game_state
                    st.session_state.party           = party
                    st.session_state.player          = active_char
                    st.session_state.history         = []
                    st.session_state.event_manager   = EventManager(
                        st.session_state.llm, st.session_state.rag, session
                    )
                    # Reset board state for fresh session
                    st.session_state.world_map        = {}
                    st.session_state.player_positions = {}
                    st.session_state.manual_dice      = {}
                    names = ", ".join(c.name for c in party)
                    st.success(f"Loaded party [{names}]!")
                    st.rerun()
                else:
                    st.error("Failed to load save file.")

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
    """Tab 1 — 遊戲板: world map + score board + manual dice roller."""
    flag    = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else '👤'
    ai_cfgs = getattr(state, 'ai_configs', None) or {}
    is_ai   = ai_cfgs.get(str(active_idx), {}).get('is_ai', False)

    # Turn indicator
    if active_char.hp > 0:
        if is_ai:
            st.info(f"🤖 {flag} **{active_char.name}** (AI) 正在行動…  "
                    f"切換至 📖 故事 頁籤查看。")
        else:
            st.info(f"{flag} **{active_char.name}** 的回合！  "
                    f"切換至 📖 故事 頁籤輸入行動。")

    col_map, col_right = st.columns([3, 1])

    with col_map:
        st.markdown("#### 🗺️ 世界地圖")
        if st.session_state.world_map:
            _render_world_map_widget(party, active_char)
            st.caption(
                "◀ ACTIVE = 當前行動玩家  ·  ❓ = 未探索區域  ·  "
                + "  ".join(
                    f"{config.PLAYER_FLAGS[i]} = {char.name}"
                    for i, char in enumerate(party)
                    if i < len(config.PLAYER_FLAGS)
                )
            )
        else:
            st.info("地圖將在遊戲開始後顯示（切換至 📖 故事 頁籤開始第一個回合）。")

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
        else:
            scene_type = item.get('scene_type', 'exploration')
            _render_scene_label(scene_type)
            _render_dice_result(item.get('dice_result'))
            st.markdown(f"**{dm_lbl}:** {item['content']}")
            if item.get('image'):
                st.image(item['image'], caption="Scene visualization")

    # ---- Action input ----
    current_choices = []
    if st.session_state.history and st.session_state.history[-1]['role'] == 'dm':
        current_choices = st.session_state.history[-1].get('choices', [])

    action_taken = None

    if active_char.hp <= 0:
        st.warning(f"**{active_char.name}** 已倒下！等待下一位玩家…")

    elif current_choices:
        # Branching narrative choices — display prominently
        st.markdown("---")
        st.markdown(
            f"<div style='font-size:1.1em;font-weight:bold;color:#c0c0ff;"
            f"margin-bottom:6px'>🔀 {flag} {active_char.name}，選擇你的行動:</div>",
            unsafe_allow_html=True,
        )
        n_cols     = min(len(current_choices), 2)
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
            "role":    "player",
            "actor":   f"{flag} {active_char.name}" if len(party) > 1 else "",
            "content": action_taken,
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

            scene_image = None
            if "look" in action_taken.lower() or len(st.session_state.history) % 6 == 0:
                try:
                    scene_image = st.session_state.img_gen.generate_image(
                        f"A fantasy scene. {state.current_location}. {response[:100]}"
                    )
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

    if st.sidebar.button("Save & Quit"):
        st.session_state.current_session.commit()
        st.session_state.current_session.close()
        for key in ('current_session', 'game_state', 'player', 'event_manager'):
            st.session_state[key] = None
        st.session_state.party           = []
        st.session_state.history         = []
        st.session_state.world_map        = {}
        st.session_state.player_positions = {}
        st.session_state.manual_dice      = {}
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
            "role":    "player",
            "actor":   f"{flag} 🤖 {active_char.name}",
            "content": action_text,
        })
        st.session_state.history.append({
            "role":        "dm",
            "content":     response,
            "choices":     choices,
            "scene_type":  turn_data.get('scene_type', 'exploration'),
            "dice_result": dice_result,
            "image":       None,
        })
        st.rerun()

    # ---- Tabs ----
    tab_board, tab_story, tab_chars, tab_rules = st.tabs(
        ["🗺️ 遊戲板", "📖 故事", "👥 角色", "📜 規則"]
    )

    with tab_board:
        _render_game_board_tab(party, state, active_char, active_idx)

    with tab_story:
        _render_story_tab(party, state, active_char, active_idx, ws_id)

    with tab_chars:
        _render_characters_tab(party, state, active_char)

    with tab_rules:
        _render_rules_tab(state)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

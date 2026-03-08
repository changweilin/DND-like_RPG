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
    st.session_state.player          = None   # active character (backward compat)
    st.session_state.party           = []     # list[Character] — all party members
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

def _render_world_selector(form_key_prefix=""):
    """World setting selectbox + info card. Returns (world_id, world_lore_override)."""
    ws_labels = [
        f"[{ws['category']}] {ws['name']}" for ws in config.WORLD_SETTINGS
    ]
    ws_ids = [ws['id'] for ws in config.WORLD_SETTINGS]

    selected_ws_idx = st.selectbox(
        "World Setting",
        range(len(config.WORLD_SETTINGS)),
        format_func=lambda i: ws_labels[i],
        key=f"{form_key_prefix}world_setting_select",
    )
    ws = config.WORLD_SETTINGS[selected_ws_idx]

    # World info card
    with st.expander(f"ℹ️ About: {ws['name']}", expanded=False):
        st.caption(ws.get('tone', ''))
        tm = ws.get('term_map', {})
        st.markdown(
            f"| Stat | Term |\n|---|---|\n"
            f"| HP | **{tm.get('hp_name', 'HP')}** |\n"
            f"| MP / Magic resource | **{tm.get('mp_name', 'MP')}** |\n"
            f"| Currency | **{tm.get('gold_name', 'gold')}** |\n"
            f"| Fighter | **{tm.get('warrior_class', 'Warrior')}** |\n"
            f"| Mage | **{tm.get('mage_class', 'Mage')}** |\n"
            f"| Rogue | **{tm.get('rogue_class', 'Rogue')}** |\n"
            f"| Healer | **{tm.get('cleric_class', 'Cleric')}** |\n"
            f"| GM title | **{tm.get('dm_title', 'Game Master')}** |"
        )
        st.caption(f"Starting location: *{ws['starting_location']}*")

    # Optional custom world lore override
    custom_lore = st.text_area(
        "Custom World Lore (optional — leave blank to use setting default)",
        placeholder=ws.get('world_lore', '')[:200] + "...",
        key=f"{form_key_prefix}world_lore",
        height=80,
    )
    return ws['id'], custom_lore


def _player_config_fields(idx, key_prefix):
    """
    Render config fields for one party member.

    Slot 0 is always human (party leader). Slots 1-5 may be AI-controlled.
    Returns (name, race, char_class, appearance, personality,
             is_ai, ai_personality, ai_difficulty).
    """
    flag  = config.PLAYER_FLAGS[idx] if idx < len(config.PLAYER_FLAGS) else '👤'
    label = f"{flag} Player 1 (Party Leader)" if idx == 0 else f"{flag} Player {idx + 1}"
    st.markdown(f"**{label}**")

    # AI toggle — only for slots 1+; party leader is always human
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
        # AI config: personality + difficulty selectors
        ai_cols = st.columns(2)
        personalities      = list(config.AI_PERSONALITIES.keys())
        personality_labels = [config.AI_PERSONALITIES[p]['name'] for p in personalities]
        ai_p_idx = ai_cols[0].selectbox(
            "AI Personality",
            range(len(personalities)),
            format_func=lambda i: personality_labels[i],
            key=f"{key_prefix}_ai_pers_{idx}",
        )
        ai_personality = personalities[ai_p_idx]

        difficulties      = list(config.AI_DIFFICULTIES.keys())
        difficulty_labels = [config.AI_DIFFICULTIES[d]['name'] for d in difficulties]
        ai_d_idx = ai_cols[1].selectbox(
            "AI Difficulty",
            range(len(difficulties)),
            format_func=lambda i: difficulty_labels[i],
            key=f"{key_prefix}_ai_diff_{idx}",
        )
        ai_difficulty = difficulties[ai_d_idx]

        p_desc = config.AI_PERSONALITIES.get(ai_personality, {}).get('description', '')
        d_desc = config.AI_DIFFICULTIES.get(ai_difficulty, {}).get('description', '')
        st.caption(f"🧠 {p_desc}  ·  ⚡ {d_desc}")
        appearance  = ""
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

            # World setting selector
            st.markdown("**World Setting**")
            ws_labels = [f"[{ws['category']}] {ws['name']}" for ws in config.WORLD_SETTINGS]
            ws_ids    = [ws['id'] for ws in config.WORLD_SETTINGS]
            ws_idx    = st.selectbox(
                "Universe",
                range(len(config.WORLD_SETTINGS)),
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

            # Party size selector (1-6 players; unfilled slots can be AI)
            st.markdown("---")
            st.markdown("**Party (1-6 players)**")
            num_players = st.selectbox(
                "Number of players", list(range(1, 7)), key="new_game_num_players"
            )

            # Per-player config fields
            player_fields = []
            for i in range(num_players):
                player_fields.append(
                    _player_config_fields(i, key_prefix="ng")
                )
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
                            'name':           name or f'Adventurer {len(extra)+2}',
                            'race':           race,
                            'char_class':     char_class,
                            'appearance':     app,
                            'personality':    per,
                            'is_ai':          is_ai,
                            'ai_personality': ai_pers,
                            'ai_difficulty':  ai_diff,
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
                        names = ", ".join(c.name for c in party)
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
            save_labels  = [
                f"{s['save_name']} — {s['location']} "
                f"({s['party_size']}p · turn {s['turns']})"
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
                    active_idx = game_state.active_player_index or 0
                    active_char = party[active_idx % len(party)]
                    st.session_state.current_session = session
                    st.session_state.game_state      = game_state
                    st.session_state.party           = party
                    st.session_state.player          = active_char
                    st.session_state.history         = []
                    st.session_state.event_manager   = EventManager(
                        st.session_state.llm, st.session_state.rag, session
                    )
                    names = ", ".join(c.name for c in party)
                    st.success(f"Loaded party [{names}]!")
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
# Game Loop helpers — party sidebar
# ---------------------------------------------------------------------------

def _render_party_sidebar(party, state, active_char):
    """Sidebar: compact card per party member with flag, AI badge, and active highlight."""
    ws_id  = getattr(state, 'world_setting', None) or 'dnd5e'
    tm     = config.get_world_setting(ws_id)['term_map']
    hp_lbl = tm.get('hp_name', 'HP')
    mp_lbl = tm.get('mp_name', 'MP')
    ai_cfgs = getattr(state, 'ai_configs', None) or {}

    st.sidebar.title("Party")

    # CSS for the flashing active-player highlight (injected once per render)
    st.sidebar.markdown(
        "<style>"
        "@keyframes pulse-active{"
        "  0%,100%{background:#1a4a1a;}"
        "  50%{background:#2d6b2d;}"
        "}"
        ".active-slot{animation:pulse-active 1.8s ease-in-out infinite;"
        "  border-left:4px solid #4caf50;padding:4px 6px;border-radius:4px;margin:2px 0;}"
        ".dead-slot{border-left:4px solid #8b0000;padding:4px 6px;border-radius:4px;margin:2px 0;"
        "  background:#2a0a0a;}"
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
                personality_label = f" · {config.AI_PERSONALITIES.get(p, {}).get('name', p)}"
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
                f"**{flag}{ai_badge} {char.name}**  "
                f"*{char.race} {char.char_class}*"
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


# ---------------------------------------------------------------------------
# Game Loop
# ---------------------------------------------------------------------------

def game_loop():
    party  = st.session_state.party or [st.session_state.player]
    state  = st.session_state.game_state

    # Determine active player from state
    active_idx  = (state.active_player_index or 0) % max(len(party), 1)
    active_char = party[active_idx]
    # Keep backward-compat key in sync
    st.session_state.player = active_char

    # --- Sidebar: party sheet ---
    _render_party_sidebar(party, state, active_char)

    st.sidebar.write(f"**Turn:** {state.turn_count or 0}  "
                     f"*(memory: last {config.SESSION_MEMORY_WINDOW})*")

    # NPC / faction tracker
    _render_npc_tracker(state)

    # Model switcher
    _render_model_switcher()

    if st.sidebar.button("Save & Quit"):
        st.session_state.current_session.commit()
        st.session_state.current_session.close()
        st.session_state.current_session = None
        st.session_state.game_state      = None
        st.session_state.player          = None
        st.session_state.party           = []
        st.session_state.event_manager   = None
        st.session_state.history         = []
        st.rerun()

    st.title(f"Location: {state.current_location}")

    # World setting + active model badges
    ws_id = getattr(state, 'world_setting', None) or 'dnd5e'
    ws    = config.get_world_setting(ws_id)
    active_preset = next(
        (p for p in config.MODEL_PRESETS if p['id'] == st.session_state.active_model_id),
        None,
    )
    badge_parts = [f"🌍 **{ws['name']}**"]
    if active_preset:
        badge_parts.append(f"🤖 {active_preset['name']}")
    if len(party) > 1:
        ai_count = sum(
            1 for i in range(len(party))
            if (getattr(state, 'ai_configs', None) or {}).get(str(i), {}).get('is_ai', False)
        )
        human_count = len(party) - ai_count
        party_badge = f"👥 {human_count}H"
        if ai_count:
            party_badge += f"+{ai_count}🤖"
        badge_parts.append(party_badge)
    st.caption("  ·  ".join(badge_parts))

    # --- Chat history ---
    if 'history' not in st.session_state:
        st.session_state.history = []

    for item in st.session_state.history:
        if item['role'] == 'player':
            actor = item.get('actor', '')
            prefix = f"**{actor}:**" if actor else "**You:**"
            st.markdown(f"{prefix} {item['content']}")
        else:
            # Scene type badge (Waidrin Narrative Event style)
            scene_type = item.get('scene_type', 'exploration')
            _render_scene_label(scene_type)
            # Dice result banner (if a skill check occurred)
            _render_dice_result(item.get('dice_result'))
            st.markdown(f"**DM:** {item['content']}")
            if item.get('image'):
                st.image(item['image'], caption="Scene visualization")

    # --- Auto-run AI turns before showing human input ---
    ai_cfgs    = getattr(state, 'ai_configs', None) or {}
    active_ai  = ai_cfgs.get(str(active_idx), {})
    is_ai_turn = active_ai.get('is_ai', False) and active_char.hp > 0

    if is_ai_turn:
        flag         = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else '🤖'
        personality  = active_ai.get('personality', 'tactical')
        p_name       = config.AI_PERSONALITIES.get(personality, {}).get('name', personality.title())
        spinner_msg  = f"🤖 {flag} {active_char.name} ({p_name}) is deciding..."
        with st.spinner(spinner_msg):
            action_text, response, choices, turn_data, dice_result = (
                st.session_state.event_manager.run_ai_turn(state, party)
            )
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

    # --- Input area ---
    # Multi-player: show whose turn it is with flag indicator
    flag = config.PLAYER_FLAGS[active_idx] if active_idx < len(config.PLAYER_FLAGS) else ''
    if active_char.hp <= 0:
        st.warning(f"**{active_char.name}** has been defeated! Waiting for next living player…")
    else:
        if len(party) > 1:
            st.markdown(f"### {flag} {active_char.name}'s turn — what do you do?")
        else:
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
                prompt_text = (
                    f"{flag} {active_char.name} chooses to..." if len(party) > 1
                    else "I choose to..."
                )
                action_taken = st.text_input(prompt_text, key="action_input")
            with col_submit:
                st.write("")
                st.write("")
                st.form_submit_button("Action")

    if action_taken and active_char.hp > 0:
        st.session_state.history.append({
            "role":    "player",
            "actor":   active_char.name if len(party) > 1 else "",
            "content": action_taken,
        })
        spinner_msg = (
            f"The {config.get_world_setting(ws_id)['term_map']['dm_title']} is thinking..."
        )
        with st.spinner(spinner_msg):
            response, choices, turn_data, dice_result = (
                st.session_state.event_manager.process_turn(
                    action_taken, state, active_char, party=party
                )
            )

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
# Entry point
# ---------------------------------------------------------------------------

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

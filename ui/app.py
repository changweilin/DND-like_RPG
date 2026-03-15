import streamlit as st
import os
import datetime
import threading
import torch

# ---------------------------------------------------------------------------
# Background image-model download state (module-level so worker thread can
# write progress without needing access to Streamlit session state).
# ---------------------------------------------------------------------------
_img_dl = {
    'active':   False,   # download thread running
    'model_id': None,    # model being downloaded
    'progress': 0,       # 0-100 (file-count based)
    'done':     False,   # finished successfully
    'error':    None,    # error message string or None
}

_RACES   = ["Human", "Elf", "Dwarf", "Orc", "Halfling"]
_CLASSES = ["Warrior", "Mage", "Rogue", "Cleric"]

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
from ai.image_gen import ImageGenerator, _PROVIDER_DIFFUSERS, _PROVIDER_OPENAI, _PROVIDER_STABILITY
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

    # Restore last-used image model (switches ImageGenerator model_id)
    # Restore last-used image model into ImageGenerator (single source of truth)
    st.session_state.img_gen.switch_model(
        prefs.get('active_img_model_id', config.IMAGE_MODEL_NAME)
    )
    st.session_state.img_gen_enabled = prefs.get('img_gen_enabled', True)

    # Defaults for new game fields from prefs
    st.session_state.pref_difficulty  = prefs.get('difficulty', 'Normal')
    st.session_state.pref_language    = prefs.get('language', 'English')
    st.session_state.pref_world_idx   = prefs.get('world_idx', 0)
    st.session_state.pref_img_style   = prefs.get('img_style', 0)
    st.session_state.pref_num_players = prefs.get('num_players', 1)

    # Pre-populate new-game form widget state from saved prefs
    # (name is deliberately excluded — user must re-enter each session)
    for _slot in range(6):
        _r = prefs.get(f'race_{_slot}', 'Human')
        if _r in _RACES:
            st.session_state[f"ng_race_{_slot}"] = _r
        _c = prefs.get(f'class_{_slot}', 'Warrior')
        if _c in _CLASSES:
            st.session_state[f"ng_class_{_slot}"] = _c
        st.session_state[f"ng_app_{_slot}"] = prefs.get(f'app_{_slot}', '')
        st.session_state[f"ng_per_{_slot}"] = prefs.get(f'per_{_slot}', '')
        if _slot > 0:
            st.session_state[f"ng_is_ai_{_slot}"] = prefs.get(f'is_ai_{_slot}', False)
    # Custom lore / image suffix text inputs
    st.session_state["new_game_lore"]       = prefs.get('custom_lore', '')
    st.session_state["new_game_custom_img"] = prefs.get('custom_img_suffix', '')

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
# UI language strings — add new keys here if new labels are needed
# ---------------------------------------------------------------------------
_UI_STRINGS = {
    "English": {
        "model_expander":      "⚙️ Model & Language",
        "llm_model_expander":  "⚙️ LLM Model",
        "language_expander":   "🌐 Language",
        "switch_model":    "Switch Model",
        "ui_language":     "🌐 UI Language",
        "new_game":        "New Game",
        "load_game":       "Load Game",
        "save_name":       "Save Name",
        "difficulty":      "Difficulty",
        "world_setting":   "World Setting",
        "universe":        "Universe",
        "custom_lore":     "Custom World Lore (optional)",
        "img_style_hdr":   "🎨 Image Style",
        "img_style_lbl":   "Art Style",
        "custom_suffix":   "Custom style suffix (optional)",
        "party_hdr":       "Party (1-6 players)",
        "num_players":     "Number of players",
        "start_adventure": "Start Adventure",
        "name":            "Name",
        "race":            "Race",
        "char_class":      "Class",
        "appearance":      "Appearance",
        "personality":     "Personality",
        "ai_controlled":   "🤖 AI-controlled",
        "ai_personality":  "AI Personality",
        "ai_difficulty":   "AI Difficulty",
        "no_saves":        "No saves found.",
        "save_required":   "Save Name and Player 1 Name are required.",
        "map_hint":        "🗺️ Map and portraits are generated after starting the game.",
        "dup_title":       "⚠️ Save Name Conflict",
        "dup_warning":     "already exists. Choose an action:",
        "dup_overwrite":   "🗑️ Overwrite",
        "dup_overwrite_desc": "Delete the old save and start fresh with your new settings.",
        "dup_inherit":     "📂 Load Existing",
        "dup_inherit_desc": "Discard new settings and continue from the existing save.",
        "dup_cancel":      "✖ Cancel",
    },
    "繁體中文": {
        "model_expander":      "⚙️ 模型與語言",
        "llm_model_expander":  "⚙️ LLM 模型",
        "language_expander":   "🌐 語言",
        "switch_model":    "切換模型",
        "ui_language":     "🌐 介面語言",
        "new_game":        "新遊戲",
        "load_game":       "載入遊戲",
        "save_name":       "存檔名稱",
        "difficulty":      "難度",
        "world_setting":   "世界設定",
        "universe":        "世界觀",
        "custom_lore":     "自定義世界觀（選填）",
        "img_style_hdr":   "🎨 影像風格",
        "img_style_lbl":   "藝術風格",
        "custom_suffix":   "自定義風格後綴（選填）",
        "party_hdr":       "隊伍（1-6 人）",
        "num_players":     "玩家人數",
        "start_adventure": "開始冒險",
        "name":            "名字",
        "race":            "種族",
        "char_class":      "職業",
        "appearance":      "外貌描述",
        "personality":     "個性描述",
        "ai_controlled":   "🤖 AI 操控",
        "ai_personality":  "AI 個性",
        "ai_difficulty":   "AI 難度",
        "no_saves":        "找不到存檔。",
        "save_required":   "存檔名稱與玩家 1 名字為必填。",
        "map_hint":        "🗺️ 大陸地圖與角色肖像在開始遊戲後生成。",
        "dup_title":       "⚠️ 存檔名稱衝突",
        "dup_warning":     "已存在。請選擇操作：",
        "dup_overwrite":   "🗑️ 覆蓋",
        "dup_overwrite_desc": "刪除舊存檔，以目前設定重新開始。",
        "dup_inherit":     "📂 繼承存檔",
        "dup_inherit_desc": "放棄新設定，繼續讀取現有存檔。",
        "dup_cancel":      "✖ 取消",
    },
    "日本語": {
        "model_expander":      "⚙️ モデルと言語",
        "llm_model_expander":  "⚙️ LLMモデル",
        "language_expander":   "🌐 言語",
        "switch_model":    "モデル切替",
        "ui_language":     "🌐 UI言語",
        "new_game":        "新規ゲーム",
        "load_game":       "ゲーム読込",
        "save_name":       "セーブ名",
        "difficulty":      "難易度",
        "world_setting":   "世界設定",
        "universe":        "世界観",
        "custom_lore":     "カスタム世界観（省略可）",
        "img_style_hdr":   "🎨 画像スタイル",
        "img_style_lbl":   "アートスタイル",
        "custom_suffix":   "カスタムスタイル後置（省略可）",
        "party_hdr":       "パーティ（1〜6人）",
        "num_players":     "プレイヤー数",
        "start_adventure": "冒険を始める",
        "name":            "名前",
        "race":            "種族",
        "char_class":      "職業",
        "appearance":      "外見描写",
        "personality":     "性格",
        "ai_controlled":   "🤖 AI操作",
        "ai_personality":  "AIの個性",
        "ai_difficulty":   "AIの難易度",
        "no_saves":        "セーブデータが見つかりません。",
        "save_required":   "セーブ名とプレイヤー1の名前は必須です。",
        "map_hint":        "🗺️ マップとポートレートはゲーム開始後に生成されます。",
        "dup_title":       "⚠️ セーブ名の競合",
        "dup_warning":     "はすでに存在します。操作を選択してください：",
        "dup_overwrite":   "🗑️ 上書き",
        "dup_overwrite_desc": "古いセーブを削除し、新しい設定で開始します。",
        "dup_inherit":     "📂 既存を読込",
        "dup_inherit_desc": "新しい設定を破棄し、既存のセーブを続けます。",
        "dup_cancel":      "✖ キャンセル",
    },
    "Español": {
        "model_expander":      "⚙️ Modelo e Idioma",
        "llm_model_expander":  "⚙️ Modelo LLM",
        "language_expander":   "🌐 Idioma",
        "switch_model":    "Cambiar modelo",
        "ui_language":     "🌐 Idioma de interfaz",
        "new_game":        "Nuevo Juego",
        "load_game":       "Cargar Partida",
        "save_name":       "Nombre de guardado",
        "difficulty":      "Dificultad",
        "world_setting":   "Mundo",
        "universe":        "Universo",
        "custom_lore":     "Trasfondo personalizado (opcional)",
        "img_style_hdr":   "🎨 Estilo de imagen",
        "img_style_lbl":   "Estilo artístico",
        "custom_suffix":   "Sufijo de estilo personalizado (opcional)",
        "party_hdr":       "Grupo (1-6 jugadores)",
        "num_players":     "Número de jugadores",
        "start_adventure": "Comenzar aventura",
        "name":            "Nombre",
        "race":            "Raza",
        "char_class":      "Clase",
        "appearance":      "Apariencia",
        "personality":     "Personalidad",
        "ai_controlled":   "🤖 IA controlada",
        "ai_personality":  "Personalidad IA",
        "ai_difficulty":   "Dificultad IA",
        "no_saves":        "No hay partidas guardadas.",
        "save_required":   "El nombre de guardado y el nombre del Jugador 1 son obligatorios.",
        "map_hint":        "🗺️ El mapa y retratos se generan al iniciar el juego.",
        "dup_title":       "⚠️ Conflicto de nombre",
        "dup_warning":     "ya existe. Elige una acción:",
        "dup_overwrite":   "🗑️ Sobreescribir",
        "dup_overwrite_desc": "Elimina el guardado antiguo y empieza con la nueva configuración.",
        "dup_inherit":     "📂 Cargar existente",
        "dup_inherit_desc": "Descarta la nueva configuración y continúa desde el guardado existente.",
        "dup_cancel":      "✖ Cancelar",
    },
}

_LANGUAGES = ["English", "繁體中文", "日本語", "Español"]


def _t(key):
    """Return UI string for the current UI language."""
    lang    = st.session_state.get('pref_language', 'English')
    strings = _UI_STRINGS.get(lang, _UI_STRINGS['English'])
    return strings.get(key, _UI_STRINGS['English'].get(key, key))

# Sentinel model-ID used when the user disables image generation entirely
_DISABLED_IMG_ID = "__disabled__"


@st.dialog("⚠️ Save Conflict / 存檔衝突")
def _duplicate_save_dialog(pending):
    """Modal popup shown when a new-game save name already exists.

    Offers three choices:
      Overwrite  — delete old save, create fresh with current settings
      Inherit    — load the existing save, ignore new settings
      Cancel     — dismiss and return to the menu
    """
    save_name = pending['save_name']
    st.markdown(f"**`{save_name}`** {_t('dup_warning')}")
    st.markdown("---")

    c1, c2, c3 = st.columns(3)

    if c1.button(_t('dup_overwrite'), use_container_width=True, type="primary"):
        st.session_state.save_manager.delete_game(save_name)
        lead = pending['lead_fields']
        party, game_state, session = st.session_state.save_manager.create_new_game(
            save_name, lead[0], lead[1], lead[2], lead[3], lead[4],
            pending['difficulty'], pending['language'],
            world_context=pending['world_context'],
            world_setting=pending['world_setting'],
            extra_players=pending['extra_players'] or None,
            llm=st.session_state.llm,
            rag=st.session_state.rag,
        )
        if party:
            st.session_state.image_style       = pending['img_style']
            st.session_state.custom_img_suffix = pending['custom_img_suffix']
            st.session_state.duplicate_save_pending = None
            active_idx  = (game_state.active_player_index or 0) % len(party)
            active_char = party[active_idx]
            st.session_state.current_session = session
            st.session_state.game_state      = game_state
            st.session_state.party           = party
            st.session_state.player          = active_char
            st.session_state.history         = []
            st.session_state.event_manager   = EventManager(
                st.session_state.llm, st.session_state.rag, session
            )
            st.session_state.world_map        = {}
            st.session_state.player_positions = {}
            st.session_state.manual_dice      = {}
            st.session_state.portraits        = {}
            st.session_state.continent_map    = None
            st.session_state.book_page_idx   = 0
            st.rerun()
        else:
            st.error("Failed to overwrite.")

    if c2.button(_t('dup_inherit'), use_container_width=True):
        party, game_state, session = st.session_state.save_manager.load_game(save_name)
        if party:
            prior_log  = load_story_log(save_name)
            prior_hist = restore_history_from_log(prior_log, n=2)
            st.session_state.current_session = session
            st.session_state.game_state      = game_state
            st.session_state.party           = party
            st.session_state.player          = party[game_state.active_player_index or 0]
            st.session_state.history         = prior_hist
            st.session_state.event_manager   = EventManager(
                st.session_state.llm, st.session_state.rag, session
            )
            st.session_state.duplicate_save_pending = None
            st.rerun()
        else:
            st.error("Failed to load existing save.")

    if c3.button(_t('dup_cancel'), use_container_width=True):
        st.session_state.duplicate_save_pending = None
        st.rerun()


def _img_enabled():
    """Return True when image generation is active (user has not disabled it)."""
    return st.session_state.get('img_gen_enabled', True)


# ---------------------------------------------------------------------------
# Image model download helpers
# ---------------------------------------------------------------------------

def _is_img_model_cached(model_id):
    """
    Return True only if the model has a completed HuggingFace snapshot on disk.
    Checks for a 'snapshots/' subdirectory so partially-downloaded models are
    not mistakenly treated as ready (HF Hub creates the top-level dir immediately
    at the start of a download).
    Result is cached in session state per model_id so the os.path call is not
    repeated on every Streamlit rerun.
    """
    cache_key = f'_img_cached_{model_id}'
    if st.session_state.get(cache_key) is not None:
        return st.session_state[cache_key]
    cache_dir  = os.path.expanduser('~/.cache/huggingface/hub')
    local_name = 'models--' + model_id.replace('/', '--')
    result = os.path.isdir(os.path.join(cache_dir, local_name, 'snapshots'))
    st.session_state[cache_key] = result
    return result


def _invalidate_img_cache(model_id):
    """Clear the per-model cache entry so the next render re-checks disk."""
    st.session_state.pop(f'_img_cached_{model_id}', None)


def _start_img_model_download(model_id):
    """
    Start downloading a HuggingFace diffusers model in a background daemon thread.
    Progress (0-100, file-count based) is written to the module-level _img_dl dict.
    No-op if a download is already active.
    """
    if _img_dl.get('active'):
        return  # already downloading something

    # Use .update() for all writes so the GIL-protected dict object is never
    # replaced wholesale from the worker thread (avoids TOCTOU on the reference).
    _img_dl.update({'active': True, 'model_id': model_id, 'progress': 0,
                    'done': False, 'error': None})

    def _worker():
        try:
            from huggingface_hub import HfApi, hf_hub_download
            api   = HfApi()
            files = list(api.list_repo_files(model_id))
            total = len(files)
            for i, filename in enumerate(files):
                hf_hub_download(repo_id=model_id, filename=filename)
                _img_dl['progress'] = int((i + 1) / total * 100) if total else 100
            _img_dl.update({'active': False, 'progress': 100, 'done': True, 'error': None})
        except Exception as exc:
            _img_dl.update({'active': False, 'progress': 0, 'done': False, 'error': str(exc)})

    threading.Thread(target=_worker, daemon=True).start()


def _apply_img_model_switch(preset):
    """Switch the active image model, update ImageGenerator and persist to prefs."""
    model_id = preset['id']
    img_gen  = st.session_state.get('img_gen')
    if img_gen:
        img_gen.switch_model(model_id)
    # Invalidate the cache-check entry so the next render re-reads disk state
    _invalidate_img_cache(model_id)
    prefs = PersistenceManager.load_prefs()
    prefs['active_img_model_id'] = model_id
    PersistenceManager.save_prefs(prefs)


def _render_image_model_selector():
    """
    Sidebar expander for selecting the image generation model.

    - Local diffusers models: checks HuggingFace cache, offers background
      download with per-file progress bar + Refresh button.
    - Cloud models (OpenAI, Stability): API key check + password input flow.
    - Switch button applies change without reloading the page.
    """
    presets = config.IMAGE_MODEL_PRESETS
    # Prepend the disabled sentinel so index 0 always means "off"
    all_ids    = [_DISABLED_IMG_ID] + [p['id'] for p in presets]
    all_labels = ["🚫 不啟用 (Disabled)"] + [
        f"[{'LOCAL' if p['provider']==_PROVIDER_DIFFUSERS else 'CLOUD'}] {p['name']}"
        for p in presets
    ]

    img_gen = st.session_state.get('img_gen')
    if not _img_enabled():
        cur_idx = 0                                     # sentinel is selected
    else:
        cur_id = img_gen.model_id if img_gen else config.IMAGE_MODEL_NAME
        try:
            cur_idx = all_ids.index(cur_id)
        except ValueError:
            cur_idx = 1                                 # fall back to first real model

    with st.sidebar.expander("🖼️ Image Model", expanded=False):
        sel_idx = st.selectbox(
            "Model",
            range(len(all_ids)),
            index=cur_idx,
            format_func=lambda i: all_labels[i],
            key="img_model_selector",
        )

        # ---- Disabled sentinel -----------------------------------------------
        if sel_idx == 0:
            if _img_enabled():
                st.session_state.img_gen_enabled = False
                prefs = PersistenceManager.load_prefs()
                prefs['img_gen_enabled'] = False
                PersistenceManager.save_prefs(prefs)
                st.rerun()
            st.info("🚫 影像生成已停用，所有生成按鈕均已凍結。")
            # Still render the global download bar below, then return early
        else:
            if not _img_enabled():
                st.session_state.img_gen_enabled = True
                prefs = PersistenceManager.load_prefs()
                prefs['img_gen_enabled'] = True
                PersistenceManager.save_prefs(prefs)
                st.rerun()

        if sel_idx != 0:
            # remap back to the real presets list (offset by 1 for the sentinel)
            preset   = presets[sel_idx - 1]
            provider = preset.get('provider', 'diffusers')
            model_id = preset['id']
            is_active = (model_id == (img_gen.model_id if img_gen else config.IMAGE_MODEL_NAME))

            st.caption(preset.get('description', ''))
            vram = preset.get('vram_gb', 0)
            if vram:
                st.caption(f"💾 VRAM: ~{vram} GB")

            # ---- Local diffusers --------------------------------------------
            if provider == _PROVIDER_DIFFUSERS:
                dl      = _img_dl
                dl_this = (dl.get('model_id') == model_id)
                cached  = _is_img_model_cached(model_id)

                if dl.get('active') and dl_this:
                    pct = dl.get('progress', 0)
                    st.progress(pct / 100,
                                text=f"⬇️ Downloading {preset['name']}… {pct}%")
                    if st.button("🔄 Refresh progress", key="img_dl_refresh",
                                 use_container_width=True):
                        st.rerun()

                elif dl.get('done') and dl_this and not is_active:
                    _invalidate_img_cache(model_id)
                    st.success(f"✅ {preset['name']} downloaded!")
                    _apply_img_model_switch(preset)
                    st.rerun()

                elif dl.get('error') and dl_this:
                    st.error(f"❌ Download failed: {dl['error']}")
                    if st.button("🔄 Retry download", key="img_retry_dl",
                                 use_container_width=True):
                        _start_img_model_download(model_id)
                        st.rerun()

                elif not cached:
                    st.warning(f"⬇️ **{preset['name']}** not in local cache.")
                    if st.button(f"⬇️ Download & Switch",
                                 key="img_dl_btn", use_container_width=True):
                        _start_img_model_download(model_id)
                        st.info("Download started in background. "
                                "You may continue using the app. "
                                "Click **Refresh progress** to check status.")
                        st.rerun()

                elif is_active:
                    st.success(f"▶ **{preset['name']}** is active")

                else:
                    st.success(f"✅ {preset['name']} cached locally.")
                    _apply_img_model_switch(preset)
                    st.rerun()

            # ---- Cloud API providers ----------------------------------------
            else:
                env_key = preset.get('env_key', '')
                has_key = bool(os.environ.get(env_key, ''))

                if has_key:
                    st.success(f"🔑 `{env_key}` is set")
                else:
                    st.warning(f"🔑 `{env_key}` not found in environment")
                    entered = st.text_input(
                        f"Enter {env_key}:",
                        type="password",
                        key=f"img_api_key_{env_key}",
                    )
                    if entered:
                        if st.button("💾 Save key (this session)",
                                     key=f"img_save_key_{env_key}",
                                     use_container_width=True):
                            os.environ[env_key] = entered
                            st.rerun()

                if has_key:
                    if is_active:
                        st.success(f"▶ **{preset['name']}** is active")
                    else:
                        _apply_img_model_switch(preset)
                        st.rerun()

    # Global download progress bar at very bottom of sidebar (visible even when
    # the expander is collapsed, so user always sees ongoing downloads)
    dl = _img_dl
    if dl.get('active'):
        pct = dl.get('progress', 0)
        st.sidebar.progress(
            pct / 100,
            text=f"⬇️ Downloading {dl.get('model_id', '')} … {pct}%",
        )
    elif dl.get('done') and not dl.get('error'):
        st.sidebar.success(f"✅ Downloaded: {dl.get('model_id', '')}")
    elif dl.get('error'):
        st.sidebar.error(f"❌ Download error: {dl['error']}")


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

def _render_language_switcher():
    """Sidebar expander: select UI language, switch live on selection."""
    with st.sidebar.expander(_t("language_expander"), expanded=False):
        try:
            lang_idx = _LANGUAGES.index(st.session_state.pref_language)
        except ValueError:
            lang_idx = 0
        new_lang_idx = st.selectbox(
            _t("ui_language"),
            range(len(_LANGUAGES)),
            index=lang_idx,
            format_func=lambda i: _LANGUAGES[i],
            key="sidebar_language_select",
        )
        if _LANGUAGES[new_lang_idx] != st.session_state.pref_language:
            new_lang = _LANGUAGES[new_lang_idx]
            st.session_state.pref_language = new_lang
            prefs = PersistenceManager.load_prefs()
            prefs['language'] = new_lang
            PersistenceManager.save_prefs(prefs)
            st.rerun()

    # Always keep game_state.language in sync with pref_language.
    # Do NOT call session.commit() here — SQLAlchemy expire_on_commit would clear
    # the attribute, and the subsequent lazy-load races with _build_system_prompt.
    # Instead just set the Python attribute directly; SQLAlchemy's Unit of Work
    # will include this dirty field in the next commit inside process_turn.
    _gs = st.session_state.get('game_state')
    if _gs is not None:
        _gs.language = st.session_state.pref_language


def _render_model_switcher():
    """Sidebar expander: select LLM model, switch live on dropdown selection."""
    # VRAM / CUDA status panel — shown ABOVE the expander so it's always visible
    _active_preset = next(
        (p for p in config.MODEL_PRESETS
         if p['id'] == st.session_state.get('active_model_id')),
        config.MODEL_PRESETS[0],
    )
    _vram_req = _active_preset.get('vram_gb', 0)
    _provider = _active_preset.get('provider', 'ollama')
    if _provider == 'ollama':
        _cuda_ok = torch.cuda.is_available()
        if _cuda_ok:
            try:
                _free_b, _total_b = torch.cuda.mem_get_info()
                _free_gb  = _free_b  / (1024 ** 3)
                _total_gb = _total_b / (1024 ** 3)
                _gpu_name = torch.cuda.get_device_name(0)
                st.sidebar.caption(
                    f"🖥️ **{_gpu_name}** — {_total_gb:.1f} GB total · {_free_gb:.1f} GB free"
                )
                if _vram_req:
                    if _total_gb >= _vram_req:
                        st.sidebar.success(f"✅ VRAM 足夠（需 ~{_vram_req} GB）")
                    else:
                        st.sidebar.error(
                            f"❌ VRAM 不足（需 ~{_vram_req} GB，GPU 僅 {_total_gb:.1f} GB）"
                        )
            except Exception:
                st.sidebar.warning(f"⚠️ CUDA 可用，但無法讀取 VRAM（需 ~{_vram_req} GB）")
        else:
            msg = f"❌ 無 CUDA GPU（需 ~{_vram_req} GB）" if _vram_req else "❌ 無 CUDA GPU"
            st.sidebar.error(msg)

    with st.sidebar.expander(_t("llm_model_expander"), expanded=False):
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
        new_id = preset['id']

        st.caption(preset.get('description', ''))
        if preset.get('pros'):
            st.markdown(f"✅ **Pros:** {preset['pros']}")
        if preset.get('cons'):
            st.markdown(f"⚠️ **Cons:** {preset['cons']}")

        env_key = preset.get('env_key')
        key_ready = True  # False if cloud model selected but key missing

        if env_key:
            has_key = bool(os.environ.get(env_key, ''))
            if has_key:
                st.success(f"🔑 `{env_key}` is set")
            else:
                key_ready = False
                st.warning(f"🔑 `{env_key}` not found in environment")
                entered = st.text_input(
                    f"Enter {env_key}:",
                    type="password",
                    key=f"llm_api_key_{env_key}",
                    placeholder="Paste your API key here…",
                )
                if entered:
                    if st.button("💾 套用金鑰 Apply key (this session)",
                                 key=f"llm_save_key_{env_key}",
                                 use_container_width=True):
                        os.environ[env_key] = entered
                        st.rerun()
                else:
                    st.caption(
                        f"⚠️ 選擇此模型前需先填入 `{env_key}`。  "
                        "The model will switch once a valid key is saved."
                    )

        # Auto-switch when dropdown selection differs from active model
        # Block switch for cloud models until the API key is actually present.
        if new_id != st.session_state.active_model_id:
            if key_ready:
                st.session_state.llm.switch_model(new_id)
                st.session_state.active_model_id = new_id
                prefs = PersistenceManager.load_prefs()
                prefs['active_model_id'] = new_id
                PersistenceManager.save_prefs(prefs)
                st.success(f"✅ Switched to **{preset['name']}**")
            # else: key not ready — don't switch; warning already shown above
        else:
            if key_ready:
                st.success(f"▶ **{preset['name']}** is active")

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
        is_ai = st.checkbox(_t("ai_controlled"), key=f"{key_prefix}_is_ai_{idx}", value=False)

    cols = st.columns([2, 1, 1])
    name       = cols[0].text_input(_t("name"),  key=f"{key_prefix}_name_{idx}")
    race       = cols[1].selectbox(_t("race"), _RACES,
                                   key=f"{key_prefix}_race_{idx}")
    char_class = cols[2].selectbox(_t("char_class"), _CLASSES,
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
            _t("ai_personality"), range(len(personalities)),
            format_func=lambda i: personality_labels[i],
            key=f"{key_prefix}_ai_pers_{idx}",
        )
        ai_personality = personalities[ai_p_idx]

        difficulties      = list(config.AI_DIFFICULTIES.keys())
        difficulty_labels = [config.AI_DIFFICULTIES[d]['name'] for d in difficulties]
        ai_d_idx = ai_cols[1].selectbox(
            _t("ai_difficulty"), range(len(difficulties)),
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
        appearance       = st.text_input(_t("appearance"),  key=f"{key_prefix}_app_{idx}",
                                         placeholder="A brave adventurer.")
        personality_text = st.text_input(_t("personality"), key=f"{key_prefix}_per_{idx}",
                                         placeholder="Courageous and kind.")

    return name, race, char_class, appearance, personality_text, is_ai, ai_personality, ai_difficulty


def main_menu():
    _check_model_updates()
    _render_language_switcher()
    _render_model_switcher()
    _render_image_model_selector()

    st.title("D&D AI RPG Engine")

    col1, col2 = st.columns(2)

    with col1:
        st.header(_t("new_game"))
        with st.form("new_game_form"):
            save_name  = st.text_input(_t("save_name"))
            difficulty = st.selectbox(_t("difficulty"), ["Easy", "Normal", "Hard"],
                                      index=["Easy", "Normal", "Hard"].index(st.session_state.pref_difficulty))
            # Language is now set via the model/language expander in the sidebar;
            # read it from session state so the game uses the selected language.
            language = st.session_state.pref_language

            st.markdown(f"**{_t('world_setting')}**")
            ws_labels = [f"[{ws['category']}] {ws['name']}" for ws in config.WORLD_SETTINGS]
            ws_ids    = [ws['id'] for ws in config.WORLD_SETTINGS]
            ws_idx    = st.selectbox(
                _t("universe"), range(len(config.WORLD_SETTINGS)),
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
                _t("custom_lore"),
                placeholder=ws.get('world_lore', '')[:150] + "...",
                height=60, key="new_game_lore",
            )

            st.markdown("---")
            st.markdown(f"**{_t('img_style_hdr')}**")
            _style_keys   = list(IMAGE_STYLES.keys())
            _style_labels = [
                f"{IMAGE_STYLES[k]['name']} — {IMAGE_STYLES[k]['name_en']}"
                for k in _style_keys
            ]
            img_style_idx = st.selectbox(
                _t("img_style_lbl"),
                range(len(_style_keys)),
                format_func=lambda i: _style_labels[i],
                index=st.session_state.pref_img_style,
                key="new_game_img_style",
            )
            custom_img_suffix = st.text_input(
                _t("custom_suffix"),
                key="new_game_custom_img",
                placeholder="e.g. 'oil painting, baroque style, rich colors'",
            )
            st.caption(_t("map_hint"))

            allow_custom_action = st.checkbox("允許自訂行動輸入", value=True,
                                              help="勾選後，遊戲中可輸入非選項的自訂行動")

            st.markdown("---")
            st.markdown(f"**{_t('party_hdr')}**")
            num_players = st.selectbox(
                _t("num_players"), list(range(1, config.MAX_PARTY_SIZE + 1)),
                index=min(st.session_state.pref_num_players - 1, config.MAX_PARTY_SIZE - 1),
                key="new_game_num_players"
            )

            player_fields = []
            for i in range(num_players):
                player_fields.append(_player_config_fields(i, key_prefix="ng"))
                if i < num_players - 1:
                    st.markdown("---")

            if st.form_submit_button(_t("start_adventure")):
                lead = player_fields[0]
                if not save_name or not lead[0]:
                    st.error(_t("save_required"))
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
                            allow_custom_action=allow_custom_action,
                            llm=st.session_state.llm,
                            rag=st.session_state.rag,
                        )
                    )
                    if party is not None:
                        names    = ", ".join(c.name for c in party)
                        ai_count = sum(1 for e in extra if e.get('is_ai'))
                        # Store image style selection for this session
                        st.session_state.image_style       = _style_keys[img_style_idx]
                        st.session_state.custom_img_suffix = custom_img_suffix.strip()
                        st.session_state.continent_map     = None
                        st.session_state.portraits         = {}

                        # Save preferences — includes per-slot race/class/app/per
                        # (name excluded deliberately — user re-enters each session)
                        new_prefs = {
                            'active_model_id':   st.session_state.active_model_id,
                            'difficulty':        difficulty,
                            'language':          language,
                            'world_idx':         ws_idx,
                            'img_style':         img_style_idx,
                            'num_players':       num_players,
                            'custom_img_suffix': custom_img_suffix.strip(),
                            'custom_lore':       custom_lore,
                            # Player 1 (lead)
                            'race_0':  lead[1], 'class_0': lead[2],
                            'app_0':   lead[3], 'per_0':   lead[4],
                        }
                        for _si, _ep in enumerate(extra, start=1):
                            new_prefs[f'race_{_si}']           = _ep['race']
                            new_prefs[f'class_{_si}']          = _ep['char_class']
                            new_prefs[f'is_ai_{_si}']          = _ep['is_ai']
                            new_prefs[f'ai_personality_{_si}'] = _ep['ai_personality']
                            new_prefs[f'ai_difficulty_{_si}']  = _ep['ai_difficulty']
                        PersistenceManager.save_prefs(new_prefs)

                        # Auto-load the new game immediately (no separate Load step)
                        active_idx  = (game_state.active_player_index or 0) % len(party)
                        active_char = party[active_idx]
                        st.session_state.current_session = session
                        st.session_state.game_state      = game_state
                        st.session_state.party           = party
                        st.session_state.player          = active_char
                        st.session_state.history         = []
                        st.session_state.event_manager   = EventManager(
                            st.session_state.llm, st.session_state.rag, session
                        )
                        st.session_state.world_map        = {}
                        st.session_state.player_positions = {}
                        st.session_state.manual_dice      = {}
                        st.session_state.book_page_idx   = 0
                        st.rerun()
                    else:
                        # Duplicate name handle
                        st.session_state.duplicate_save_pending = {
                            'save_name': save_name,
                            'lead_fields': lead,
                            'difficulty': difficulty,
                            'language': st.session_state.pref_language,
                            'world_context': custom_lore,
                            'world_setting': ws_ids[ws_idx],
                            'extra_players': extra,
                            'img_style': _style_keys[img_style_idx],
                            'custom_img_suffix': custom_img_suffix.strip()
                        }
                        st.rerun()

    with col2:
        st.header(_t("load_game"))
        saves = st.session_state.save_manager.list_saves()
        if not saves:
            st.info(_t("no_saves"))
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

    # ---- Duplicate Save Dialog (popup modal) ----
    if st.session_state.duplicate_save_pending:
        _duplicate_save_dialog(st.session_state.duplicate_save_pending)

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
    """Sidebar: NPC affinity bars, mood, goals, and scene-volatile state."""
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
            proper   = data.get('proper_name', '')
            emotion  = data.get('emotion', '')
            action   = data.get('action', '')
            health   = data.get('health', '')
        else:
            affinity, mood, goal = int(data), 'Neutral', ''
            proper = emotion = action = health = ''

        display = f"{name} ({proper})" if proper and proper != name else name
        bar = _affinity_bar(affinity)
        # Show emotion badge if NPC is currently in scene
        emotion_badge = f"　🎭 *{emotion}*" if emotion else ""
        st.sidebar.write(f"**{display}**{emotion_badge}")
        st.sidebar.write(f"  {bar} {affinity:+d} · {mood}")
        if health and health.lower() not in ('healthy', '健康', ''):
            st.sidebar.caption(f"  ❤️ {health}")
        if action:
            st.sidebar.caption(f"  ▶ {action}")
        if goal:
            st.sidebar.caption(f"  ◎ {goal}")


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


def _render_prologue_badge(container=None):
    """Render the prologue badge used in both the story tab and book renderer."""
    ctx = container if container is not None else st
    ctx.markdown(
        "<div style='background:#0a0a1a;border-left:4px solid #4a6aaa;"
        "padding:6px 10px;border-radius:4px;margin-bottom:6px;"
        "font-size:0.82em;color:#8898cc'>📜 開場白 · Turn 0</div>",
        unsafe_allow_html=True,
    )


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
        if st.button("🔄 重新生成地圖", key="regen_map_btn",
                     disabled=not _img_enabled()):
            st.session_state.continent_map = None
            _generate_continent_map(ws)
    else:
        gen_col, _ = st.columns([2, 3])
        if gen_col.button("🎨 生成大陸地圖", key="gen_map_btn",
                          use_container_width=True, disabled=not _img_enabled()):
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

    # ── Relationship graph ─────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🔗 關係圖")
    _render_relation_graph(state, party)

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
                _render_prologue_badge()
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

        if getattr(state, 'allow_custom_action', 1):
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
            state.language = st.session_state.pref_language
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

            if _img_enabled() and not img_gen.is_disabled():
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
    from engine.world import WorldManager
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
                                 help=f"重新生成 {char.name} 肖像",
                                 disabled=not _img_enabled()):
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
                                 use_container_width=True,
                                 disabled=not _img_enabled()):
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

            # ── Relations for this character ─────────────────────────────
            try:
                _db      = st.session_state.save_manager.db
                _sess    = _db.get_session()
                _world   = WorldManager(_sess, state)
                char_key = str(char.name).lower()
                char_rels = _world.get_relations('char', char_key)
                if not char_rels:
                    # also try by id string in case edges were stored that way
                    char_rels = _world.get_relations('char', str(char.id))
                if char_rels:
                    # Build a lookup from org + NPC names
                    _lk = {o['name'].lower(): o['name']
                           for o in _world.list_organizations()}
                    for npc in (state.relationships or {}):
                        _lk[npc.lower()] = npc
                    for c in party:
                        _lk[c.name.lower()] = c.name
                    with st.expander("🔗 關係", expanded=False):
                        _render_relation_rows(char_rels, char_key, _lk)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Organizations tab — faction / org codex discovered during the story
# ---------------------------------------------------------------------------

# Type labels → display emoji
_ORG_TYPE_ICONS = {
    'government':     '🏛️',
    'army':           '⚔️',
    'guild':          '🔨',
    'cult':           '🕯️',
    'religious order':'⛪',
    'academy':        '📚',
    'mercenary':      '💰',
    'secret society': '🕵️',
    'noble house':    '👑',
}


_RELATION_STRENGTH_COLOUR = {
    range(-100, -60): '#f44336',   # hostile     — red
    range(-60,  -20): '#ff9800',   # unfriendly  — orange
    range(-20,   20): '#9e9e9e',   # neutral     — grey
    range( 20,   60): '#8bc34a',   # friendly    — light green
    range( 60,  101): '#4caf50',   # allied      — green
}

def _strength_colour(strength):
    for rng, colour in _RELATION_STRENGTH_COLOUR.items():
        if strength in rng:
            return colour
    return '#9e9e9e'


def _build_relation_label_lookup(state):
    """Build entity_key → display label dict from orgs + NPCs + party."""
    lookup = {}
    if state is None:
        return lookup
    raw = getattr(state, 'organizations', None) or {}
    for o in raw.values():
        lookup[o['name'].lower()] = o['name']
    for npc_name in (state.relationships or {}):
        lookup[npc_name.lower()] = npc_name
    return lookup


def _render_relation_graph(state, party=None):
    """
    Full filterable relationship graph panel — embeddable anywhere.
    Loads relations from DB, shows type-filter multiselect and rows.
    """
    from engine.world import WorldManager

    label_lookup = _build_relation_label_lookup(state)
    if party:
        for c in party:
            label_lookup[c.name.lower()] = c.name

    all_relations = []
    try:
        db      = st.session_state.save_manager.db
        session = db.get_session()
        wm      = WorldManager(session, state)
        all_relations = wm.list_all_relations()
    except Exception:
        pass

    if not all_relations:
        st.info("尚無關係資料。隨著故事發展，角色與組織的關係將自動記錄於此。")
        return

    st.caption(f"共 **{len(all_relations)}** 條關係記錄")
    rel_types = sorted({r.relation_type for r in all_relations})
    sel_types = st.multiselect("篩選關係類型", rel_types, default=rel_types,
                               key="board_rel_type_filter")
    shown = [r for r in all_relations if r.relation_type in sel_types]
    for rel in sorted(shown, key=lambda r: r.since_turn):
        src_label = label_lookup.get(rel.source_key, rel.source_key.title())
        tgt_label = label_lookup.get(rel.target_key, rel.target_key.title())
        colour    = _strength_colour(rel.strength)
        turn_lbl  = "開場白" if rel.since_turn == 0 else f"第 {rel.since_turn} 回合"
        st.markdown(
            f"<div style='padding:5px 0;border-bottom:1px solid #1e1e2e'>"
            f"<b>{src_label}</b>"
            f" <span style='color:{colour}'> — {rel.relation_type} → </span>"
            f"<b>{tgt_label}</b>"
            f"&nbsp;&nbsp;<span style='color:{colour};font-size:0.85em'>{rel.strength:+d}</span>"
            f"&nbsp;&nbsp;<span style='color:#555;font-size:0.8em'>{turn_lbl}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if rel.description:
            st.caption(f"  {rel.description}")


def _render_relation_rows(relations, self_key, label_lookup):
    """
    Render a compact table of EntityRelation rows for one entity.

    self_key    — entity key for the focal entity (so we can show the other side)
    label_lookup — dict mapping entity_key → display label
    """
    if not relations:
        st.caption("無已知關係")
        return
    for rel in sorted(relations, key=lambda r: -abs(r.strength)):
        is_outgoing = rel.source_key == self_key.lower()
        other_key   = rel.target_key   if is_outgoing else rel.source_key
        other_label = label_lookup.get(other_key, other_key.title())
        arrow       = "→" if is_outgoing else "←"
        colour      = _strength_colour(rel.strength)
        turn_label  = "開場白" if rel.since_turn == 0 else f"第 {rel.since_turn} 回合"
        st.markdown(
            f"<div style='display:flex;align-items:baseline;gap:8px;"
            f"padding:4px 0;border-bottom:1px solid #1e1e2e'>"
            f"<span style='color:{colour};font-weight:bold;min-width:24px'>{arrow}</span>"
            f"<span style='flex:1'><b>{other_label}</b>"
            f" &nbsp;<span style='color:#aaa;font-size:0.85em'>{rel.relation_type}</span></span>"
            f"<span style='color:{colour};font-size:0.85em'>{rel.strength:+d}</span>"
            f"<span style='color:#555;font-size:0.8em;min-width:60px;text-align:right'>{turn_label}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if rel.description:
            st.caption(f"  {rel.description}")


def _render_organizations_tab(state):
    """Tab — 🏛️ 組織: faction / organization codex built from the story so far."""
    from engine.world import WorldManager

    orgs = []
    all_relations = []
    label_lookup  = {}    # entity_key → display label

    if state is not None:
        raw = getattr(state, 'organizations', None) or {}
        orgs = sorted(raw.values(), key=lambda o: o.get('first_seen_turn', 0))
        for o in orgs:
            label_lookup[o['name'].lower()] = o['name']
        for npc_name in (state.relationships or {}):
            label_lookup[npc_name.lower()] = npc_name
        try:
            db = st.session_state.save_manager.db
            session = db.get_session()
            world_manager = WorldManager(session, state)
            all_relations = world_manager.list_all_relations()
        except Exception:
            pass

    if not orgs:
        st.info("尚未發現任何組織。繼續冒險，組織情報將會自動記錄在此。")
        return

    # Search bar
    search = st.text_input("🔍 搜尋組織", key="org_search",
                           placeholder="輸入名稱、類型、領導人…")
    query = search.strip().lower()
    filtered = orgs
    if query:
        filtered = [o for o in orgs
                    if query in (o.get('name') or '').lower()
                    or query in (o.get('type') or '').lower()
                    or query in (o.get('current_leader') or '').lower()
                    or query in (o.get('headquarters') or '').lower()]

    st.caption(f"共記錄 **{len(filtered)}** 個組織")

    for org in filtered:
        org_type = (org.get('type') or 'unknown').lower()
        icon     = _ORG_TYPE_ICONS.get(org_type, '🏢')
        label    = f"{icon} {org.get('name', '（未命名）')}"
        if org.get('type'):
            label += f"  ·  *{org['type'].title()}*"

        with st.expander(label, expanded=False):
            cols = st.columns([1, 1])
            with cols[0]:
                if org.get('founder'):
                    st.markdown(f"**創辦人** {org['founder']}")
                if org.get('current_leader'):
                    st.markdown(f"**現任領導人** {org['current_leader']}")
                if org.get('member_count'):
                    st.markdown(f"**成員規模** {org['member_count']}")
            with cols[1]:
                if org.get('headquarters'):
                    st.markdown(f"**據點** {org['headquarters']}")
                if org.get('alignment'):
                    st.markdown(f"**陣營傾向** {org['alignment']}")
                turn = org.get('first_seen_turn')
                if turn is not None:
                    label_t = "開場白" if turn == 0 else f"第 {turn} 回合"
                    st.markdown(f"**首次登場** {label_t}")
            if org.get('description'):
                st.markdown(f"> {org['description']}")
            if org.get('history'):
                st.markdown("**歷史沿革**")
                st.markdown(org['history'])
            # Per-org relation rows
            org_key  = org['name'].lower()
            org_rels = [r for r in all_relations
                        if r.source_key == org_key or r.target_key == org_key]
            if org_rels:
                st.markdown("**關係**")
                _render_relation_rows(org_rels, org_key, label_lookup)


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
        _render_prologue_badge(ctx)

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
                     key="sidebar_regen_images", disabled=not _img_enabled()):
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
    _render_language_switcher()
    _render_model_switcher()
    _render_image_model_selector()
    _render_image_style_switcher()

    def _clear_game_state():
        for key in ('current_session', 'game_state', 'player', 'event_manager'):
            st.session_state[key] = None
        st.session_state.party             = []
        st.session_state.history           = []
        st.session_state.world_map         = {}
        st.session_state.player_positions  = {}
        st.session_state.manual_dice       = {}
        st.session_state.continent_map     = None
        st.session_state.portraits         = {}

    sq_col, qq_col = st.sidebar.columns(2)
    if sq_col.button("💾 儲存", use_container_width=True):
        # Flush story log then commit DB — stay in game
        save_name_sq = getattr(state, 'save_name', None)
        if save_name_sq and st.session_state.history:
            save_game_log(save_name_sq, compress_game_log(st.session_state.history))
        st.session_state.current_session.commit()
        st.toast("遊戲已儲存！", icon="💾")
    if qq_col.button("🚪 離開", use_container_width=True):
        st.session_state.current_session.close()
        _clear_game_state()
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
            state.language = st.session_state.pref_language
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
            state.language = st.session_state.pref_language
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

    # ---- Sticky tab bar CSS ----
    st.markdown(
        "<style>"
        ".stTabs [data-baseweb='tab-list']{"
        "position:sticky;top:0;z-index:998;"
        "background:var(--background-color,#0e1117);padding-bottom:2px;}"
        "</style>",
        unsafe_allow_html=True,
    )

    # ---- Tabs (故事 first = default selected) ----
    tab_story, tab_board, tab_chars, tab_rules, tab_orgs, tab_book, tab_god = st.tabs(
        ["📖 故事", "🗺️ 遊戲板", "👥 角色", "📜 規則", "🏛️ 組織", "📕 書本", "🔮 上帝模式"]
    )

    with tab_story:
        _render_story_tab(party, state, active_char, active_idx, ws_id)

    with tab_board:
        _render_game_board_tab(party, state, active_char, active_idx)

    with tab_chars:
        _render_characters_tab(party, state, active_char)

    with tab_rules:
        _render_rules_tab(state)

    with tab_orgs:
        _render_organizations_tab(state)

    with tab_book:
        _render_book_tab(getattr(state, 'save_name', None))

    with tab_god:
        _render_god_mode_tab(party, state)

# ---------------------------------------------------------------------------
# God Mode tab — live DB schema + current values, RAG stats, engine config
# ---------------------------------------------------------------------------

# Schema descriptions mirrored from engine/game_state.py + engine/config.py comments.
# Format: {table: {column: (type_label, description)}}
_GOD_SCHEMA = {
    "characters": {
        "id":          ("INTEGER PK",  "Auto-increment primary key"),
        "name":        ("STRING",      "Player character display name"),
        "race":        ("STRING",      "Species — Human / Elf / Dwarf / Orc / Halfling"),
        "char_class":  ("STRING",      "Class — Warrior / Mage / Rogue / Cleric"),
        "appearance":  ("TEXT",        "Free-text physical description used for image-gen prompts"),
        "personality": ("TEXT",        "Free-text personality injected into LLM system prompt"),
        "hp":          ("INTEGER",     "Current hit points"),
        "max_hp":      ("INTEGER",     "Maximum hit points (set by class on game creation)"),
        "mp":          ("INTEGER",     "Current magic points"),
        "max_mp":      ("INTEGER",     "Maximum magic points"),
        "atk":         ("INTEGER",     "Attack stat — modifier = (atk-10)//2 added to d20 rolls"),
        "def_stat":    ("INTEGER",     "Defence stat — reduces incoming damage by def_stat//2"),
        "mov":         ("INTEGER",     "Movement range on the game board (cells per turn)"),
        "gold":        ("INTEGER",     "Currency balance"),
        "inventory":   ("JSON list",   "Items: [{name, quantity, description, …}]"),
        "skills":      ("JSON list",   "Skill strings granted by class or found items"),
    },
    "game_state": {
        "id":                  ("INTEGER PK",  "Auto-increment primary key"),
        "save_name":           ("STRING UNIQUE","Human-readable save identifier chosen at creation"),
        "player_id":           ("INTEGER FK",  "Foreign key → characters.id for the party leader"),
        "party_ids":           ("JSON list",   "Ordered list of Character.id values; index 0 = leader"),
        "active_player_index": ("INTEGER",     "Which party slot is currently taking their turn (0-based)"),
        "current_location":    ("STRING",      "In-game location name; shown in sidebar and injected into prompts"),
        "world_context":       ("TEXT",        "Narrative world description seeded into RAG world_lore at turn 0"),
        "world_setting":       ("STRING",      "World preset id (e.g. 'dnd5e') — controls vocabulary & tone"),
        "difficulty":          ("STRING",      "Easy / Normal / Hard — affects DC offsets and enemy stats"),
        "language":            ("STRING",      "Narrative language for LLM output (e.g. 'English', '繁體中文')"),
        "turn_count":          ("INTEGER",     "Number of completed turns since game creation"),
        "relationships":       ("JSON dict",   "{npc_name: {affinity: int, state: str, goal: str}} — NPC tracker"),
        "session_memory":      ("JSON list",   "Sliding window of last SESSION_MEMORY_WINDOW turns: [{turn, player_action, narrative, outcome}]"),
        "known_entities":      ("JSON dict",   "{name_lower: {type, hp, max_hp, atk, def_stat, alive, …}} — live combat HP"),
        "party_contributions": ("JSON dict",   "{str(char_id): {damage_dealt, healing_done, skill_checks_passed, turns_taken}}"),
        "ai_configs":          ("JSON dict",   "{str(slot): {is_ai, personality, difficulty}} — AI party member settings"),
    },
}


def _render_god_mode_tab(party, state):
    """
    上帝模式 — full read-only view of all database tables, live values,
    ChromaDB RAG collections, and engine configuration constants.
    """
    st.markdown(
        "<div style='background:#0d0d1a;border-left:4px solid #aa44ff;"
        "padding:8px 14px;border-radius:6px;margin-bottom:12px'>"
        "<span style='color:#cc88ff;font-size:1.05em;font-weight:bold'>"
        "🔮 上帝模式 God Mode</span>"
        "<span style='color:#888;font-size:0.82em;margin-left:10px'>"
        "完整資料庫結構與即時數值 · Read-only live view</span></div>",
        unsafe_allow_html=True,
    )

    # ----------------------------------------------------------------
    # Characters table
    # ----------------------------------------------------------------
    st.subheader("👤 characters 資料表")
    schema = _GOD_SCHEMA["characters"]
    for char in party:
        with st.expander(f"🔴 {char.name} ({char.race} {char.char_class})", expanded=False):
            rows = []
            live = {
                "id": char.id, "name": char.name, "race": char.race,
                "char_class": char.char_class, "appearance": char.appearance or "",
                "personality": char.personality or "",
                "hp": char.hp, "max_hp": char.max_hp,
                "mp": char.mp, "max_mp": char.max_mp,
                "atk": char.atk, "def_stat": char.def_stat, "mov": char.mov,
                "gold": char.gold,
                "inventory": char.inventory or [],
                "skills": char.skills or [],
            }
            for col, (typ, desc) in schema.items():
                val = live.get(col, "—")
                rows.append({"欄位": col, "型別": typ, "說明": desc, "當前值": str(val)})
            import pandas as pd
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "欄位":  st.column_config.TextColumn(width="small"),
                    "型別":  st.column_config.TextColumn(width="small"),
                    "說明":  st.column_config.TextColumn(width="large"),
                    "當前值": st.column_config.TextColumn(width="medium"),
                },
            )

    # ----------------------------------------------------------------
    # GameState table
    # ----------------------------------------------------------------
    st.subheader("🌍 game_state 資料表")
    schema_gs = _GOD_SCHEMA["game_state"]
    live_gs = {
        "id":                  state.id,
        "save_name":           state.save_name,
        "player_id":           state.player_id,
        "party_ids":           state.party_ids or [],
        "active_player_index": state.active_player_index or 0,
        "current_location":    state.current_location,
        "world_context":       state.world_context or "",
        "world_setting":       getattr(state, 'world_setting', 'dnd5e'),
        "difficulty":          state.difficulty,
        "language":            state.language,
        "turn_count":          state.turn_count or 0,
        "relationships":       state.relationships or {},
        "session_memory":      f"[{len(state.session_memory or [])} turns]",
        "known_entities":      f"[{len(state.known_entities or {})} entities]",
        "party_contributions": state.party_contributions or {},
        "ai_configs":          state.ai_configs or {},
    }
    rows_gs = []
    for col, (typ, desc) in schema_gs.items():
        val = live_gs.get(col, "—")
        rows_gs.append({"欄位": col, "型別": typ, "說明": desc, "當前值": str(val)})
    import pandas as pd
    st.dataframe(
        pd.DataFrame(rows_gs),
        use_container_width=True,
        hide_index=True,
        column_config={
            "欄位":  st.column_config.TextColumn(width="small"),
            "型別":  st.column_config.TextColumn(width="small"),
            "說明":  st.column_config.TextColumn(width="large"),
            "當前值": st.column_config.TextColumn(width="medium"),
        },
    )

    with st.expander("📖 world_context 完整內容", expanded=False):
        st.text_area("world_context", value=state.world_context or "", height=200,
                     disabled=True, label_visibility="collapsed")

    # ----------------------------------------------------------------
    # JSON sub-tables — expand each complex JSON column individually
    # ----------------------------------------------------------------
    st.subheader("📂 JSON 欄位展開")

    # NPC relationships — full profile cards
    rels = state.relationships or {}
    with st.expander("📋 NPC 完整檔案 (Full NPC Profiles)", expanded=True):
        if not rels:
            st.caption("（尚無 NPC）")
        for name, d in rels.items():
            if not isinstance(d, dict):
                continue
            proper   = d.get('proper_name', name)
            aliases  = d.get('aliases') or []
            bio      = d.get('biography', '')
            persona  = d.get('personality', '')
            traits   = d.get('traits', '')
            health   = d.get('health', '')
            action   = d.get('action', '')
            emotion  = d.get('emotion', '')
            affinity = d.get('affinity', 0)
            state_lbl= d.get('state', 'Neutral')
            goal     = d.get('goal', '')

            header = f"**{name}**"
            if proper and proper != name:
                header += f"（本名：{proper}）"
            if emotion:
                header += f"　🎭 {emotion}"
            st.markdown(header)

            info_cols = st.columns(3)
            info_cols[0].metric("Affinity", f"{affinity:+d}", label_visibility="visible")
            info_cols[1].write(f"**狀態** {state_lbl}")
            info_cols[2].write(f"**健康** {health or '—'}")

            if aliases:
                st.caption(f"代稱／稱號：{' · '.join(aliases)}")
            if action:
                st.caption(f"行動：{action}")
            if goal:
                st.caption(f"目標：{goal}")
            if persona:
                st.info(f"**性格** {persona}", icon="🧠")
            if traits:
                st.info(f"**特質** {traits}", icon="👁️")
            if bio:
                st.info(f"**生平** {bio}", icon="📜")
            st.markdown("---")

    with st.expander("⚔️ known_entities (戰鬥實體)", expanded=False):
        ents = state.known_entities or {}
        if ents:
            ent_rows = [
                {"name": name,
                 "type": d.get('type', ''),
                 "hp": f"{d.get('hp','?')}/{d.get('max_hp','?')}",
                 "atk": d.get('atk', '?'),
                 "def": d.get('def_stat', '?'),
                 "alive": '✅' if d.get('alive', True) else '💀'}
                for name, d in ents.items()
            ]
            st.dataframe(pd.DataFrame(ent_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("（尚無遭遇實體）")

    with st.expander("🧠 session_memory (滑動記憶窗口)", expanded=False):
        mem = state.session_memory or []
        if mem:
            mem_rows = []
            for m in mem:
                chars    = m.get('characters_present') or []
                unchosen = m.get('unchosen_choices') or []
                mem_rows.append({
                    "turn":      m.get('turn', ''),
                    "location":  m.get('location', ''),
                    "在場NPC":   ', '.join(chars) if chars else '—',
                    "outcome":   m.get('outcome', ''),
                    "已選行動":   (m.get('player_action', '') or '')[:50],
                    "未選選項1":  unchosen[0][:50] if len(unchosen) > 0 else '—',
                    "未選選項2":  unchosen[1][:50] if len(unchosen) > 1 else '—',
                    "narrative": (m.get('narrative', '') or '')[:60],
                })
            st.dataframe(pd.DataFrame(mem_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("（記憶窗口為空）")

    with st.expander("🎒 inventory & skills (全隊)", expanded=False):
        for char in party:
            st.markdown(f"**{char.name}** — 金幣 {char.gold}")
            inv = char.inventory or []
            if inv:
                st.dataframe(pd.DataFrame(inv), use_container_width=True, hide_index=True)
            else:
                st.caption("背包為空")
            skills = char.skills or []
            if skills:
                st.caption("技能: " + ", ".join(str(s) for s in skills))

    # ----------------------------------------------------------------
    # ChromaDB RAG collections
    # ----------------------------------------------------------------
    st.subheader("🗂️ ChromaDB RAG 集合")
    rag = st.session_state.get('rag')
    rag_cols = [
        ("world_lore",   "靜態世界觀資料：遊戲創建時注入，提供 LLM 世界背景知識"),
        ("story_events", "動態事件記錄：每回合結束後儲存，供語意搜尋相關過去情節"),
        ("game_rules",   "規則資料庫：怪物屬性表、咒語描述、DC 表格等機械規則"),
    ]
    # Map collection display names to RAGSystem attribute names
    _RAG_ATTR_MAP = {
        'world_lore':   'lore_collection',
        'story_events': 'story_collection',
        'game_rules':   'rules_collection',
    }
    rag_rows = []
    for cname, cdesc in rag_cols:
        count = "—"
        if rag:
            try:
                col = getattr(rag, _RAG_ATTR_MAP.get(cname, cname + '_collection'), None)
                if col:
                    count = str(col.count())
            except Exception:
                count = "—"
        rag_rows.append({"集合": cname, "說明": cdesc, "文件數": count})
    st.dataframe(pd.DataFrame(rag_rows), use_container_width=True, hide_index=True,
                 column_config={"說明": st.column_config.TextColumn(width="large")})

    # ----------------------------------------------------------------
    # Engine configuration constants
    # ----------------------------------------------------------------
    st.subheader("⚙️ 引擎設定常數 (engine/config.py)")
    cfg_rows = [
        ("LLM_MODEL_NAME",          config.LLM_MODEL_NAME,          "Ollama/API 語言模型識別碼"),
        ("IMAGE_MODEL_NAME",        config.IMAGE_MODEL_NAME,         "預設影像模型識別碼"),
        ("VRAM_STRATEGY",           config.VRAM_STRATEGY,            "A=跳過影像 / B=換模型"),
        ("USER_VRAM_GB",            config.USER_VRAM_GB,             "總 GPU VRAM 預算（GB）"),
        ("IMAGE_VRAM_REQUIRED_GB",  config.IMAGE_VRAM_REQUIRED_GB,   "最低可用 VRAM 門檻（GB）"),
        ("IMAGE_GEN_MAX_FAILURES",  config.IMAGE_GEN_MAX_FAILURES,   "連續失敗幾次後停用影像生成"),
        ("IMAGE_GEN_MILESTONE_TURNS",config.IMAGE_GEN_MILESTONE_TURNS,"每 N 回合強制生成場景圖"),
        ("SESSION_MEMORY_WINDOW",   config.SESSION_MEMORY_WINDOW,    "滑動記憶窗口大小（回合數）"),
        ("CONTEXT_WINDOW_SIZE",     config.CONTEXT_WINDOW_SIZE,      "目標 token 預算（需與模型一致）"),
        ("EMBEDDING_MODEL",         config.EMBEDDING_MODEL or "(default MiniLM)", "ChromaDB 嵌入模型路徑"),
        ("SAVE_DIR",                config.SAVE_DIR,                 "SQLite 存檔目錄"),
        ("CHROMA_DB_DIR",           config.CHROMA_DB_DIR,            "ChromaDB 持久化目錄"),
    ]
    st.dataframe(
        pd.DataFrame(cfg_rows, columns=["常數", "值", "說明"]),
        use_container_width=True, hide_index=True,
        column_config={"說明": st.column_config.TextColumn(width="large")},
    )

    # Active image model details
    img_gen = st.session_state.get('img_gen')
    if img_gen:
        p = config.get_image_preset(img_gen.model_id)
        st.caption(
            f"🖼️ 目前影像模型: **{p['name']}** ({p['provider']}) — "
            f"{p.get('description','')} | "
            f"disabled={img_gen.is_disabled()} fail_count={img_gen._fail_count}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if st.session_state.current_session is None:
    main_menu()
else:
    game_loop()

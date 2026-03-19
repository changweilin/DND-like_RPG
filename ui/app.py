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

_RACES_FALLBACK   = ["Human", "Elf", "Dwarf", "Orc", "Halfling"]
_CLASSES_FALLBACK = ["Warrior", "Mage", "Rogue", "Cleric"]

# ---------------------------------------------------------------------------
# Localized display names for races and classes
# Internal values remain English; these are used only for UI display via
# format_func on selectboxes.
# ---------------------------------------------------------------------------
_RACE_L10N = {
    "zh": {
        "Human": "人類", "Elf": "精靈", "Dwarf": "矮人", "Halfling": "半身人",
        "Half-Orc": "半獸人", "Tiefling": "提夫林", "Dragonborn": "龍裔",
        "Gnome": "侏儒", "Orc": "獸人", "Goblin": "哥布林", "Leshy": "樹靈",
        "Human (Empire)": "人類（帝國）", "Human (Bretonnian)": "人類（布列托尼亞）",
        "Wood Elf": "木精靈", "High Elf": "高等精靈",
        "Ogryn": "歐乏人", "Ratling": "鼠人",
        "Ork": "獸人", "Troll": "巨魔",
        "Human (Cygnaran)": "人類（席格納）", "Human (Khadoran)": "人類（凱多）",
        "Dwarf (Rhulfolk)": "矮人（盧爾族）", "Gobber": "哥布",
        "Trollkin": "巨魔族", "Iosan Elf": "艾歐珊精靈",
        "Mutant": "變種人", "Human Survivor": "人類倖存者",
        "Inox": "伊諾克斯", "Valrath": "瓦拉斯", "Quatryl": "夸特瑞爾",
        "Orchid": "蘭花族", "Savvas": "薩瓦斯", "Aesther": "艾斯瑟",
        "Harrower": "收割者",
    },
    "ja": {
        "Human": "人間", "Elf": "エルフ", "Dwarf": "ドワーフ",
        "Halfling": "ハーフリング", "Half-Orc": "ハーフオーク",
        "Tiefling": "ティーフリング", "Dragonborn": "ドラゴンボーン",
        "Gnome": "ノーム", "Orc": "オーク", "Goblin": "ゴブリン",
        "Troll": "トロール", "Ork": "オーク", "Mutant": "ミュータント",
    },
    "ko": {
        "Human": "인간", "Elf": "엘프", "Dwarf": "드워프",
        "Halfling": "하프링", "Half-Orc": "하프오크",
        "Tiefling": "티플링", "Dragonborn": "드래곤본",
        "Gnome": "노움", "Orc": "오크", "Goblin": "고블린",
        "Troll": "트롤", "Ork": "오크", "Mutant": "변종인",
        "High Elf": "하이엘프", "Wood Elf": "우드엘프",
        "Human Survivor": "인간 생존자",
    },
    "fr": {
        "Human": "Humain", "Elf": "Elfe", "Dwarf": "Nain",
        "Halfling": "Halfelin", "Half-Orc": "Demi-Orc",
        "Tiefling": "Tiefelin", "Dragonborn": "Drakéide",
        "Gnome": "Gnome", "Orc": "Orque", "Goblin": "Gobelin",
        "Troll": "Troll", "Ork": "Orque", "Mutant": "Mutant",
        "High Elf": "Haut-Elfe", "Wood Elf": "Elfe sylvain",
        "Human Survivor": "Survivant humain",
    },
    "de": {
        "Human": "Mensch", "Elf": "Elf", "Dwarf": "Zwerg",
        "Halfling": "Halbling", "Half-Orc": "Halbork",
        "Tiefling": "Tiefling", "Dragonborn": "Drachengeborener",
        "Gnome": "Gnom", "Orc": "Ork", "Goblin": "Goblin",
        "Troll": "Troll", "Ork": "Ork", "Mutant": "Mutant",
        "High Elf": "Hochelfe", "Wood Elf": "Waldelfe",
        "Human Survivor": "Menschlicher Überlebender",
    },
    "es": {
        "Human": "Humano", "Elf": "Elfo", "Dwarf": "Enano",
        "Halfling": "Mediano", "Half-Orc": "Semiorco",
        "Gnome": "Gnomo", "Orc": "Orco", "Goblin": "Goblin",
        "Troll": "Trol", "Mutant": "Mutante",
    },
}

_CLASS_L10N = {
    "zh": {"Warrior": "戰士", "Mage": "法師", "Rogue": "盜賊", "Cleric": "牧師"},
    "ja": {"Warrior": "戦士", "Mage": "魔法使い", "Rogue": "盗賊", "Cleric": "聖職者"},
    "ko": {"Warrior": "전사", "Mage": "마법사", "Rogue": "도적", "Cleric": "성직자"},
    "fr": {"Warrior": "Guerrier", "Mage": "Mage", "Rogue": "Roublard", "Cleric": "Clerc"},
    "de": {"Warrior": "Krieger", "Mage": "Magier", "Rogue": "Schurke", "Cleric": "Kleriker"},
    "es": {"Warrior": "Guerrero", "Mage": "Mago", "Rogue": "Pícaro", "Cleric": "Clérigo"},
}
_GENDERS          = ["Male", "Female", "Non-binary", "Other"]

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
    st.session_state.img_gen         = ImageGenerator(
        on_vram_acquire=lambda: st.session_state.llm.unload_from_vram(),
        on_vram_release=lambda: st.session_state.llm.preload_to_vram(),
    )

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
        st.session_state[f"ng_race_{_slot}"] = _r
        _c = prefs.get(f'class_{_slot}', 'Warrior')
        st.session_state[f"ng_class_{_slot}"] = _c
        st.session_state[f"ng_app_{_slot}"] = prefs.get(f'app_{_slot}', '')
        st.session_state[f"ng_per_{_slot}"] = prefs.get(f'per_{_slot}', '')
        if _slot > 0:
            st.session_state[f"ng_is_ai_{_slot}"] = prefs.get(f'is_ai_{_slot}', False)
    # Custom lore / image suffix text inputs
    st.session_state["new_game_lore"]       = prefs.get('custom_lore', '')
    st.session_state["new_game_custom_img"] = prefs.get('custom_img_suffix', '')

    # Form-field keys — initialised here so values survive reruns caused by
    # the world-setting selectbox (which lives outside the form).
    st.session_state["ng_save_name"]      = ''
    st.session_state["ng_difficulty"]     = prefs.get('difficulty', 'Normal')
    st.session_state["ng_img_style"]      = prefs.get('img_style', 0)
    st.session_state["ng_num_players"]    = prefs.get('num_players', 1)
    st.session_state["new_game_ws_select"] = prefs.get('world_idx', 0)

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
        "regen_appearance": "🎲 Regenerate appearance",
        "personality":     "Personality",
        "gender":          "Gender",
        "ai_controlled":   "🤖 AI-controlled",
        "ai_personality":  "AI Personality",
        "ai_difficulty":   "AI Difficulty",
        "difficulty_easy":   "Easy",
        "difficulty_normal": "Normal",
        "difficulty_hard":   "Hard",
        "no_saves":        "No saves found.",
        "save_required":   "Player 1 Name is required.",
        "map_hint":        "🗺️ Map and portraits are generated after starting the game.",
        "dup_title":       "⚠️ Save Name Conflict",
        "dup_warning":     "already exists. Choose an action:",
        "dup_overwrite":   "🗑️ Overwrite",
        "dup_overwrite_desc": "Delete the old save and start fresh with your new settings.",
        "dup_inherit":     "📂 Load Existing",
        "dup_inherit_desc": "Discard new settings and continue from the existing save.",
        "dup_cancel":      "✖ Cancel",
        # Game tabs
        "tab_story":         "📖 Story",
        "tab_gameboard":     "🗺️ Game Board",
        "tab_characters":    "👥 Characters",
        "tab_rules":         "📜 Rules",
        "tab_orgs":          "🏛️ Organizations",
        "tab_book":          "📕 Book",
        "tab_godmode":       "🔮 God Mode",
        # Game board
        "manual_dice":       "🎲 Manual Dice",
        "dice_legend":       "🟡 Max  🔴 Min  — Normal",
        "scoreboard":        "🏆 Score Board",
        "continent_map":     "🌍 Continent Map",
        "regen_map":         "🔄 Regenerate Map",
        "gen_map":           "🎨 Generate Continent Map",
        "location_map":      "🗺️ Location Tracking Map",
        "location_map_hint": "Location map will appear after the game starts (switch to the Story tab to begin your first turn).",
        "relation_graph":    "🔗 Relationship Graph",
        "ai_acting":         "(AI) is acting…  Switch to the Story tab to view.",
        "player_turn":       "it's your turn!  Switch to the Story tab to enter your action.",
        # Story tab
        "char_fallen":       "has fallen!  Waiting for the next player…",
        "choose_action":     "{name}, choose your action:",
        "custom_action_expander": "✏️ Or custom action…",
        "custom_action_input":    "Enter custom action:",
        "execute":           "Execute",
        "action_prompt_single": "Enter your action…",
        "action_prompt_multi":  "{flag} {name}'s action…",
        "dm_thinking":       "is thinking…",
        "allow_custom":      "Allow custom action input",
        "allow_custom_help": "When checked, you can type a custom action during the game instead of choosing from options.",
        "prologue_badge":    "📜 Prologue · Turn 0",
        "writing_prologue":  "is writing the prologue…",
        # Characters tab
        "party_details":     "👥 Party Members — Details",
        "dead_tag":          " ☠ Fallen",
        "active_tag":        " ◀ Active",
        "portrait_not_generated": "🖼️ Not generated",
        "gen_portrait":      "🎨 Generate Portrait",
        "regen_portrait_help": "Regenerate {name}'s portrait",
        "skills_label":      "Skills",
        "inventory_label":   "Inventory",
        "appearance_label":  "Appearance",
        "personality_label": "Personality",
        "relations_expander":"🔗 Relations",
        # Relations / NPC tab
        "no_relations":      "No relationship data yet. As the story progresses, character and organization relationships will be automatically recorded here.",
        "no_known_relations":"No known relations",
        "rel_type_filter":   "Filter relation types",
        # Organizations tab
        "no_orgs":           "No organizations discovered yet. Continue your adventure and organization intel will be automatically recorded here.",
        "search_orgs":       "🔍 Search Organizations",
        "search_orgs_ph":    "Enter name, type, leader…",
        "org_founder":       "Founder",
        "org_leader":        "Current Leader",
        "org_members":       "Members",
        "org_hq":            "Headquarters",
        "org_alignment":     "Alignment",
        "org_first_seen":    "First Appeared",
        "org_history":       "History",
        "org_relations":     "Relations",
        "org_count":         "**{n}** organizations recorded",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — Known Characters ({n})",
        "no_npcs":           "No NPCs encountered yet. As the story progresses, NPCs will be automatically recorded here.",
        "npc_affinity":      "Affinity",
        "npc_state_lbl":     "State",
        "npc_health_lbl":    "Health",
        "npc_goal_lbl":      "🎯 Goal:",
        "npc_aliases_lbl":   "Aliases:",
        "npc_bio_lbl":       "Biography",
        # Relation graph
        "rel_count":         "**{n}** relationships recorded",
        "prologue_turn":     "Prologue",
        "turn_label":        "Turn {n}",
        # Rules tab
        "search_manual":     "🔍 Search Manual",
        "search_manual_ph":  "Enter keywords, e.g.: attack, dice, stealth…",
        "chapter_label":     "Chapter",
        "toc_expander":      "📋 Table of Contents — Click to jump",
        # Book tab
        "no_story":          "📕 No story log yet.\nAfter playing, each turn's story and images are auto-saved here.",
        "latest_pages":      "📌 Latest Pages (Last 2)",
        "read_pages_header": "📖 Read Pages",
        # Image generation
        "img_style_expander":        "🎨 Image Style",
        "regen_all_images":          "🔄 Regenerate All Images",
        "img_gen_disabled_auto":     "⚠️ Image generation auto-disabled (repeated VRAM shortage)",
        "img_gen_reenable":          "🔄 Re-enable",
        "img_gen_ready":             "✅ Image generation ready",
        "img_gen_vram_warn":         "⚡ VRAM may be insufficient, some scene images may be skipped",
        "img_gen_disabled_notice":   "🚫 Image generation disabled, all generate buttons are frozen.",
        "img_gen_map_fail":          "⚠️ Map generation failed (VRAM shortage or GPU not ready).",
        "img_gen_portrait_fail":     "⚠️ {name} portrait generation failed.",
        "img_style_cur":             "Current",
        # Sidebar
        "save_game":         "💾 Save",
        "quit_game":         "🚪 Quit",
        "game_saved":        "Game saved!",
        # VRAM
        "vram_ok":           "✅ VRAM sufficient (~{n} GB needed)",
        "vram_insufficient": "❌ VRAM insufficient (~{n} GB needed, GPU only {total:.1f} GB)",
        "vram_warning":      "⚠️ CUDA available but VRAM unreadable (~{n} GB needed)",
        "no_cuda":           "❌ No CUDA GPU",
        # Save / load messages
        "loaded_party":      "Loaded party [{names}]!",
        "load_failed":       "Failed to load save file.",
        "deleted_save":      "Deleted save '{save}'.",
        "delete_failed":     "Failed to delete save '{save}'.",
        "overwrite_failed":  "Failed to overwrite.",
        "load_existing_failed": "Failed to load existing save.",
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
        "regen_appearance": "🎲 重新生成外貌",
        "personality":     "個性描述",
        "gender":          "性別",
        "ai_controlled":   "🤖 AI 操控",
        "ai_personality":  "AI 個性",
        "ai_difficulty":   "AI 難度",
        "difficulty_easy":   "簡單",
        "difficulty_normal": "普通",
        "difficulty_hard":   "困難",
        "no_saves":        "找不到存檔。",
        "save_required":   "玩家 1 名字為必填。",
        "map_hint":        "🗺️ 大陸地圖與角色肖像在開始遊戲後生成。",
        "dup_title":       "⚠️ 存檔名稱衝突",
        "dup_warning":     "已存在。請選擇操作：",
        "dup_overwrite":   "🗑️ 覆蓋",
        "dup_overwrite_desc": "刪除舊存檔，以目前設定重新開始。",
        "dup_inherit":     "📂 繼承存檔",
        "dup_inherit_desc": "放棄新設定，繼續讀取現有存檔。",
        "dup_cancel":      "✖ 取消",
        # Game tabs
        "tab_story":         "📖 故事",
        "tab_gameboard":     "🗺️ 遊戲板",
        "tab_characters":    "👥 角色",
        "tab_rules":         "📜 規則",
        "tab_orgs":          "🏛️ 組織",
        "tab_book":          "📕 書本",
        "tab_godmode":       "🔮 上帝模式",
        # Game board
        "manual_dice":       "🎲 手動擲骰",
        "dice_legend":       "🟡 最大值  🔴 最小值  — 普通結果",
        "scoreboard":        "🏆 得分板",
        "continent_map":     "🌍 大陸地圖",
        "regen_map":         "🔄 重新生成地圖",
        "gen_map":           "🎨 生成大陸地圖",
        "location_map":      "🗺️ 位置追蹤地圖",
        "location_map_hint": "位置地圖將在遊戲開始後顯示（切換至故事頁籤開始第一個回合）。",
        "relation_graph":    "🔗 關係圖",
        "ai_acting":         "(AI) 正在行動… 切換至故事頁籤查看。",
        "player_turn":       "的回合！切換至故事頁籤輸入行動。",
        # Story tab
        "char_fallen":       "已倒下！等待下一位玩家…",
        "choose_action":     "{name}，選擇你的行動:",
        "custom_action_expander": "✏️ 或自訂行動…",
        "custom_action_input":    "輸入其他行動:",
        "execute":           "執行",
        "action_prompt_single": "輸入你的行動…",
        "action_prompt_multi":  "{flag} {name} 的行動…",
        "dm_thinking":       "正在思考…",
        "allow_custom":      "允許自訂行動輸入",
        "allow_custom_help": "勾選後，遊戲中可輸入非選項的自訂行動",
        "prologue_badge":    "📜 開場白 · Turn 0",
        "writing_prologue":  "正在書寫開場白…",
        # Characters tab
        "party_details":     "👥 隊伍成員 — 詳細數值",
        "dead_tag":          " ☠ 已倒下",
        "active_tag":        " ◀ 行動中",
        "portrait_not_generated": "🖼️ 尚未生成",
        "gen_portrait":      "🎨 生成肖像",
        "regen_portrait_help": "重新生成 {name} 肖像",
        "skills_label":      "技能",
        "inventory_label":   "背包",
        "appearance_label":  "外觀",
        "personality_label": "性格",
        "relations_expander":"🔗 關係",
        # Relations / NPC tab
        "no_relations":      "尚無關係資料。隨著故事發展，角色與組織的關係將自動記錄於此。",
        "no_known_relations":"無已知關係",
        "rel_type_filter":   "篩選關係類型",
        # Organizations tab
        "no_orgs":           "尚未發現任何組織。繼續冒險，組織情報將會自動記錄在此。",
        "search_orgs":       "🔍 搜尋組織",
        "search_orgs_ph":    "輸入名稱、類型、領導人…",
        "org_founder":       "創辦人",
        "org_leader":        "現任領導人",
        "org_members":       "成員規模",
        "org_hq":            "據點",
        "org_alignment":     "陣營傾向",
        "org_first_seen":    "首次登場",
        "org_history":       "歷史沿革",
        "org_relations":     "關係",
        "org_count":         "共記錄 **{n}** 個組織",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — 已知角色（{n}）",
        "no_npcs":           "尚未遭遇任何 NPC。隨著故事發展，NPC 將會自動記錄在此。",
        "npc_affinity":      "好感度",
        "npc_state_lbl":     "狀態",
        "npc_health_lbl":    "健康",
        "npc_goal_lbl":      "🎯 目標：",
        "npc_aliases_lbl":   "代稱：",
        "npc_bio_lbl":       "生平",
        # Relation graph
        "rel_count":         "共 **{n}** 條關係記錄",
        "prologue_turn":     "開場白",
        "turn_label":        "第 {n} 回合",
        # Rules tab
        "search_manual":     "🔍 搜尋手冊關鍵字",
        "search_manual_ph":  "輸入關鍵字，例如：attack、骰子、stealth…",
        "chapter_label":     "章節",
        "toc_expander":      "📋 目錄 — 點擊快速跳章",
        # Book tab
        "no_story":          "📕 尚無故事紀錄。\n遊戲進行後，每回合會自動儲存故事與圖片，在此閱讀完整冒險記錄。",
        "latest_pages":      "📌 最新記錄（最近 2 頁）",
        "read_pages_header": "📖 翻頁閱讀",
        # Image generation
        "img_style_expander":        "🎨 影像風格",
        "regen_all_images":          "🔄 重新生成所有影像",
        "img_gen_disabled_auto":     "⚠️ 影像生成已自動停用（連續 VRAM 不足）",
        "img_gen_reenable":          "🔄 重新啟用",
        "img_gen_ready":             "✅ 影像生成就緒",
        "img_gen_vram_warn":         "⚡ VRAM 可能不足，部分場景圖可能跳過生成",
        "img_gen_disabled_notice":   "🚫 影像生成已停用，所有生成按鈕均已凍結。",
        "img_gen_map_fail":          "⚠️ 地圖生成失敗（VRAM 不足或 GPU 尚未就緒）。",
        "img_gen_portrait_fail":     "⚠️ {name} 肖像生成失敗。",
        "img_style_cur":             "目前",
        # Sidebar
        "save_game":         "💾 儲存",
        "quit_game":         "🚪 離開",
        "game_saved":        "遊戲已儲存！",
        # VRAM
        "vram_ok":           "✅ VRAM 足夠（需 ~{n} GB）",
        "vram_insufficient": "❌ VRAM 不足（需 ~{n} GB，GPU 僅 {total:.1f} GB）",
        "vram_warning":      "⚠️ CUDA 可用，但無法讀取 VRAM（需 ~{n} GB）",
        "no_cuda":           "❌ 無 CUDA GPU",
        # Save / load messages
        "loaded_party":      "成功載入隊伍 [{names}]！",
        "load_failed":       "載入存檔失敗。",
        "deleted_save":      "已刪除存檔「{save}」。",
        "delete_failed":     "刪除存檔「{save}」失敗。",
        "overwrite_failed":  "覆蓋失敗。",
        "load_existing_failed": "載入現有存檔失敗。",
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
        "gender":          "性別",
        "ai_controlled":   "🤖 AI操作",
        "ai_personality":  "AIの個性",
        "ai_difficulty":   "AIの難易度",
        "difficulty_easy":   "簡単",
        "difficulty_normal": "普通",
        "difficulty_hard":   "難しい",
        "no_saves":        "セーブデータが見つかりません。",
        "save_required":   "プレイヤー1の名前は必須です。",
        "map_hint":        "🗺️ マップとポートレートはゲーム開始後に生成されます。",
        "dup_title":       "⚠️ セーブ名の競合",
        "dup_warning":     "はすでに存在します。操作を選択してください：",
        "dup_overwrite":   "🗑️ 上書き",
        "dup_overwrite_desc": "古いセーブを削除し、新しい設定で開始します。",
        "dup_inherit":     "📂 既存を読込",
        "dup_inherit_desc": "新しい設定を破棄し、既存のセーブを続けます。",
        "dup_cancel":      "✖ キャンセル",
        # Game tabs
        "tab_story":         "📖 ストーリー",
        "tab_gameboard":     "🗺️ ゲーム板",
        "tab_characters":    "👥 キャラクター",
        "tab_rules":         "📜 ルール",
        "tab_orgs":          "🏛️ 組織",
        "tab_book":          "📕 ブック",
        "tab_godmode":       "🔮 神モード",
        # Game board
        "manual_dice":       "🎲 手動ダイス",
        "dice_legend":       "🟡 最大値  🔴 最小値  — 通常",
        "scoreboard":        "🏆 スコアボード",
        "continent_map":     "🌍 大陸マップ",
        "regen_map":         "🔄 マップ再生成",
        "gen_map":           "🎨 大陸マップ生成",
        "location_map":      "🗺️ 位置追跡マップ",
        "location_map_hint": "ゲーム開始後に位置マップが表示されます（ストーリータブで最初のターンを開始してください）。",
        "relation_graph":    "🔗 関係グラフ",
        "ai_acting":         "(AI) が行動中… ストーリータブで確認してください。",
        "player_turn":       "のターン！ ストーリータブで行動を入力してください。",
        # Story tab
        "char_fallen":       "は倒れました！次のプレイヤーを待っています…",
        "choose_action":     "{name}、行動を選んでください:",
        "custom_action_expander": "✏️ またはカスタム行動…",
        "custom_action_input":    "カスタム行動を入力:",
        "execute":           "実行",
        "action_prompt_single": "行動を入力…",
        "action_prompt_multi":  "{flag} {name} の行動…",
        "dm_thinking":       "考え中…",
        "allow_custom":      "カスタム行動入力を許可",
        "allow_custom_help": "チェックすると、ゲーム中に選択肢以外のカスタム行動を入力できます",
        "prologue_badge":    "📜 プロローグ · ターン 0",
        "writing_prologue":  "プロローグを作成中…",
        # Characters tab
        "party_details":     "👥 パーティメンバー — 詳細",
        "dead_tag":          " ☠ 倒れた",
        "active_tag":        " ◀ 行動中",
        "portrait_not_generated": "🖼️ 未生成",
        "gen_portrait":      "🎨 ポートレート生成",
        "regen_portrait_help": "{name}のポートレートを再生成",
        "skills_label":      "スキル",
        "inventory_label":   "インベントリ",
        "appearance_label":  "外見",
        "personality_label": "性格",
        "relations_expander":"🔗 関係",
        # Relations / NPC tab
        "no_relations":      "関係データがまだありません。ストーリーが進むにつれ、キャラクターと組織の関係が自動的に記録されます。",
        "no_known_relations":"既知の関係なし",
        "rel_type_filter":   "関係タイプをフィルター",
        # Organizations tab
        "no_orgs":           "組織はまだ発見されていません。冒険を続けると、組織情報が自動的に記録されます。",
        "search_orgs":       "🔍 組織を検索",
        "search_orgs_ph":    "名前、タイプ、リーダーを入力…",
        "org_founder":       "創設者",
        "org_leader":        "現在のリーダー",
        "org_members":       "メンバー数",
        "org_hq":            "本部",
        "org_alignment":     "陣営",
        "org_first_seen":    "初登場",
        "org_history":       "歴史",
        "org_relations":     "関係",
        "org_count":         "**{n}** 件の組織を記録",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — 既知のキャラクター（{n}）",
        "no_npcs":           "まだNPCに遭遇していません。ストーリーが進むにつれ、NPCが自動的に記録されます。",
        "npc_affinity":      "好感度",
        "npc_state_lbl":     "状態",
        "npc_health_lbl":    "健康",
        "npc_goal_lbl":      "🎯 目標：",
        "npc_aliases_lbl":   "別名：",
        "npc_bio_lbl":       "経歴",
        # Relation graph
        "rel_count":         "**{n}** 件の関係を記録",
        "prologue_turn":     "プロローグ",
        "turn_label":        "ターン {n}",
        # Rules tab
        "search_manual":     "🔍 マニュアルを検索",
        "search_manual_ph":  "キーワードを入力（例：attack、ダイス、stealth）…",
        "chapter_label":     "章",
        "toc_expander":      "📋 目次 — クリックして章にジャンプ",
        # Book tab
        "no_story":          "📕 ストーリーログがまだありません。\nゲームを進めると、各ターンのストーリーと画像が自動的に保存されます。",
        "latest_pages":      "📌 最新ページ（直近 2 ページ）",
        "read_pages_header": "📖 ページ閲覧",
        # Image generation
        "img_style_expander":        "🎨 画像スタイル",
        "regen_all_images":          "🔄 すべての画像を再生成",
        "img_gen_disabled_auto":     "⚠️ 画像生成が自動的に無効化されました（VRAM 不足が続いています）",
        "img_gen_reenable":          "🔄 再有効化",
        "img_gen_ready":             "✅ 画像生成準備完了",
        "img_gen_vram_warn":         "⚡ VRAM が不足している可能性があります。一部のシーン画像はスキップされる場合があります",
        "img_gen_disabled_notice":   "🚫 画像生成が無効になっています。すべての生成ボタンが凍結されています。",
        "img_gen_map_fail":          "⚠️ マップ生成に失敗しました（VRAM 不足または GPU 未準備）。",
        "img_gen_portrait_fail":     "⚠️ {name} のポートレート生成に失敗しました。",
        "img_style_cur":             "現在",
        # Sidebar
        "save_game":         "💾 保存",
        "quit_game":         "🚪 終了",
        "game_saved":        "ゲームが保存されました！",
        # VRAM
        "vram_ok":           "✅ VRAM 十分（~{n} GB 必要）",
        "vram_insufficient": "❌ VRAM 不足（~{n} GB 必要、GPU は {total:.1f} GB のみ）",
        "vram_warning":      "⚠️ CUDA 使用可能ですが VRAM を読み取れません（~{n} GB 必要）",
        "no_cuda":           "❌ CUDA GPU なし",
        # Save / load messages
        "loaded_party":      "パーティを読み込みました [{names}]！",
        "load_failed":       "セーブファイルの読み込みに失敗しました。",
        "deleted_save":      "セーブ「{save}」を削除しました。",
        "delete_failed":     "セーブ「{save}」の削除に失敗しました。",
        "overwrite_failed":  "上書きに失敗しました。",
        "load_existing_failed": "既存のセーブの読み込みに失敗しました。",
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
        "gender":          "Género",
        "ai_controlled":   "🤖 IA controlada",
        "ai_personality":  "Personalidad IA",
        "ai_difficulty":   "Dificultad IA",
        "difficulty_easy":   "Fácil",
        "difficulty_normal": "Normal",
        "difficulty_hard":   "Difícil",
        "no_saves":        "No hay partidas guardadas.",
        "save_required":   "El nombre del Jugador 1 es obligatorio.",
        "map_hint":        "🗺️ El mapa y retratos se generan al iniciar el juego.",
        "dup_title":       "⚠️ Conflicto de nombre",
        "dup_warning":     "ya existe. Elige una acción:",
        "dup_overwrite":   "🗑️ Sobreescribir",
        "dup_overwrite_desc": "Elimina el guardado antiguo y empieza con la nueva configuración.",
        "dup_inherit":     "📂 Cargar existente",
        "dup_inherit_desc": "Descarta la nueva configuración y continúa desde el guardado existente.",
        "dup_cancel":      "✖ Cancelar",
        # Game tabs
        "tab_story":         "📖 Historia",
        "tab_gameboard":     "🗺️ Tablero",
        "tab_characters":    "👥 Personajes",
        "tab_rules":         "📜 Reglas",
        "tab_orgs":          "🏛️ Organizaciones",
        "tab_book":          "📕 Libro",
        "tab_godmode":       "🔮 Modo Dios",
        # Game board
        "manual_dice":       "🎲 Dados manuales",
        "dice_legend":       "🟡 Máx  🔴 Mín  — Normal",
        "scoreboard":        "🏆 Marcador",
        "continent_map":     "🌍 Mapa continental",
        "regen_map":         "🔄 Regenerar mapa",
        "gen_map":           "🎨 Generar mapa continental",
        "location_map":      "🗺️ Mapa de ubicación",
        "location_map_hint": "El mapa de ubicación aparecerá después de iniciar el juego (cambia a la pestaña Historia para comenzar tu primer turno).",
        "relation_graph":    "🔗 Gráfico de relaciones",
        "ai_acting":         "(IA) está actuando… Cambia a la pestaña Historia para ver.",
        "player_turn":       "¡es tu turno!  Cambia a la pestaña Historia para ingresar tu acción.",
        # Story tab
        "char_fallen":       "¡ha caído!  Esperando al siguiente jugador…",
        "choose_action":     "{name}, elige tu acción:",
        "custom_action_expander": "✏️ O acción personalizada…",
        "custom_action_input":    "Ingresa acción personalizada:",
        "execute":           "Ejecutar",
        "action_prompt_single": "Ingresa tu acción…",
        "action_prompt_multi":  "{flag} Acción de {name}…",
        "dm_thinking":       "está pensando…",
        "allow_custom":      "Permitir entrada de acción personalizada",
        "allow_custom_help": "Si está marcado, puedes escribir una acción personalizada durante el juego en lugar de elegir entre las opciones.",
        "prologue_badge":    "📜 Prólogo · Turno 0",
        "writing_prologue":  "está escribiendo el prólogo…",
        # Characters tab
        "party_details":     "👥 Miembros del grupo — Detalles",
        "dead_tag":          " ☠ Caído",
        "active_tag":        " ◀ Activo",
        "portrait_not_generated": "🖼️ No generado",
        "gen_portrait":      "🎨 Generar retrato",
        "regen_portrait_help": "Regenerar retrato de {name}",
        "skills_label":      "Habilidades",
        "inventory_label":   "Inventario",
        "appearance_label":  "Apariencia",
        "personality_label": "Personalidad",
        "relations_expander":"🔗 Relaciones",
        # Relations / NPC tab
        "no_relations":      "No hay datos de relaciones todavía. A medida que avance la historia, las relaciones entre personajes y organizaciones se registrarán automáticamente aquí.",
        "no_known_relations":"Sin relaciones conocidas",
        "rel_type_filter":   "Filtrar tipos de relación",
        # Organizations tab
        "no_orgs":           "No se han descubierto organizaciones. Continúa la aventura y la información de organizaciones se registrará automáticamente.",
        "search_orgs":       "🔍 Buscar organizaciones",
        "search_orgs_ph":    "Ingresa nombre, tipo, líder…",
        "org_founder":       "Fundador",
        "org_leader":        "Líder actual",
        "org_members":       "Miembros",
        "org_hq":            "Sede",
        "org_alignment":     "Alineamiento",
        "org_first_seen":    "Primera aparición",
        "org_history":       "Historia",
        "org_relations":     "Relaciones",
        "org_count":         "**{n}** organizaciones registradas",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — Personajes conocidos ({n})",
        "no_npcs":           "Aún no se ha encontrado ningún NPC. A medida que avance la historia, los NPCs se registrarán automáticamente.",
        "npc_affinity":      "Afinidad",
        "npc_state_lbl":     "Estado",
        "npc_health_lbl":    "Salud",
        "npc_goal_lbl":      "🎯 Objetivo:",
        "npc_aliases_lbl":   "Alias:",
        "npc_bio_lbl":       "Biografía",
        # Relation graph
        "rel_count":         "**{n}** relaciones registradas",
        "prologue_turn":     "Prólogo",
        "turn_label":        "Turno {n}",
        # Rules tab
        "search_manual":     "🔍 Buscar en el manual",
        "search_manual_ph":  "Ingresa palabras clave, ej.: ataque, dado, stealth…",
        "chapter_label":     "Capítulo",
        "toc_expander":      "📋 Tabla de contenidos — Clic para saltar",
        # Book tab
        "no_story":          "📕 Sin registro de historia todavía.\nDespués de jugar, la historia e imágenes de cada turno se guardan automáticamente.",
        "latest_pages":      "📌 Páginas recientes (últimas 2)",
        "read_pages_header": "📖 Leer páginas",
        # Image generation
        "img_style_expander":        "🎨 Estilo de imagen",
        "regen_all_images":          "🔄 Regenerar todas las imágenes",
        "img_gen_disabled_auto":     "⚠️ Generación de imágenes desactivada automáticamente (VRAM insuficiente repetida)",
        "img_gen_reenable":          "🔄 Volver a activar",
        "img_gen_ready":             "✅ Generación de imágenes lista",
        "img_gen_vram_warn":         "⚡ La VRAM puede ser insuficiente, algunas imágenes de escena pueden omitirse",
        "img_gen_disabled_notice":   "🚫 Generación de imágenes desactivada, todos los botones de generación están bloqueados.",
        "img_gen_map_fail":          "⚠️ Error al generar el mapa (VRAM insuficiente o GPU no lista).",
        "img_gen_portrait_fail":     "⚠️ Error al generar el retrato de {name}.",
        "img_style_cur":             "Actual",
        # Sidebar
        "save_game":         "💾 Guardar",
        "quit_game":         "🚪 Salir",
        "game_saved":        "¡Partida guardada!",
        # VRAM
        "vram_ok":           "✅ VRAM suficiente (~{n} GB necesarios)",
        "vram_insufficient": "❌ VRAM insuficiente (~{n} GB necesarios, GPU solo {total:.1f} GB)",
        "vram_warning":      "⚠️ CUDA disponible pero VRAM no legible (~{n} GB necesarios)",
        "no_cuda":           "❌ Sin GPU CUDA",
        # Save / load messages
        "loaded_party":      "¡Grupo cargado [{names}]!",
        "load_failed":       "Error al cargar el archivo de guardado.",
        "deleted_save":      "Guardado '{save}' eliminado.",
        "delete_failed":     "Error al eliminar '{save}'.",
        "overwrite_failed":  "Error al sobreescribir.",
        "load_existing_failed": "Error al cargar el guardado existente.",
    },
    "简体中文": {
        "model_expander":      "⚙️ 模型与语言",
        "llm_model_expander":  "⚙️ LLM 模型",
        "language_expander":   "🌐 语言",
        "switch_model":    "切换模型",
        "ui_language":     "🌐 界面语言",
        "new_game":        "新游戏",
        "load_game":       "加载游戏",
        "save_name":       "存档名称",
        "difficulty":      "难度",
        "world_setting":   "世界设定",
        "universe":        "世界观",
        "custom_lore":     "自定义世界观（选填）",
        "img_style_hdr":   "🎨 图像风格",
        "img_style_lbl":   "艺术风格",
        "custom_suffix":   "自定义风格后缀（选填）",
        "party_hdr":       "队伍（1-6 人）",
        "num_players":     "玩家人数",
        "start_adventure": "开始冒险",
        "name":            "名字",
        "race":            "种族",
        "char_class":      "职业",
        "appearance":      "外貌描述",
        "regen_appearance": "🎲 重新生成外貌",
        "personality":     "个性描述",
        "gender":          "性别",
        "ai_controlled":   "🤖 AI 操控",
        "ai_personality":  "AI 个性",
        "ai_difficulty":   "AI 难度",
        "difficulty_easy":   "简单",
        "difficulty_normal": "普通",
        "difficulty_hard":   "困难",
        "no_saves":        "找不到存档。",
        "save_required":   "存档名称与玩家 1 名字为必填。",
        "map_hint":        "🗺️ 大陆地图与角色肖像在开始游戏后生成。",
        "dup_title":       "⚠️ 存档名称冲突",
        "dup_warning":     "已存在。请选择操作：",
        "dup_overwrite":   "🗑️ 覆盖",
        "dup_overwrite_desc": "删除旧存档，以当前设定重新开始。",
        "dup_inherit":     "📂 继承存档",
        "dup_inherit_desc": "放弃新设定，继续读取现有存档。",
        "dup_cancel":      "✖ 取消",
        # Game tabs
        "tab_story":         "📖 故事",
        "tab_gameboard":     "🗺️ 游戏板",
        "tab_characters":    "👥 角色",
        "tab_rules":         "📜 规则",
        "tab_orgs":          "🏛️ 组织",
        "tab_book":          "📕 书本",
        "tab_godmode":       "🔮 上帝模式",
        # Game board
        "manual_dice":       "🎲 手动掷骰",
        "dice_legend":       "🟡 最大值  🔴 最小值  — 普通结果",
        "scoreboard":        "🏆 得分板",
        "continent_map":     "🌍 大陆地图",
        "regen_map":         "🔄 重新生成地图",
        "gen_map":           "🎨 生成大陆地图",
        "location_map":      "🗺️ 位置追踪地图",
        "location_map_hint": "位置地图将在游戏开始后显示（切换至故事页签开始第一个回合）。",
        "relation_graph":    "🔗 关系图",
        "ai_acting":         "(AI) 正在行动… 切换至故事页签查看。",
        "player_turn":       "的回合！切换至故事页签输入行动。",
        # Story tab
        "char_fallen":       "已倒下！等待下一位玩家…",
        "choose_action":     "{name}，选择你的行动:",
        "custom_action_expander": "✏️ 或自定义行动…",
        "custom_action_input":    "输入其他行动:",
        "execute":           "执行",
        "action_prompt_single": "输入你的行动…",
        "action_prompt_multi":  "{flag} {name} 的行动…",
        "dm_thinking":       "正在思考…",
        "allow_custom":      "允许自定义行动输入",
        "allow_custom_help": "勾选后，游戏中可输入非选项的自定义行动",
        "prologue_badge":    "📜 开场白 · Turn 0",
        "writing_prologue":  "正在书写开场白…",
        # Characters tab
        "party_details":     "👥 队伍成员 — 详细数值",
        "dead_tag":          " ☠ 已倒下",
        "active_tag":        " ◀ 行动中",
        "portrait_not_generated": "🖼️ 尚未生成",
        "gen_portrait":      "🎨 生成肖像",
        "regen_portrait_help": "重新生成 {name} 肖像",
        "skills_label":      "技能",
        "inventory_label":   "背包",
        "appearance_label":  "外观",
        "personality_label": "性格",
        "relations_expander":"🔗 关系",
        # Relations / NPC tab
        "no_relations":      "尚无关系资料。随着故事发展，角色与组织的关系将自动记录于此。",
        "no_known_relations":"无已知关系",
        "rel_type_filter":   "筛选关系类型",
        # Organizations tab
        "no_orgs":           "尚未发现任何组织。继续冒险，组织情报将会自动记录在此。",
        "search_orgs":       "🔍 搜索组织",
        "search_orgs_ph":    "输入名称、类型、领导人…",
        "org_founder":       "创始人",
        "org_leader":        "现任领导人",
        "org_members":       "成员规模",
        "org_hq":            "据点",
        "org_alignment":     "阵营倾向",
        "org_first_seen":    "首次登场",
        "org_history":       "历史沿革",
        "org_relations":     "关系",
        "org_count":         "共记录 **{n}** 个组织",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — 已知角色（{n}）",
        "no_npcs":           "尚未遭遇任何 NPC。随着故事发展，NPC 将会自动记录在此。",
        "npc_affinity":      "好感度",
        "npc_state_lbl":     "状态",
        "npc_health_lbl":    "健康",
        "npc_goal_lbl":      "🎯 目标：",
        "npc_aliases_lbl":   "代称：",
        "npc_bio_lbl":       "生平",
        # Relation graph
        "rel_count":         "共 **{n}** 条关系记录",
        "prologue_turn":     "开场白",
        "turn_label":        "第 {n} 回合",
        # Rules tab
        "search_manual":     "🔍 搜索手册关键字",
        "search_manual_ph":  "输入关键字，例如：attack、骰子、stealth…",
        "chapter_label":     "章节",
        "toc_expander":      "📋 目录 — 点击快速跳章",
        # Book tab
        "no_story":          "📕 尚无故事记录。\n游戏进行后，每回合会自动保存故事与图片，在此阅读完整冒险记录。",
        "latest_pages":      "📌 最新记录（最近 2 页）",
        "read_pages_header": "📖 翻页阅读",
        # Image generation
        "img_style_expander":        "🎨 图像风格",
        "regen_all_images":          "🔄 重新生成所有图像",
        "img_gen_disabled_auto":     "⚠️ 图像生成已自动停用（连续 VRAM 不足）",
        "img_gen_reenable":          "🔄 重新启用",
        "img_gen_ready":             "✅ 图像生成就绪",
        "img_gen_vram_warn":         "⚡ VRAM 可能不足，部分场景图可能跳过生成",
        "img_gen_disabled_notice":   "🚫 图像生成已停用，所有生成按钮均已冻结。",
        "img_gen_map_fail":          "⚠️ 地图生成失败（VRAM 不足或 GPU 尚未就绪）。",
        "img_gen_portrait_fail":     "⚠️ {name} 肖像生成失败。",
        "img_style_cur":             "当前",
        # Sidebar
        "save_game":         "💾 保存",
        "quit_game":         "🚪 离开",
        "game_saved":        "游戏已保存！",
        # VRAM
        "vram_ok":           "✅ VRAM 足够（需 ~{n} GB）",
        "vram_insufficient": "❌ VRAM 不足（需 ~{n} GB，GPU 仅 {total:.1f} GB）",
        "vram_warning":      "⚠️ CUDA 可用，但无法读取 VRAM（需 ~{n} GB）",
        "no_cuda":           "❌ 无 CUDA GPU",
        # Save / load messages
        "loaded_party":      "成功加载队伍 [{names}]！",
        "load_failed":       "加载存档失败。",
        "deleted_save":      "已删除存档「{save}」。",
        "delete_failed":     "删除存档「{save}」失败。",
        "overwrite_failed":  "覆盖失败。",
        "load_existing_failed": "加载现有存档失败。",
    },
    "한국어": {
        "model_expander":      "⚙️ 모델 및 언어",
        "llm_model_expander":  "⚙️ LLM 모델",
        "language_expander":   "🌐 언어",
        "switch_model":    "모델 변경",
        "ui_language":     "🌐 UI 언어",
        "new_game":        "새 게임",
        "load_game":       "게임 불러오기",
        "save_name":       "저장 이름",
        "difficulty":      "난이도",
        "world_setting":   "세계 설정",
        "universe":        "세계관",
        "custom_lore":     "커스텀 세계관 (선택)",
        "img_style_hdr":   "🎨 이미지 스타일",
        "img_style_lbl":   "아트 스타일",
        "custom_suffix":   "커스텀 스타일 접미사 (선택)",
        "party_hdr":       "파티 (1-6명)",
        "num_players":     "플레이어 수",
        "start_adventure": "모험 시작",
        "name":            "이름",
        "race":            "종족",
        "char_class":      "직업",
        "appearance":      "외모 설명",
        "personality":     "성격 설명",
        "gender":          "성별",
        "ai_controlled":   "🤖 AI 조종",
        "ai_personality":  "AI 개성",
        "ai_difficulty":   "AI 난이도",
        "difficulty_easy":   "쉬움",
        "difficulty_normal": "보통",
        "difficulty_hard":   "어려움",
        "no_saves":        "저장 파일을 찾을 수 없습니다.",
        "save_required":   "저장 이름과 플레이어 1 이름은 필수입니다.",
        "map_hint":        "🗺️ 지도와 초상화는 게임 시작 후 생성됩니다.",
        "dup_title":       "⚠️ 저장 이름 충돌",
        "dup_warning":     "이(가) 이미 존재합니다. 작업을 선택하세요:",
        "dup_overwrite":   "🗑️ 덮어쓰기",
        "dup_overwrite_desc": "이전 저장을 삭제하고 새 설정으로 시작합니다.",
        "dup_inherit":     "📂 기존 불러오기",
        "dup_inherit_desc": "새 설정을 버리고 기존 저장에서 계속합니다.",
        "dup_cancel":      "✖ 취소",
        # Game tabs
        "tab_story":         "📖 스토리",
        "tab_gameboard":     "🗺️ 게임판",
        "tab_characters":    "👥 캐릭터",
        "tab_rules":         "📜 규칙",
        "tab_orgs":          "🏛️ 조직",
        "tab_book":          "📕 책",
        "tab_godmode":       "🔮 신 모드",
        # Game board
        "manual_dice":       "🎲 주사위 굴리기",
        "dice_legend":       "🟡 최대  🔴 최소  — 보통",
        "scoreboard":        "🏆 점수판",
        "continent_map":     "🌍 대륙 지도",
        "regen_map":         "🔄 지도 재생성",
        "gen_map":           "🎨 대륙 지도 생성",
        "location_map":      "🗺️ 위치 추적 지도",
        "location_map_hint": "게임 시작 후 위치 지도가 표시됩니다 (스토리 탭으로 전환하여 첫 번째 턴을 시작하세요).",
        "relation_graph":    "🔗 관계 그래프",
        "ai_acting":         "(AI)가 행동 중… 스토리 탭으로 전환하여 확인하세요.",
        "player_turn":       "의 차례입니다!  스토리 탭으로 전환하여 행동을 입력하세요.",
        # Story tab
        "char_fallen":       "이(가) 쓰러졌습니다!  다음 플레이어를 기다리는 중…",
        "choose_action":     "{name}, 행동을 선택하세요:",
        "custom_action_expander": "✏️ 또는 커스텀 행동…",
        "custom_action_input":    "다른 행동 입력:",
        "execute":           "실행",
        "action_prompt_single": "행동을 입력하세요…",
        "action_prompt_multi":  "{flag} {name}의 행동…",
        "dm_thinking":       "생각 중…",
        "allow_custom":      "커스텀 행동 입력 허용",
        "allow_custom_help": "체크하면 게임 중 선택지 외 커스텀 행동을 입력할 수 있습니다",
        "prologue_badge":    "📜 프롤로그 · 턴 0",
        "writing_prologue":  "프롤로그 작성 중…",
        # Characters tab
        "party_details":     "👥 파티원 — 상세 정보",
        "dead_tag":          " ☠ 쓰러짐",
        "active_tag":        " ◀ 행동 중",
        "portrait_not_generated": "🖼️ 미생성",
        "gen_portrait":      "🎨 초상화 생성",
        "regen_portrait_help": "{name} 초상화 재생성",
        "skills_label":      "기술",
        "inventory_label":   "인벤토리",
        "appearance_label":  "외모",
        "personality_label": "성격",
        "relations_expander":"🔗 관계",
        # Relations / NPC tab
        "no_relations":      "관계 데이터가 없습니다. 스토리가 진행되면 캐릭터와 조직의 관계가 자동으로 기록됩니다.",
        "no_known_relations":"알려진 관계 없음",
        "rel_type_filter":   "관계 유형 필터",
        # Organizations tab
        "no_orgs":           "아직 발견된 조직이 없습니다. 모험을 계속하면 조직 정보가 자동으로 기록됩니다.",
        "search_orgs":       "🔍 조직 검색",
        "search_orgs_ph":    "이름, 유형, 리더를 입력…",
        "org_founder":       "창립자",
        "org_leader":        "현재 리더",
        "org_members":       "회원 수",
        "org_hq":            "본부",
        "org_alignment":     "성향",
        "org_first_seen":    "첫 등장",
        "org_history":       "역사",
        "org_relations":     "관계",
        "org_count":         "**{n}**개 조직 기록됨",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NPC — 알려진 캐릭터 ({n})",
        "no_npcs":           "아직 NPC를 만나지 못했습니다. 스토리가 진행되면 NPC가 자동으로 기록됩니다.",
        "npc_affinity":      "호감도",
        "npc_state_lbl":     "상태",
        "npc_health_lbl":    "건강",
        "npc_goal_lbl":      "🎯 목표:",
        "npc_aliases_lbl":   "별칭:",
        "npc_bio_lbl":       "약력",
        # Relation graph
        "rel_count":         "**{n}**개 관계 기록됨",
        "prologue_turn":     "프롤로그",
        "turn_label":        "{n}턴",
        # Rules tab
        "search_manual":     "🔍 매뉴얼 검색",
        "search_manual_ph":  "키워드 입력 (예: attack, 주사위, stealth)…",
        "chapter_label":     "챕터",
        "toc_expander":      "📋 목차 — 클릭하여 장으로 이동",
        # Book tab
        "no_story":          "📕 아직 스토리 기록이 없습니다.\n게임을 진행하면 각 턴의 스토리와 이미지가 자동으로 저장됩니다.",
        "latest_pages":      "📌 최신 페이지 (최근 2페이지)",
        "read_pages_header": "📖 페이지 읽기",
        # Image generation
        "img_style_expander":        "🎨 이미지 스타일",
        "regen_all_images":          "🔄 모든 이미지 재생성",
        "img_gen_disabled_auto":     "⚠️ 이미지 생성이 자동으로 비활성화되었습니다 (VRAM 부족 반복)",
        "img_gen_reenable":          "🔄 다시 활성화",
        "img_gen_ready":             "✅ 이미지 생성 준비 완료",
        "img_gen_vram_warn":         "⚡ VRAM이 부족할 수 있습니다. 일부 장면 이미지가 건너뛰어질 수 있습니다",
        "img_gen_disabled_notice":   "🚫 이미지 생성이 비활성화되어 있습니다. 모든 생성 버튼이 잠겨 있습니다.",
        "img_gen_map_fail":          "⚠️ 지도 생성 실패 (VRAM 부족 또는 GPU 준비 안 됨).",
        "img_gen_portrait_fail":     "⚠️ {name} 초상화 생성 실패.",
        "img_style_cur":             "현재",
        # Sidebar
        "save_game":         "💾 저장",
        "quit_game":         "🚪 종료",
        "game_saved":        "게임이 저장되었습니다!",
        # VRAM
        "vram_ok":           "✅ VRAM 충분 (~{n} GB 필요)",
        "vram_insufficient": "❌ VRAM 부족 (~{n} GB 필요, GPU는 {total:.1f} GB만 있음)",
        "vram_warning":      "⚠️ CUDA 사용 가능하지만 VRAM 읽기 불가 (~{n} GB 필요)",
        "no_cuda":           "❌ CUDA GPU 없음",
        # Save / load messages
        "loaded_party":      "파티를 불러왔습니다 [{names}]!",
        "load_failed":       "저장 파일 불러오기에 실패했습니다.",
        "deleted_save":      "저장 '{save}'을(를) 삭제했습니다.",
        "delete_failed":     "저장 '{save}' 삭제에 실패했습니다.",
        "overwrite_failed":  "덮어쓰기에 실패했습니다.",
        "load_existing_failed": "기존 저장 불러오기에 실패했습니다.",
    },
    "Français": {
        "model_expander":      "⚙️ Modèle et langue",
        "llm_model_expander":  "⚙️ Modèle LLM",
        "language_expander":   "🌐 Langue",
        "switch_model":    "Changer de modèle",
        "ui_language":     "🌐 Langue de l'interface",
        "new_game":        "Nouvelle partie",
        "load_game":       "Charger une partie",
        "save_name":       "Nom de la sauvegarde",
        "difficulty":      "Difficulté",
        "world_setting":   "Univers",
        "universe":        "Cadre",
        "custom_lore":     "Contexte personnalisé (optionnel)",
        "img_style_hdr":   "🎨 Style d'image",
        "img_style_lbl":   "Style artistique",
        "custom_suffix":   "Suffixe de style personnalisé (optionnel)",
        "party_hdr":       "Groupe (1-6 joueurs)",
        "num_players":     "Nombre de joueurs",
        "start_adventure": "Commencer l'aventure",
        "name":            "Nom",
        "race":            "Race",
        "char_class":      "Classe",
        "appearance":      "Apparence",
        "personality":     "Personnalité",
        "gender":          "Genre",
        "ai_controlled":   "🤖 Contrôlé par IA",
        "ai_personality":  "Personnalité IA",
        "ai_difficulty":   "Difficulté IA",
        "difficulty_easy":   "Facile",
        "difficulty_normal": "Normal",
        "difficulty_hard":   "Difficile",
        "no_saves":        "Aucune sauvegarde trouvée.",
        "save_required":   "Le nom de la sauvegarde et le nom du Joueur 1 sont obligatoires.",
        "map_hint":        "🗺️ La carte et les portraits sont générés après le début du jeu.",
        "dup_title":       "⚠️ Conflit de nom de sauvegarde",
        "dup_warning":     "existe déjà. Choisissez une action :",
        "dup_overwrite":   "🗑️ Écraser",
        "dup_overwrite_desc": "Supprime l'ancienne sauvegarde et commence avec les nouveaux paramètres.",
        "dup_inherit":     "📂 Charger l'existant",
        "dup_inherit_desc": "Ignore les nouveaux paramètres et continue depuis la sauvegarde existante.",
        "dup_cancel":      "✖ Annuler",
        # Game tabs
        "tab_story":         "📖 Histoire",
        "tab_gameboard":     "🗺️ Plateau",
        "tab_characters":    "👥 Personnages",
        "tab_rules":         "📜 Règles",
        "tab_orgs":          "🏛️ Organisations",
        "tab_book":          "📕 Livre",
        "tab_godmode":       "🔮 Mode Dieu",
        # Game board
        "manual_dice":       "🎲 Dés manuels",
        "dice_legend":       "🟡 Max  🔴 Min  — Normal",
        "scoreboard":        "🏆 Classement",
        "continent_map":     "🌍 Carte continentale",
        "regen_map":         "🔄 Régénérer la carte",
        "gen_map":           "🎨 Générer la carte",
        "location_map":      "🗺️ Carte de localisation",
        "location_map_hint": "La carte de localisation apparaîtra après le début du jeu (passez à l'onglet Histoire pour commencer votre premier tour).",
        "relation_graph":    "🔗 Graphique des relations",
        "ai_acting":         "(IA) agit… Passez à l'onglet Histoire pour voir.",
        "player_turn":       "c'est votre tour !  Passez à l'onglet Histoire pour entrer votre action.",
        # Story tab
        "char_fallen":       "est tombé !  En attente du prochain joueur…",
        "choose_action":     "{name}, choisissez votre action :",
        "custom_action_expander": "✏️ Ou action personnalisée…",
        "custom_action_input":    "Entrez une action personnalisée :",
        "execute":           "Exécuter",
        "action_prompt_single": "Entrez votre action…",
        "action_prompt_multi":  "{flag} Action de {name}…",
        "dm_thinking":       "réfléchit…",
        "allow_custom":      "Autoriser la saisie d'action personnalisée",
        "allow_custom_help": "Si coché, vous pouvez saisir une action personnalisée pendant le jeu au lieu de choisir parmi les options.",
        "prologue_badge":    "📜 Prologue · Tour 0",
        "writing_prologue":  "écrit le prologue…",
        # Characters tab
        "party_details":     "👥 Membres du groupe — Détails",
        "dead_tag":          " ☠ Tombé",
        "active_tag":        " ◀ Actif",
        "portrait_not_generated": "🖼️ Non généré",
        "gen_portrait":      "🎨 Générer le portrait",
        "regen_portrait_help": "Régénérer le portrait de {name}",
        "skills_label":      "Compétences",
        "inventory_label":   "Inventaire",
        "appearance_label":  "Apparence",
        "personality_label": "Personnalité",
        "relations_expander":"🔗 Relations",
        # Relations / NPC tab
        "no_relations":      "Aucune donnée de relation pour l'instant. Au fil de l'histoire, les relations entre personnages et organisations seront automatiquement enregistrées ici.",
        "no_known_relations":"Aucune relation connue",
        "rel_type_filter":   "Filtrer les types de relations",
        # Organizations tab
        "no_orgs":           "Aucune organisation découverte. Continuez l'aventure et les informations sur les organisations seront enregistrées automatiquement.",
        "search_orgs":       "🔍 Rechercher des organisations",
        "search_orgs_ph":    "Entrez nom, type, chef…",
        "org_founder":       "Fondateur",
        "org_leader":        "Chef actuel",
        "org_members":       "Membres",
        "org_hq":            "Quartier général",
        "org_alignment":     "Alignement",
        "org_first_seen":    "Première apparition",
        "org_history":       "Histoire",
        "org_relations":     "Relations",
        "org_count":         "**{n}** organisation(s) enregistrée(s)",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 PNJ — Personnages connus ({n})",
        "no_npcs":           "Aucun PNJ rencontré pour l'instant. Au fil de l'histoire, les PNJ seront automatiquement enregistrés ici.",
        "npc_affinity":      "Affinité",
        "npc_state_lbl":     "État",
        "npc_health_lbl":    "Santé",
        "npc_goal_lbl":      "🎯 Objectif :",
        "npc_aliases_lbl":   "Alias :",
        "npc_bio_lbl":       "Biographie",
        # Relation graph
        "rel_count":         "**{n}** relation(s) enregistrée(s)",
        "prologue_turn":     "Prologue",
        "turn_label":        "Tour {n}",
        # Rules tab
        "search_manual":     "🔍 Rechercher dans le manuel",
        "search_manual_ph":  "Entrez des mots-clés, ex. : attaque, dé, stealth…",
        "chapter_label":     "Chapitre",
        "toc_expander":      "📋 Table des matières — Cliquer pour accéder",
        # Book tab
        "no_story":          "📕 Aucun journal d'histoire pour l'instant.\nAprès avoir joué, l'histoire et les images de chaque tour sont sauvegardées automatiquement.",
        "latest_pages":      "📌 Pages récentes (2 dernières)",
        "read_pages_header": "📖 Lire les pages",
        # Image generation
        "img_style_expander":        "🎨 Style d'image",
        "regen_all_images":          "🔄 Régénérer toutes les images",
        "img_gen_disabled_auto":     "⚠️ Génération d'images désactivée automatiquement (VRAM insuffisante répétée)",
        "img_gen_reenable":          "🔄 Réactiver",
        "img_gen_ready":             "✅ Génération d'images prête",
        "img_gen_vram_warn":         "⚡ La VRAM peut être insuffisante, certaines images de scènes peuvent être ignorées",
        "img_gen_disabled_notice":   "🚫 Génération d'images désactivée, tous les boutons de génération sont gelés.",
        "img_gen_map_fail":          "⚠️ Échec de la génération de la carte (VRAM insuffisante ou GPU non prêt).",
        "img_gen_portrait_fail":     "⚠️ Échec de la génération du portrait de {name}.",
        "img_style_cur":             "Actuel",
        # Sidebar
        "save_game":         "💾 Sauvegarder",
        "quit_game":         "🚪 Quitter",
        "game_saved":        "Partie sauvegardée !",
        # VRAM
        "vram_ok":           "✅ VRAM suffisante (~{n} Go requis)",
        "vram_insufficient": "❌ VRAM insuffisante (~{n} Go requis, GPU : {total:.1f} Go seulement)",
        "vram_warning":      "⚠️ CUDA disponible mais VRAM illisible (~{n} Go requis)",
        "no_cuda":           "❌ Pas de GPU CUDA",
        # Save / load messages
        "loaded_party":      "Groupe chargé [{names}] !",
        "load_failed":       "Échec du chargement de la sauvegarde.",
        "deleted_save":      "Sauvegarde '{save}' supprimée.",
        "delete_failed":     "Échec de la suppression de '{save}'.",
        "overwrite_failed":  "Échec de l'écrasement.",
        "load_existing_failed": "Échec du chargement de la sauvegarde existante.",
    },
    "Deutsch": {
        "model_expander":      "⚙️ Modell & Sprache",
        "llm_model_expander":  "⚙️ LLM-Modell",
        "language_expander":   "🌐 Sprache",
        "switch_model":    "Modell wechseln",
        "ui_language":     "🌐 UI-Sprache",
        "new_game":        "Neues Spiel",
        "load_game":       "Spiel laden",
        "save_name":       "Speichername",
        "difficulty":      "Schwierigkeit",
        "world_setting":   "Welteinstellung",
        "universe":        "Universum",
        "custom_lore":     "Benutzerdefinierter Hintergrund (optional)",
        "img_style_hdr":   "🎨 Bildstil",
        "img_style_lbl":   "Kunststil",
        "custom_suffix":   "Benutzerdefiniertes Stil-Suffix (optional)",
        "party_hdr":       "Gruppe (1-6 Spieler)",
        "num_players":     "Spielerzahl",
        "start_adventure": "Abenteuer starten",
        "name":            "Name",
        "race":            "Rasse",
        "char_class":      "Klasse",
        "appearance":      "Aussehen",
        "personality":     "Persönlichkeit",
        "gender":          "Geschlecht",
        "ai_controlled":   "🤖 KI-gesteuert",
        "ai_personality":  "KI-Persönlichkeit",
        "ai_difficulty":   "KI-Schwierigkeit",
        "difficulty_easy":   "Leicht",
        "difficulty_normal": "Normal",
        "difficulty_hard":   "Schwer",
        "no_saves":        "Keine Spielstände gefunden.",
        "save_required":   "Speichername und Spieler-1-Name sind erforderlich.",
        "map_hint":        "🗺️ Karte und Porträts werden nach dem Spielstart generiert.",
        "dup_title":       "⚠️ Speichernamenskonflikt",
        "dup_warning":     "existiert bereits. Wählen Sie eine Aktion:",
        "dup_overwrite":   "🗑️ Überschreiben",
        "dup_overwrite_desc": "Löscht den alten Spielstand und beginnt mit den neuen Einstellungen.",
        "dup_inherit":     "📂 Vorhandenen laden",
        "dup_inherit_desc": "Verwirft die neuen Einstellungen und setzt den vorhandenen Spielstand fort.",
        "dup_cancel":      "✖ Abbrechen",
        # Game tabs
        "tab_story":         "📖 Geschichte",
        "tab_gameboard":     "🗺️ Spielfeld",
        "tab_characters":    "👥 Charaktere",
        "tab_rules":         "📜 Regeln",
        "tab_orgs":          "🏛️ Organisationen",
        "tab_book":          "📕 Buch",
        "tab_godmode":       "🔮 Gottmodus",
        # Game board
        "manual_dice":       "🎲 Manuelle Würfel",
        "dice_legend":       "🟡 Max  🔴 Min  — Normal",
        "scoreboard":        "🏆 Punktestand",
        "continent_map":     "🌍 Kontinentkarte",
        "regen_map":         "🔄 Karte regenerieren",
        "gen_map":           "🎨 Kontinentkarte erstellen",
        "location_map":      "🗺️ Positionskarte",
        "location_map_hint": "Die Positionskarte erscheint nach dem Spielstart (wechseln Sie zum Geschichte-Tab, um Ihren ersten Zug zu beginnen).",
        "relation_graph":    "🔗 Beziehungsgraph",
        "ai_acting":         "(KI) handelt… Wechseln Sie zum Geschichte-Tab, um es zu sehen.",
        "player_turn":       "Sie sind dran!  Wechseln Sie zum Geschichte-Tab, um Ihre Aktion einzugeben.",
        # Story tab
        "char_fallen":       "ist gefallen!  Warten auf den nächsten Spieler…",
        "choose_action":     "{name}, wähle deine Aktion:",
        "custom_action_expander": "✏️ Oder benutzerdefinierte Aktion…",
        "custom_action_input":    "Benutzerdefinierte Aktion eingeben:",
        "execute":           "Ausführen",
        "action_prompt_single": "Gib deine Aktion ein…",
        "action_prompt_multi":  "{flag} {name}s Aktion…",
        "dm_thinking":       "denkt nach…",
        "allow_custom":      "Benutzerdefinierte Aktionseingabe erlauben",
        "allow_custom_help": "Wenn aktiviert, können Sie im Spiel eine benutzerdefinierte Aktion eingeben statt aus den Optionen zu wählen.",
        "prologue_badge":    "📜 Prolog · Zug 0",
        "writing_prologue":  "schreibt den Prolog…",
        # Characters tab
        "party_details":     "👥 Gruppenmitglieder — Details",
        "dead_tag":          " ☠ Gefallen",
        "active_tag":        " ◀ Aktiv",
        "portrait_not_generated": "🖼️ Nicht generiert",
        "gen_portrait":      "🎨 Porträt generieren",
        "regen_portrait_help": "{name}s Porträt regenerieren",
        "skills_label":      "Fähigkeiten",
        "inventory_label":   "Inventar",
        "appearance_label":  "Aussehen",
        "personality_label": "Persönlichkeit",
        "relations_expander":"🔗 Beziehungen",
        # Relations / NPC tab
        "no_relations":      "Noch keine Beziehungsdaten. Im Laufe der Geschichte werden die Beziehungen zwischen Charakteren und Organisationen automatisch aufgezeichnet.",
        "no_known_relations":"Keine bekannten Beziehungen",
        "rel_type_filter":   "Beziehungstypen filtern",
        # Organizations tab
        "no_orgs":           "Noch keine Organisationen entdeckt. Setze das Abenteuer fort und Organisationsdaten werden automatisch aufgezeichnet.",
        "search_orgs":       "🔍 Organisationen suchen",
        "search_orgs_ph":    "Name, Typ, Anführer eingeben…",
        "org_founder":       "Gründer",
        "org_leader":        "Aktueller Anführer",
        "org_members":       "Mitglieder",
        "org_hq":            "Hauptquartier",
        "org_alignment":     "Gesinnung",
        "org_first_seen":    "Erstmals erschienen",
        "org_history":       "Geschichte",
        "org_relations":     "Beziehungen",
        "org_count":         "**{n}** Organisation(en) aufgezeichnet",
        # NPC section
        "npc_known":         "🧑‍🤝‍🧑 NSC — Bekannte Charaktere ({n})",
        "no_npcs":           "Noch keine NSCs begegnet. Im Verlauf der Geschichte werden NSCs automatisch hier aufgezeichnet.",
        "npc_affinity":      "Affinität",
        "npc_state_lbl":     "Zustand",
        "npc_health_lbl":    "Gesundheit",
        "npc_goal_lbl":      "🎯 Ziel:",
        "npc_aliases_lbl":   "Aliasse:",
        "npc_bio_lbl":       "Biografie",
        # Relation graph
        "rel_count":         "**{n}** Beziehung(en) aufgezeichnet",
        "prologue_turn":     "Prolog",
        "turn_label":        "Runde {n}",
        # Rules tab
        "search_manual":     "🔍 Handbuch durchsuchen",
        "search_manual_ph":  "Stichwörter eingeben, z.B.: Angriff, Würfel, Stealth…",
        "chapter_label":     "Kapitel",
        "toc_expander":      "📋 Inhaltsverzeichnis — Klicken zum Springen",
        # Book tab
        "no_story":          "📕 Noch kein Storyprotokoll.\nNach dem Spielen werden Geschichte und Bilder jedes Zuges automatisch gespeichert.",
        "latest_pages":      "📌 Neueste Seiten (letzte 2)",
        "read_pages_header": "📖 Seiten lesen",
        # Image generation
        "img_style_expander":        "🎨 Bildstil",
        "regen_all_images":          "🔄 Alle Bilder regenerieren",
        "img_gen_disabled_auto":     "⚠️ Bildgenerierung automatisch deaktiviert (wiederholter VRAM-Mangel)",
        "img_gen_reenable":          "🔄 Wieder aktivieren",
        "img_gen_ready":             "✅ Bildgenerierung bereit",
        "img_gen_vram_warn":         "⚡ VRAM könnte unzureichend sein, einige Szenenbilder werden möglicherweise übersprungen",
        "img_gen_disabled_notice":   "🚫 Bildgenerierung deaktiviert, alle Generierungsschaltflächen sind gesperrt.",
        "img_gen_map_fail":          "⚠️ Kartengenerierung fehlgeschlagen (VRAM unzureichend oder GPU nicht bereit).",
        "img_gen_portrait_fail":     "⚠️ Porträtgenerierung für {name} fehlgeschlagen.",
        "img_style_cur":             "Aktuell",
        # Sidebar
        "save_game":         "💾 Speichern",
        "quit_game":         "🚪 Beenden",
        "game_saved":        "Spiel gespeichert!",
        # VRAM
        "vram_ok":           "✅ VRAM ausreichend (~{n} GB benötigt)",
        "vram_insufficient": "❌ VRAM unzureichend (~{n} GB benötigt, GPU nur {total:.1f} GB)",
        "vram_warning":      "⚠️ CUDA verfügbar, aber VRAM nicht lesbar (~{n} GB benötigt)",
        "no_cuda":           "❌ Kein CUDA-GPU",
        # Save / load messages
        "loaded_party":      "Gruppe geladen [{names}]!",
        "load_failed":       "Spielstand konnte nicht geladen werden.",
        "deleted_save":      "Spielstand '{save}' gelöscht.",
        "delete_failed":     "Spielstand '{save}' konnte nicht gelöscht werden.",
        "overwrite_failed":  "Überschreiben fehlgeschlagen.",
        "load_existing_failed": "Vorhandener Spielstand konnte nicht geladen werden.",
    },
}

_LANGUAGES = ["English", "繁體中文", "简体中文", "日本語", "한국어", "Français", "Deutsch", "Español"]

# Substrings that indicate a "look/examine" player action across all 8 supported languages.
# Used to trigger a non-cinematic scene image when the player explicitly looks around.
_LOOK_KEYWORDS = {
    # English
    "look", "examine", "observe", "inspect", "survey", "scout", "search",
    # Traditional / Simplified Chinese
    "看", "觀察", "观察", "審視", "审视", "環顧", "环顾", "查看", "搜索", "探索",
    # Japanese
    "見る", "みる", "観察", "調べる", "しらべる", "見回す", "みまわす", "探す", "さがす",
    # Korean
    "보다", "관찰", "살펴", "둘러", "수색",
    # French
    "regarder", "examiner", "observer", "inspecter",
    # German
    "schauen", "untersuchen", "beobachten", "erkunden",
    # Spanish
    "mirar", "examinar", "observar", "inspeccionar", "explorar",
}


def _t(key):
    """Return UI string for the current UI language."""
    lang    = st.session_state.get('pref_language', 'English')
    strings = _UI_STRINGS.get(lang, _UI_STRINGS['English'])
    return strings.get(key, _UI_STRINGS['English'].get(key, key))

def _tr_race(r):
    """Translate a race name to the current UI language."""
    lang = st.session_state.get('pref_language', 'English')
    lk = {'繁體中文': 'zh', '日本語': 'ja', 'Español': 'es'}.get(lang, 'en')
    tr = config.RACE_TRANSLATIONS.get(r, {}).get(lk)
    return f"{tr}（{r}）" if tr else r

def _tr_class(c):
    """Translate a class name to the current UI language."""
    lang = st.session_state.get('pref_language', 'English')
    lk = {'繁體中文': 'zh', '日本語': 'ja', 'Español': 'es'}.get(lang, 'en')
    tr = config.CLASS_TRANSLATIONS.get(c, {}).get(lk)
    return f"{tr}（{c}）" if tr else c

def _tr_rc(race, char_class):
    """Translate race + class as a combined short label."""
    lang = st.session_state.get('pref_language', 'English')
    lk = {'繁體中文': 'zh', '日本語': 'ja', 'Español': 'es'}.get(lang, 'en')
    r = config.RACE_TRANSLATIONS.get(race, {}).get(lk) or race
    c = config.CLASS_TRANSLATIONS.get(char_class, {}).get(lk) or char_class
    return f"{r} {c}"

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
            gender=lead[5] if len(lead) > 5 else '',
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
            st.error(_t('overwrite_failed'))

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
            st.error(_t('load_existing_failed'))

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
            st.info(_t('img_gen_disabled_notice'))
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
                        st.sidebar.success(_t('vram_ok').format(n=_vram_req))
                    else:
                        st.sidebar.error(
                            _t('vram_insufficient').format(n=_vram_req, total=_total_gb)
                        )
            except Exception:
                st.sidebar.warning(_t('vram_warning').format(n=_vram_req))
        else:
            msg = _t('vram_insufficient').format(n=_vram_req, total=0) if _vram_req else _t('no_cuda')
            st.sidebar.error(msg)

    with st.sidebar.expander(_t("llm_model_expander"), expanded=False):
        # ── Step 1: size / tier category ────────────────────────────────────
        _SIZE_CATEGORIES = ["雲端", "8B", "14B", "32B"]
        _SIZE_LABELS = {
            "雲端": "☁️ 雲端 (Cloud)",
            "8B":   "🏠 8B  (~6-8 GB VRAM)",
            "14B":  "🏠 14B (~10 GB VRAM)",
            "32B":  "🏠 32B (~20 GB VRAM)",
        }
        # Derive default category from the currently active model
        _active_cat = _active_preset.get('size_category', '8B')
        try:
            _cat_idx = _SIZE_CATEGORIES.index(_active_cat)
        except ValueError:
            _cat_idx = 0

        sel_cat_idx = st.selectbox(
            "類別 / Category",
            range(len(_SIZE_CATEGORIES)),
            index=_cat_idx,
            format_func=lambda i: _SIZE_LABELS[_SIZE_CATEGORIES[i]],
            key="model_size_cat_sel",
        )
        sel_cat = _SIZE_CATEGORIES[sel_cat_idx]

        # ── Step 2: model name, filtered by chosen category ──────────────────
        filtered_presets = [p for p in config.MODEL_PRESETS
                            if p.get('size_category') == sel_cat]
        if not filtered_presets:
            filtered_presets = config.MODEL_PRESETS  # fallback

        # Check which Ollama models are already downloaded locally
        _installed_ollama = set()
        try:
            import ollama as _ollama_lib
            for _m in _ollama_lib.list().models:
                _installed_ollama.add(_m.model)
                if ':' in _m.model:
                    _installed_ollama.add(_m.model.split(':')[0])
        except Exception:
            pass

        def _model_label(i):
            p = filtered_presets[i]
            if p.get('provider') == 'ollama':
                pid = p['id']
                inst = pid in _installed_ollama or pid.split(':')[0] in _installed_ollama
                icon = "✅" if inst else "⬇️"
                return f"{icon} {p['name']}"
            return f"☁️ {p['name']}"

        filtered_ids = [p['id'] for p in filtered_presets]
        try:
            cur_filtered_idx = filtered_ids.index(st.session_state.active_model_id)
        except ValueError:
            cur_filtered_idx = 0

        selected_filtered_idx = st.selectbox(
            "模型 / Model",
            range(len(filtered_presets)),
            index=cur_filtered_idx,
            format_func=_model_label,
            key="model_name_sel",
        )
        preset = filtered_presets[selected_filtered_idx]
        new_id = preset['id']

        st.caption(preset.get('description', ''))
        if preset.get('pros'):
            st.markdown(f"✅ **Pros:** {preset['pros']}")
        if preset.get('cons'):
            st.markdown(f"⚠️ **Cons:** {preset['cons']}")

        # ── Ollama: not installed → show download instructions ────────────────
        if preset.get('provider') == 'ollama':
            _pid = preset['id']
            _installed = _pid in _installed_ollama or _pid.split(':')[0] in _installed_ollama
            if not _installed:
                st.warning(f"⬇️ **{preset['name']}** 尚未下載 (not downloaded)")
                st.code(f"ollama pull {_pid}", language="bash")
                st.caption(
                    "在終端執行上述指令下載模型，完成後重新整理頁面即可使用。\n"
                    "Run the command above in your terminal, then refresh this page."
                )
                if st.button(f"⬇️ 背景下載 {preset['name']}",
                             key="llm_bg_download_btn",
                             use_container_width=True):
                    try:
                        import subprocess
                        subprocess.Popen(["ollama", "pull", _pid])
                        st.success(
                            "✅ 下載已在背景啟動，請稍候後重新整理頁面。\n"
                            "Download started in background — refresh once complete."
                        )
                    except Exception as _dl_err:
                        st.error(f"無法啟動下載 / Could not start download: {_dl_err}")
                # Don't switch to a model that isn't downloaded yet
                return

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

        # Auto-switch when dropdown selection differs from active model.
        # Block switch for cloud models until the API key is actually present.
        if new_id != st.session_state.active_model_id:
            if key_ready:
                st.session_state.llm.switch_model(new_id)
                st.session_state.active_model_id = new_id
                prefs = PersistenceManager.load_prefs()
                prefs['active_model_id'] = new_id
                PersistenceManager.save_prefs(prefs)
                st.success(f"✅ Switched to **{preset['name']}**")
            # else: key not ready — warning already shown above
        else:
            if key_ready:
                st.success(f"▶ **{preset['name']}** is active")

# ---------------------------------------------------------------------------
# Main Menu helpers
# ---------------------------------------------------------------------------

def _player_config_fields(idx, key_prefix, ws=None):
    """
    Render config fields for one party member (inside a form).

    Slot 0 is always human (party leader). Slots 1-5 may be AI-controlled.
    ws — the world setting dict; when provided, races/classes/names/descriptions
         are drawn from the setting to match the world's flavour.
    Returns (name, race, char_class, appearance, personality, gender,
             is_ai, ai_personality, ai_difficulty).
    """
    import random

    flag  = config.PLAYER_FLAGS[idx] if idx < len(config.PLAYER_FLAGS) else '👤'
    label = (f"{flag} Player 1 (Party Leader)" if idx == 0
             else f"{flag} Player {idx + 1}")
    st.markdown(f"**{label}**")

    is_ai          = False
    ai_personality = 'tactical'
    ai_difficulty  = 'normal'
    if idx > 0:
        is_ai = st.checkbox(_t("ai_controlled"), key=f"{key_prefix}_is_ai_{idx}", value=False)

    # World-specific race / class lists
    ws_races   = (ws.get('races')   if ws else None) or _RACES_FALLBACK
    ws_classes = (ws.get('classes') if ws else None) or _CLASSES_FALLBACK
    tm = (ws.get('term_map', {}) if ws else {})

    # Build class display labels from term_map (English world-specific aliases).
    # Language-aware display is handled by _fmt_class() below.
    _class_display = {
        'Warrior': tm.get('warrior_class', 'Warrior'),
        'Mage':    tm.get('mage_class',    'Mage'),
        'Rogue':   tm.get('rogue_class',   'Rogue'),
        'Cleric':  tm.get('cleric_class',  'Cleric'),
    }

    # Random default name (world-fitting) — generate once and store in session
    rand_key = f"_rand_name_{key_prefix}_{idx}"
    if rand_key not in st.session_state:
        names_m = (ws.get('names_m') if ws else None) or ["Adventurer"]
        names_f = (ws.get('names_f') if ws else None) or ["Adventurer"]
        pool = names_m + names_f
        st.session_state[rand_key] = random.choice(pool)

    # Random default gender — infer from name pool
    gender_rand_key = f"_rand_gender_{key_prefix}_{idx}"
    if gender_rand_key not in st.session_state:
        names_m = (ws.get('names_m') if ws else None) or []
        default_name = st.session_state[rand_key]
        st.session_state[gender_rand_key] = "Male" if default_name in names_m else "Female"

    # Resolve localized pools
    lang = st.session_state.get('pref_language', 'English')
    _lang_key_map = {
        '繁體中文': 'zh', '简体中文': 'zh',
        '日本語': 'ja', '한국어': 'ko',
        'Français': 'fr', 'Deutsch': 'de', 'Español': 'es',
    }
    lang_key = _lang_key_map.get(lang, 'en')

    def _pick_localized(field):
        raw = ws.get(field) if ws else None
        if isinstance(raw, dict):
            return raw.get(lang_key) or raw.get('en') or []
        return raw or []

    # MBTI personality — pick a random type as default
    mbti_types = list(config.MBTI_DATABASE.keys())
    mbti_rand_key = f"_rand_mbti_{key_prefix}_{idx}"
    if mbti_rand_key not in st.session_state:
        st.session_state[mbti_rand_key] = random.choice(mbti_types)

    def _mbti_label(t):
        entry = config.MBTI_DATABASE.get(t, {})
        return entry.get(lang_key) or entry.get('en') or t

    # Generate appearance text based on race + gender + class + MBTI + world pool
    def _generate_appearance(r, g, c='', mbti='', force_new=False):
        pool = _pick_localized('appearances')
        if not pool:
            return ""
        if force_new:
            import time as _time_mod
            rng = random.Random(f"{r}_{g}_{c}_{mbti}_{_time_mod.time()}")
        else:
            rng = random.Random(f"{key_prefix}_{idx}_{r}_{g}_{c}_{mbti}")
        return rng.choice(pool)

    # Random default appearance — initial
    app_rand_key = f"_rand_app_{key_prefix}_{idx}"
    if app_rand_key not in st.session_state:
        init_race   = st.session_state.get(f"{key_prefix}_race_{idx}", ws_races[0])
        init_gender = st.session_state.get(gender_rand_key, 'Male')
        st.session_state[app_rand_key] = _generate_appearance(init_race, init_gender)

    # Pre-populate session state with random defaults (only if not already set)
    name_key   = f"{key_prefix}_name_{idx}"
    race_key   = f"{key_prefix}_race_{idx}"
    class_key  = f"{key_prefix}_class_{idx}"
    gender_key = f"{key_prefix}_gender_{idx}"
    mbti_key   = f"{key_prefix}_mbti_{idx}"
    # Track whether the user has manually edited appearance
    app_manual_key = f"_app_manual_{key_prefix}_{idx}"
    # Track previous race + gender to detect changes
    prev_race_key   = f"_prev_race_{key_prefix}_{idx}"
    prev_gender_key = f"_prev_gender_{key_prefix}_{idx}"

    if name_key not in st.session_state:
        st.session_state[name_key] = st.session_state[rand_key]
    if race_key not in st.session_state:
        st.session_state[race_key] = ws_races[0]
    elif st.session_state[race_key] not in ws_races:
        st.session_state[race_key] = ws_races[0]
    if class_key not in st.session_state:
        st.session_state[class_key] = ws_classes[0]
    elif st.session_state[class_key] not in ws_classes:
        st.session_state[class_key] = ws_classes[0]
    if gender_key not in st.session_state:
        st.session_state[gender_key] = st.session_state[gender_rand_key]
    if mbti_key not in st.session_state:
        st.session_state[mbti_key] = st.session_state[mbti_rand_key]
    if app_manual_key not in st.session_state:
        st.session_state[app_manual_key] = False

    # Translation helpers for race / class / gender display
    def _race_label(r):
        tr = config.RACE_TRANSLATIONS.get(r, {}).get(lang_key)
        return f"{tr}（{r}）" if tr else r

    def _class_label(c):
        # term_map label (world-specific) takes priority, then generic translation
        tm_label = _class_display.get(c, c)
        generic  = config.CLASS_TRANSLATIONS.get(c, {}).get(lang_key)
        if tm_label != c:
            # World has a custom name (e.g. Fighter, Street Samurai)
            return f"{generic or tm_label}（{tm_label}）" if generic and generic != tm_label else tm_label
        return f"{generic}（{c}）" if generic else c

    def _gender_label(g):
        tr = config.GENDER_TRANSLATIONS.get(g, {}).get(lang_key)
        return tr if tr else g

    # Row 1: Name / Gender
    row1 = st.columns([3, 1])
    name   = row1[0].text_input(_t("name"), key=name_key)
    gender = row1[1].selectbox(_t("gender"), _GENDERS, format_func=_gender_label, key=gender_key)

    # Row 2: Race / Class (localized display names)
    row2 = st.columns([1, 1])
    _race_loc  = _RACE_L10N.get(lang_key, {})
    _class_loc = _CLASS_L10N.get(lang_key, {})

    race = row2[0].selectbox(
        _t("race"), ws_races, key=race_key,
        format_func=lambda r: f"{_race_loc[r]} ({r})" if r in _race_loc else r,
    )

    def _fmt_class(c):
        c_key     = {'Warrior': 'warrior', 'Mage': 'mage', 'Rogue': 'rogue', 'Cleric': 'cleric'}.get(c, c.lower())
        loc_world = tm.get(f'{c_key}_class_{lang_key}')   # world-specific localized, e.g. "鬥士"
        en_world  = tm.get(f'{c_key}_class')               # world-specific English, e.g. "Fighter"
        loc_gen   = _class_loc.get(c)                      # generic locale, e.g. "戰士"
        if loc_world:
            return f"{loc_world} ({c})"
        if loc_gen and en_world and en_world != c:
            return f"{loc_gen} ({en_world})"
        if loc_gen:
            return f"{loc_gen} ({c})"
        if en_world and en_world != c:
            return f"{en_world} ({c})"
        return c

    char_class = row2[1].selectbox(
        _t("char_class"), ws_classes,
        format_func=_fmt_class,
        key=class_key,
    )

    # Detect race/gender change → auto-refresh appearance (unless manually edited)
    cur_race   = st.session_state[race_key]
    cur_gender = st.session_state[gender_key]
    old_race   = st.session_state.get(prev_race_key)
    old_gender = st.session_state.get(prev_gender_key)
    if old_race is not None or old_gender is not None:
        if (cur_race != old_race or cur_gender != old_gender):
            if not st.session_state[app_manual_key]:
                _cur_class = st.session_state.get(class_key, ws_classes[0])
                _cur_mbti  = st.session_state.get(mbti_key, '')
                new_app = _generate_appearance(cur_race, cur_gender, _cur_class, _cur_mbti)
                st.session_state[f"{key_prefix}_app_{idx}"] = new_app
                st.session_state[app_rand_key] = new_app
    st.session_state[prev_race_key]   = cur_race
    st.session_state[prev_gender_key] = cur_gender

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
        app_key = f"{key_prefix}_app_{idx}"
        if app_key not in st.session_state or not st.session_state[app_key]:
            st.session_state[app_key] = st.session_state[app_rand_key]

        _app_col, _regen_col = st.columns([5, 1])
        with _app_col:
            appearance = st.text_input(_t("appearance"), key=app_key)
        with _regen_col:
            st.write("")
            _regen_clicked = st.form_submit_button("🎲", help=_t("regen_appearance"))

        if _regen_clicked:
            _r = st.session_state.get(race_key, ws_races[0])
            _g = st.session_state.get(gender_key, "Male")
            _c = st.session_state.get(class_key, ws_classes[0])
            _m = st.session_state.get(mbti_key, "")
            _llm = st.session_state.get('llm')
            _new_app = ""
            if _llm:
                _world_ctx = ws.get('world_lore', '') if ws else ''
                _new_app = _llm.generate_character_appearance(
                    race=_r, gender=_g, char_class=_c, mbti=_m,
                    world_context=_world_ctx, language=lang,
                )
            if not _new_app:
                _new_app = _generate_appearance(_r, _g, _c, _m, force_new=True)
            st.session_state[app_rand_key]   = _new_app
            st.session_state[app_manual_key] = False
            st.session_state.pop(app_key, None)  # cleared; re-init from app_rand_key on next run
            st.rerun()

        # Detect manual appearance edit — compare current value against last auto-generated
        if st.session_state.get(app_key, '') != st.session_state.get(app_rand_key, ''):
            st.session_state[app_manual_key] = True

        # MBTI personality selector
        mbti_sel = st.selectbox(
            _t("personality"), mbti_types,
            format_func=_mbti_label,
            key=mbti_key,
        )
        personality_text = _mbti_label(mbti_sel)

    return name, race, char_class, appearance, personality_text, gender, is_ai, ai_personality, ai_difficulty


def main_menu():
    _check_model_updates()
    _render_language_switcher()
    _render_model_switcher()
    _render_image_model_selector()

    # Restore form fields from saved prefs when returning from an in-progress game.
    # This ensures the create-new-game form always shows the last-used settings
    # rather than stale or re-initialised defaults after the game widgets unrendered.
    if st.session_state.pop('_menu_needs_restore', False):
        _p = PersistenceManager.load_prefs()
        for _k, _v in {
            'ng_difficulty':      _p.get('difficulty', 'Normal'),
            'ng_num_players':     _p.get('num_players', 1),
            'ng_img_style':       _p.get('img_style', 0),
            'new_game_ws_select': _p.get('world_idx', 0),
            'new_game_lore':      _p.get('custom_lore', ''),
            'new_game_custom_img':_p.get('custom_img_suffix', ''),
        }.items():
            st.session_state[_k] = _v
        for _si in range(6):
            for _f in ('race', 'class', 'app', 'gender', 'mbti'):
                _pv = _p.get(f'{_f}_{_si}')
                if _pv:
                    st.session_state[f'ng_{_f}_{_si}'] = _pv
            if _si > 0:
                _pv = _p.get(f'is_ai_{_si}')
                if _pv is not None:
                    st.session_state[f'ng_is_ai_{_si}'] = _pv

    st.title("D&D AI RPG Engine")

    col1, col2 = st.columns(2)

    with col1:
        st.header(_t("new_game"))

        # World setting selector — OUTSIDE form so changes trigger an immediate
        # Streamlit rerun, updating race / class / name options interactively.
        st.markdown(f"**{_t('world_setting')}**")
        ws_labels = [f"[{_w['category']}] {_w['name']}" for _w in config.WORLD_SETTINGS]
        ws_ids    = [_w['id'] for _w in config.WORLD_SETTINGS]
        ws_idx    = st.selectbox(
            _t("universe"), range(len(config.WORLD_SETTINGS)),
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

        # Reset per-slot random caches when world changes so names /
        # appearances regenerate from the new world's pools.
        _ws_prev_key = "_prev_ws_idx"
        if _ws_prev_key in st.session_state and st.session_state[_ws_prev_key] != ws_idx:
            for _slot in range(config.MAX_PARTY_SIZE + 1):
                for _pfx in ('_rand_name_ng_', '_rand_gender_ng_', '_rand_app_ng_'):
                    st.session_state.pop(f"{_pfx}{_slot}", None)
        st.session_state[_ws_prev_key] = ws_idx

        with st.form("new_game_form"):
            save_name  = st.text_input(_t("save_name"), key="ng_save_name")
            _DIFF_KEYS = ["Easy", "Normal", "Hard"]
            difficulty = st.selectbox(
                _t("difficulty"), _DIFF_KEYS,
                format_func=lambda d: _t(f"difficulty_{d.lower()}"),
                key="ng_difficulty",
            )
            # Language is now set via the model/language expander in the sidebar;
            # read it from session state so the game uses the selected language.
            language = st.session_state.pref_language

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
                key="ng_img_style",
            )
            custom_img_suffix = st.text_input(
                _t("custom_suffix"),
                key="new_game_custom_img",
                placeholder="e.g. 'oil painting, baroque style, rich colors'",
            )
            st.caption(_t("map_hint"))

            allow_custom_action = st.checkbox(_t('allow_custom'), value=True,
                                              help=_t('allow_custom_help'))

            st.markdown("---")
            st.markdown(f"**{_t('party_hdr')}**")
            num_players = st.selectbox(
                _t("num_players"), list(range(1, config.MAX_PARTY_SIZE + 1)),
                key="ng_num_players",
            )

            player_fields = []
            for i in range(num_players):
                player_fields.append(_player_config_fields(i, key_prefix="ng", ws=ws))
                if i < num_players - 1:
                    st.markdown("---")

            if st.form_submit_button(_t("start_adventure")):
                lead = player_fields[0]
                if not lead[0]:
                    st.error(_t("save_required"))
                else:
                    # Auto-generate save name: sequential ID + world setting id
                    import time as _time
                    _existing = st.session_state.save_manager.list_saves()
                    _next_id  = len(_existing) + 1
                    _ws_tag   = ws_ids[ws_idx]
                    save_name = f"{_next_id:03d}_{_ws_tag}"
                    # Ensure uniqueness by appending timestamp if collision
                    _existing_names = {s['save_name'] for s in _existing}
                    while save_name in _existing_names:
                        _next_id += 1
                        save_name = f"{_next_id:03d}_{_ws_tag}"

                    extra = []
                    for name, race, char_class, app, per, gender, is_ai, ai_pers, ai_diff in player_fields[1:]:
                        extra.append({
                            'name': name or f'Adventurer {len(extra)+2}',
                            'race': race, 'char_class': char_class,
                            'appearance': app, 'personality': per,
                            'gender': gender,
                            'is_ai': is_ai,
                            'ai_personality': ai_pers, 'ai_difficulty': ai_diff,
                        })
                    party, game_state, session = (
                        st.session_state.save_manager.create_new_game(
                            save_name, lead[0], lead[1], lead[2], lead[3], lead[4],
                            difficulty, language,
                            gender=lead[5],
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
                            'race_0':   lead[1], 'class_0':  lead[2],
                            'app_0':    lead[3], 'per_0':    lead[4],
                            'gender_0': lead[5],
                            'mbti_0':   st.session_state.get('ng_mbti_0', ''),
                        }
                        for _si, _ep in enumerate(extra, start=1):
                            new_prefs[f'race_{_si}']           = _ep['race']
                            new_prefs[f'class_{_si}']          = _ep['char_class']
                            new_prefs[f'is_ai_{_si}']          = _ep['is_ai']
                            new_prefs[f'ai_personality_{_si}'] = _ep['ai_personality']
                            new_prefs[f'ai_difficulty_{_si}']  = _ep['ai_difficulty']
                            new_prefs[f'gender_{_si}']         = _ep['gender']
                            new_prefs[f'mbti_{_si}']           = st.session_state.get(f'ng_mbti_{_si}', '')
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
                    st.success(_t('loaded_party').format(names=names))
                    st.rerun()
                else:
                    st.error(_t('load_failed'))

            if d_col.button("🗑️ Delete", use_container_width=True):
                selected_save = save_names[selected_idx]
                if st.session_state.save_manager.delete_game(selected_save):
                    st.success(_t('deleted_save').format(save=selected_save))
                    st.rerun()
                else:
                    st.error(_t('delete_failed').format(save=selected_save))

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
                f"<i style='font-size:0.85em'>{_tr_rc(char.race, char.char_class)}</i>"
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
                f"<i style='font-size:0.85em'>{_tr_rc(char.race, char.char_class)}</i>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.sidebar.markdown(
                f"**{flag}{ai_badge} {char.name}**  *{_tr_rc(char.race, char.char_class)}*"
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
        f"font-size:0.82em;color:#8898cc'>{_t('prologue_badge')}</div>",
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
    st.markdown(f"#### {_t('manual_dice')}")
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
    st.caption(_t('dice_legend'))


def _render_score_board(party, state):
    """Contribution score board: damage, healing, skill checks, gold."""
    st.markdown(f"#### {_t('scoreboard')}")
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
            f"<small style='color:#aaa'>({_tr_class(char.char_class)})</small>  `{score:.0f}pt`",
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
            st.info(f"🤖 {flag} **{active_char.name}** {_t('ai_acting')}")
        else:
            st.info(f"{flag} **{active_char.name}** {_t('player_turn')}")

    # ── Continent map image ────────────────────────────────────────────────
    st.markdown(f"#### {_t('continent_map')}")
    continent_img = st.session_state.get('continent_map')
    img_style     = st.session_state.get('image_style', 'fantasy_art')
    style_name    = IMAGE_STYLES.get(img_style, {}).get('name', img_style)

    if continent_img is not None:
        st.image(continent_img, caption=f"🌍 {ws['name']}  ·  {_t('img_style_cur')}: {style_name}",
                 use_container_width=True)
        if st.button(_t('regen_map'), key="regen_map_btn",
                     disabled=not _img_enabled()):
            st.session_state.continent_map = None
            _generate_continent_map(ws)
    else:
        gen_col, _ = st.columns([2, 3])
        if gen_col.button(_t('gen_map'), key="gen_map_btn",
                          use_container_width=True, disabled=not _img_enabled()):
            _generate_continent_map(ws)
        else:
            st.caption(
                f"🎨 {ws['name']}  ·  {style_name}"
            )

    st.divider()

    # ── Token grid map ─────────────────────────────────────────────────────
    col_map, col_right = st.columns([3, 1])

    with col_map:
        st.markdown(f"#### {_t('location_map')}")
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
            st.info(_t('location_map_hint'))

    with col_right:
        _render_score_board(party, state)
        st.divider()
        _render_manual_dice_roller()

    # ── Relationship graph ─────────────────────────────────────────────────
    st.divider()
    st.markdown(f"#### {_t('relation_graph')}")
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
        st.warning(f"**{active_char.name}** {_t('char_fallen')}")

    elif current_choices:
        # Branching narrative choices — display prominently (≥3 choices, 3-col layout)
        st.markdown("---")
        st.markdown(
            f"<div style='font-size:1.1em;font-weight:bold;color:#c0c0ff;"
            f"margin-bottom:6px'>🔀 {flag} {_t('choose_action').format(name=active_char.name)}</div>",
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
            with st.expander(_t('custom_action_expander')):
                with st.form("custom_action_form", clear_on_submit=True):
                    custom = st.text_input(_t('custom_action_input'))
                    if st.form_submit_button(_t('execute')) and custom:
                        action_taken = custom
    else:
        # Free-text action input
        with st.form("action_form", clear_on_submit=True):
            prompt_text = (
                _t('action_prompt_multi').format(flag=flag, name=active_char.name) if len(party) > 1
                else _t('action_prompt_single')
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
        with st.spinner(f"📖 {dm_lbl} {_t('dm_thinking')}"):
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

                elif not cinematic and any(kw in action_taken for kw in _LOOK_KEYWORDS):
                    # Non-cinematic: only generate on explicit look/examine actions (all languages)
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
                    st.warning(_t('img_gen_disabled_auto'))

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
    """Tab 3 — 角色: Player characters on top, then NPCs below."""
    from engine.world import WorldManager
    ws_id   = getattr(state, 'world_setting', None) or 'dnd5e'
    ws      = config.get_world_setting(ws_id)
    tm      = ws.get('term_map', {})
    ai_cfgs = getattr(state, 'ai_configs', None) or {}

    st.subheader(_t('party_details'))

    class_icons = {'warrior': '⚔️', 'mage': '🔮', 'rogue': '🗡️', 'cleric': '✨'}

    for i, char in enumerate(party):
        flag      = config.PLAYER_FLAGS[i] if i < len(config.PLAYER_FLAGS) else '👤'
        is_active = (char.id == active_char.id)
        is_dead   = (char.hp <= 0)
        ai_cfg    = ai_cfgs.get(str(i), {})
        is_ai     = ai_cfg.get('is_ai', False)

        dead_tag   = _t('dead_tag')   if is_dead   else ""
        active_tag = _t('active_tag') if is_active else ""
        if is_ai:
            p = ai_cfg.get('personality', '')
            d = ai_cfg.get('difficulty', '')
            pn = config.AI_PERSONALITIES.get(p, {}).get('name', p)
            dn = config.AI_DIFFICULTIES.get(d, {}).get('name', d)
            ai_tag = f" 🤖 [{pn} · {dn}]"
        else:
            ai_tag = ""

        cls_icon = class_icons.get(char.char_class.lower(), '👤')
        gender_lbl = f" ({getattr(char, 'gender', '') or ''})" if getattr(char, 'gender', '') else ""
        header   = f"{flag} {cls_icon} {char.name}{gender_lbl} — {_tr_rc(char.race, char.char_class)}{ai_tag}{active_tag}{dead_tag}"

        with st.expander(header, expanded=is_active):
            # ── Portrait ────────────────────────────────────────────────────
            portrait = st.session_state.portraits.get(char.id)
            por_col, stat_col = st.columns([1, 3])
            with por_col:
                if portrait is not None:
                    st.image(portrait, caption=char.name, use_container_width=True)
                    if st.button("🔄", key=f"regen_por_{char.id}",
                                 help=_t('regen_portrait_help').format(name=char.name),
                                 disabled=not _img_enabled()):
                        del st.session_state.portraits[char.id]
                        _generate_portrait(char, ws)
                else:
                    st.markdown(
                        "<div style='background:#0d1117;border:1px dashed #333;"
                        "height:150px;display:flex;align-items:center;"
                        "justify-content:center;border-radius:6px;"
                        f"color:#555;font-size:0.8em'>{_t('portrait_not_generated')}</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(_t('gen_portrait'), key=f"gen_por_{char.id}",
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
                        st.markdown(f"**{_t('skills_label')}:**")
                        for skill in char.skills:
                            st.write(f"  • {skill}")
                    if char.inventory:
                        st.markdown(f"**{_t('inventory_label')}:**")
                        for item in char.inventory:
                            nm = item.get('name', item) if isinstance(item, dict) else item
                            st.write(f"  • {nm}")
                    if char.appearance:
                        st.caption(f"{_t('appearance_label')}: {char.appearance}")
                    if char.personality:
                        st.caption(f"{_t('personality_label')}: {char.personality}")

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
                    with st.expander(_t('relations_expander'), expanded=False):
                        _render_relation_rows(char_rels, char_key, _lk)
            except Exception:
                pass

    # ── NPC Characters ───────────────────────────────────────────────
    rels = (state.relationships or {}) if state else {}
    npc_list = [(name, d) for name, d in rels.items() if isinstance(d, dict)]
    st.subheader(_t('npc_known').format(n=len(npc_list)))

    if not npc_list:
        st.caption(_t('no_npcs'))
    else:
        for name, d in npc_list:
            proper   = d.get('proper_name', name)
            affinity = d.get('affinity', 0)
            state_lbl= d.get('state', 'Neutral')
            goal     = d.get('goal', '')
            emotion  = d.get('emotion', '')
            health   = d.get('health', '')

            npc_gender = d.get('gender', '')

            aff_clr = '#4caf50' if affinity > 20 else ('#f44336' if affinity < -20 else '#9e9e9e')
            header = f"{'🎭' if emotion else '👤'} {proper or name}"
            if npc_gender:
                header += f" ({npc_gender})"
            if state_lbl:
                header += f"  ·  {state_lbl}"
            if emotion:
                header += f"  ·  {emotion}"

            with st.expander(header, expanded=False):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(
                        f"**{_t('npc_affinity')}** <span style='color:{aff_clr};font-size:1.4em;"
                        f"font-weight:bold'>{affinity:+d}</span>",
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(f"**{_t('npc_state_lbl')}** {state_lbl or '—'}")
                with c3:
                    st.markdown(f"**{_t('npc_health_lbl')}** {health or '—'}")

                if goal:
                    st.caption(f"{_t('npc_goal_lbl')} {goal}")

                aliases = d.get('aliases') or []
                if aliases:
                    st.caption(f"{_t('npc_aliases_lbl')} {' · '.join(aliases)}")

                bio = d.get('biography', '')
                if bio:
                    st.info(f"**{_t('npc_bio_lbl')}** {bio}", icon="📜")

                # Relations for this NPC
                try:
                    _db    = st.session_state.save_manager.db
                    _sess  = _db.get_session()
                    _world = WorldManager(_sess, state)
                    npc_key = name.lower()
                    npc_rels = _world.get_relations('char', npc_key)
                    if npc_rels:
                        _lk = {o['name'].lower(): o['name']
                               for o in _world.list_organizations()}
                        for nn in rels:
                            _lk[nn.lower()] = rels[nn].get('proper_name', nn) if isinstance(rels[nn], dict) else nn
                        for c in party:
                            _lk[c.name.lower()] = c.name
                        with st.expander(_t('relations_expander'), expanded=False):
                            _render_relation_rows(npc_rels, npc_key, _lk)
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
        st.info(_t('no_relations'))
        return

    st.caption(_t('rel_count').format(n=len(all_relations)))
    rel_types = sorted({r.relation_type for r in all_relations})
    sel_types = st.multiselect(_t('rel_type_filter'), rel_types, default=rel_types,
                               key="board_rel_type_filter")
    shown = [r for r in all_relations if r.relation_type in sel_types]
    for rel in sorted(shown, key=lambda r: r.since_turn):
        src_label = label_lookup.get(rel.source_key, rel.source_key.title())
        tgt_label = label_lookup.get(rel.target_key, rel.target_key.title())
        colour    = _strength_colour(rel.strength)
        turn_lbl  = _t('prologue_turn') if rel.since_turn == 0 else _t('turn_label').format(n=rel.since_turn)
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
        st.caption(_t('no_known_relations'))
        return
    for rel in sorted(relations, key=lambda r: -abs(r.strength)):
        is_outgoing = rel.source_key == self_key.lower()
        other_key   = rel.target_key   if is_outgoing else rel.source_key
        other_label = label_lookup.get(other_key, other_key.title())
        arrow       = "→" if is_outgoing else "←"
        colour      = _strength_colour(rel.strength)
        turn_label  = _t('prologue_turn') if rel.since_turn == 0 else _t('turn_label').format(n=rel.since_turn)
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
        st.info(_t('no_orgs'))
        return

    # Search bar
    search = st.text_input(_t('search_orgs'), key="org_search",
                           placeholder=_t('search_orgs_ph'))
    query = search.strip().lower()
    filtered = orgs
    if query:
        filtered = [o for o in orgs
                    if query in (o.get('name') or '').lower()
                    or query in (o.get('type') or '').lower()
                    or query in (o.get('current_leader') or '').lower()
                    or query in (o.get('headquarters') or '').lower()]

    st.caption(_t('org_count').format(n=len(filtered)))

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
                    st.markdown(f"**{_t('org_founder')}** {org['founder']}")
                if org.get('current_leader'):
                    st.markdown(f"**{_t('org_leader')}** {org['current_leader']}")
                if org.get('member_count'):
                    st.markdown(f"**{_t('org_members')}** {org['member_count']}")
            with cols[1]:
                if org.get('headquarters'):
                    st.markdown(f"**{_t('org_hq')}** {org['headquarters']}")
                if org.get('alignment'):
                    st.markdown(f"**{_t('org_alignment')}** {org['alignment']}")
                turn = org.get('first_seen_turn')
                if turn is not None:
                    st.markdown(f"**{_t('org_first_seen')}** Turn {turn}")
            if org.get('description'):
                st.markdown(f"> {org['description']}")
            if org.get('history'):
                st.markdown(f"**{_t('org_history')}**")
                st.markdown(org['history'])
            # Per-org relation rows
            org_key  = org['name'].lower()
            org_rels = [r for r in all_relations
                        if r.source_key == org_key or r.target_key == org_key]
            if org_rels:
                st.markdown(f"**{_t('org_relations')}**")
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
        _t('search_manual'),
        key="manual_search",
        placeholder=_t('search_manual_ph'),
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
            _t('chapter_label'),
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
    with st.expander(_t('toc_expander'), expanded=False):
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
        st.info(_t('no_story'))
        return

    n = len(pages)
    st.caption(f"📕 共 **{n}** 頁故事記錄  ·  存檔：`{save_name}`")

    # ── 📌 最新記錄 — always show last 2 pages at top ──────────────────────
    with st.expander(_t('latest_pages'), expanded=True):
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
    st.markdown(f"#### {_t('read_pages_header')}")

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
    st.caption("  \n".join(dot_rows) + f"  　{idx+1} / {n}  ★")


# ---------------------------------------------------------------------------
# Image style sidebar switcher
# ---------------------------------------------------------------------------

def _render_image_style_switcher():
    """Sidebar expander: switch image art style and regenerate map/portraits."""
    with st.sidebar.expander(_t('img_style_expander'), expanded=False):
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

        if st.button(_t('regen_all_images'), use_container_width=True,
                     key="sidebar_regen_images", disabled=not _img_enabled()):
            st.session_state.continent_map = None
            st.session_state.portraits     = {}
            st.rerun()

        cur_style = IMAGE_STYLES.get(st.session_state.get('image_style', 'fantasy_art'), {})
        st.caption(f"{_t('img_style_cur')}: **{cur_style.get('name','')}** — {cur_style.get('name_en','')}")

        # VRAM status
        img_gen = st.session_state.get('img_gen')
        if img_gen:
            if img_gen.is_disabled():
                st.error(_t('img_gen_disabled_auto'))
                if st.button(_t('img_gen_reenable'), key="reenable_img_gen",
                             use_container_width=True):
                    img_gen.reset_disabled()
                    st.rerun()
            elif not img_gen.can_generate_safely():
                st.warning(_t('img_gen_vram_warn'))
            else:
                st.success(_t('img_gen_ready'))


# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------

def _generate_continent_map(ws):
    """Generate and cache the continent map for the current world setting."""
    img_style = st.session_state.get('image_style', 'fantasy_art')
    prompt = build_map_prompt(ws, img_style, st.session_state.get('custom_img_suffix', ''))
    neg    = get_map_negative_prompt(img_style)
    with st.spinner(f"🎨 {ws['name']}…"):
        img = st.session_state.img_gen.generate_image(prompt, negative_prompt=neg)
    if img:
        st.session_state.continent_map = img
        state     = st.session_state.get('game_state')
        save_name = getattr(state, 'save_name', None)
        if save_name:
            save_image_with_text(save_name, img, ws.get('name', ''), 0, 'map')
        st.rerun()
    else:
        st.warning(_t('img_gen_map_fail'))


def _generate_portrait(char, ws):
    """Generate and cache a portrait for a single character."""
    img_style = st.session_state.get('image_style', 'fantasy_art')
    prompt = build_portrait_prompt(char, ws, img_style, st.session_state.get('custom_img_suffix', ''))
    neg    = get_portrait_negative_prompt(img_style)
    with st.spinner(f"🎨 {char.name}…"):
        img = st.session_state.img_gen.generate_image(prompt, negative_prompt=neg)
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
        st.warning(_t('img_gen_portrait_fail').format(name=char.name))


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
        st.session_state['_menu_needs_restore'] = True
        st.session_state.history           = []
        st.session_state.world_map         = {}
        st.session_state.player_positions  = {}
        st.session_state.manual_dice       = {}
        st.session_state.continent_map     = None
        st.session_state.portraits         = {}

    sq_col, qq_col = st.sidebar.columns(2)
    if sq_col.button(_t('save_game'), use_container_width=True):
        # Flush story log then commit DB — stay in game
        save_name_sq = getattr(state, 'save_name', None)
        if save_name_sq and st.session_state.history:
            save_game_log(save_name_sq, compress_game_log(st.session_state.history))
        st.session_state.current_session.commit()
        st.toast(_t('game_saved'), icon="💾")
    if qq_col.button(_t('quit_game'), use_container_width=True):
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
        with st.spinner(f"📖 {dm_lbl_p} {_t('writing_prologue')}"):
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
    tab_story, tab_board, tab_chars, tab_rules, tab_orgs, tab_book, tab_god = st.tabs([
        _t('tab_story'), _t('tab_gameboard'), _t('tab_characters'),
        _t('tab_rules'), _t('tab_orgs'), _t('tab_book'), _t('tab_godmode'),
    ])

    with tab_story:
        _render_story_tab(party, state, active_char, active_idx, ws_id)

    with tab_board:
        _render_game_board_tab(party, state, active_char, active_idx)

    with tab_chars:
        _render_characters_tab(party, state, active_char)

    with tab_orgs:
        _render_organizations_tab(state)

    with tab_rules:
        _render_rules_tab(state)

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
        "gender":      ("STRING",      "Character gender — Male / Female / Non-binary / Other"),
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
        with st.expander(f"🔴 {char.name} ({_tr_rc(char.race, char.char_class)})", expanded=False):
            rows = []
            live = {
                "id": char.id, "name": char.name, "race": char.race,
                "char_class": char.char_class, "gender": getattr(char, 'gender', '') or '',
                "appearance": char.appearance or "",
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

            npc_gender = d.get('gender', '')
            header = f"**{name}**"
            if proper and proper != name:
                header += f"（本名：{proper}）"
            if npc_gender:
                header += f"　👤 {npc_gender}"
            if emotion:
                header += f"　🎭 {emotion}"
            st.markdown(header)

            info_cols = st.columns(4)
            info_cols[0].metric("Affinity", f"{affinity:+d}", label_visibility="visible")
            info_cols[1].write(f"**狀態** {state_lbl}")
            info_cols[2].write(f"**性別** {npc_gender or '—'}")
            info_cols[3].write(f"**健康** {health or '—'}")

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

    # Organization full profiles — hidden parameters visible only in God Mode
    orgs_raw = getattr(state, 'organizations', None) or {}
    orgs_list = sorted(orgs_raw.values(), key=lambda o: o.get('first_seen_turn', 0))
    with st.expander(f"🏛️ 組織完整檔案 (Full Organization Profiles) — {len(orgs_list)} 個", expanded=False):
        if not orgs_list:
            st.caption("（尚無組織）")
        for org in orgs_list:
            org_type = (org.get('type') or 'unknown').lower()
            icon     = _ORG_TYPE_ICONS.get(org_type, '🏢')
            st.markdown(f"**{icon} {org.get('name', '—')}**")

            info_cols = st.columns(3)
            with info_cols[0]:
                st.write(f"**類型** {org.get('type') or '—'}")
                st.write(f"**創辦人** {org.get('founder') or '—'}")
                st.write(f"**成員規模** {org.get('member_count') or '—'}")
            with info_cols[1]:
                st.write(f"**現任領導人** {org.get('current_leader') or '—'}")
                st.write(f"**據點** {org.get('headquarters') or '—'}")
                st.write(f"**陣營傾向** {org.get('alignment') or '—'}")
            with info_cols[2]:
                turn = org.get('first_seen_turn')
                turn_lbl = _t('prologue_turn') if turn == 0 else _t('turn_label').format(n=turn) if turn is not None else '—'
                st.write(f"**首次登場** {turn_lbl}")

            if org.get('description'):
                st.info(f"**描述** {org['description']}", icon="📝")
            if org.get('history'):
                st.info(f"**歷史沿革** {org['history']}", icon="📜")

            # Show raw JSON for all fields (including any unlisted/hidden ones)
            visible_keys = {'name', 'type', 'founder', 'history', 'member_count',
                            'current_leader', 'headquarters', 'alignment',
                            'description', 'first_seen_turn'}
            hidden = {k: v for k, v in org.items() if k not in visible_keys and v}
            if hidden:
                st.caption("🔒 隱藏欄位（僅上帝模式可見）")
                for k, v in hidden.items():
                    st.write(f"  **{k}**: {v}")

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
        ("LLM_MODEL_NAME",           str(config.LLM_MODEL_NAME),          "Ollama/API 語言模型識別碼"),
        ("IMAGE_MODEL_NAME",         str(config.IMAGE_MODEL_NAME),         "預設影像模型識別碼"),
        ("VRAM_STRATEGY",            str(config.VRAM_STRATEGY),            "A=跳過影像 / B=換模型"),
        ("USER_VRAM_GB",             str(config.USER_VRAM_GB),             "總 GPU VRAM 預算（GB）"),
        ("IMAGE_VRAM_REQUIRED_GB",   str(config.IMAGE_VRAM_REQUIRED_GB),   "最低可用 VRAM 門檻（GB）"),
        ("IMAGE_GEN_MAX_FAILURES",   str(config.IMAGE_GEN_MAX_FAILURES),   "連續失敗幾次後停用影像生成"),
        ("IMAGE_GEN_MILESTONE_TURNS",str(config.IMAGE_GEN_MILESTONE_TURNS),"每 N 回合強制生成場景圖"),
        ("SESSION_MEMORY_WINDOW",    str(config.SESSION_MEMORY_WINDOW),    "滑動記憶窗口大小（回合數）"),
        ("CONTEXT_WINDOW_SIZE",      str(config.CONTEXT_WINDOW_SIZE),      "目標 token 預算（需與模型一致）"),
        ("EMBEDDING_MODEL",          str(config.EMBEDDING_MODEL or "(default MiniLM)"), "ChromaDB 嵌入模型路徑"),
        ("SAVE_DIR",                 str(config.SAVE_DIR),                 "SQLite 存檔目錄"),
        ("CHROMA_DB_DIR",            str(config.CHROMA_DB_DIR),            "ChromaDB 持久化目錄"),
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

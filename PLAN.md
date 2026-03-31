# DND-like RPG — 開發計畫

本文件追蹤所有待實作功能。依優先級由上而下執行。

---

## 狀態標記
- ✅ 已完成
- 🔄 進行中
- ⬜ 待實作

---

## Phase 1 — 核心戰鬥系統 ✅

| 功能 | 狀態 | 檔案 |
|---|---|---|
| 29 種怪物花名冊（4 tier）| ✅ | `data/monsters.py` |
| 25 種特殊能力 | ✅ | `data/monsters.py` |
| CombatEngine（攻擊判定、暴擊、防禦減傷）| ✅ | `engine/combat.py` |
| 職業技能（Warrior/Mage/Rogue/Cleric）| ✅ | `engine/combat.py` |
| 狀態效果（毒、燃燒、暈眩、虛弱）| ✅ | `engine/combat.py` |
| XP / 等級系統（D&D 5e 里程碑表）| ✅ | `engine/combat.py` |
| 戰利品掉落（50% 機率 per item）| ✅ | `engine/combat.py` |
| 怪物 AI 觸發（狂暴、歌聲、召喚）| ✅ | `logic/events.py` |
| NPC 意圖識別整合 | ✅ | `engine/intent_parser.py` |
| DB 欄位：xp, level, in_combat | ✅ | `engine/game_state.py` |

## Phase 2 — UI 戰鬥顯示層 ✅

| 功能 | 狀態 | 檔案 |
|---|---|---|
| 戰鬥橫幅（命中 / 暴擊 / 未命中）| ✅ | `ui/app.py` |
| 敵人 HP 條（側邊欄，顏色分級）| ✅ | `ui/app.py` |
| 狀態效果徽章 | ✅ | `ui/app.py` |
| 職業技能快捷面板 | ✅ | `ui/app.py` |
| XP 進度條（每位角色）| ✅ | `ui/app.py` |
| 掉落物 / XP / 升級橫幅 | ✅ | `ui/app.py` |

## Phase 3 — Bug 修復 ✅

| Bug | 狀態 |
|---|---|
| 毒/燃燒僅在反擊命中時 tick | ✅ |
| 吸血讀取過期快照 | ✅ |
| `char_logic` 無 None 保護 | ✅ |
| 重複 `not is_dead` 條件 | ✅ |

---

## Phase 4 — 遊戲流程完整化 ✅

### 4-1 逃跑機制 ✅
**目標**：玩家輸入「逃跑/flee/retreat」時進行 MOV 骰子判定；成功則脫離戰鬥，失敗則敵人反擊。

- `logic/events.py`：偵測 `_FLEE_RE`；呼叫 `_resolve_flee()`
- `engine/combat.py`：`resolve_flee(character, target_entry, dice)` — `1d20 + MOV modifier` vs `DC 10 + target.mov`
- 成功：`in_combat = 0`，清空 `_used_abilities`，narrative 注入逃跑事實
- 失敗：敵人自動反擊一次（`resolve_enemy_counter_attack`）

### 4-2 `in_combat` 旗標完整整合 ✅
**目標**：`in_combat` 正確追蹤戰鬥狀態，影響 UI 與規則。

- 戰鬥開始（偵測到 attack intent 且有存活敵人）→ `in_combat = 1`
- 敵人全滅 → `in_combat = 0`，重置 `_used_abilities`
- 逃跑成功 → `in_combat = 0`
- UI：`in_combat == 1` 時側邊欄顯示「⚔️ 戰鬥中」警示，職業技能面板自動展開

### 4-3 多敵人遭遇 ✅
**目標**：LLM 描述「房間裡有 3 隻哥布林」時，自動生成多個 `known_entities` 入口。

- `logic/events.py` Step 4：解析 `target` 欄位的複數描述（`3x goblin`、`goblin x3`）
- 批次呼叫 `_auto_register_entity()` 為每個實例建立唯一 key（`goblin_1`、`goblin_2`）
- AoE 攻擊自動命中全部同類敵人
- 敵人側邊欄顯示編號

### 4-4 玩家死亡流程 ✅
**目標**：HP ≤ 0 時觸發 Game Over，提供重新開始或讀取存檔選項。

- `logic/events.py`：在 `_apply_mechanics` 後檢查 `character.hp <= 0`，回傳 `{'game_over': True}`
- `ui/app.py`：偵測 `game_over` → 清除 `st.session_state`，顯示 Game Over 畫面
- 保留存檔不刪除；提供「讀取存檔」與「新遊戲」按鈕

### 4-5 難度動態調整 ✅
**目標**：根據玩家等級自動選擇適合的怪物 tier，避免高等玩家遇到過弱/過強的敵人。

- `engine/intent_parser.py`：`get_entity_base_stats()` 加入 `player_level` 參數
- 等級對應 tier：Lv 1-2 → Tier 1、Lv 3-5 → Tier 2、Lv 6-8 → Tier 3、Lv 9-10 → Tier 4
- 若指定怪物超出 tier 範圍，套用額外縮放係數（不替換，保留玩家自由度）

---

## Phase 5 — 資料與訓練（低優先）✅

### 5-1 D&D 5e SRD 資料植入 ✅
工具 `tools/seed_srd.py` 已完整實作，`data/srd/` 目錄已建立。
執行方式（需先下載 JSON 文件）：
```bash
git clone https://github.com/soryy708/dnd5-srd /tmp/dnd5-srd
cp /tmp/dnd5-srd/src/5e-SRD-*.json data/srd/
python tools/seed_srd.py
```

### 5-2 LoRA 訓練資料生成 ✅
工具 `tools/gen_lora_data.py` 已完整實作，支援 Alpaca / ChatML 格式。
```bash
python tools/gen_lora_data.py --samples 200
```

### 5-3 音效管理器 ✅
`ai/audio_gen.py` 已升級為場景感知 stub：
- `_SCENE_BGM` 對映表：combat → battle_intense、social → tavern_ambient 等
- `_EVENT_SFX` 對映表：hit/crit/miss/flee/loot/level_up/game_over 等
- `on_scene_change(scene_type, combat_result, flee_result, loot_xp)` — 每回合呼叫
- `play_cue(event_key)` — 單次音效（如死亡、升級）
- 整合至 `ui/app.py`：session_state 初始化、每回合 on_scene_change、game_over 觸發
- 後端接入路徑：HTML5 `<audio>` via `st.html()` 或 MusicGen / Bark TTS

---

## Phase 6 — 戰鬥品質與遊戲流程優化 ✅

### 6-A 擴充攻擊關鍵字 ✅
**目標**：減少不必要的 LLM fallback，提升意圖識別速度與準確率。

- `engine/intent_parser.py`：`_ATTACK_RE` 補充同義詞：
  - 中文：傷害、刺穿、揮砍、痛擊、刺殺、暗殺、重擊、踢、踹、打倒
  - 英文：hurt、wound、assault、maul、batter、slay、dispatch、stab at、cut down、lunge、ram、gore、shred

### 6-B 戰鬥自動連續模式 ✅
**目標**：`in_combat=1` 時若玩家未輸入，自動提示「繼續攻擊 / 使用技能 / 逃跑」三個快捷按鈕，減少每回合重複輸入。

- `ui/app.py`：`_render_story_tab()` 中，`in_combat=1` 且有存活敵人時，在選擇肢之前顯示戰鬥快捷列
- 快捷按鈕動態產生：「⚔️ 繼續攻擊 {target}」、「✨ 使用技能」、「🏃 逃跑」
- 點擊按鈕直接觸發 `action_taken`，不需要表單提交

### 6-C Boss 遭遇特殊演出 ✅
**目標**：tier=4 怪物（Lich、Ancient Dragon 等）首次出現時顯示特殊橫幅和 stat 面板。

- `ui/app.py`：新增 `_render_boss_encounter_banner(entity_entry)` 函式
- 觸發條件：`known_entities` 中新增 tier=4 且 `alive=True` 的實體
- 顯示：特殊暗色橫幅（名稱、HP、特殊能力、弱點/抗性），配合 `⚠️` 圖示
- `logic/events.py`：在 Step 5 後若新生成 boss 實體，在 `turn_data` 中記錄 `_boss_encounter` key

### 6-D 死亡後讀取存檔修復 ✅
**目標**：Game Over 畫面的「讀取存檔」按鈕實際跳轉到讀檔選單。

- `ui/app.py`：檢查 `_show_load_game` 旗標在主流程中的路由
- 確保 Game Over → 設旗標 → `st.rerun()` → 主選單顯示讀檔 UI
- 若需要，在 `main()` / `menu()` 入口補上旗標檢測分支

### 6-E 多人 AI 自動行動 ✅
**目標**：Party 中的 AI 角色每輪自動行動，不需玩家手動操作。

- `logic/events.py`：`_advance_active_player()` 後，若下一位是 AI 角色，自動呼叫 `AIPlayerController.decide_action()`
- `ui/app.py`：AI 行動結果以「🤖 {name}：{action}」格式顯示在歷史紀錄中

### 6-F 物品使用系統 ✅
**目標**：在戰鬥和探索中使用 inventory 內的消耗品（藥水、投擲物）。

- `engine/intent_parser.py`：新增 `_ITEM_USE_RE` 識別「喝藥水 / use potion / drink」等意圖
- `engine/character.py`：`use_item(item_name)` → 從 inventory 移除並返回效果 dict
- `engine/combat.py`：物品效果映射（healing_potion → hp_healed, poison_vial → apply_status）
- `logic/events.py`：Step 5 後處理 item_use intent，套用效果並注入敘事事實
- `ui/app.py`：側邊欄 inventory 物品可點擊（戰鬥中顯示「使用」按鈕）

### 6-G 地城地圖生成 ✅
**目標**：WorldManager 支援房間/走廊樹狀結構，讓逃跑、探索有實際地理意義。

- `engine/world.py`：`generate_dungeon(room_count, seed)` → 隨機生成連接圖
  - 每個房間：`{id, name, description, connections: [room_id], enemies: [], loot: [], visited: bool}`
  - 演算法：隨機深度優先展開，確保連通性
- `engine/game_state.py`：`GameState` 新增 `dungeon_map = Column(JSON, default={})`
- `ui/app.py`：`_render_game_board_tab()` 顯示簡易 ASCII 或 Streamlit graph 地城地圖
- `logic/events.py`：location_change 時查詢鄰接房間，逃跑成功移動到連接房間

---

## 變更歷史

| 日期 | 版本 | 摘要 |
|---|---|---|
| 2026-03-30 | Phase 1-3 | 怪物系統、戰鬥引擎、UI 顯示層、4 項 bug 修復 |
| 2026-03-30 | Phase 4   | 逃跑機制、in_combat UI、多敵人生成、死亡流程、難度動態縮放 |
| 2026-03-31 | Phase 5   | SRD seeder 工具、LoRA 資料生成器、音效管理器升級與整合 |
| 2026-03-31 | Phase 6   | 攻擊詞擴充、戰鬥快捷列、Boss 橫幅、死亡讀檔、AI 自動行動、物品使用、地城地圖 |

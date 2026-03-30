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

## Phase 5 — 資料與訓練（低優先）

### 5-1 D&D 5e SRD 資料植入 ⬜
```bash
git clone https://github.com/soryy708/dnd5-srd /tmp/dnd5-srd
cp /tmp/dnd5-srd/src/5e-SRD-*.json data/srd/
python tools/seed_srd.py
```

### 5-2 LoRA 訓練資料生成 ⬜
```bash
python tools/gen_lora_data.py --samples 200
```

### 5-3 音效存根實作 ⬜
- `ai/audio_gen.py`：接入 Bark / Coqui TTS，戰鬥/勝利/死亡各一段音效

---

## 變更歷史

| 日期 | 版本 | 摘要 |
|---|---|---|
| 2026-03-30 | Phase 1-3 | 怪物系統、戰鬥引擎、UI 顯示層、4 項 bug 修復 |
| 2026-03-30 | Phase 4   | 逃跑機制、in_combat UI、多敵人生成、死亡流程、難度動態縮放 |

# LLM 使用分析報告

> 分析日期：2026-03-13
> 分析範圍：`ai/llm_client.py`、`logic/events.py`

---

## 一、已正確使用 LLM 的部分（文本生成 / 語意理解）

| 功能 | 檔案位置 | 用途 | 狀態 |
|------|----------|------|------|
| `render_narrative()` | llm_client.py:416 | 將機械結果轉為故事文字 | ✅ 正確 |
| `generate_prologue()` | llm_client.py:824 | 開場白文本生成 | ✅ 正確 |
| `summarize_memory_segment()` | llm_client.py:1081 | 溢出回合摘要 | ✅ 正確 |
| `_ensure_min_length()` | llm_client.py:492 | 文本續寫（relay continuation） | ✅ 正確 |
| `_localize_narrative()` | llm_client.py:649 | 翻譯選項 / 物品 / 地點 | ✅ 正確 |
| `_localize_stat_block()` | llm_client.py:737 | 翻譯 stat block 文字欄位 | ✅ 正確 |
| `_fix_placeholder_choices()` | llm_client.py:588 | 根據敘事重新生成具體選項 | ✅ 正確 |
| `generate_diverse_choices()` | llm_client.py:617 | 生成與近期行動不同的替代選項 | ✅ 正確 |

---

## 二、可替換為傳統演算法的部分

### 2.1 `parse_intent()` — 部分可替換

**現狀**：每回合呼叫 LLM 分類玩家行動，輸出 `action_type`、`requires_roll`、`skill`、`dc`、`target`。

**可用傳統演算法替代的欄位**：

| 欄位 | 替代方案 |
|------|----------|
| `action_type` | 關鍵字 / 正則匹配：`攻擊/attack/strike/hit` → `attack`；`搜索/search/examine` → `explore`；`說服/persuade/talk` → `social` |
| `target` | Fuzzy match 在 `known_entities` 列表中找最接近的實體名稱 |
| `requires_roll` | 查表：`attack` → True；`direct_action` → False |
| `dc` | 查表 + difficulty 設定：Easy=10, Normal=15, Hard=20 |
| `skill` | 關鍵字映射表：`偷偷/sneak/stealth` → `stealth`；`說服/persuade` → `persuasion` |

**建議：混合模式**

先跑關鍵字規則引擎；明確模式直接返回，無法匹配再 fallback 到 LLM。
預估可節省 60–70% 的 intent parsing LLM 呼叫。

```python
# 概念範例（logic/events.py 或新增 engine/intent_parser.py）
ATTACK_PATTERN  = re.compile(r'(攻擊|attack|strike|hit|slash|stab)', re.I)
EXPLORE_PATTERN = re.compile(r'(搜索|search|look|examine|investigate|explore)', re.I)
SOCIAL_PATTERN  = re.compile(r'(說話|說服|talk|persuade|negotiate|convince)', re.I)

DC_TABLE = {'easy': 10, 'normal': 15, 'hard': 20, 'deadly': 25}

SKILL_KEYWORDS = {
    'stealth':    ['偷偷', '潛行', 'sneak', 'stealth', 'hide'],
    'persuasion': ['說服', 'persuade', 'convince', 'negotiate'],
    'perception': ['觀察', '察覺', 'look', 'search', 'notice'],
    'athletics':  ['攀爬', '跳躍', 'climb', 'jump', 'swim'],
    'acrobatics': ['翻滾', '閃避', 'dodge', 'roll', 'tumble'],
    'medicine':   ['治療', '急救', 'heal', 'treat', 'bandage'],
    'intimidation': ['威嚇', '恐嚇', 'intimidate', 'threaten'],
    'arcana':     ['魔法', '法術', 'magic', 'arcane', 'spell'],
}

def parse_intent_hybrid(player_action, known_entities, difficulty, llm_client, game_context):
    action_lower = player_action.lower()

    if ATTACK_PATTERN.search(action_lower):
        target = fuzzy_match_entity(player_action, known_entities)
        return {
            "action_type": "attack", "requires_roll": True,
            "skill": "", "dc": 0,
            "target": target, "summary": player_action,
        }

    matched_skill = next(
        (skill for skill, kws in SKILL_KEYWORDS.items()
         if any(kw in action_lower for kw in kws)),
        None
    )
    if matched_skill:
        dc = DC_TABLE.get(difficulty.lower(), 15)
        return {
            "action_type": "skill_check", "requires_roll": True,
            "skill": matched_skill, "dc": dc,
            "target": fuzzy_match_entity(player_action, known_entities),
            "summary": player_action,
        }

    # Fallback: LLM handles ambiguous / complex input
    return llm_client.parse_intent(player_action, game_context)
```

---

### 2.2 `render_narrative()` 中的機械數值欄位 — **應優先替換（違反設計原則）**

**問題**：這是最嚴重的設計缺陷。專案設計原則明確聲明：

> *"All dice rolling, stat mutations, and rule adjudication happen in deterministic Python code, never inside the LLM."*

但 `render_narrative()` 的 JSON schema 要求 LLM 輸出以下機械數值，並在 Step 8 直接套用：

| 欄位 | 問題 | 替代方案 |
|------|------|----------|
| `damage_taken` | LLM 決定玩家受多少傷害 | 規則引擎：敵人 ATK − 玩家 DEF // 2，結合骰子 |
| `hp_healed` | LLM 決定回復多少 HP | 規則引擎：技能 / 物品查表（固定值或骰子） |
| `mp_used` | LLM 決定消耗多少 MP | 規則引擎：法術 MP 消耗查表 |
| `items_found` | LLM 決定獲得什麼物品 | 戰利品表（loot table）+ 骰子隨機抽取 |
| `location_change` | LLM 決定是否移動 | 由 intent `action_type == 'explore'` + 地圖結構決定 |

**影響**：目前 LLM 可以任意輸出「受到 999 傷害」或「獲得神器」，完全繞過遊戲平衡。

**建議修正方向**：

在 `process_turn()` Step 6（骰子結算）之後，由規則引擎預先計算這些值，然後作為 hard facts 注入 narrative prompt。LLM 的 schema 中移除這些數值欄位，只保留純敘事輸出。

```python
# engine/rules.py（新增）
def calculate_damage_taken(scene_type, outcome, character, known_entities):
    """根據場景類型和結果計算玩家受傷。"""
    if scene_type != 'combat' or outcome in ('success', 'critical_success'):
        return 0
    enemies = [e for e in known_entities.values() if e.get('alive') and e.get('type') == 'monster']
    if not enemies:
        return 0
    # 取最強敵人的反擊傷害
    strongest = max(enemies, key=lambda e: e.get('atk', 0))
    base = DiceRoller().roll('1d6')[2] + (strongest.get('atk', 5) - 10) // 2
    return max(0, base - character.def_stat // 2)

def get_mp_cost(skill, action_type):
    """根據技能類型返回 MP 消耗。"""
    MP_COSTS = {'arcana': 3, 'magic': 5, 'medicine': 2}
    if action_type == 'magic':
        return MP_COSTS.get(skill, 3)
    return 0
```

---

### 2.3 `generate_entity_stat_block()` 的數值部分 — 可替換

**現狀**：LLM 生成 `hp`、`atk`、`def_stat` 等數值。

**替代方案**：基於 entity type + difficulty 的查表系統，加上 ±20% 隨機浮動。

```python
# engine/character.py 或 engine/rules.py
import random

ENTITY_STAT_TABLE = {
    # entity_type: {difficulty: (hp, atk, def_stat)}
    'monster':  {'easy': (15, 8, 8),   'normal': (25, 12, 10), 'hard': (40, 16, 14), 'deadly': (60, 20, 18)},
    'boss':     {'easy': (40, 12, 12), 'normal': (60, 18, 16), 'hard': (100, 22, 20), 'deadly': (150, 28, 24)},
    'npc':      {'easy': (10, 5, 5),   'normal': (15, 8, 8),   'hard': (20, 12, 10), 'deadly': (25, 14, 12)},
    'guard':    {'easy': (12, 8, 10),  'normal': (20, 10, 12), 'hard': (30, 14, 16), 'deadly': (45, 18, 20)},
    'merchant': {'easy': (8, 4, 4),    'normal': (10, 5, 5),   'hard': (12, 6, 6),   'deadly': (15, 8, 8)},
}

def get_base_stats(entity_type, difficulty):
    diff_key = difficulty.lower() if difficulty else 'normal'
    table = ENTITY_STAT_TABLE.get(entity_type, ENTITY_STAT_TABLE['npc'])
    hp, atk, def_stat = table.get(diff_key, table['normal'])
    # ±20% 隨機浮動
    variance = lambda v: max(1, int(v * random.uniform(0.8, 1.2)))
    return variance(hp), variance(atk), variance(def_stat)
```

**LLM 保留**：`description`、`special_ability`、`skills`、`loot` 的文字生成仍需 LLM。

---

### 2.4 `evaluate_npc_reactions()` 的 `affinity_delta` — 部分可替換

**現狀**：LLM 決定 affinity 變化量（-30 ~ +30）。

**可替換部分**：`affinity_delta` 可由規則引擎根據 `action_type` + `outcome` 計算：

```python
AFFINITY_RULES = {
    ('attack',    'hit'):              -25,
    ('attack',    'miss'):             -10,
    ('social',    'critical_success'): +20,
    ('social',    'success'):          +10,
    ('social',    'failure'):          -5,
    ('social',    'critical_failure'): -15,
    ('magic',     'success'):          +5,
    ('explore',   'success'):          0,
    ('direct_action', 'NO_ROLL'):      0,
}

def calculate_affinity_delta(action_type, outcome):
    return AFFINITY_RULES.get((action_type, outcome), 0)
```

**LLM 保留**：`state`（情緒狀態分類）、`goal`（目標更新）、`emotion`、`action`（當前行為描述）仍適合 LLM 的語意 / 情緒判斷。

---

## 三、優先順序建議

| 優先級 | 項目 | 影響範圍 | 理由 |
|--------|------|----------|------|
| **P0** | `render_narrative()` 中的 `damage_taken` / `hp_healed` / `mp_used` | 遊戲平衡 | 違反設計原則，LLM 可任意修改玩家狀態 |
| **P1** | `parse_intent()` 混合模式 | 效能 / 延遲 | 每回合必呼叫，節省 60-70% LLM 調用 |
| **P2** | `generate_entity_stat_block()` 數值查表 | 數值平衡 | 確保難度曲線穩定，減少 LLM 呼叫 |
| **P3** | `evaluate_npc_reactions()` affinity 規則化 | NPC 行為 | 影響較小，但增加結果確定性 |

---

## 四、結論

目前 LLM 的職責劃分大致符合 neuro-symbolic 設計原則，但有兩個明顯缺口：

1. **`render_narrative()` 的機械數值輸出**：這讓 LLM 實際上在做規則引擎的工作，應優先修正。
2. **`parse_intent()` 缺乏規則層**：每次都呼叫 LLM 處理明確模式（如「攻擊」），浪費資源。

修正後，LLM 的角色將嚴格限制在：
- **文本生成**：敘事、選項、摘要、開場白
- **語意理解**：模糊意圖解析（fallback 層）
- **情緒 / 目標判斷**：NPC 反應中的質性描述

所有數值計算、狀態變更、分類決策均由 Python 規則引擎處理。

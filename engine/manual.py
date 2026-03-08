"""
engine/manual.py — World-setting-aware player handbook content builder.

Returns a list of chapter dicts:
    [{'icon': str, 'title': str, 'content': str, 'tags': [str]}, ...]

All content is plain Markdown.  Tags are lowercase keywords used by the
search function in the UI.  No Streamlit imports — pure data builder.
"""

# Skill → governing stat mapping (mirrors CharacterLogic._SKILL_STAT_MAP)
_SKILLS = [
    ('athletics',     'atk',      '力量 — 爬牆、抓握、撞擊等體力動作'),
    ('intimidation',  'atk',      '力量 — 威嚇、恐嚇敵人或施壓'),
    ('acrobatics',    'mov',      '敏捷 — 跳躍、翻滾、保持平衡'),
    ('stealth',       'mov',      '敏捷 — 潛行、隱蔽、偷偷接近'),
    ('perception',    'def_stat', '感知 — 察覺隱藏物件、陷阱或敵人'),
    ('persuasion',    'def_stat', '感知 — 說服、談判、外交手段'),
    ('medicine',      'def_stat', '感知 — 急救、辨識疾病、穩定傷者'),
    ('arcana',        'mp',       '智識 — 施法知識、識別咒語或魔法物品'),
]

# Stat display names (generic fallback before world-vocab substitution)
_STAT_LABELS = {
    'atk':      'ATK (攻擊)',
    'def_stat': 'DEF (防禦)',
    'mov':      'MOV (移動)',
    'mp':       'MP (魔力)',
}

# Class weapon damage dice
_CLASS_DICE = {
    'warrior': '1d8',
    'mage':    '1d4',
    'rogue':   '1d6',
    'cleric':  '1d6',
}

# Scene type descriptions (world-agnostic)
_SCENE_TYPES = [
    ('⚔️ combat',      '戰鬥',  '角色發起攻擊或被敵人攻擊。規則引擎計算命中、傷害與致命一擊。'),
    ('💬 social',      '社交',  '與 NPC 對話、談判或進行說服/威嚇。NPC 的情感狀態與目標在每回合更新。'),
    ('🗺️ exploration', '探索',  '角色移動、搜索環境或觸發技能檢定。地圖 Token 會隨地點變更移動。'),
    ('🧩 puzzle',      '謎題',  '解謎、機關或智識挑戰。通常需要 Arcana 或 Perception 技能檢定。'),
    ('🏕️ rest',        '休息',  '角色回復 HP/MP。無攻擊行動。有助於為下一次冒險做準備。'),
]


def _chapter_overview(ws):
    tm = ws.get('term_map', {})
    snpc = ws.get('starting_npc', {})
    lore = ws.get('world_lore', '')
    return f"""\
### 遊戲簡介

**{ws['name']}** 是一款 **回合制、文字驅動的 RPG**，由本地 AI 語言模型擔任{tm.get('dm_title', '遊戲主持人')}（GM），即時生成劇情、選擇與機制判定。

> {ws.get('tone', '')}

---

### 世界背景

{lore}

**起始地點:** {ws.get('starting_location', '？')}

**初始 NPC:** {snpc.get('name', '—')} *({snpc.get('state', 'Neutral')}, 目標: {snpc.get('goal', '—')})*

---

### 核心遊戲循環

每個回合分為 **10 個步驟** (由 EventManager 自動執行):

| 步驟 | 說明 |
|:---:|:---|
| 1 | **RAG 檢索** — 從向量資料庫取得相關世界記憶與規則 |
| 2 | **世界記憶植入** — 遊戲開始時將世界觀分段存入 RAG |
| 3 | **意圖解析** — LLM 將玩家輸入轉為結構化 JSON 意圖 |
| 4 | **動態實體生成** — 首次遭遇新 NPC/怪物時生成完整數值 |
| 5 | **戰鬥引擎** — 全確定性：骰子 + 規則 Python 計算 |
| 6 | **技能骰投** — DC 判定由 DiceRoller 執行，LLM 不插手 |
| 7 | **敘事生成** — LLM 接收機制結果，撰寫場景文字 |
| 8 | **套用機制** — HP/MP/物品/地點依 LLM 輸出更新 |
| 9 | **NPC 反應** — 社交場景後各 NPC 獨立更新情緒與目標 |
| 10 | **記憶更新** — 將本回合存入滑動窗口；舊回合壓縮為章節摘要 |

---

### 多人模式

- 支援 **1–6 位** 玩家，人類與 AI 可混搭
- 每位玩家擁有獨立旗幟 (🔴🔵🟢🟡🟣🟠) 顯示於地圖與側欄
- AI 玩家由 **決策樹 + LLM 混合** 控制，可選性格與難度
- 回合以 **輪替制** 進行，陣亡角色自動跳過
"""


def _chapter_combat(ws):
    tm = ws.get('term_map', {})
    hp   = tm.get('hp_name', 'HP')
    wc   = tm.get('warrior_class', 'Warrior')
    mc   = tm.get('mage_class', 'Mage')
    rc   = tm.get('rogue_class', 'Rogue')
    cc   = tm.get('cleric_class', 'Cleric')
    sc   = tm.get('skill_check', 'skill check')
    return f"""\
### ⚔️ 戰鬥規則 ({ws['name']})

> 所有戰鬥由 **確定性 Python 規則引擎** 計算，LLM 只負責敘述結果，絕不自行裁決勝負。

---

#### 攻擊判定

```
攻擊骰:   1d20 + ATK修正   ≥   目標 DEF  →  命中
ATK修正 = (ATK − 10) ÷ 2  (向下取整)
致命一擊: 自然骰面 = 20   (無視 DEF，自動命中)
```

#### 傷害計算

```
傷害    = 傷害骰 + ATK修正
淨傷害  = max(0, 傷害 − 目標DEF ÷ 2)
致命一擊 → 骰子數值部分 × 2，ATK修正不翻倍
```

#### 職業武器傷害骰

| 職業 ({ws['name']}) | 骰型 | 說明 |
|:---|:---:|:---|
| {wc} (Warrior) | **1d8** | 重型武器，穩定傷害 |
| {mc} (Mage)    | **1d4** | 近戰弱，魔法技能強 |
| {rc} (Rogue)   | **1d6** | 靈巧武器，高爆發潛力 |
| {cc} (Cleric)  | **1d6** | 戰錘/法杖，兼顧輔助 |

#### NPC/敵人狀態追蹤

- 敵人 {hp} 儲存於 `known_entities[名稱][hp]`
- 擊敗 → `alive = False`，LLM 照此敘述死亡場景
- 首次遭遇新敵人時，引擎自動生成完整數值 (Infinite Monster Engine)

#### 範例回合

```
玩家輸入:  "我揮劍攻擊哥布林"
→ 攻擊骰:  1d20(結果14) + ATK修正(+3) = 17  vs  哥布林DEF=12  → 命中
→ 傷害:    1d8(結果5) + 3 = 8；淨傷 = 8 − (12÷2=6) = 2
→ 哥布林 HP: 30 → 28
→ LLM 接收: "命中，2點淨傷" → 生成戰鬥描述
```

> 提示: 在「🗺️ 遊戲板」頁籤可手動擲骰 (d4–d20) 作為參考，但正式戰鬥骰由引擎處理。
"""


def _chapter_dice(ws):
    tm = ws.get('term_map', {})
    sc = tm.get('skill_check', 'skill check')
    return f"""\
### 🎲 骰子系統與技能檢定

> 骰子引擎 (`engine/dice.py`) 是遊戲唯一的隨機來源。LLM **絕不** 自行模擬骰子結果。

---

#### 難度等級 (DC) 對照表

| DC | 難度描述 | 情境範例 |
|:---:|:---:|:---|
| 5  | 極易   | 開鎖容易的門、爬矮牆 |
| 10 | 容易   | 說服友善的 NPC |
| 12 | 普通   | 跳越小溝渠 |
| 15 | 中等   | 識別魔法符文 |
| 18 | 困難   | 潛行繞過警衛巡邏線 |
| 20 | 極難   | 徒手攀登光滑牆壁 |
| 25 | 英雄壯舉 | 在黑暗中識破幻術 |
| 30 | 近乎不可能 | 挑戰命運本身 |

---

#### 結果判定

| 骰面 | 結果類型 | 顯示 | 說明 |
|:---:|:---:|:---:|:---|
| 自然 20 | 💛 致命成功 | 金色橫幅 | 超出預期的完美結果 |
| ≥ DC   | 💚 成功     | 綠色橫幅 | 達標，獲得期望效果 |
| < DC   | 🔴 失敗     | 紅色橫幅 | 未達標，觸發負面後果 |
| 自然 1  | 💀 致命失敗 | 黑色橫幅 | 嚴重失敗，可能有額外懲罰 |

---

#### 修正值公式 ({sc})

```
修正值 = (對應能力值 − 10) ÷ 2  (Python 整數除法，向下取整)

範例:
  ATK = 16  →  修正 = (16−10)÷2 = +3
  MOV = 8   →  修正 = (8−10)÷2  = −1
  DEF = 10  →  修正 = 0
```

---

#### 可用骰型

| 骰型 | 範圍 | 常用情境 |
|:---:|:---:|:---|
| d4  | 1–4  | 法師近戰傷害 |
| d6  | 1–6  | 盜賊/牧師傷害、隨機事件 |
| d8  | 1–8  | 戰士武器傷害 |
| d10 | 1–10 | 特殊武器或強力技能 |
| d12 | 1–12 | 重武器或特殊職業技能 |
| d20 | 1–20 | 所有攻擊骰與技能檢定 |

可在「🗺️ 遊戲板」→ **手動骰子** 欄位中試擲各型骰子。
"""


def _chapter_classes(ws):
    from engine.config import GameConfig
    tm = ws.get('term_map', {})
    classes_tm = {
        'warrior': tm.get('warrior_class', 'Warrior'),
        'mage':    tm.get('mage_class',    'Mage'),
        'rogue':   tm.get('rogue_class',   'Rogue'),
        'cleric':  tm.get('cleric_class',  'Cleric'),
    }
    hp_n = tm.get('hp_name', 'HP')
    mp_n = tm.get('mp_name', 'MP')

    rows = []
    for cls, base in GameConfig.CLASS_BASE_STATS.items():
        display = classes_tm.get(cls, cls.title())
        dice    = _CLASS_DICE.get(cls, '1d6')
        rows.append(
            f"| {display} | {base['hp']} | {base['mp']} | "
            f"{base['atk']} | {base['def_stat']} | {base['mov']} | "
            f"{dice} | {base['role']} |"
        )
    table = '\n'.join(rows)

    detail_blocks = []
    for cls, base in GameConfig.CLASS_BASE_STATS.items():
        display = classes_tm.get(cls, cls.title())
        dice    = _CLASS_DICE.get(cls, '1d6')
        skills  = [s for s, stat, _ in _SKILLS if stat == 'atk' and cls in ('warrior',)] \
                  + ([s for s, stat, _ in _SKILLS if stat == 'mov'] if cls == 'rogue' else []) \
                  + ([s for s, stat, _ in _SKILLS if stat == 'mp']  if cls == 'mage'   else []) \
                  + ([s for s, stat, _ in _SKILLS if stat == 'def_stat'] if cls == 'cleric' else [])
        if not skills:
            skills = [s for s, stat, _ in _SKILLS if stat in ('def_stat', 'atk')][:2]
        detail_blocks.append(f"""\
**{display}** `{cls}`
- {hp_n}: **{base['hp']}**  ·  {mp_n}: **{base['mp']}**  ·  ATK: {base['atk']}  DEF: {base['def_stat']}  MOV: {base['mov']}
- 武器: {dice} + ATK修正  ·  終局獎勵權重: ×{base['reward_weight']:.2f}
- 定位: {base['role']}
- 強勢技能: {', '.join(skills[:3])}
""")

    details = '\n'.join(detail_blocks)

    return f"""\
### 🧙 職業說明 ({ws['name']})

> 四種職業設計上擁有相同的**戰鬥力預算**，但分佈方式不同，終局獎勵權重補償非戰鬥貢獻。

---

#### 數值總覽

| 職業 | {hp_n} | {mp_n} | ATK | DEF | MOV | 傷害骰 | 定位 |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|
{table}

---

#### 詳細說明

{details}

---

#### 終局金幣分配公式

```
個人分數 = (傷害輸出 × 1.0
           + 治療量   × 1.5
           + 技能成功 × 20) × 獎勵權重

個人金幣 = 隊伍總金幣 × (個人分數 ÷ 全員分數總和)
```

> 治療和技能貢獻有額外加權，讓輔助職業 ({classes_tm.get('cleric', 'Cleric')}) 的終局收益不低於純輸出職業。
"""


def _chapter_skills(ws):
    tm = ws.get('term_map', {})
    sc = tm.get('skill_check', 'skill check')
    skill_rows = []
    for skill, stat, desc in _SKILLS:
        stat_label = _STAT_LABELS.get(stat, stat)
        skill_rows.append(f"| **{skill}** | {stat_label} | {desc} |")
    skill_table = '\n'.join(skill_rows)

    return f"""\
### 🛡️ 技能系統 ({sc})

> 技能由對應的**能力值**決定修正值。公式: `(能力值 − 10) ÷ 2`

---

#### 技能一覽

| 技能名稱 | 對應能力值 | 說明 |
|:---|:---|:---|
{skill_table}

---

#### 修正值快速對照

| 能力值 | 修正值 | 強度描述 |
|:---:|:---:|:---|
| 8  | −1 | 弱  |
| 10 | 0  | 平均 |
| 12 | +1 | 略優 |
| 14 | +2 | 熟練 |
| 16 | +3 | 精英 |
| 18 | +4 | 英雄 |
| 20 | +5 | 傳說 |

---

#### 技能優勢分析 (按職業)

| 技能 | 最強職業 | 原因 |
|:---|:---|:---|
| athletics / intimidation | {tm.get('warrior_class','Warrior')} | ATK 最高 |
| acrobatics / stealth | {tm.get('rogue_class','Rogue')} | MOV 8，修正+最高 |
| perception / persuasion / medicine | {tm.get('cleric_class','Cleric')} | DEF_stat 13 |
| arcana | {tm.get('mage_class','Mage')} | MP 100，魔法知識最強 |

---

#### 如何觸發技能檢定

1. 玩家輸入行動 (如「我試著偷偷潛入」)
2. LLM 解析意圖 → `requires_roll: true, skill: stealth, dc: 15`
3. Python 計算: `DiceRoller.roll_skill_check(dc=15, modifier=char_logic.get_skill_modifier('stealth'))`
4. 結果顯示為彩色橫幅，LLM 以此撰寫敘事

LLM **永遠不得自行捏造**骰子結果。
"""


def _chapter_exploration(ws):
    tm = ws.get('term_map', {})
    sa = tm.get('starting_area', '城鎮')
    return f"""\
### 🗺️ 場景探索與地圖系統

---

#### 世界地圖格線

地圖為 **8 欄 × 5 列** 的網格，從上到下分為五個地形帶:

| 列 | 地形類型 | 代表場所 |
|:---:|:---:|:---|
| 0 | ✨ 異界/星界 | 異次元、星界平面、虛空 |
| 1 | ⛰️ 荒野/山地 | 山脈、廢墟、荒漠、凍原 |
| 2 | 🌲 自然區域 | 森林、沼澤、河流、平原 |
| 3 | 🏘️ 城鎮/聚落 | {sa}、城堡、神廟、港口 ← 起始區域 |
| 4 | 💀 地下城/洞穴 | 地牢、洞窟、礦坑、下水道 |

---

#### Token 移動規則

- 每位玩家在地圖上有對應旗幟 (🔴🔵🟢🟡🟣🟠)
- 當 LLM 輸出 `location_change` 欄位時，玩家 Token 自動移動
- 新地點由**地名關鍵字**判斷對應地形帶 (如「dungeon」→ 第 4 列)
- 未探索地點顯示為 **❓ 迷霧格**

---

#### 場景類型

| 場景 | 圖示 | 說明 |
|:---|:---:|:---|
{"".join(f'| {n} | {i.split()[0]} | {desc} |' + chr(10) for i, n, desc in _SCENE_TYPES)}\

---

#### 記憶系統 (滑動窗口)

- 每回合記錄儲存於 `session_memory` (SQLite JSON 欄位)
- 保留最新 **15 回合**; 舊回合由 LLM 壓縮為「章節摘要」存入 RAG
- LLM 每次生成前會注入: 世界背景 + 近期記憶 + RAG 檢索結果

---

#### 分叉點敘事 (Branching Choices)

每個回合結束後，LLM 提供 2–4 個**分叉選項**。玩家可:
- 點選選項按鈕 (A/B/C/D) — 直接送出該行動
- 或在文字框輸入自訂行動

點選後進入下一回合，LLM 生成對應劇情。
"""


def _chapter_vocabulary(ws):
    tm = ws.get('term_map', {})
    rows = [
        ('HP (生命值)',       tm.get('hp_name',       'HP')),
        ('MP (魔力/資源)',    tm.get('mp_name',        'MP')),
        ('金幣 (貨幣)',       tm.get('gold_name',      'gold')),
        ('戰士 職業',        tm.get('warrior_class',  'Warrior')),
        ('法師 職業',        tm.get('mage_class',     'Mage')),
        ('盜賊 職業',        tm.get('rogue_class',    'Rogue')),
        ('牧師 職業',        tm.get('cleric_class',   'Cleric')),
        ('遊戲主持人',       tm.get('dm_title',       'Game Master')),
        ('技能檢定',         tm.get('skill_check',    'skill check')),
        ('起始地區類型',     tm.get('starting_area',  '城鎮')),
    ]
    table_rows = '\n'.join(f'| {g} | **{s}** |' for g, s in rows)

    return f"""\
### 📚 術語對照表 — {ws['name']}

> 以下為本世界設定的專屬詞彙。LLM 在生成敘事時會以右欄用語取代通用 DnD 術語，
> 維持世界觀一致性。

---

#### 詞彙對照

| 通用術語 (DnD 基礎) | **{ws['name']} 用語** |
|:---|:---|
{table_rows}

---

#### 世界背景摘要

**類別:** {ws.get('category', '—')}

**描述:** {ws.get('description', '—')}

**敘事基調:**
> {ws.get('tone', '—')}

---

#### 起始條件

| 項目 | 值 |
|:---|:---|
| 起始地點 | {ws.get('starting_location', '—')} |
| 初始 NPC | {ws.get('starting_npc', {}).get('name', '—')} |
| NPC 初始情緒 | {ws.get('starting_npc', {}).get('state', '—')} |
| NPC 目標 | {ws.get('starting_npc', {}).get('goal', '—')} |
"""


def _chapter_ai(ws):
    from engine.config import GameConfig
    tm = ws.get('term_map', {})
    p_rows = '\n'.join(
        f"| **{p['name']}** | {p['description']} | {p['action_bias']} | {p['heal_threshold']} |"
        for p in GameConfig.AI_PERSONALITIES.values()
    )
    d_rows = '\n'.join(
        f"| **{d['name']}** | {d['description']} | {d['use_decision_tree']} | {d['use_llm']} |"
        for d in GameConfig.AI_DIFFICULTIES.values()
    )
    return f"""\
### 🤖 AI 同伴系統 ({ws['name']})

> AI 玩家由「決策樹狀態機 + LLM 混合」控制，性格與難度在建立角色時設定。

---

#### 性格特質 (Personality)

| 性格 | 說明 | 行動偏向 | 治療閾值 |
|:---|:---|:---:|:---:|
{p_rows}

---

#### 難度等級 (Difficulty)

| 難度 | 說明 | 決策樹 | 使用 LLM |
|:---|:---|:---:|:---:|
{d_rows}

---

#### 決策流程 (優先順序)

```
1. Aggressive 且有存活敵人         → 攻擊最強敵人
2. HP 極低 (< threshold×50%) + MP 足夠   → 自我治療
3. Support/Cleric + 隊友 HP 低     → 治療最傷最重的隊友
4. HP 低 (< threshold) + 非 Aggressive   → 自我治療/防禦
5. 有存活敵人                      → 攻擊 (Tactical=最弱; Aggressive=最強)
6. Chaotic                         → 隨機行動池中選取
7. 預設                            → 探索/偵察場景
```

---

#### 回合整合

- AI 行動在 **UI 渲染 tabs 之前**自動執行，不需玩家手動操作
- 每次 AI 行動後呼叫 `st.rerun()` 無縫切換到下一位玩家
- AI 行動的地點變更也會同步更新地圖 Token

---

#### 設定方式

建立新遊戲時，未填滿的玩家槽可設為 AI:
1. 勾選「AI 玩家」
2. 選擇性格 ({'/'.join(p['name'] for p in list(GameConfig.AI_PERSONALITIES.values())[:3])}…)
3. 選擇難度 (Easy / Normal / Hard / Deadly)
"""


def build_manual_chapters(ws):
    """Return list of chapter dicts for the given world setting dict."""
    return [
        {
            'icon':    '📖',
            'title':   '遊戲大綱',
            'tags':    ['overview', '大綱', '簡介', '世界', 'lore', '回合', '流程',
                        ws.get('id', ''), ws.get('name', '').lower()],
            'content': _chapter_overview(ws),
        },
        {
            'icon':    '⚔️',
            'title':   '戰鬥規則',
            'tags':    ['combat', '戰鬥', 'attack', '攻擊', 'damage', '傷害',
                        'critical', '致命', 'hp', ws.get('term_map', {}).get('hp_name', '').lower()],
            'content': _chapter_combat(ws),
        },
        {
            'icon':    '🎲',
            'title':   '骰子系統',
            'tags':    ['dice', '骰子', 'dc', 'difficulty', '難度', 'skill check',
                        '技能', 'roll', '骰', 'modifier', '修正', 'd20'],
            'content': _chapter_dice(ws),
        },
        {
            'icon':    '🧙',
            'title':   '職業說明',
            'tags':    ['class', '職業', 'warrior', 'mage', 'rogue', 'cleric',
                        ws.get('term_map', {}).get('warrior_class', '').lower(),
                        ws.get('term_map', {}).get('mage_class', '').lower(),
                        'hp', 'stats', '數值', 'reward', '獎勵'],
            'content': _chapter_classes(ws),
        },
        {
            'icon':    '🛡️',
            'title':   '技能系統',
            'tags':    ['skill', '技能', 'athletics', 'acrobatics', 'stealth',
                        'perception', 'arcana', 'persuasion', 'medicine',
                        'intimidation', 'modifier', '修正', 'ability'],
            'content': _chapter_skills(ws),
        },
        {
            'icon':    '🗺️',
            'title':   '場景探索',
            'tags':    ['exploration', '探索', 'map', '地圖', 'location', '地點',
                        'scene', '場景', 'memory', '記憶', 'choice', '選擇', 'fog'],
            'content': _chapter_exploration(ws),
        },
        {
            'icon':    '📚',
            'title':   '術語對照',
            'tags':    ['vocabulary', '術語', 'glossary', '對照', 'term',
                        ws.get('id', ''), ws.get('name', '').lower(),
                        ws.get('term_map', {}).get('dm_title', '').lower()],
            'content': _chapter_vocabulary(ws),
        },
        {
            'icon':    '🤖',
            'title':   'AI 同伴系統',
            'tags':    ['ai', '人工智慧', 'companion', '同伴', 'personality', '性格',
                        'aggressive', 'cautious', 'support', 'tactical', 'chaotic',
                        'difficulty', '難度', 'decision', '決策', 'easy', 'hard', 'deadly'],
            'content': _chapter_ai(ws),
        },
    ]

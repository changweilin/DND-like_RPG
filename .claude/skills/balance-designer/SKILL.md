# Balance Designer Skill

## Description
Trigger this skill when the user asks about game balance, class restrictions on items/equipment, whether each class has fair access to useful gear, or whether skill checks (stealth, perception, intimidation, athletics) produce real mechanical effects during combat.

Trigger on: "職業限制", "裝備平衡", "技能點數有效嗎", "潛行有偷襲嗎", "感知有搶先嗎", "class restriction", "item balance", "skill combat effect", "balance audit", "class balance".

## Category
**Code Quality & Review** — audit game mechanics, identify imbalances, and implement deterministic fixes.

## Workflow

When invoked, run this three-phase process:

### Phase 1 — Audit (read-only)
Investigate the three balance pillars in parallel:

**A. Class Equipment Restrictions**
- Read `data/shop.py` → check if any items have `restricted_to` field
- Read `engine/character.py` → look for `_CLASS_EQUIP_RULES` in `equip()`
- Verdict: are class restrictions enforced at equip time?

**B. Per-Class Item Balance**
- Count items in `SHOP_CATALOGUE` that are meaningfully useful per class
- Check weapon/armor coverage: warrior, mage, rogue, cleric
- Flag any class with fewer than 3 dedicated items
- Check that ATK/DEF/MP/HP bonuses are approximately equal across classes

**C. Skill → Combat Integration**
- Check `logic/events.py` for `_sneak_ready`, `_perception_bonus`, `_intimidated_*`
- Check if successful stealth roll before attack grants sneak attack bonus
- Check if perception roll before combat grants initiative (player attacks first)
- Check if intimidation during combat suppresses enemy counter-attack

### Phase 2 — Design (plan changes)
After audit, produce a concrete change plan:
1. `_CLASS_EQUIP_RULES` table entries to add/modify in `engine/character.py`
2. New items to add to `data/shop.py` for underpopulated classes
3. Combat modifier logic to add to `logic/events.py`

### Phase 3 — Implement (write code)
Make all changes following the codebase conventions:
- No type annotations, no docstrings
- All numeric outcomes in Python, never in LLM
- Use `flag_modified(current_state, 'known_entities')` after mutating known_entities
- Syntax-check with `python -m py_compile` before committing

## Key Reference: Class Equipment Rules

```python
# In engine/character.py, CharacterLogic:
_CLASS_EQUIP_RULES = {
    # item_type → set of allowed class names (lowercase)
    'weapon': {
        'iron sword':    {'warrior', 'rogue'},
        'steel sword':   {'warrior'},
        'mage staff':    {'mage'},
        'longbow':       {'rogue', 'warrior'},
        'holy mace':     {'cleric'},
        # ... default: all classes
    },
    'armor': {
        'leather armor': {'warrior', 'rogue', 'cleric'},
        'chainmail':     {'warrior', 'cleric'},
        'steel shield':  {'warrior', 'cleric'},
        'plate armor':   {'warrior'},
        # ... mage cannot equip any armor (except accessories)
    },
    'tool': {
        'thieves tools': {'rogue'},
        'lockpicks':     {'rogue'},
        'disguise kit':  {'rogue', 'mage'},
        # ... default: all classes
    },
}
```

## Key Reference: Skill → Combat Flags

```python
# In logic/events.py, after a successful skill check:

# Stealth → sneak attack ready
if skill == 'stealth' and outcome in ('success', 'critical_success'):
    known_mut = dict(current_state.known_entities or {})
    known_mut['_sneak_ready'] = True
    current_state.known_entities = known_mut
    flag_modified(current_state, 'known_entities')

# Perception → initiative bonus (player attacks before counter-attack this turn)
if skill == 'perception' and outcome in ('success', 'critical_success'):
    known_mut = dict(current_state.known_entities or {})
    known_mut['_perception_bonus'] = True
    current_state.known_entities = known_mut
    flag_modified(current_state, 'known_entities')

# Intimidation → suppress next enemy counter-attack
if skill == 'intimidation' and outcome in ('success', 'critical_success') and target:
    key = f'_intimidated_{target.lower().replace(" ", "_")}'
    known_mut = dict(current_state.known_entities or {})
    known_mut[key] = True
    current_state.known_entities = known_mut
    flag_modified(current_state, 'known_entities')
```

```python
# In logic/events.py, resolve_attack() call site:
# Check sneak attack
sneak_ready = (current_state.known_entities or {}).get('_sneak_ready', False)

# Pass to combat engine, then clear flag:
if sneak_ready:
    known_clear = dict(current_state.known_entities)
    known_clear.pop('_sneak_ready', None)
    current_state.known_entities = known_clear
    flag_modified(current_state, 'known_entities')
```

## Gotchas

- **Scroll bypass**: Scrolls bypass class restrictions by design. Never add `restricted_to` to scroll items.
- **Upgrade kits**: Available to all classes — they raise character stats, not item slots.
- **Rogue sneak attack**: Should only trigger once per combat initiation (when entering combat with stealth), not every turn.
- **Perception bonus**: Should be consumed on the first attack of combat, then cleared.
- **Equip restriction UX**: When `equip()` rejects an item due to class restriction, return a clear error like `{'equipped': False, 'reason': 'class_restriction', 'allowed_classes': [...]}`.
- **Don't break existing tests**: There are no tests in this codebase; use `python -m py_compile` to catch syntax errors.
- **Balance target**: Each class should have 3–6 items where they get the most value (bonus or exclusive access). Count this after changes.

import re
import time
import random
from sqlalchemy.orm.attributes import flag_modified
from engine.dice import DiceRoller
from engine.character import CharacterLogic
from engine.world import WorldManager
from engine.config import config
from engine.intent_parser import (
    try_parse as _rule_parse_intent,
    detect_entity_type,
    get_entity_base_stats,
    calculate_affinity_delta,
)
from engine.combat import (CombatEngine, STATUS_EFFECTS, compute_level, xp_for_level,
                           roll_loot, roll_combat_gold,
                           DIFFICULTY_REWARD, DIFFICULTY_DEATH_PENALTY)
from data.monsters import get_monster_by_name, get_special_ability

# Rule engine constants for _calculate_mechanics()
_MP_COST_TABLE = {
    'arcana':    4,
    'medicine':  2,
}
_MP_COST_DEFAULT = 3  # fallback for unrecognised magic skills

# Physical skills where a critical failure deals minor fall/trap damage
_PHYSICAL_SKILLS = {'athletics', 'acrobatics'}

# Regex for healing intent in raw player text
_HEAL_RE = re.compile(
    r'(heal|cure|mend|restore|potion|治療|恢復|回復|藥水|復原)',
    re.I,
)

# Regex for flee/escape intent
_FLEE_RE = re.compile(
    r'(flee|run away|escape|retreat|逃跑|逃走|撤退|逃離|落荒而逃|逃之夭夭)',
    re.I,
)

def _char_similarity(a, b):
    """Character-set overlap: |chars(a) ∩ chars(b)| / min(len(a), len(b))"""
    if not a or not b:
        return 0.0
    return len(set(a) & set(b)) / min(len(a), len(b))

# Human-readable labels for the rule engine's outcome codes
_OUTCOME_LABELS = {
    'critical_success': 'CRITICAL SUCCESS',
    'success':          'SUCCESS',
    'failure':          'FAILURE',
    'critical_failure': 'CRITICAL FAILURE',
}

class AIPlayerController:
    """
    Hybrid decision-tree + template AI player controller.

    Difficulty levels:
      Easy   — random safe action from a predefined pool (no strategy)
      Normal — basic decision tree: HP%, enemy presence, class role
      Hard   — extended decision tree with multi-factor evaluation
      Deadly — same tree + LLM contextual refinement (if llm available)

    Personality archetypes affect decision thresholds and target selection.
    Action output is a plain string (e.g. "I attack the goblin") that is
    passed directly to EventManager.process_turn() exactly like a human input.
    """

    _COMBAT_TEMPLATES = [
        "I attack {target} with my weapon",
        "I strike {target} with a powerful blow",
        "I charge at {target}",
    ]
    _HEAL_SELF_TEMPLATES = [
        "I use my healing ability to restore my own wounds",
        "I drink a healing potion to recover health",
        "I cast a self-healing spell",
    ]
    _HEAL_ALLY_TEMPLATES = [
        "I cast a healing spell on {target}",
        "I rush to {target} and use my healing ability",
        "I tend to {target}'s wounds with my healing arts",
    ]
    _EXPLORE_TEMPLATES = [
        "I search the area for clues and useful items",
        "I carefully examine my surroundings",
        "I investigate the room for anything of interest",
        "I check for traps and hidden passages",
        "I scout the area ahead",
    ]
    _SUPPORT_TEMPLATES = [
        "I provide tactical support to my allies",
        "I take up a flanking position to assist the party",
        "I help my ally and look for an opening",
    ]
    _RETREAT_TEMPLATES = [
        "I take a defensive stance and catch my breath",
        "I fall back to a safer position",
        "I find cover and assess the situation",
    ]

    def decide_action(self, ai_char, state, party, ai_config):
        """
        Decide the AI player's action for this turn.

        Returns an action string ready for EventManager.process_turn().
        """
        personality = ai_config.get('personality', 'tactical')
        difficulty  = ai_config.get('difficulty', 'normal')

        p_cfg = config.AI_PERSONALITIES.get(personality,
                    config.AI_PERSONALITIES['tactical'])
        d_cfg = config.AI_DIFFICULTIES.get(difficulty,
                    config.AI_DIFFICULTIES['normal'])

        if not d_cfg.get('use_decision_tree', True) or personality == 'chaotic':
            return self._random_action(ai_char, state)

        return self._decision_tree_action(ai_char, state, party, p_cfg)

    def _random_action(self, ai_char, state):
        """Easy / Chaotic: pick randomly from a safe, varied action pool."""
        hp_pct  = ai_char.hp / max(ai_char.max_hp, 1)
        enemies = self._get_living_enemies(state)
        pool    = list(self._EXPLORE_TEMPLATES)

        if hp_pct < 0.4:
            pool.extend(self._RETREAT_TEMPLATES)

        if enemies:
            target = random.choice(enemies)
            for tmpl in self._COMBAT_TEMPLATES:
                pool.append(tmpl.format(target=target))

        return random.choice(pool)

    def _decision_tree_action(self, ai_char, state, party, p_cfg):
        """Normal / Hard / Deadly: structured rule-based decision tree."""
        hp_pct          = ai_char.hp / max(ai_char.max_hp, 1)
        mp_pct          = ai_char.mp / max(ai_char.max_mp, 1)
        heal_threshold  = p_cfg.get('heal_threshold', 0.35)
        role            = ai_char.char_class.lower()
        enemies         = self._get_living_enemies(state)
        action_bias     = p_cfg.get('action_bias', 'optimal')

        # Priority 1: Aggressive — attack immediately if enemies present
        if action_bias == 'combat' and enemies:
            target = self._pick_target(enemies, state, action_bias)
            return random.choice(self._COMBAT_TEMPLATES).format(target=target)

        # Priority 2: Critical self-heal (all roles when near death)
        if hp_pct < heal_threshold * 0.5 and mp_pct > 0.1:
            return random.choice(self._HEAL_SELF_TEMPLATES)

        # Priority 3: Support / Cleric — heal most-wounded ally
        if (action_bias == 'healing' or role == 'cleric') and mp_pct > 0.1:
            wounded = self._get_most_wounded_ally(party, ai_char)
            if wounded and wounded.hp / max(wounded.max_hp, 1) < heal_threshold:
                return random.choice(self._HEAL_ALLY_TEMPLATES).format(target=wounded.name)

        # Priority 4: Self-heal when HP below threshold (non-aggressive builds)
        if hp_pct < heal_threshold and mp_pct > 0.1 and action_bias != 'combat':
            return random.choice(self._HEAL_SELF_TEMPLATES)

        # Priority 5: Attack if enemies present
        if enemies:
            target = self._pick_target(enemies, state, action_bias)
            return random.choice(self._COMBAT_TEMPLATES).format(target=target)

        # Priority 6: Defensive retreat if low HP and no enemies
        if hp_pct < 0.25:
            return random.choice(self._RETREAT_TEMPLATES)

        # Default: explore
        return random.choice(self._EXPLORE_TEMPLATES)

    def _get_living_enemies(self, state):
        """Return names of alive hostile entities from known_entities."""
        known = getattr(state, 'known_entities', None) or {}
        return [
            name for name, data in known.items()
            if isinstance(data, dict)
            and data.get('alive', True)
            and data.get('type', 'monster') not in ('npc', 'ally')
        ]

    def _get_most_wounded_ally(self, party, self_char):
        """Return the living ally with the lowest HP% (excluding self)."""
        others = [c for c in party if c.id != self_char.id and c.hp > 0]
        if not others:
            return None
        return min(others, key=lambda c: c.hp / max(c.max_hp, 1))

    def _pick_target(self, enemies, state, action_bias):
        """Select a target based on personality bias."""
        known = getattr(state, 'known_entities', None) or {}
        if action_bias == 'combat':
            # Aggressive: prefer the strongest remaining enemy
            return max(enemies, key=lambda e: known.get(e, {}).get('hp', 0))
        elif action_bias == 'optimal':
            # Tactical: finish off the weakest enemy first
            return min(enemies, key=lambda e: known.get(e, {}).get('hp', 99999))
        return random.choice(enemies)


class EventManager:
    """
    Orchestrates one full game turn using a neuro-symbolic approach.

    Design principles from the research guide (Chapters 2 & 3):

      Waidrin / IBM Rule Agent:
        — LLM generates structured Narrative Events, not free chat messages.
        — scene_type tags each turn so UI can apply appropriate styling.

      One Trillion and One Nights (Guided Thinking):
        — parse_intent() includes a thought_process field filled FIRST,
          forcing chain-of-thought reasoning before classification.

      Infinite Monster Engine:
        — New entities encountered mid-game get a stat block generated
          on first encounter and cached in the game_rules RAG collection
          and the known_entities DB column.

      TaskingAI D&D Game Master:
        — World context and basic rules are pre-seeded into RAG so the
          LLM can retrieve exact facts instead of hallucinating them.

      Generative Agents (Park et al. 2023) — Section 3.5:
        — After social/NPC turns, evaluate_npc_reactions() is called so
          each NPC updates its own goal and emotional state independently.

      Memory Summarization — Section 3.1:
        — When the session_memory window overflows, the discarded turns
          are summarized via LLM and stored as a chapter summary in the
          world_lore RAG collection for long-term continuity.

    Turn flow — 10 steps:
      1. RAG retrieval (long-term semantic context)
      2. World lore seeding (first turn only)
      3. parse_intent — Guided Thinking → structured intent
      4. Dynamic entity stat block generation (new targets only)
      5. Combat rule engine (attack + damage rolls, deterministic)
      6. Dice roll + rule engine for skill checks (deterministic)
      7. render_narrative — receives hard mechanical facts
      8. Apply mechanics (HP/MP/items/location/relationships)
      9. NPC generative agent reactions (social/NPC turns)
     10. Update session memory + persist to RAG
    """

    def __init__(self, llm_client, rag_system, db_session):
        self.llm     = llm_client
        self.rag     = rag_system
        self.session = db_session
        self.dice    = DiceRoller()
        self.combat  = CombatEngine(self.dice)
        # Track once-per-combat ability usage per game-session (cleared on new combat)
        self._used_abilities = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_turn(self, player_action, current_state, character, party=None):
        """
        Run one full turn and return:
            narrative   (str)        — story text to display
            choices     (list[str])  — suggested next actions
            turn_data   (dict)       — full Narrative Event dict (scene_type, mechanics …)
            dice_result (dict|None)  — dice roll details, or None if no roll occurred

        party (list[Character] | None) — full party roster for multi-player prompt context.
        If None, falls back to single-player mode (party = [character]).
        """
        char_logic = CharacterLogic(self.session, character)
        world      = WorldManager(self.session, current_state)
        all_chars  = party if party else [character]

        # --- Step 1: Retrieve long-term context from RAG ---
        rag_context = self.rag.retrieve_context(player_action)

        # --- Step 2: Seed world lore on the very first turn (TaskingAI style) ---
        if (current_state.turn_count or 0) == 0 and not self.rag.world_lore_seeded():
            self._seed_world_lore(current_state)

        # --- Step 2.5: Tick player status effects at turn start ---
        player_status_tick = self.combat.tick_player_status_effects(current_state)
        if player_status_tick.get('damage', 0) > 0:
            char_logic.take_damage(player_status_tick['damage'])
        flag_modified(current_state, 'known_entities')
        self.session.commit()

        # --- Step 3: Parse intent — rule engine first, LLM fallback ---
        # Rule engine handles clear keyword patterns (attack, magic, stealth, social …)
        # without an LLM call.  Ambiguous or complex inputs fall back to the LLM's
        # Guided Thinking parser (One Trillion and One Nights approach).
        intent = _rule_parse_intent(
            player_action,
            known_entities=current_state.known_entities or {},
            difficulty=current_state.difficulty or 'Normal',
            char_class=character.char_class,
        )
        if intent is None:
            game_context_summary = (
                f"Character: {character.name}, {character.race} {character.char_class}. "
                f"HP: {character.hp}/{character.max_hp}, MP: {character.mp}/{character.max_mp}. "
                f"Location: {current_state.current_location}. "
                f"Difficulty: {current_state.difficulty}."
            )
            intent = self.llm.parse_intent(player_action, game_context_summary)

        # Ensure class_ability key is always present (LLM path may omit it)
        if 'class_ability' not in intent:
            from engine.combat import detect_class_ability
            intent['class_ability'] = detect_class_ability(player_action, character.char_class)

        # --- Step 3.5: Flee mechanics (deterministic MOV check) ---
        # Detected before Step 4/5 so we can short-circuit combat processing.
        flee_result = None
        if _FLEE_RE.search(player_action) and current_state.in_combat:
            flee_result = self.combat.resolve_flee(character, current_state)
            if flee_result['fled']:
                current_state.in_combat = 0
                self._used_abilities.clear()
            else:
                # Failed flee: apply counter-attack damage
                if flee_result['damage_taken'] > 0:
                    char_logic.take_damage(flee_result['damage_taken'])
            self.session.commit()

        # --- Step 3.6: Random encounter on travel actions ---
        random_encounter_entry = None
        if intent.get('action_type') == 'travel' and not current_state.in_combat:
            encounter_chance = config.RANDOM_ENCOUNTER_CHANCE
            roll_val, _, _ = self.dice.roll('1d20')
            if roll_val <= int(encounter_chance * 20):
                # Pick a tier-appropriate random monster
                from data.monsters import MONSTER_ROSTER
                player_lvl = character.level or 1
                if player_lvl <= 2:
                    tiers = [1, 2]
                elif player_lvl <= 5:
                    tiers = [2, 3]
                else:
                    tiers = [3, 4]
                candidates = [m for m in MONSTER_ROSTER if m.get('tier') in tiers]
                if candidates:
                    monster = random.choice(candidates)
                    enc_name = monster['name'].lower()
                    if enc_name not in (current_state.known_entities or {}):
                        self._generate_and_store_stat_block(
                            monster['name'], {'action_type': 'attack'}, current_state
                        )
                    random_encounter_entry = monster
                    # Inject as attack intent so combat begins
                    intent = dict(intent)
                    intent['action_type'] = 'attack'
                    intent['target']      = monster['name'].lower()

        # --- Step 4: Dynamic entity stat block (Infinite Monster Engine) ---
        target = intent.get('target', '').strip()
        # Detect "3 goblins" / "goblin x3" patterns → spawn multiple instances
        _spawn_count, _base_name = self._parse_multi_enemy_count(target)
        if _spawn_count > 1:
            spawned_keys = self._spawn_multi_enemies(_base_name, _spawn_count, intent, current_state)
            # Redirect first-attack target to the first instance key
            target = spawned_keys[0] if spawned_keys else target
            intent  = dict(intent); intent['target'] = target
        elif target and not self.rag.entity_stat_block_exists(target):
            self._generate_and_store_stat_block(target, intent, current_state)

        # Detect boss first-encounter (tier=4 entity just registered, not yet seen before)
        boss_encounter_entry = None
        if target:
            _tk = target.lower()
            _entity = (current_state.known_entities or {}).get(_tk, {})
            if (_entity.get('type') in ('boss',) and _entity.get('alive', True)
                    and not _entity.get('boss_announced')):
                roster_entry = get_monster_by_name(target)
                if roster_entry and roster_entry.get('tier', 0) >= 4:
                    boss_encounter_entry = dict(roster_entry)
                    boss_encounter_entry['hp']     = _entity.get('hp', roster_entry['hp'])
                    boss_encounter_entry['max_hp'] = _entity.get('max_hp', roster_entry['hp'])
                    # Mark so we don't re-announce on subsequent turns
                    _known_mark = dict(current_state.known_entities or {})
                    _e2 = dict(_known_mark.get(_tk, {})); _e2['boss_announced'] = True
                    _known_mark[_tk] = _e2
                    current_state.known_entities = _known_mark
                    flag_modified(current_state, 'known_entities')
                    self.session.commit()

        # --- Step 5: Combat rule engine (Section 3.3) ---
        # Fully deterministic: attack roll → hit/miss → damage roll → net damage.
        # The LLM is never asked to adjudicate combat — it only narrates the result.
        combat_result  = None
        utility_result = None
        class_ability  = intent.get('class_ability')
        loot_xp_result = None   # populated after combat kill (Step 5 → Step 8 boundary)

        if intent.get('action_type') == 'attack' and target and not flee_result:
            combat_result = self.combat.resolve_attack(
                character, char_logic, target, current_state,
                class_ability_key=class_ability,
            )
            if combat_result['hit'] and combat_result['net_damage'] > 0:
                self._apply_combat_damage_to_entity(target, combat_result['net_damage'], current_state)
                # Pre-compute loot/XP so it's available for narrative injection (Step 7)
                killed_entry = (current_state.known_entities or {}).get(target.lower(), {})
                if (not killed_entry.get('alive', True)
                        and killed_entry.get('type') in ('monster', 'boss', 'guard')
                        and not killed_entry.get('loot_granted')):
                    loot_xp_result = self._grant_loot_and_xp(
                        target.lower(), killed_entry, character, char_logic, current_state
                    )
                    known_mark = dict(current_state.known_entities or {})
                    tk = target.lower()
                    if tk in known_mark:
                        e2 = dict(known_mark[tk]); e2['loot_granted'] = True
                        known_mark[tk] = e2
                        current_state.known_entities = known_mark
                        flag_modified(current_state, 'known_entities')
                        self.session.commit()
            # Apply status to target if ability triggered one
            if combat_result.get('status_applied') and combat_result['hit']:
                self.combat.apply_status_to_entity(
                    target.lower(), combat_result['status_applied'], current_state
                )
                flag_modified(current_state, 'known_entities')
                self.session.commit()
            # Handle AoE: apply damage to all other living enemies
            if combat_result.get('ability_aoe'):
                known = current_state.known_entities or {}
                aoe_targets = []
                for k, e in known.items():
                    if k.startswith('_') or k == target.lower():
                        continue
                    if e.get('alive', True):
                        self._apply_combat_damage_to_entity(k, combat_result['net_damage'], current_state)
                        aoe_targets.append(k)
                combat_result['aoe_targets'] = aoe_targets

            # Consume once-per-combat abilities
            if class_ability:
                from engine.combat import get_ability_definition
                adef = get_ability_definition(character.char_class, class_ability)
                if adef and adef.get('once_per_combat'):
                    self._used_abilities.add(class_ability)

        elif intent.get('action_type') == 'item_use':
            # --- Item use (consumable / throwable) ---
            item_name   = intent.get('target', '') or intent.get('summary', '')
            item_result = char_logic.use_item(item_name, dice_roller=self.dice)
            if item_result.get('used'):
                utility_result = {
                    'ability_name':   item_result['item_name'],
                    'ability_key':    'item_use',
                    'mp_cost':        0,
                    'hp_healed':      item_result.get('hp_healed', 0),
                    'def_bonus':      0,
                    'fled_enemies':   [],
                    'damage_reduction': 0.0,
                    'item_result':    item_result,
                }
                # Apply status cures to player
                if item_result.get('cures_status'):
                    known3 = dict(current_state.known_entities or {})
                    buffs3 = [b for b in known3.get('_player_buffs', [])
                              if b.get('key') != item_result['cures_status']]
                    known3['_player_buffs'] = buffs3
                    current_state.known_entities = known3
                    flag_modified(current_state, 'known_entities')
                    self.session.commit()
                # Throwable damage → apply to current target
                if item_result.get('damage_dice') and target:
                    _, _, throw_dmg = self.dice.roll(item_result['damage_dice'])
                    self._apply_combat_damage_to_entity(target, throw_dmg, current_state)
                    if item_result.get('aoe'):
                        known_aoe = current_state.known_entities or {}
                        for k, e in known_aoe.items():
                            if not k.startswith('_') and k != target.lower() and e.get('alive', True):
                                self._apply_combat_damage_to_entity(k, throw_dmg, current_state)
            else:
                utility_result = {'error': f"No item '{item_name}' in inventory"}

        elif intent.get('action_type') in ('magic', 'direct_action') and class_ability:
            # Utility ability (heal, shield, turn undead, evasion, etc.)
            from engine.combat import get_ability_definition
            adef = get_ability_definition(character.char_class, class_ability)
            if adef and not char_logic.can_use_ability(class_ability, self._used_abilities):
                utility_result = {'error': 'Cannot use ability', 'ability_key': class_ability}
            elif adef and (adef.get('heal_dice') or adef.get('def_bonus')
                           or adef.get('affects_undead_only') or adef.get('damage_reduction')):
                utility_result = self.combat.resolve_utility_ability(
                    character, char_logic, target, current_state, class_ability
                )
                if adef.get('once_per_combat'):
                    self._used_abilities.add(class_ability)

        elif intent.get('action_type') == 'magic' and not class_ability:
            # Named spell cast — look up in spell compendium
            from data.spells import get_spell
            from engine.intent_parser import _CAST_NAME_RE
            spell_name_match = _CAST_NAME_RE.search(player_action)
            spell_name = spell_name_match.group(1).strip() if spell_name_match else ''
            spell = get_spell(spell_name) if spell_name else None
            if spell:
                mp_cost = spell.get('mp_cost', 2)
                if character.mp < mp_cost:
                    utility_result = {'error': 'Not enough MP', 'mp_cost': mp_cost}
                else:
                    hp_healed_spell = 0
                    spell_damage = 0
                    if spell.get('heal_dice'):
                        _, _, hp_healed_spell = self.dice.roll(spell['heal_dice'])
                        char_logic.heal(hp_healed_spell)
                    if spell.get('damage_dice') and target:
                        _, _, raw_dmg = self.dice.roll(spell['damage_dice'])
                        # Radiant/holy bonus vs undead
                        entity_data = (current_state.known_entities or {}).get(target, {})
                        is_undead = 'undead' in entity_data.get('description', '').lower()
                        if spell.get('undead_only') and not is_undead:
                            raw_dmg = max(1, raw_dmg // 2)
                        self._apply_combat_damage_to_entity(target, raw_dmg, current_state)
                        spell_damage = raw_dmg
                        if spell.get('aoe'):
                            for k, e in (current_state.known_entities or {}).items():
                                if not k.startswith('_') and k != target and e.get('alive', True):
                                    self._apply_combat_damage_to_entity(k, raw_dmg, current_state)
                    if spell.get('status_apply') and target:
                        self.combat.apply_status_to_entity(
                            target, spell['status_apply'], current_state
                        )
                        flag_modified(current_state, 'known_entities')
                        self.session.commit()
                    char_logic.use_mp(mp_cost)
                    utility_result = {
                        'ability_name': spell_name,
                        'ability_key':  'spell',
                        'mp_cost':      mp_cost,
                        'hp_healed':    hp_healed_spell,
                        'spell_damage': spell_damage,
                        'spell':        spell,
                    }

        elif intent.get('action_type') == 'short_rest':
            if current_state.in_combat:
                utility_result = {'error': 'Cannot rest during combat'}
            else:
                healed = char_logic.short_rest(self.dice)
                utility_result = {'rest_type': 'short', 'hp_healed': healed}

        elif intent.get('action_type') == 'long_rest':
            if current_state.in_combat:
                utility_result = {'error': 'Cannot rest during combat'}
            else:
                char_logic.long_rest()
                utility_result = {
                    'rest_type': 'long',
                    'hp_restored': character.max_hp,
                    'mp_restored': character.max_mp,
                }

        elif intent.get('action_type') == 'buy':
            item_name = intent.get('target', '')
            # Apply faction reputation price modifier from any merchant NPC present
            faction_mult = 1.0
            for npc_name in (characters_present or []):
                # Detect merchant NPCs by entity type
                known_ent = (current_state.known_entities or {}).get(npc_name.lower(), {})
                if known_ent.get('type') == 'merchant':
                    faction_mult = world.get_faction_price_modifier(npc_name)
                    break
            from data.shop import get_shop_item
            shop_entry = get_shop_item(item_name)
            adj_price = None
            if shop_entry and faction_mult != 1.0:
                adj_price = max(1, int(shop_entry['price'] * faction_mult))
            buy_result = char_logic.buy_item(item_name, price=adj_price)
            buy_result['faction_mult'] = faction_mult
            utility_result = {'trade': 'buy', **buy_result}

        elif intent.get('action_type') == 'sell':
            item_name = intent.get('target', '')
            # Apply faction reputation modifier: friendly merchants pay more
            faction_mult = 1.0
            for npc_name in (characters_present or []):
                known_ent = (current_state.known_entities or {}).get(npc_name.lower(), {})
                if known_ent.get('type') == 'merchant':
                    raw_mult = world.get_faction_price_modifier(npc_name)
                    # Invert: if merchant charges less (0.9), they also pay more (1.1)
                    faction_mult = max(0.5, min(1.5, 2.0 - raw_mult))
                    break
            sell_result = char_logic.sell_item(item_name, price_mult=faction_mult)
            sell_result['faction_mult'] = faction_mult
            sell_result['item_name']    = item_name
            utility_result = {'trade': 'sell', **sell_result}

        elif intent.get('action_type') == 'equip':
            item_name = intent.get('target', '')
            slot = char_logic.equip(item_name)
            utility_result = {'equipped': bool(slot), 'slot': slot, 'item_name': item_name}

        elif intent.get('action_type') == 'unequip':
            item_name = intent.get('target', '')
            # Support slot name OR item name
            slot_names = {'weapon', 'armor', 'accessory', '武器', '防具', '飾品'}
            if item_name.lower() in slot_names:
                removed = char_logic.unequip(item_name.lower().replace('武器', 'weapon')
                                             .replace('防具', 'armor')
                                             .replace('飾品', 'accessory'))
            else:
                # Unequip by item name: find which slot holds it
                removed = ''
                for sl, equipped_item in (character.equipment or {}).items():
                    if equipped_item and equipped_item.get('name', '').lower() == item_name.lower():
                        removed = char_logic.unequip(sl)
                        break
            utility_result = {'unequipped': bool(removed), 'item_name': removed or item_name}

        # --- Step 6: Dice roll + rule engine for skill checks (deterministic) ---
        dice_result   = None
        outcome_label = "NO_ROLL"

        if intent.get('requires_roll') and intent.get('dc', 0) > 0 and not combat_result:
            skill    = intent.get('skill', '')
            modifier = char_logic.get_skill_modifier(skill) if skill else 0
            dice_result   = self.dice.roll_skill_check(dc=intent['dc'], modifier=modifier)
            outcome_label = _OUTCOME_LABELS[dice_result['outcome']]

        # --- Step 6.5: Rule engine mechanics ---
        # damage_taken / hp_healed / mp_used are computed here, never by the LLM.
        mechanics = self._calculate_mechanics(
            intent, dice_result, combat_result, player_action, character, current_state,
            utility_result=utility_result, char_logic=char_logic,
        )

        # --- Step 7: Render Narrative Event (LLM Phase 2) ---
        session_memory_text = self._format_session_memory(current_state)
        system_prompt       = self._build_system_prompt(character, current_state, all_chars)

        thought = intent.get('thought_process', '')
        outcome_parts = [f"Player action: {player_action}"]
        if thought:
            outcome_parts.append(f"Action analysis: {thought}")
        # Random encounter during travel
        if random_encounter_entry:
            enc_name = random_encounter_entry.get('name', 'unknown creature')
            enc_spec = random_encounter_entry.get('special_ability', '')
            outcome_parts.append(
                f"RANDOM ENCOUNTER — while travelling, {enc_name} ambushes the party! "
                + (f"Special: {enc_spec}. " if enc_spec else "")
                + "Describe the sudden ambush dramatically."
            )
        # Boss first-encounter — narrator should build dramatic tension
        if boss_encounter_entry:
            bname = boss_encounter_entry.get('display_name', target)
            bspec = boss_encounter_entry.get('special_ability', '')
            outcome_parts.append(
                f"BOSS ENCOUNTER — {bname} appears for the first time! "
                f"HP: {boss_encounter_entry.get('hp')}, "
                f"ATK: {boss_encounter_entry.get('atk')}, "
                f"DEF: {boss_encounter_entry.get('def_stat')}. "
                + (f"Special: {bspec}. " if bspec else "")
                + "Narrate with maximum dramatic impact."
            )

        # Inject player status effects that ticked this turn
        if player_status_tick.get('damage', 0) > 0:
            active_labels = [
                STATUS_EFFECTS.get(k, {}).get('cn_name', k)
                for k in player_status_tick.get('active', [])
            ]
            outcome_parts.append(
                f"Status effects on player: "
                + ', '.join(active_labels or ['none'])
                + f". Status damage this turn: {player_status_tick['damage']} HP."
            )
        if player_status_tick.get('expired'):
            expired_labels = [
                STATUS_EFFECTS.get(k, {}).get('cn_name', k)
                for k in player_status_tick['expired']
            ]
            outcome_parts.append(f"Status effects expired: {', '.join(expired_labels)}.")

        # Inject combat hard facts so the LLM narrates from them, never invents them
        if combat_result:
            ability_label = (
                f" [{combat_result['class_ability']}]" if combat_result.get('class_ability') else ''
            )
            outcome_parts.append(
                f"Combat{ability_label}: {character.name} attacks {target}. "
                f"Attack roll: {combat_result['attack_roll']} + {combat_result['atk_modifier']} "
                f"= {combat_result['attack_total']} vs DEF {combat_result['target_def']}. "
                f"{'AUTO-HIT' if combat_result.get('ability_auto_hit') else ('HIT' if combat_result['hit'] else 'MISS')}."
            )
            if combat_result['hit']:
                bonus_note = (
                    f" (ability bonus: +{combat_result['ability_bonus_dmg']})"
                    if combat_result.get('ability_bonus_dmg') else ''
                )
                outcome_parts.append(
                    f"Damage roll: {combat_result['damage_notation']} "
                    f"= {combat_result['raw_damage']}{bonus_note} "
                    f"(net after DEF reduction: {combat_result['net_damage']})."
                )
                if combat_result.get('critical'):
                    outcome_parts.append("CRITICAL HIT — doubled dice damage!")
                if combat_result.get('ability_aoe') and combat_result.get('aoe_targets'):
                    outcome_parts.append(
                        f"AoE also hits: {', '.join(combat_result['aoe_targets'])}."
                    )
                if combat_result.get('status_applied'):
                    status_label = STATUS_EFFECTS.get(
                        combat_result['status_applied'], {}
                    ).get('cn_name', combat_result['status_applied'])
                    outcome_parts.append(f"{target} is now {status_label}.")
                entity_hp = combat_result.get('entity_hp_remaining')
                if entity_hp is not None:
                    if entity_hp <= 0:
                        outcome_parts.append(f"{target} is DEFEATED (HP reduced to 0).")
                    else:
                        outcome_parts.append(f"{target} HP remaining: {entity_hp}.")
            outcome_label = "CRITICAL SUCCESS" if combat_result.get('critical') else (
                "SUCCESS" if combat_result['hit'] else "FAILURE"
            )

        # Inject utility ability results
        if utility_result and not utility_result.get('error'):
            ab_name = utility_result.get('ability_name', class_ability)
            if utility_result.get('hp_healed', 0) > 0:
                outcome_parts.append(
                    f"Ability [{ab_name}]: heals {utility_result['hp_healed']} HP."
                )
            if utility_result.get('def_bonus', 0) > 0:
                outcome_parts.append(
                    f"Ability [{ab_name}]: +{utility_result['def_bonus']} DEF until next hit."
                )
            if utility_result.get('fled_enemies'):
                outcome_parts.append(
                    f"Ability [{ab_name}]: undead flee — {', '.join(utility_result['fled_enemies'])}."
                )
            if utility_result.get('damage_reduction', 0) > 0:
                outcome_parts.append(
                    f"Ability [{ab_name}]: next incoming damage halved."
                )
            # Item use facts
            ir = utility_result.get('item_result', {})
            if ir.get('cures_status'):
                outcome_parts.append(
                    f"Item [{ab_name}] used: cures {ir['cures_status']} status."
                )
            if ir.get('apply_status'):
                outcome_parts.append(
                    f"Item [{ab_name}] used: inflicts {ir['apply_status']} on {target or 'target'}."
                )
            if ir.get('damage_dice') and target:
                outcome_parts.append(
                    f"Item [{ab_name}] thrown at {target}: {ir['damage_dice']} damage"
                    + (" (AoE — hits all enemies)." if ir.get('aoe') else ".")
                )

        # Inject spell/rest/trade/equip facts
        if utility_result and not utility_result.get('error'):
            atype = intent.get('action_type', '')
            if atype in ('short_rest', 'long_rest') or utility_result.get('rest_type'):
                rtype = utility_result.get('rest_type', 'short')
                if rtype == 'long':
                    outcome_parts.append(
                        f"Long rest: character fully restores HP to {character.max_hp} "
                        f"and MP to {character.max_mp}."
                    )
                else:
                    outcome_parts.append(
                        f"Short rest: character heals {utility_result.get('hp_healed', 0)} HP."
                    )
            elif utility_result.get('trade') == 'buy':
                if utility_result.get('bought'):
                    outcome_parts.append(
                        f"Purchased [{utility_result.get('item_name')}] for "
                        f"{utility_result.get('price', 0)} gold. "
                        f"Gold remaining: {character.gold}."
                    )
                else:
                    outcome_parts.append(
                        f"Cannot buy [{intent.get('target', '')}]: "
                        f"{utility_result.get('reason', 'unknown error')}."
                    )
            elif utility_result.get('trade') == 'sell':
                if utility_result.get('sold'):
                    faction_mult = utility_result.get('faction_mult', 1.0)
                    mult_note = (f" (faction modifier ×{faction_mult:.2f})"
                                 if abs(faction_mult - 1.0) >= 0.01 else "")
                    outcome_parts.append(
                        f"Sold [{intent.get('target', '')}] for "
                        f"{utility_result.get('gold', 0)} gold{mult_note}. "
                        f"Gold total: {character.gold}."
                    )
                else:
                    outcome_parts.append(
                        f"Cannot sell [{intent.get('target', '')}]: "
                        f"{utility_result.get('reason', 'unknown error')}."
                    )
            elif utility_result.get('equipped'):
                outcome_parts.append(
                    f"Equipped [{utility_result.get('item_name')}] in "
                    f"{utility_result.get('slot', '?')} slot."
                )
            elif 'unequipped' in utility_result:
                if utility_result.get('unequipped'):
                    outcome_parts.append(
                        f"Unequipped [{utility_result.get('item_name')}] — returned to inventory."
                    )
            elif utility_result.get('spell'):
                sp = utility_result['spell']
                outcome_parts.append(
                    f"Spell [{utility_result.get('ability_name')}] cast. "
                    f"MP cost: {utility_result.get('mp_cost', 0)}."
                )
                if utility_result.get('spell_damage', 0) > 0:
                    outcome_parts.append(
                        f"Spell deals {utility_result['spell_damage']} {sp.get('element', '')} damage"
                        + (" (AoE — hits all enemies)." if sp.get('aoe') else f" to {target}.")
                    )
                if utility_result.get('hp_healed', 0) > 0:
                    outcome_parts.append(
                        f"Spell heals {utility_result['hp_healed']} HP."
                    )
        elif utility_result and utility_result.get('error'):
            outcome_parts.append(f"Action failed: {utility_result['error']}")

        # Inject flee facts so the LLM narrates the exact outcome
        if flee_result:
            flee_sign = '+' if flee_result['mov_modifier'] >= 0 else ''
            outcome_parts.append(
                f"Flee attempt: 1d20{flee_sign}{flee_result['mov_modifier']} "
                f"= {flee_result['flee_total']} vs DC {flee_result['flee_dc']}."
            )
            if flee_result['fled']:
                outcome_parts.append("FLED SUCCESSFULLY — player escapes combat.")
            else:
                outcome_parts.append("FLEE FAILED — enemy blocks escape.")
                if flee_result['damage_taken'] > 0:
                    outcome_parts.append(
                        f"Punishing counter-attack: player takes {flee_result['damage_taken']} damage."
                    )

        if dice_result:
            outcome_parts.append(
                f"Skill checked: {intent.get('skill', 'general')} vs DC {dice_result['dc']}"
            )
            outcome_parts.append(
                f"Dice roll: {dice_result['notation']} → "
                f"{dice_result['raw_roll']} + {dice_result['modifier']} "
                f"= {dice_result['total']} — {_OUTCOME_LABELS[dice_result['outcome']]}"
            )

        # Inject rule-engine mechanics as hard facts for the narrator
        mech_parts = []
        if mechanics['damage_taken'] > 0:
            mech_parts.append(f"player takes {mechanics['damage_taken']} damage")
        if mechanics['hp_healed'] > 0:
            mech_parts.append(f"player recovers {mechanics['hp_healed']} HP")
        if mechanics['mp_used'] > 0:
            mech_parts.append(f"player expends {mechanics['mp_used']} MP")
        if mechanics.get('counter_status'):
            status_label = STATUS_EFFECTS.get(
                mechanics['counter_status'], {}
            ).get('cn_name', mechanics['counter_status'])
            mech_parts.append(f"player is now {status_label} from enemy counter-attack")
        if mech_parts:
            outcome_parts.append(
                "Mechanical outcomes (hard facts — narrate these): " + "; ".join(mech_parts) + "."
            )

        # Loot/XP facts injected BEFORE narrative rendering so LLM can narrate them
        if loot_xp_result:
            loot_line = (
                f"Loot gained: {', '.join(loot_xp_result['loot_dropped'])}"
                if loot_xp_result['loot_dropped'] else "No loot dropped"
            )
            xp_line = f"XP gained: {loot_xp_result['xp_gained']}"
            outcome_parts.append(f"{loot_line}. {xp_line}.")
            if loot_xp_result.get('leveled_up'):
                outcome_parts.append(
                    f"LEVEL UP! {character.name} is now Level {loot_xp_result['new_level']}!"
                )

        outcome_parts.append(f"Recent session history:\n{session_memory_text}")

        # Inject recently chosen actions so the LLM generates DIFFERENT choices
        _session_mem = current_state.session_memory or []
        recent_chosen = [
            m.get('player_action', '')
            for m in _session_mem[-8:]
            if m.get('player_action')
        ]
        if recent_chosen:
            constraint = (
                "CHOICES CONSTRAINT — player has recently taken: "
                + "; ".join(f'"{a}"' for a in recent_chosen[-5:])
                + ". Generate choices that explore DIFFERENT directions. "
                "Do NOT repeat or closely paraphrase any of the above."
            )
            # Add recent location history so choices push toward new ground
            recent_locations = list(dict.fromkeys(
                m.get('location', '') for m in _session_mem[-6:]
                if m.get('location')
            ))
            if recent_locations:
                constraint += (
                    f" Story has recently revolved around: {', '.join(recent_locations)}."
                    " Prefer choices that advance the plot or open unexplored threads."
                )
            # Detect scene-type monotony and nudge toward variety
            recent_scene_types = [
                m.get('scene_type', '') for m in _session_mem[-5:]
                if m.get('scene_type')
            ]
            if recent_scene_types:
                from collections import Counter
                dominant_type, dominant_count = Counter(recent_scene_types).most_common(1)[0]
                if dominant_count >= 3:
                    constraint += (
                        f" The last {dominant_count} turns were all '{dominant_type}' scenes."
                        " Mix in a different scene type (combat/social/exploration/puzzle/rest)"
                        " to maintain narrative momentum."
                    )
            outcome_parts.append(constraint)

        outcome_context = "\n".join(outcome_parts)

        turn_data = self.llm.render_narrative(
            system_prompt, outcome_context, rag_context,
            language=current_state.language or 'English',
        )

        narrative          = turn_data.get('narrative', "The DM stares blankly into space...")
        choices            = turn_data.get('choices', ["Look around", "Wait"])
        characters_present = [n for n in (turn_data.get('characters_present') or []) if n and n.strip()]

        # Filter choices — compare against all options offered in the last 3 turns
        # (not just last 1) so the same choices can't cycle back after one turn gap.
        _prev_mem = current_state.session_memory or []
        _seen_offered = set()
        unchosen_multi = []
        for m in _prev_mem[-3:]:
            for c in (m.get('offered_choices') or m.get('choices') or []):
                if c and c.strip() != player_action.strip() and c not in _seen_offered:
                    unchosen_multi.append(c)
                    _seen_offered.add(c)
        choices = self._filter_similar_choices(
            choices, player_action, unchosen_multi, narrative,
            current_state.language or 'English',
            session_memory_text=session_memory_text,
        )

        # Build a mutable set for dedup throughout the supplement passes below.
        _present_lower_set = {n.lower() for n in characters_present}

        # Supplement pass 1 — npc_relationship_changes keys.
        # If the LLM generated a relationship change for an NPC this turn, they
        # were in the scene even if characters_present was left empty or incomplete.
        for npc_name in (turn_data.get('npc_relationship_changes') or {}):
            if npc_name and npc_name.strip() and npc_name.lower() not in _present_lower_set:
                characters_present.append(npc_name)
                _present_lower_set.add(npc_name.lower())

        # Supplement pass 2 — narrative text scan for already-tracked NPCs.
        # The LLM sometimes forgets to populate characters_present even when a known
        # NPC clearly appears in the narrative it just wrote.  Scan the narrative for
        # every NPC name (and their proper_name / aliases) that is already tracked in
        # relationships.  This is deterministic and cannot hallucinate new NPCs.
        narrative_lower = narrative.lower()
        for npc_name, npc_data in (current_state.relationships or {}).items():
            if npc_name.lower() in _present_lower_set:
                continue
            # Collect all name variants for this NPC
            search_names = [npc_name]
            if isinstance(npc_data, dict):
                if npc_data.get('proper_name'):
                    search_names.append(npc_data['proper_name'])
                for alias in (npc_data.get('aliases') or []):
                    if alias:
                        search_names.append(alias)
            if any(n.lower() in narrative_lower for n in search_names if n and len(n) > 1):
                characters_present.append(npc_name)
                _present_lower_set.add(npc_name.lower())

        # Supplement pass 3 — carry forward NPCs who were present last turn and
        # haven't left.  Runs only when the scene stays in the same location
        # (no location_change in this turn's turn_data) and the NPC isn't dead.
        # This prevents an NPC from "disappearing" simply because the LLM forgot
        # to include them in characters_present this turn.
        if _prev_mem and not turn_data.get('location_change'):
            last_mem       = _prev_mem[-1]
            last_location  = last_mem.get('location', '')
            cur_location   = current_state.current_location or ''
            # Only carry forward if we know the previous location and it matches
            if not last_location or last_location == cur_location:
                lower_to_display = {n.lower(): n for n in (current_state.relationships or {})}
                known_entities   = current_state.known_entities or {}
                for key in (last_mem.get('characters_present') or []):
                    if not key.startswith('npc:'):
                        continue
                    npc_lower = key[4:]   # strip "npc:" prefix
                    if npc_lower in _present_lower_set:
                        continue
                    # Never carry forward a defeated NPC
                    entity_info = known_entities.get(npc_lower, {})
                    if entity_info and not entity_info.get('alive', True):
                        continue
                    display_name = lower_to_display.get(npc_lower)
                    if display_name:
                        characters_present.append(display_name)
                        _present_lower_set.add(npc_lower)

        # --- Step 7.5: Auto-register new NPCs from the scene ---
        # Extract organizations from the narrative FIRST so their names are
        # known before we filter characters_present and register NPCs.
        # This mirrors the generate_prologue order and prevents org names from
        # being accidentally registered as NPCs.
        self._extract_and_register_organizations(narrative, current_state, world)

        # Filter out organization names that the LLM mistakenly placed in
        # characters_present — they belong in the organizations dict, not
        # relationships (NPC list).
        org_names_lower = {
            (org.get('name') or key).lower()
            for key, org in (current_state.organizations or {}).items()
        }
        characters_present = [
            n for n in characters_present
            if n.lower() not in org_names_lower
        ]
        party_names = {c.name for c in all_chars}
        self._auto_register_npcs(characters_present, current_state, world, party_names)

        # --- Step 8: Apply deterministic mechanics ---
        # Values come from the rule engine (Step 6.5), NOT from the LLM narrative output.
        damage_taken   = mechanics['damage_taken']
        hp_healed      = mechanics['hp_healed']
        mp_used        = mechanics['mp_used']
        counter_status = mechanics.get('counter_status')

        if damage_taken:
            char_logic.take_damage(damage_taken)
        if hp_healed:
            char_logic.heal(hp_healed)
        if mp_used:
            char_logic.use_mp(mp_used)

        # --- Game Over check: player HP reached 0 ---
        if character.hp <= 0:
            character.hp = 0
            current_state.in_combat = 0
            # Apply difficulty-based death penalty before saving
            death_penalty = self._apply_death_penalty(character, char_logic, current_state)
            self.session.commit()
            turn_data['game_over'] = True
            turn_data['_combat_result'] = combat_result
            turn_data['_death_penalty'] = death_penalty
            return narrative, choices, turn_data, dice_result

        # Apply any status effect the enemy inflicted on the player via counter-attack
        if counter_status:
            self.combat.apply_status_to_player(counter_status, current_state)
            flag_modified(current_state, 'known_entities')
            self.session.commit()
        for item in (turn_data.get('items_found') or []):
            char_logic.add_item({'name': item} if isinstance(item, str) else item)
        if turn_data.get('location_change'):
            world.update_location(turn_data['location_change'])
        present_lower = {n.lower() for n in characters_present}
        for npc, delta in (turn_data.get('npc_relationship_changes') or {}).items():
            # Only apply relationship changes for NPCs present in this scene (Req. 5)
            if characters_present and npc.lower() not in present_lower:
                continue
            world.update_relationship(npc, delta)

        # --- Phase 3: Update combat state ---
        self._update_combat_state(intent, combat_result, current_state,
                                  flee_result=flee_result)

        # Apply quest completion rewards from narrative
        for quest_name in (turn_data.get('quest_completed') or []):
            if not quest_name:
                continue
            for q in world.get_active_quests():
                if q.get('name', '').lower() == str(quest_name).lower():
                    rewards = world.complete_quest(q['quest_id'],
                                                   current_state.turn_count)
                    if rewards.get('reward_xp'):
                        character.xp = (character.xp or 0) + rewards['reward_xp']
                        new_lvl = compute_level(character.xp)
                        if new_lvl > (character.level or 1):
                            character.level = new_lvl
                    if rewards.get('reward_gold'):
                        character.gold = (character.gold or 0) + rewards['reward_gold']
                    self.session.commit()
                    turn_data.setdefault('_quest_rewards', []).append({
                        'quest_name': q['name'],
                        'xp':         rewards.get('reward_xp', 0),
                        'gold':       rewards.get('reward_gold', 0),
                    })
                    break

        # Store result refs in turn_data for UI rendering
        turn_data['_combat_result'] = combat_result
        if loot_xp_result:
            turn_data['_loot_xp'] = loot_xp_result
        if flee_result:
            turn_data['_flee_result'] = flee_result
        if boss_encounter_entry:
            turn_data['_boss_encounter'] = boss_encounter_entry
        if random_encounter_entry:
            turn_data['_random_encounter'] = random_encounter_entry
        if utility_result:
            turn_data['_utility_result'] = utility_result

        # Track contribution for balanced reward calculation
        checks_passed = 1 if (dice_result and dice_result.get('outcome') in
                               ('success', 'critical_success')) else 0
        net_damage_dealt = 0
        if combat_result and combat_result.get('hit'):
            net_damage_dealt = combat_result.get('net_damage', 0)
        self._update_contributions(
            current_state, character,
            damage_dealt=net_damage_dealt,
            healing_done=hp_healed,
            checks_passed=checks_passed,
        )

        # Advance to next living player (multi-player round-robin)
        self._advance_active_player(current_state, all_chars)

        # Clear emotion for NPCs who left the scene (Req. 4)
        self._clear_absent_npc_emotions(characters_present, current_state)

        # --- Step 9: NPC generative agent reactions (Section 3.5) ---
        # After social turns or turns that touched NPC relationships, let each
        # NPC update their own goal and emotional state independently.
        # Only evaluates NPCs present in this scene (Req. 5).
        scene_type = turn_data.get('scene_type', 'exploration')
        npc_changes = turn_data.get('npc_relationship_changes') or {}
        if scene_type == 'social' or npc_changes or characters_present:
            self._evaluate_npc_reactions(
                narrative, current_state, world,
                characters_present=characters_present,
                action_type=intent.get('action_type', 'direct_action'),
                outcome_label=outcome_label,
            )

        # --- Step 10a: Update sliding-window session memory ---
        # Convert NPC names → "npc:{name.lower()}" entity keys (max 6).
        # Party members are excluded (they're always implicitly present).
        npc_only_present = [n for n in characters_present if n not in party_names]
        char_entity_keys = [
            f"npc:{n.lower()}" for n in npc_only_present[:6]
        ]
        # Scan known organizations to find those mentioned in this turn's narrative (max 3).
        narrative_lower  = narrative.lower()
        orgs_dict        = current_state.organizations or {}
        org_entity_keys  = [
            f"org:{key}"
            for key, org in orgs_dict.items()
            if (org.get('name') or key).lower() in narrative_lower
        ][:3]
        self._update_session_memory(
            current_state, player_action, narrative, outcome_label,
            choices=choices,
            location=current_state.current_location,
            characters_present=char_entity_keys,
            organizations_mentioned=org_entity_keys,
            scene_type=scene_type,
        )

        # --- Step 10b: Persist Narrative Event to RAG long-term memory ---
        event_id  = f"event_{character.id}_{current_state.id}_{int(time.time() * 1000)}"
        scene_tag = scene_type.upper()
        self.rag.add_story_event(
            f"[{scene_tag}] Player: {player_action}\nDM: {narrative}",
            event_id=event_id,
        )

        # --- Step 10c: Extract entity relationships from this turn's narrative ---
        # (Organization extraction already ran at Step 7.5 before NPC registration)
        self._extract_and_register_relations(narrative, current_state, world, party or [])

        return narrative, choices, turn_data, dice_result

    def generate_prologue(self, current_state, party):
        """
        Generate the Turn 0 opening prologue for a new game.

        Seeds world lore into RAG, calls the LLM for a ≥1000-char opening
        narrative, and stores the prologue in the story_events RAG.

        Returns:
            narrative  (str)        — prologue text
            choices    (list[str])  — ≥3 opening action choices
            turn_data  (dict)       — full validated narrative dict
        """
        # Seed world lore into RAG (same as Step 2 of process_turn)
        self._seed_world_lore(current_state)

        turn_data = self.llm.generate_prologue(current_state, party)
        narrative = turn_data.get('narrative', '')
        choices   = turn_data.get('choices', [])

        # Store prologue in RAG as story event 0
        try:
            self.rag.add_story_event(
                f"[PROLOGUE] {narrative[:600]}",
                event_id=f"prologue_{current_state.id}",
            )
        except Exception as e:
            print(f"Prologue RAG store error: {e}")

        # Auto-register organizations first so we can filter them from characters_present
        world = WorldManager(self.session, current_state)
        self._extract_and_register_organizations(narrative, current_state, world, turn_number=0)

        # Register NPCs — filter out organization names the LLM may have mixed in
        party_names = {c.name for c in party}
        characters_present = [n for n in (turn_data.get('characters_present') or []) if n and n.strip()]
        org_names_lower = {
            (org.get('name') or key).lower()
            for key, org in (current_state.organizations or {}).items()
        }
        characters_present = [n for n in characters_present if n.lower() not in org_names_lower]
        self._auto_register_npcs(characters_present, current_state, world, party_names)
        self._extract_and_register_relations(narrative, current_state, world, party, turn_number=0)

        # Store prologue choices in session_memory so the first player turn
        # can compute unchosen_choices (what the player did NOT pick).
        npc_only = [n for n in characters_present if n not in party_names]
        char_keys = [f"npc:{n.lower()}" for n in npc_only[:6]]
        narrative_lower = narrative.lower()
        orgs_dict = current_state.organizations or {}
        org_keys  = [
            f"org:{key}"
            for key, org in orgs_dict.items()
            if (org.get('name') or key).lower() in narrative_lower
        ][:3]
        memory = list(current_state.session_memory or [])
        memory.append({
            "turn":                    0,
            "player_action":           "[prologue]",
            "narrative":               narrative[:200],
            "outcome":                 "PROLOGUE",
            "unchosen_choices":        [],
            "offered_choices":         choices or [],
            "location":                current_state.current_location or "",
            "characters_present":      char_keys,
            "organizations_mentioned": org_keys,
            "scene_type":              turn_data.get('scene_type', 'exploration'),
        })
        current_state.session_memory = memory
        flag_modified(current_state, 'session_memory')
        self.session.commit()

        return narrative, choices, turn_data

    def run_ai_turn(self, current_state, party):
        """
        Execute one AI-controlled player's turn automatically.

        Reads the active slot's AI config from current_state.ai_configs,
        generates an action via AIPlayerController, then delegates to
        process_turn() exactly as if a human had submitted the action.

        Returns (action_text, narrative, choices, turn_data, dice_result).
        action_text is the string the AI chose — shown in chat history.
        """
        active_idx = (current_state.active_player_index or 0) % max(len(party), 1)
        ai_char    = party[active_idx]
        ai_configs = current_state.ai_configs or {}
        ai_config  = ai_configs.get(str(active_idx), {})

        controller  = AIPlayerController()
        action_text = controller.decide_action(ai_char, current_state, party, ai_config)

        narrative, choices, turn_data, dice_result = self.process_turn(
            action_text, current_state, ai_char, party=party
        )
        return action_text, narrative, choices, turn_data, dice_result

    # ------------------------------------------------------------------
    # Internal helpers — combat  (Section 3.3)
    # ------------------------------------------------------------------

    # _resolve_combat removed — replaced by CombatEngine.resolve_attack (engine/combat.py)

    def _apply_combat_damage_to_entity(self, entity_name, net_damage, current_state):
        """Decrement the target's HP in known_entities; mark alive=False on death."""
        known = dict(current_state.known_entities or {})
        key   = entity_name.lower()
        if key not in known:
            return
        entry = dict(known[key])
        entry['hp'] = max(0, entry.get('hp', 0) - net_damage)
        if entry['hp'] <= 0:
            entry['alive'] = False
        known[key] = entry
        current_state.known_entities = known
        flag_modified(current_state, 'known_entities')
        self.session.commit()

    # ------------------------------------------------------------------
    # Internal helpers — NPC reactions  (Section 3.5)
    # ------------------------------------------------------------------

    def _evaluate_npc_reactions(self, narrative, current_state, world,
                                characters_present=None,
                                action_type='direct_action', outcome_label='NO_ROLL'):
        """
        Ask the LLM how tracked NPCs react to the narrative event independently.
        Only runs for social scenes or turns that already touched NPC relationships.
        Only evaluates NPCs who are present in the current scene (characters_present).
        Updates each changed NPC's affinity, state, goal, emotion, and action in the DB.

        affinity_delta is computed by calculate_affinity_delta() (rule engine) and
        applied to every present NPC; the LLM only decides state/goal/emotion/action.
        """
        rels = current_state.relationships or {}
        if not rels:
            return

        # Filter to NPCs present in this scene; if characters_present is empty/None,
        # fall back to all tracked NPCs to preserve backward compatibility
        if characters_present:
            present_lower = {n.strip().lower() for n in characters_present}
            npc_states = {
                name: data for name, data in rels.items()
                if isinstance(data, dict) and name.lower() in present_lower
            }
        else:
            npc_states = {name: data for name, data in rels.items() if isinstance(data, dict)}

        if not npc_states:
            return

        # Rule-engine affinity delta — same base value for all present NPCs
        rule_delta = calculate_affinity_delta(action_type, outcome_label)

        try:
            reactions = self.llm.evaluate_npc_reactions(
                event_summary=narrative[:500],
                npc_states=npc_states,
                language=current_state.language or 'English',
            )
            # Apply to NPCs the LLM flagged as changed
            updated = set()
            for npc_name, changes in reactions.items():
                if not isinstance(changes, dict):
                    continue
                world.update_relationship(
                    npc_name,
                    rule_delta,                    # rule engine, not LLM
                    state=changes.get('state'),
                    goal=changes.get('goal'),
                    emotion=changes.get('emotion'),
                    action=changes.get('action'),
                )
                updated.add(npc_name.lower())

            # Apply the affinity delta to present NPCs the LLM didn't mention
            # (they witnessed the event too — they just didn't change state/goal)
            if rule_delta != 0:
                for npc_name in npc_states:
                    if npc_name.lower() not in updated:
                        world.update_relationship(npc_name, rule_delta)

        except Exception as e:
            print(f"NPC reaction step error: {e}")

    # ------------------------------------------------------------------
    # Internal helpers — choice diversity filtering
    # ------------------------------------------------------------------

    def _filter_similar_choices(self, choices, chosen_action, unchosen_prev, narrative, language,
                                session_memory_text=""):
        """
        Enforce diversity on newly generated choices using character-set similarity.

        Thresholds:
          chosen_action  (what player just did):           must be < 20% similar
          unchosen_prev  (last turn's non-selected opts):  must be < 50% similar
          inter-choice   (any two choices in same turn):   must be < 20% similar

        Choices that fail are replaced by requesting alternatives from the LLM.
        session_memory_text is forwarded to generate_diverse_choices so replacement
        choices are grounded in ongoing story context.
        Always returns at least 3 choices.
        """
        CHOSEN_THRESH      = 0.20
        UNCHOSEN_THRESH    = 0.50
        INTER_CHOICE_THRESH = 0.20

        # accepted_good is threaded in so each candidate is also checked against
        # already-accepted choices (inter-choice diversity within the same turn).
        def passes(c, accepted_good):
            if chosen_action and _char_similarity(c, chosen_action) >= CHOSEN_THRESH:
                return False
            if any(_char_similarity(c, u) >= UNCHOSEN_THRESH for u in (unchosen_prev or [])):
                return False
            return not any(_char_similarity(c, g) >= INTER_CHOICE_THRESH for g in accepted_good)

        # Build good list greedily so inter-choice check accumulates correctly.
        good = []
        for c in choices:
            if passes(c, good):
                good.append(c)

        target = max(3, len(choices))
        if len(good) == target:
            return good  # all pass — skip LLM call

        needed = target - len(good)
        # avoid list = everything already seen or accepted, so LLM gets full context
        avoid = [chosen_action] + list(unchosen_prev or []) + choices
        try:
            replacements = self.llm.generate_diverse_choices(
                narrative=narrative,
                avoid_choices=[c for c in avoid if c],
                count=needed + 1,   # request extra in case some still fail
                language=language,
                session_memory_text=session_memory_text,
            )
            for c in replacements:
                if len(good) >= target:
                    break
                if passes(c, good):
                    good.append(c)
        except Exception as e:
            print(f"Choice diversity retry error: {e}")

        # Ensure minimum 3 — fall back to originals only if still short
        for c in choices:
            if len(good) >= target:
                break
            if c not in good:
                good.append(c)

        return good[:target]

    # ------------------------------------------------------------------
    # Internal helpers — NPC auto-registration and scene lifecycle
    # ------------------------------------------------------------------

    def _auto_register_npcs(self, characters_present, current_state, world, party_names):
        """
        For each name in characters_present that is not already in relationships
        and is not a party member, generate and register a full NPC profile.

        Also back-fills profiles for NPCs already in relationships that were
        created without one (e.g. the starting NPC, NPCs from npc_relationship_changes).
        Limited to 2 back-fills per call to keep LLM overhead bounded.
        """
        rels = current_state.relationships or {}

        # Build set of known organization names so we never register an org as an NPC
        _org_names_lower = set()
        for key, org in (current_state.organizations or {}).items():
            _org_names_lower.add(key.lower())
            if org.get('name'):
                _org_names_lower.add(org['name'].lower())

        # Pass 1: generate profiles for NPCs newly appearing in this scene
        for name in (characters_present or []):
            name = name.strip()
            if not name or name in party_names or name.lower() in _org_names_lower:
                continue
            existing_rel = rels.get(name)
            if existing_rel is not None and isinstance(existing_rel, dict) and existing_rel.get('biography'):
                continue
            try:
                profile = self.llm.generate_npc_profile(
                    display_name=name,
                    world_context=current_state.world_context,
                    existing_rel=existing_rel if isinstance(existing_rel, dict) else {},
                    language=current_state.language or 'English',
                )
            except Exception as e:
                print(f"NPC profile error for {name!r}: {e}")
                profile = {}
            world.register_npc(name, profile)
            rels = current_state.relationships or {}

        # Pass 2: back-fill profiles for any tracked NPC that still has no biography
        # (covers starting NPC from create_new_game and NPCs from npc_relationship_changes)
        back_fill = 0
        for name, data in list((current_state.relationships or {}).items()):
            if back_fill >= 2:
                break
            if not isinstance(data, dict) or name in party_names:
                continue
            _NPC_IMPORTANT = ('biography', 'personality', 'traits', 'proper_name')
            if all(data.get(f) for f in _NPC_IMPORTANT):
                continue
            try:
                profile = self.llm.generate_npc_profile(
                    display_name=name,
                    world_context=current_state.world_context,
                    existing_rel=data,
                    language=current_state.language or 'English',
                )
                world.register_npc(name, profile)
                back_fill += 1
            except Exception as e:
                print(f"NPC back-fill profile error for {name!r}: {e}")

    def _clear_absent_npc_emotions(self, characters_present, current_state):
        """
        Clear the emotion field for any NPC NOT in the current scene.
        Ensures emotion is only ever populated while the NPC is present (Req. 4).
        NPCs present in this scene keep their emotion unchanged (Req. 5).
        """
        present_lower = {n.strip().lower() for n in (characters_present or [])}
        rels = dict(current_state.relationships or {})
        changed = False
        for name, data in rels.items():
            if not isinstance(data, dict):
                continue
            if name.lower() in present_lower:
                continue   # NPC is present — leave emotion intact
            if data.get('emotion'):
                data = dict(data)
                data['emotion'] = ""
                rels[name] = data
                changed = True
        if changed:
            current_state.relationships = rels
            flag_modified(current_state, 'relationships')
            self.session.commit()

    # ------------------------------------------------------------------
    # Internal helpers — prompts and memory
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — multi-player party management
    # ------------------------------------------------------------------

    def _advance_active_player(self, current_state, party):
        """
        Rotate active_player_index to the next living party member.
        Skips defeated characters (HP ≤ 0).  No-op for single-player.
        """
        if len(party) <= 1:
            return
        n = len(party)
        idx = (current_state.active_player_index or 0)
        for _ in range(n):
            idx = (idx + 1) % n
            if party[idx].hp > 0:
                break
        if current_state.active_player_index != idx:
            current_state.active_player_index = idx
            flag_modified(current_state, 'active_player_index')
            self.session.commit()

    def _update_contributions(self, current_state, character,
                              damage_dealt=0, healing_done=0, checks_passed=0):
        """Accumulate per-player contribution metrics for end-game reward split."""
        contribs = dict(current_state.party_contributions or {})
        key = str(character.id)
        entry = dict(contribs.get(key, {
            'damage_dealt': 0, 'healing_done': 0,
            'skill_checks_passed': 0, 'turns_taken': 0,
        }))
        entry['damage_dealt']        = entry.get('damage_dealt', 0)        + damage_dealt
        entry['healing_done']        = entry.get('healing_done', 0)        + healing_done
        entry['skill_checks_passed'] = entry.get('skill_checks_passed', 0) + checks_passed
        entry['turns_taken']         = entry.get('turns_taken', 0)         + 1
        contribs[key] = entry
        current_state.party_contributions = contribs
        flag_modified(current_state, 'party_contributions')
        self.session.commit()

    def _calculate_mechanics(self, intent, dice_result, combat_result,
                             player_action, character, current_state,
                             utility_result=None, char_logic=None):
        """
        Rule engine for damage_taken, hp_healed, mp_used.

        Replaces the LLM's role in deciding these mechanical values so that
        stat mutations are always deterministic (neuro-symbolic design principle).

        Rules:
          damage_taken — enemy counter-attack after player's attack (if target alive);
                         minor fall/trap damage on critical_failure of a physical skill;
                         status effect damage already applied in step 2.5.
          hp_healed    — class utility ability heal; medicine skill + successful dice outcome.
          mp_used      — any magic-type action or class ability with mp_cost.

        Returns a dict {damage_taken, hp_healed, mp_used, counter_status}.
        """
        action_type = intent.get('action_type', 'direct_action')
        skill       = intent.get('skill', '').lower()
        outcome     = dice_result['outcome'] if dice_result else None

        damage_taken   = 0
        hp_healed      = 0
        mp_used        = 0
        counter_status = None  # status effect applied to player by enemy counter

        # --- Enemy counter-attack (after player's attack) ---
        if combat_result:
            target_name  = combat_result.get('target', '').lower()
            known        = current_state.known_entities or {}
            target_entry = known.get(target_name, {})
            hp_after = combat_result.get('entity_hp_remaining')
            is_alive = target_entry.get('alive', True) and (hp_after is None or hp_after > 0)
            if is_alive:
                # Phase 4: trigger monster AI specials (berserk, song, etc.)
                target_entry = self._apply_monster_ai_triggers(
                    target_name, target_entry, current_state
                )
                counter = self.combat.resolve_enemy_counter_attack(target_entry, character)
                if counter.get('hit'):
                    # Consume Arcane Shield bonus if active
                    shield_bonus = char_logic.consume_def_bonus(current_state) if char_logic else 0
                    # Evasion: check player buff
                    evasion_active = any(
                        b.get('key') == '_evasion'
                        for b in (current_state.known_entities or {}).get('_player_buffs', [])
                    )
                    raw_dmg = counter.get('raw_damage', 0)
                    if evasion_active:
                        raw_dmg = int(raw_dmg * 0.5)
                        # Remove evasion buff
                        known2 = dict(current_state.known_entities or {})
                        known2['_player_buffs'] = [
                            b for b in known2.get('_player_buffs', [])
                            if b.get('key') != '_evasion'
                        ]
                        current_state.known_entities = known2
                    effective_def = character.def_stat + shield_bonus
                    damage_taken = max(0, raw_dmg - (effective_def // 2))
                    counter_status = counter.get('status_applied')
                    # Apply lifesteal (wight / vampire_spawn) — read fresh after AI triggers
                    if counter.get('lifesteal', 0) > 0 and is_alive:
                        entry2 = dict((current_state.known_entities or {}).get(target_name, {}))
                        entry2['hp'] = min(
                            entry2.get('max_hp', entry2.get('hp', 0)),
                            entry2.get('hp', 0) + counter['lifesteal'],
                        )
                        known2 = dict(current_state.known_entities or {})
                        known2[target_name] = entry2
                        current_state.known_entities = known2

                # Tick enemy status effects (e.g. poison on the enemy) — always runs while alive
                enemy_tick = self.combat.tick_entity_status_effects(target_name, current_state)
                if enemy_tick.get('damage', 0) > 0:
                    # Reapply damage from tick to entity
                    self._apply_combat_damage_to_entity(
                        target_name, enemy_tick['damage'], current_state
                    )

                    flag_modified(current_state, 'known_entities')
                    self.session.commit()

        # --- Physical-skill critical failure → minor fall/hazard damage ---
        elif dice_result and skill in _PHYSICAL_SKILLS and outcome == 'critical_failure':
            damage_taken = self.dice.roll('1d4')[2]

        # --- Class utility ability: healing, arcane shield, evasion, turn undead ---
        if utility_result and not utility_result.get('error'):
            hp_healed = utility_result.get('hp_healed', 0)
            mp_used   = utility_result.get('mp_cost', 0)
            # Arcane Shield — store DEF bonus in player buffs
            def_bonus = utility_result.get('def_bonus', 0)
            if char_logic and def_bonus > 0:
                char_logic.apply_def_bonus(def_bonus, current_state)
                flag_modified(current_state, 'known_entities')
                self.session.commit()
            # Evasion — store as player buff
            if utility_result.get('damage_reduction', 0) > 0:
                known3 = dict(current_state.known_entities or {})
                buffs3 = [b for b in known3.get('_player_buffs', []) if b.get('key') != '_evasion']
                buffs3.append({'key': '_evasion', 'turns_remaining': 1})
                known3['_player_buffs'] = buffs3
                current_state.known_entities = known3
            # Turn Undead — mark fled enemies as dead
            for fled_key in utility_result.get('fled_enemies', []):
                self._apply_combat_damage_to_entity(fled_key, 9999, current_state)
        else:
            # --- Magic action → MP cost (even on failure, if no utility_result) ---
            if action_type == 'magic' and not utility_result:
                class_ability = intent.get('class_ability')
                if class_ability:
                    from engine.combat import get_ability_definition
                    adef = get_ability_definition(character.char_class, class_ability)
                    mp_used = min(adef.get('mp_cost', _MP_COST_DEFAULT), character.mp) if adef else 0
                else:
                    cost    = _MP_COST_TABLE.get(skill, _MP_COST_DEFAULT)
                    mp_used = min(cost, character.mp)

            # --- Healing: medicine skill or healing keywords + successful outcome ---
            if outcome in ('success', 'critical_success') and character.hp < character.max_hp:
                is_healing = (
                    skill == 'medicine'
                    or _HEAL_RE.search(player_action or '')
                )
                if is_healing:
                    notation  = '2d4' if outcome == 'critical_success' else '1d4'
                    hp_healed = self.dice.roll(notation)[2]
                    if mp_used == 0 and character.mp > 0:
                        mp_used = min(2, character.mp)

        return {
            'damage_taken':   damage_taken,
            'hp_healed':      hp_healed,
            'mp_used':        mp_used,
            'counter_status': counter_status,
        }

    def _build_system_prompt(self, character, current_state, party=None):
        npc_context      = self._format_npc_state(current_state)
        world_context    = self._format_world_setting(current_state)
        org_context      = self._format_org_context(current_state)
        relation_context = self._format_relation_context(current_state, party)
        ws_id  = getattr(current_state, 'world_setting', None) or 'dnd5e'
        tm     = config.get_world_setting(ws_id)['term_map']
        hp_lbl = tm['hp_name']
        mp_lbl = tm['mp_name']

        # Party roster block (shown for all players so DM can address each by name)
        all_chars = party if (party and len(party) > 1) else None
        party_block = ""
        if all_chars:
            lines = ["Party roster:"]
            for c in all_chars:
                status = "DEFEATED" if c.hp <= 0 else f"{hp_lbl} {c.hp}/{c.max_hp}"
                active_marker = " ◀ ACTIVE" if c.id == character.id else ""
                lines.append(
                    f"  {c.name} ({c.race} {c.char_class}) — {status}"
                    f"  {mp_lbl} {c.mp}/{c.max_mp}  ATK {c.atk}  DEF {c.def_stat}"
                    f"{active_marker}"
                )
            party_block = "\n".join(lines) + "\n"

        return (
            f"{world_context}"
            f"{party_block}"
            f"Active player this turn: {character.name} ({character.race} {character.char_class}).\n"
            f"{hp_lbl}: {character.hp}/{character.max_hp}  "
            f"{mp_lbl}: {character.mp}/{character.max_mp}  "
            f"ATK: {character.atk}  DEF: {character.def_stat}  "
            f"Gold: {character.gold or 0}.\n"
            f"Location: {current_state.current_location}.\n"
            f"World lore: {current_state.world_context}\n"
            f"Difficulty: {current_state.difficulty}\n"
            f"{npc_context}"
            f"{org_context}"
            f"{relation_context}"
            f"CRITICAL: Write ALL narrative and choices EXCLUSIVELY in "
            f"{current_state.language or 'English'}.\n"
            "Do NOT invent dice rolls or mechanical outcomes — "
            "those are provided to you as hard structured facts."
        )

    def _format_world_setting(self, current_state):
        """Build a vocabulary + tone block from the active world setting."""
        ws_id = getattr(current_state, 'world_setting', None) or 'dnd5e'
        ws    = config.get_world_setting(ws_id)
        tm    = ws.get('term_map', {})
        lines = [
            f"You are a {tm.get('dm_title', 'Game Master')} running a {ws['name']} campaign.",
            f"Tone: {ws.get('tone', '')}",
            "Vocabulary — always use these setting-specific terms instead of generic DnD words:",
            f"  HP → {tm.get('hp_name', 'HP')} | "
            f"MP → {tm.get('mp_name', 'MP')} | "
            f"Currency → {tm.get('gold_name', 'gold')}",
            f"  Fighter class → {tm.get('warrior_class', 'Warrior')} | "
            f"Mage class → {tm.get('mage_class', 'Mage')} | "
            f"Rogue class → {tm.get('rogue_class', 'Rogue')} | "
            f"Cleric class → {tm.get('cleric_class', 'Cleric')}",
            f"  Ability checks → '{tm.get('skill_check', 'skill check')}'",
            "",
        ]
        return "\n".join(lines) + "\n"

    def _format_npc_state(self, current_state):
        """Format current NPC relationships as a concise DM context block."""
        rels = current_state.relationships or {}
        if not rels:
            return ""
        lines = ["Current NPC states:"]
        for name, data in rels.items():
            if not isinstance(data, dict):
                continue
            affinity   = data.get('affinity', 0)
            state      = data.get('state', 'Neutral')
            goal       = data.get('goal', '')
            proper     = data.get('proper_name', '')
            emotion    = data.get('emotion', '')
            action     = data.get('action', '')
            health     = data.get('health', '')
            personality = data.get('personality', '')

            display = name
            if proper and proper != name:
                display = f"{name} ({proper})"

            line = f"  - {display}: {state} ({affinity:+d})"
            if health and health.lower() not in ('healthy', '健康', ''):
                line += f", health: {health}"
            if emotion:
                line += f", emotion: {emotion}"
            if action:
                line += f", doing: {action}"
            if goal:
                line += f", goal: {goal}"
            if personality:
                line += f", personality: {personality}"
            lines.append(line)
        return "\n".join(lines) + "\n"

    def _format_org_context(self, current_state):
        """
        Build a compact organisation summary block for the system prompt.

        Emits one line per known organisation:
          Name (type, alignment) — leader: X, HQ: Y
        Capped at 10 organisations to bound token cost (~200 tokens worst-case).
        Returns empty string when no organisations are known.
        """
        orgs = current_state.organizations or {}
        if not orgs:
            return ""
        lines = ["Known organisations:"]
        for org in sorted(orgs.values(), key=lambda o: o.get('first_seen_turn', 0))[:10]:
            name   = org.get('name', '')
            if not name:
                continue
            parts  = []
            otype  = org.get('type', '')
            align  = org.get('alignment', '')
            if otype or align:
                parts.append(f"{otype}{', ' + align if otype and align else align}")
            leader = org.get('current_leader', '')
            hq     = org.get('headquarters', '')
            meta   = []
            if leader:
                meta.append(f"leader: {leader}")
            if hq:
                meta.append(f"HQ: {hq}")
            line = f"  - {name}"
            if parts:
                line += f" ({', '.join(parts)})"
            if meta:
                line += f" — {', '.join(meta)}"
            lines.append(line)
        return "\n".join(lines) + "\n"

    def _format_relation_context(self, current_state, party=None):
        """
        Build a compact entity-relation block for the system prompt.

        Strategy: include only edges where at least one endpoint is a party
        member or an NPC currently in the relationship dict (i.e. relevant to
        the current scene).  Edges purely between organisations with no party
        involvement are included only when both organisations are already known
        (max 5 org-org edges).  Total cap: 15 edges (~150 tokens worst-case).
        Returns empty string when no relations exist.
        """
        try:
            from engine.world import WorldManager
            wm   = WorldManager(self.session, current_state)
            rows = wm.list_all_relations()
        except Exception:
            return ""
        if not rows:
            return ""

        party_keys = {c.name.lower() for c in (party or [])}
        npc_keys   = {k.lower() for k in (current_state.relationships or {})}
        relevant   = set(party_keys) | npc_keys

        # Build label lookup
        orgs_dict  = current_state.organizations or {}
        label = {}
        for o in orgs_dict.values():
            label[o['name'].lower()] = o['name']
        for k in (current_state.relationships or {}):
            label[k.lower()] = k
        for c in (party or []):
            label[c.name.lower()] = c.name

        def _lbl(key):
            return label.get(key, key.title())

        selected  = []
        org_org   = []
        for r in sorted(rows, key=lambda x: -abs(x.strength)):
            sk, tk = r.source_key, r.target_key
            is_relevant = sk in relevant or tk in relevant
            is_org_org  = r.source_type == 'org' and r.target_type == 'org'
            if is_relevant:
                selected.append(r)
            elif is_org_org and len(org_org) < 5:
                org_org.append(r)
            if len(selected) >= 10:
                break

        combined = (selected + org_org)[:15]
        if not combined:
            return ""

        lines = ["Entity relations:"]
        for r in combined:
            colour = '+' if r.strength >= 0 else ''
            lines.append(
                f"  - {_lbl(r.source_key)} —[{r.relation_type} {colour}{r.strength}]→ {_lbl(r.target_key)}"
            )
        return "\n".join(lines) + "\n"

    def _format_session_memory(self, current_state):
        """Format the last N turns from the sliding window as readable text."""
        memory = current_state.session_memory or []
        if not memory:
            return "(No prior turns in this session)"
        # Build lookup tables to resolve entity keys → display names
        rels_keys  = current_state.relationships or {}
        orgs_dict  = current_state.organizations  or {}

        def _resolve_key(key):
            # "npc:village elder" → "Village Elder"
            # "org:iron vanguard" → org display name or title-cased key
            # legacy plain name   → return as-is
            if key.startswith('npc:'):
                name = key[4:]
                # prefer the proper_name stored in relationships if available
                rel = rels_keys.get(name) or rels_keys.get(name.title()) or {}
                return rel.get('proper_name') or name.title()
            if key.startswith('org:'):
                ok = key[4:]
                org = orgs_dict.get(ok, {})
                return org.get('name') or ok.title()
            return key  # backward-compat: old entries stored plain names

        lines = []
        for entry in memory[-config.SESSION_MEMORY_WINDOW:]:
            location = entry.get('location', '')
            chars    = entry.get('characters_present') or []
            orgs     = entry.get('organizations_mentioned') or []
            offered  = entry.get('offered_choices') or entry.get('choices') or []
            unchosen = entry.get('unchosen_choices') or []

            line = f"Turn {entry.get('turn', '?')}: "
            if location:
                line += f"[{location}] "
            if chars:
                line += f"Present: {', '.join(_resolve_key(k) for k in chars)}. "
            if orgs:
                line += f"Orgs: {', '.join(_resolve_key(k) for k in orgs)}. "
            line += f"Player: {entry.get('player_action', '')} → {entry.get('outcome', '')}"
            if unchosen:
                line += f" | 未選: {'; '.join(unchosen)}"
            lines.append(line)
        return "\n".join(lines)

    def _update_session_memory(self, current_state, player_action, narrative, outcome_label,
                               choices=None, location=None, characters_present=None,
                               organizations_mentioned=None, scene_type=None):
        """
        Append this turn to session memory and enforce the sliding window.

        Fields recorded per turn:
          player_action          — what the player chose to do (the selected action)
          unchosen_choices       — options offered last turn that were NOT selected
          offered_choices        — new options offered this turn
          location               — current location at the time of the turn
          characters_present     — entity keys of NPCs in scene:
                                   "npc:{name.lower()}" — max 6 entries
          organizations_mentioned— entity keys of orgs mentioned: "org:{key}" — max 3

        When turns are trimmed (overflow), summarize them via LLM and store
        the summary as a 'chapter_summary' in world_lore RAG so long-term
        story continuity is never completely lost  (Section 3.1).
        """
        memory      = list(current_state.session_memory or [])
        turn_number = (current_state.turn_count or 0) + 1

        # Unchosen choices = what was offered last turn minus what the player just picked
        prev_offered = []
        if memory:
            last = memory[-1]
            prev_offered = last.get('offered_choices') or last.get('choices') or []
        action_stripped = (player_action or '').strip()
        unchosen_choices = [
            c for c in prev_offered
            if c and c.strip() != action_stripped
        ]

        memory.append({
            "turn":                    turn_number,
            "player_action":           player_action,
            "narrative":               narrative[:200],
            "outcome":                 outcome_label,
            "unchosen_choices":        unchosen_choices,
            "offered_choices":         choices or [],
            "location":                location or "",
            "characters_present":      (characters_present or [])[:6],
            "organizations_mentioned": (organizations_mentioned or [])[:3],
            "scene_type":              scene_type or "",
        })

        if len(memory) > config.SESSION_MEMORY_WINDOW:
            overflow = memory[:-config.SESSION_MEMORY_WINDOW]
            memory   = memory[-config.SESSION_MEMORY_WINDOW:]
            # Summarize the discarded turns and store in world_lore RAG
            try:
                summary = self.llm.summarize_memory_segment(
                    overflow,
                    language=current_state.language or 'English',
                )
                summary_id = f"chapter_{current_state.id}_{turn_number}"
                self.rag.add_world_lore(
                    f"[Chapter Summary — turns {overflow[0]['turn']}–{overflow[-1]['turn']}] "
                    f"{summary}",
                    summary_id,
                    metadata={"type": "chapter_summary", "source": "memory_overflow"},
                )
            except Exception as e:
                print(f"Memory summarization error: {e}")

        current_state.session_memory = memory
        current_state.turn_count     = turn_number
        flag_modified(current_state, 'session_memory')
        self.session.commit()

    def _extract_and_register_relations(self, narrative, current_state, world,
                                         party, turn_number=None):
        """
        Call LLMClient.extract_relations on the narrative, then persist any new
        edges via WorldManager.upsert_relation.

        Builds the known-entity lists from organizations already in the DB and
        characters in the current party + NPCs tracked in relationships.
        Wrapped in try/except — never breaks the game loop.
        """
        if not narrative:
            return
        if turn_number is None:
            turn_number = current_state.turn_count or 0
        try:
            known_orgs  = [o['name'] for o in world.list_organizations()]
            # Party characters
            char_names  = [c.name for c in (party or [])]
            # Known NPCs from relationships dict
            npc_names   = list((current_state.relationships or {}).keys())
            known_chars = char_names + [n for n in npc_names if n not in char_names]

            edges = self.llm.extract_relations(
                narrative_text=narrative,
                known_orgs=known_orgs,
                known_chars=known_chars,
                language=current_state.language or 'English',
                turn_number=turn_number,
            )
            for edge in edges:
                world.upsert_relation(
                    source_type=edge['source_type'],
                    source_key=edge['source_key'],
                    target_type=edge['target_type'],
                    target_key=edge['target_key'],
                    relation_type=edge['relation_type'],
                    strength=edge.get('strength', 0),
                    description=edge.get('description', ''),
                    since_turn=edge.get('since_turn', turn_number),
                )
        except Exception as e:
            print(f"Relation extraction error: {e}")

    def _extract_and_register_organizations(self, narrative, current_state, world, turn_number=None):
        """
        Call LLMClient.extract_organizations on the narrative, then persist any
        new organizations via WorldManager.register_organization.

        Also back-fills description/history for any tracked organization that
        still has no description — limited to 2 back-fills per call to bound
        LLM overhead (mirrors the NPC back-fill in _auto_register_npcs).

        Runs in a try/except so an LLM error never breaks the game loop.
        """
        if not narrative:
            return
        if turn_number is None:
            turn_number = current_state.turn_count or 0
        try:
            orgs = self.llm.extract_organizations(
                narrative_text=narrative,
                world_context=current_state.world_context or '',
                language=current_state.language or 'English',
                turn_number=turn_number,
            )
            for org in orgs:
                world.register_organization(org)
        except Exception as e:
            print(f"Organization extraction error: {e}")

        # Back-fill: generate description for any org registered without one
        back_fill = 0
        for key, org in list((current_state.organizations or {}).items()):
            if back_fill >= 2:
                break
            if not isinstance(org, dict):
                continue
            _ORG_IMPORTANT = ('description', 'history', 'type', 'founder',
                              'current_leader', 'headquarters')
            if all(org.get(f) for f in _ORG_IMPORTANT):
                continue
            try:
                profile = self.llm.generate_organization_profile(
                    org_name=org.get('name') or key,
                    world_context=current_state.world_context or '',
                    existing_org=org,
                    language=current_state.language or 'English',
                )
                world.register_organization(profile)
                back_fill += 1
            except Exception as e:
                print(f"Org back-fill profile error for {key!r}: {e}")

    def _seed_world_lore(self, current_state):
        """
        On the first turn of a new game, chunk the world_context string into
        the world_lore RAG collection.

        TaskingAI's key insight: breaking the world description into retrievable
        chunks means the LLM can look up specific world facts when relevant,
        instead of receiving the entire world context in every prompt.
        """
        lore = current_state.world_context or ""
        if not lore.strip():
            return
        import re
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', lore) if s.strip()]
        chunk_size = 3
        chunks = [' '.join(sentences[i:i + chunk_size])
                  for i in range(0, max(1, len(sentences)), chunk_size)]
        for idx, chunk in enumerate(chunks):
            lore_id = f"world_ctx_{current_state.id}_{idx}"
            try:
                self.rag.add_world_lore(chunk, lore_id,
                                        metadata={"type": "lore", "source": "world_context"})
            except Exception:
                pass  # already seeded (duplicate ID)

    def _generate_and_store_stat_block(self, entity_name, intent, current_state):
        """
        Generate and cache a stat block for a newly encountered entity.

        Infinite Monster Engine approach: given the action type and world context,
        produce a rule-compliant stat block and store it in:
          - game_rules RAG (for semantic retrieval)
          - known_entities DB column (for live HP tracking during combat)

        Numeric stats (hp/atk/def_stat) come from the rule-engine lookup table;
        the LLM only writes the text fields (description, skills, special_ability, loot).
        """
        action_type = intent.get('action_type', 'direct_action')
        entity_type = detect_entity_type(entity_name, action_type)
        # Fetch player level for tier-gap difficulty scaling
        from engine.game_state import Character as _CharModel
        _char_row = self.session.get(_CharModel, current_state.player_id)
        _player_level = (_char_row.level or 1) if _char_row else 1
        hp, atk, def_stat = get_entity_base_stats(
            entity_type, current_state.difficulty or 'Normal',
            entity_name=entity_name,
            player_level=_player_level,
        )

        stat_block = self.llm.generate_entity_stat_block(
            entity_name=entity_name,
            entity_type=entity_type,
            world_context=current_state.world_context,
            language=current_state.language or 'English',
            base_stats=(hp, atk, def_stat),
        )

        # Store in RAG for semantic retrieval
        skills_str = ', '.join(stat_block.get('skills', [])) or 'none'
        loot_str   = ', '.join(stat_block.get('loot', []))   or 'none'
        stat_text  = (
            f"Entity: {stat_block['name']} ({stat_block['type']}). "
            f"HP: {stat_block['hp']}, ATK: {stat_block['atk']}, DEF: {stat_block['def_stat']}. "
            f"Skills: {skills_str}. "
            f"{stat_block['description']} "
            f"Special: {stat_block['special_ability'] or 'none'}. "
            f"Drops: {loot_str}."
        )
        self.rag.add_entity_stat_block(entity_name, stat_text)

        # Store in known_entities for live HP tracking (Section 3.3)
        known = dict(current_state.known_entities or {})
        key = entity_name.lower()
        if key not in known:
            # Pull damage_dice and undead flag from monster roster if available
            roster_entry = get_monster_by_name(entity_name)
            damage_dice  = roster_entry.get('damage_dice', '1d6') if roster_entry else '1d6'
            is_undead    = roster_entry.get('undead', False) if roster_entry else False
            special_key  = (stat_block.get('special_ability') or
                            (roster_entry.get('special_ability') if roster_entry else None) or '')
            known[key] = {
                'type':            stat_block.get('type', entity_type),
                'hp':              stat_block.get('hp', 20),
                'max_hp':          stat_block.get('hp', 20),
                'atk':             stat_block.get('atk', 5),
                'def_stat':        stat_block.get('def_stat', 5),
                'damage_dice':     damage_dice,
                'undead':          is_undead,
                'skills':          stat_block.get('skills', []),
                'special_ability': special_key,
                'description':     stat_block.get('description', entity_name),
                'loot':            stat_block.get('loot', roster_entry.get('loot', []) if roster_entry else []),
                'alive':           True,
                'status_effects':  [],
            }
            current_state.known_entities = known
            flag_modified(current_state, 'known_entities')
            self.session.commit()

    # ------------------------------------------------------------------
    # Multi-enemy spawn helpers
    # ------------------------------------------------------------------

    # Regex: "3 goblins", "goblin x3", "two skeletons", "a pair of wolves"
    _MULTI_RE = re.compile(
        r'^(?:(\d+)\s+(.+?)|(.+?)\s+[xX×](\d+)|'
        r'(two|three|four|five|six)\s+(.+?))s?$',
        re.I,
    )
    _WORD_NUMS = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}

    def _parse_multi_enemy_count(self, target_str):
        """
        Parse a target string for a count > 1.
        Returns (count, base_name) or (1, target_str) for singles.
        """
        m = self._MULTI_RE.match(target_str.strip())
        if not m:
            return 1, target_str
        if m.group(1) and m.group(2):           # "3 goblins"
            return int(m.group(1)), m.group(2).rstrip('s')
        if m.group(3) and m.group(4):           # "goblin x3"
            return int(m.group(4)), m.group(3).rstrip('s')
        if m.group(5) and m.group(6):           # "two skeletons"
            return self._WORD_NUMS[m.group(5).lower()], m.group(6).rstrip('s')
        return 1, target_str

    def _spawn_multi_enemies(self, base_name, count, intent, current_state):
        """
        Register `count` instances of `base_name` in known_entities.
        Keys: base_name_1, base_name_2, … (or just base_name when count==1).
        Returns the list of keys that were newly registered.
        """
        keys = []
        for i in range(1, count + 1):
            key = f"{base_name.lower()}_{i}" if count > 1 else base_name.lower()
            if not (current_state.known_entities or {}).get(key):
                # Temporarily set intent target to key so stat block is generated
                fake_intent = dict(intent)
                fake_intent['target'] = key
                self._generate_and_store_stat_block(key, fake_intent, current_state)
            keys.append(key)
        return keys

    # ------------------------------------------------------------------
    # Phase 2: Loot drop + XP grant
    # ------------------------------------------------------------------

    def _grant_loot_and_xp(self, entity_key, entity_entry, character, char_logic,
                           current_state):
        """
        Called when an enemy is killed.  Grants:
          - Monster XP to the character (from roster or fallback formula).
          - Random loot items rolled from the monster's loot table (50 % each).
          - Level-up if XP threshold crossed (stat bumps applied automatically).

        Returns a dict {xp_gained, loot_dropped, leveled_up, new_level}.
        """
        roster   = get_monster_by_name(entity_key)
        xp_gain  = roster.get('xp', 0) if roster else 0
        if xp_gain == 0:
            xp_gain = max(10, (entity_entry.get('max_hp') or 20) * 2)

        # Scale XP by difficulty multiplier
        diff_key = (current_state.difficulty or 'normal').lower()
        xp_mult  = DIFFICULTY_REWARD.get(diff_key, DIFFICULTY_REWARD['normal'])['xp_mult']
        xp_gain  = max(1, int(xp_gain * xp_mult))

        old_level = character.level or 1
        character.xp = (character.xp or 0) + xp_gain

        new_level  = compute_level(character.xp)
        leveled_up = new_level > old_level
        if leveled_up:
            character.level = new_level
            levels_gained   = new_level - old_level
            # Baseline auto-bump: +5 HP and +3 MP per level (survival minimum)
            character.max_hp = (character.max_hp or 100) + 5 * levels_gained
            character.hp     = min(character.max_hp, (character.hp or 0) + 5 * levels_gained)
            character.max_mp = (character.max_mp or 50)  + 3 * levels_gained
            # Grant 2 free stat points per level for player-directed allocation
            character.pending_stat_points = (
                (character.pending_stat_points or 0) + 2 * levels_gained
            )

        loot_dropped = roll_loot(entity_entry, self.dice,
                                 difficulty=current_state.difficulty or 'normal')
        for item_name in loot_dropped:
            char_logic.add_item({'name': item_name, 'source': entity_key})

        # Roll direct gold drop from the defeated enemy
        gold_source  = roster if roster else entity_entry
        gold_gained  = roll_combat_gold(gold_source, self.dice,
                                        difficulty=current_state.difficulty or 'normal')
        if gold_gained > 0:
            character.gold = (character.gold or 0) + gold_gained

        self.session.commit()
        return {
            'xp_gained':    xp_gain,
            'xp_mult':      xp_mult,
            'loot_dropped': loot_dropped,
            'gold_gained':  gold_gained,
            'leveled_up':   leveled_up,
            'new_level':    new_level,
        }

    def _apply_death_penalty(self, character, char_logic, current_state):
        """
        Apply difficulty-scaled penalties when the player character dies (HP → 0).

        Returns a summary dict for UI display:
          {gold_lost, xp_lost, item_dropped, difficulty}
        """
        diff_key = (current_state.difficulty or 'normal').lower()
        penalty  = DIFFICULTY_DEATH_PENALTY.get(diff_key,
                                                DIFFICULTY_DEATH_PENALTY['normal'])
        gold_lost    = 0
        xp_lost      = 0
        item_dropped = None

        # Gold loss
        if penalty['gold_loss_pct'] > 0 and (character.gold or 0) > 0:
            gold_lost = max(1, int((character.gold or 0) * penalty['gold_loss_pct']))
            character.gold = max(0, (character.gold or 0) - gold_lost)

        # XP loss — only removes XP earned above the current level floor
        # so the player can never be pushed below their current level.
        if penalty['xp_loss_pct'] > 0 and (character.xp or 0) > 0:
            current_level  = character.level or 1
            level_floor_xp = xp_for_level(current_level)   # XP needed for this level
            xp_above_floor = max(0, (character.xp or 0) - level_floor_xp)
            xp_lost = max(0, int(xp_above_floor * penalty['xp_loss_pct']))
            character.xp = max(level_floor_xp, (character.xp or 0) - xp_lost)

        # Item drop — one random inventory item lost (Deadly only)
        if penalty['drop_item'] and char_logic.model.inventory:
            inv = list(char_logic.model.inventory)
            drop_idx  = self.dice.roll(f'1d{len(inv)}')[2] - 1
            drop_idx  = max(0, min(drop_idx, len(inv) - 1))
            dropped   = inv[drop_idx]
            item_dropped = dropped.get('name', str(dropped)) if isinstance(dropped, dict) else str(dropped)
            char_logic.remove_item(item_dropped)

        return {
            'difficulty':    diff_key,
            'gold_lost':     gold_lost,
            'xp_lost':       xp_lost,
            'item_dropped':  item_dropped,
        }

    # ------------------------------------------------------------------
    # Phase 3: Combat state tracking
    # ------------------------------------------------------------------

    def _update_combat_state(self, intent, combat_result, current_state,
                             flee_result=None):
        """
        Maintain GameState.in_combat and reset once-per-combat ability cooldowns.
        Sets in_combat=True on attack; clears when all enemies die or player flees.

        flee_result — already-resolved flee dict from Step 3.5 (or None).
        """
        was_in_combat = bool(current_state.in_combat)
        action_type   = intent.get('action_type', '')

        # Flee was resolved in Step 3.5; only clear combat if it succeeded.
        if flee_result is not None:
            if flee_result['fled']:
                current_state.in_combat = 0
                self._used_abilities.clear()
            self.session.commit()
            return

        if action_type == 'attack' and combat_result:
            if not was_in_combat:
                self._used_abilities.clear()
            current_state.in_combat = 1

        if current_state.in_combat:
            known = current_state.known_entities or {}
            living_enemies = [
                e for k, e in known.items()
                if not k.startswith('_') and isinstance(e, dict)
                and e.get('type') in ('monster', 'boss', 'guard')
                and e.get('alive', True)
            ]
            if not living_enemies:
                current_state.in_combat = 0
                self._used_abilities.clear()

        self.session.commit()

    # ------------------------------------------------------------------
    # Phase 4: Monster active AI — HP-triggered specials & archetypes
    # ------------------------------------------------------------------

    def _apply_monster_ai_triggers(self, entity_key, entity_entry, current_state):
        """
        Evaluate triggered special abilities before the monster's counter-attack.
        Returns the (possibly mutated) entity entry.
        """
        from data.monsters import SPECIAL_ABILITIES
        special_key = entity_entry.get('special_ability', '')
        defn        = SPECIAL_ABILITIES.get(special_key or '')
        if not defn:
            return entity_entry

        known = dict(current_state.known_entities or {})
        entry = dict(known.get(entity_key, entity_entry))
        hp    = entry.get('hp', 0)
        max_hp = max(entry.get('max_hp', 1) or 1, 1)

        # Berserk: +ATK when below 50 % HP (one-time activation)
        if special_key == 'berserk' and not entry.get('berserk_active'):
            if hp / max_hp <= 0.5:
                entry['berserk_active'] = True
                entry['atk'] = (entry.get('atk') or 5) + defn.get('value', 3)

        # Luring Song: charm attempt once per encounter
        if special_key == 'luring_song' and not entry.get('song_used'):
            entry['song_used'] = True
            if self.dice.roll('1d20')[2] < defn.get('dc', 13):
                self.combat.apply_status_to_player('charmed', current_state)
                flag_modified(current_state, 'known_entities')

        known[entity_key] = entry
        current_state.known_entities = known
        return entry

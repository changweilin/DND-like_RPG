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
        self.llm  = llm_client
        self.rag  = rag_system
        self.session = db_session
        self.dice = DiceRoller()

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

        # --- Step 3: Parse intent — rule engine first, LLM fallback ---
        # Rule engine handles clear keyword patterns (attack, magic, stealth, social …)
        # without an LLM call.  Ambiguous or complex inputs fall back to the LLM's
        # Guided Thinking parser (One Trillion and One Nights approach).
        intent = _rule_parse_intent(
            player_action,
            known_entities=current_state.known_entities or {},
            difficulty=current_state.difficulty or 'Normal',
        )
        if intent is None:
            game_context_summary = (
                f"Character: {character.name}, {character.race} {character.char_class}. "
                f"HP: {character.hp}/{character.max_hp}, MP: {character.mp}/{character.max_mp}. "
                f"Location: {current_state.current_location}. "
                f"Difficulty: {current_state.difficulty}."
            )
            intent = self.llm.parse_intent(player_action, game_context_summary)

        # --- Step 4: Dynamic entity stat block (Infinite Monster Engine) ---
        target = intent.get('target', '').strip()
        if target and not self.rag.entity_stat_block_exists(target):
            self._generate_and_store_stat_block(target, intent, current_state)

        # --- Step 5: Combat rule engine (Section 3.3) ---
        # Fully deterministic: attack roll → hit/miss → damage roll → net damage.
        # The LLM is never asked to adjudicate combat — it only narrates the result.
        combat_result = None
        if intent.get('action_type') == 'attack' and target:
            combat_result = self._resolve_combat(character, char_logic, target, current_state)
            if combat_result['hit'] and combat_result['net_damage'] > 0:
                self._apply_combat_damage_to_entity(target, combat_result['net_damage'], current_state)

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
        )

        # --- Step 7: Render Narrative Event (LLM Phase 2) ---
        session_memory_text = self._format_session_memory(current_state)
        system_prompt       = self._build_system_prompt(character, current_state, all_chars)

        thought = intent.get('thought_process', '')
        outcome_parts = [f"Player action: {player_action}"]
        if thought:
            outcome_parts.append(f"Action analysis: {thought}")

        # Inject combat hard facts so the LLM narrates from them, never invents them
        if combat_result:
            outcome_parts.append(
                f"Combat: {character.name} attacks {target}. "
                f"Attack roll: {combat_result['attack_roll']} + {combat_result['atk_modifier']} "
                f"= {combat_result['attack_total']} vs DEF {combat_result['target_def']}. "
                f"{'HIT' if combat_result['hit'] else 'MISS'}."
            )
            if combat_result['hit']:
                outcome_parts.append(
                    f"Damage roll: {combat_result['damage_notation']} "
                    f"= {combat_result['raw_damage']} "
                    f"(net after DEF reduction: {combat_result['net_damage']})."
                )
                if combat_result.get('critical'):
                    outcome_parts.append("CRITICAL HIT — doubled dice damage!")
                entity_hp = combat_result.get('entity_hp_remaining')
                if entity_hp is not None:
                    if entity_hp <= 0:
                        outcome_parts.append(f"{target} is DEFEATED (HP reduced to 0).")
                    else:
                        outcome_parts.append(f"{target} HP remaining: {entity_hp}.")
            outcome_label = "CRITICAL SUCCESS" if combat_result.get('critical') else (
                "SUCCESS" if combat_result['hit'] else "FAILURE"
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
            mech_parts.append(f"player takes {mechanics['damage_taken']} raw damage")
        if mechanics['hp_healed'] > 0:
            mech_parts.append(f"player recovers {mechanics['hp_healed']} HP")
        if mechanics['mp_used'] > 0:
            mech_parts.append(f"player expends {mechanics['mp_used']} MP")
        if mech_parts:
            outcome_parts.append(
                "Mechanical outcomes (hard facts — narrate these): " + "; ".join(mech_parts) + "."
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

        # Build a mutable set for dedup throughout the three supplement passes below.
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
        damage_taken = mechanics['damage_taken']
        hp_healed    = mechanics['hp_healed']
        mp_used      = mechanics['mp_used']

        if damage_taken:
            char_logic.take_damage(damage_taken)
        if hp_healed:
            char_logic.heal(hp_healed)
        if mp_used:
            char_logic.use_mp(mp_used)
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

    def _resolve_combat(self, character, char_logic, target_name, current_state):
        """
        Full deterministic combat resolution:
          1. Attack roll: 1d20 + ATK modifier vs target DEF
          2. On hit: damage roll (class weapon dice + ATK modifier)
          3. Critical (raw 20): double the dice component
          4. Net damage after target DEF reduction

        Returns a combat_result dict with all details for narrative injection.
        """
        known = (current_state.known_entities or {})
        target_entry = known.get(target_name.lower(), {})
        target_def   = target_entry.get('def_stat', 10)

        atk_modifier   = (character.atk - 10) // 2
        attack_roll    = self.dice.roll('1d20')[2]  # raw d20 value (no modifier yet)
        raw_d20        = attack_roll
        attack_total   = raw_d20 + atk_modifier
        critical       = raw_d20 == 20
        hit            = attack_total >= target_def

        damage_notation = char_logic.get_weapon_damage_notation()
        raw_damage = 0
        net_damage = 0
        entity_hp_remaining = None

        if hit:
            rolls, mod, total = self.dice.roll(damage_notation)
            if critical:
                # Double the dice component (not the modifier) for critical hits
                dice_sum = sum(rolls)
                raw_damage = dice_sum * 2 + mod
            else:
                raw_damage = total
            net_damage = max(0, raw_damage - (target_def // 2))
            entity_hp_remaining = target_entry.get('hp', None)
            if entity_hp_remaining is not None:
                entity_hp_remaining = max(0, entity_hp_remaining - net_damage)

        return {
            'target':             target_name,
            'target_def':         target_def,
            'atk_modifier':       atk_modifier,
            'attack_roll':        raw_d20,
            'attack_total':       attack_total,
            'critical':           critical,
            'hit':                hit,
            'damage_notation':    damage_notation,
            'raw_damage':         raw_damage,
            'net_damage':         net_damage,
            'entity_hp_remaining': entity_hp_remaining,
        }

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
            if not isinstance(data, dict) or name in party_names or data.get('biography'):
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
                             player_action, character, current_state):
        """
        Rule engine for damage_taken, hp_healed, mp_used.

        Replaces the LLM's role in deciding these mechanical values so that
        stat mutations are always deterministic (neuro-symbolic design principle).

        Rules:
          damage_taken — enemy counter-attack after player's attack (if target alive);
                         minor fall/trap damage on critical_failure of a physical skill.
          hp_healed    — healing keywords or medicine skill + successful dice outcome.
          mp_used      — any magic-type action (regardless of outcome).

        Returns a dict {damage_taken, hp_healed, mp_used}.
        """
        action_type = intent.get('action_type', 'direct_action')
        skill       = intent.get('skill', '').lower()
        outcome     = dice_result['outcome'] if dice_result else None

        damage_taken = 0
        hp_healed    = 0
        mp_used      = 0

        # --- Enemy counter-attack (after player's attack) ---
        if combat_result:
            target_name = combat_result.get('target', '').lower()
            known = current_state.known_entities or {}
            target_entry = known.get(target_name, {})
            # enemy HP remaining after player's hit (None if not in known_entities)
            hp_after = combat_result.get('entity_hp_remaining')
            is_alive = target_entry.get('alive', True) and (hp_after is None or hp_after > 0)
            if is_alive:
                enemy_atk     = target_entry.get('atk', 5)
                enemy_atk_mod = (enemy_atk - 10) // 2
                enemy_roll    = self.dice.roll('1d20')[2]
                if enemy_roll == 20 or enemy_roll + enemy_atk_mod >= character.def_stat:
                    # raw hit — CharacterLogic.take_damage() applies DEF reduction
                    raw_hit      = self.dice.roll('1d6')[2] + max(0, enemy_atk_mod)
                    damage_taken = max(0, raw_hit)

        # --- Physical-skill critical failure → minor fall/hazard damage ---
        elif dice_result and skill in _PHYSICAL_SKILLS and outcome == 'critical_failure':
            damage_taken = self.dice.roll('1d4')[2]

        # --- Magic action → MP cost (even on failure) ---
        if action_type == 'magic':
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
                # healing magic burns MP even if action_type wasn't flagged 'magic'
                if mp_used == 0 and character.mp > 0:
                    mp_used = min(2, character.mp)

        return {'damage_taken': damage_taken, 'hp_healed': hp_healed, 'mp_used': mp_used}

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
            f"ATK: {character.atk}  DEF: {character.def_stat}.\n"
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
        hp, atk, def_stat = get_entity_base_stats(
            entity_type, current_state.difficulty or 'Normal'
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
            known[key] = {
                'type':            stat_block.get('type', entity_type),
                'hp':              stat_block.get('hp', 20),
                'max_hp':          stat_block.get('hp', 20),
                'atk':             stat_block.get('atk', 5),
                'def_stat':        stat_block.get('def_stat', 5),
                'skills':          stat_block.get('skills', []),
                'special_ability': stat_block.get('special_ability', ''),
                'description':     stat_block.get('description', entity_name),
                'loot':            stat_block.get('loot', []),
                'alive':           True,
            }
            current_state.known_entities = known
            flag_modified(current_state, 'known_entities')
            self.session.commit()

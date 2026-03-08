import time
from sqlalchemy.orm.attributes import flag_modified
from engine.dice import DiceRoller
from engine.character import CharacterLogic
from engine.world import WorldManager
from engine.config import config

# Human-readable labels for the rule engine's outcome codes
_OUTCOME_LABELS = {
    'critical_success': 'CRITICAL SUCCESS',
    'success':          'SUCCESS',
    'failure':          'FAILURE',
    'critical_failure': 'CRITICAL FAILURE',
}

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

    def process_turn(self, player_action, current_state, character):
        """
        Run one full turn and return:
            narrative   (str)        — story text to display
            choices     (list[str])  — suggested next actions
            turn_data   (dict)       — full Narrative Event dict (scene_type, mechanics …)
            dice_result (dict|None)  — dice roll details, or None if no roll occurred
        """
        char_logic = CharacterLogic(self.session, character)
        world      = WorldManager(self.session, current_state)

        # --- Step 1: Retrieve long-term context from RAG ---
        rag_context = self.rag.retrieve_context(player_action)

        # --- Step 2: Seed world lore on the very first turn (TaskingAI style) ---
        if (current_state.turn_count or 0) == 0 and not self.rag.world_lore_seeded():
            self._seed_world_lore(current_state)

        # --- Step 3: Parse intent with Guided Thinking (One Trillion approach) ---
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

        # --- Step 7: Render Narrative Event (LLM Phase 2) ---
        session_memory_text = self._format_session_memory(current_state)
        system_prompt       = self._build_system_prompt(character, current_state)

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

        outcome_parts.append(f"Recent session history:\n{session_memory_text}")
        outcome_context = "\n".join(outcome_parts)

        turn_data = self.llm.render_narrative(system_prompt, outcome_context, rag_context)

        narrative = turn_data.get('narrative', "The DM stares blankly into space...")
        choices   = turn_data.get('choices', ["Look around", "Wait"])

        # --- Step 8: Apply deterministic mechanics ---
        if turn_data.get('damage_taken'):
            char_logic.take_damage(turn_data['damage_taken'])
        if turn_data.get('hp_healed'):
            char_logic.heal(turn_data['hp_healed'])
        if turn_data.get('mp_used'):
            char_logic.use_mp(turn_data['mp_used'])
        for item in (turn_data.get('items_found') or []):
            char_logic.add_item({'name': item} if isinstance(item, str) else item)
        if turn_data.get('location_change'):
            world.update_location(turn_data['location_change'])
        for npc, delta in (turn_data.get('npc_relationship_changes') or {}).items():
            world.update_relationship(npc, delta)

        # --- Step 9: NPC generative agent reactions (Section 3.5) ---
        # After social turns or turns that touched NPC relationships, let each
        # NPC update their own goal and emotional state independently.
        scene_type = turn_data.get('scene_type', 'exploration')
        npc_changes = turn_data.get('npc_relationship_changes') or {}
        if scene_type == 'social' or npc_changes:
            self._evaluate_npc_reactions(narrative, current_state, world)

        # --- Step 10a: Update sliding-window session memory ---
        self._update_session_memory(current_state, player_action, narrative, outcome_label)

        # --- Step 10b: Persist Narrative Event to RAG long-term memory ---
        event_id  = f"event_{character.id}_{current_state.id}_{int(time.time() * 1000)}"
        scene_tag = scene_type.upper()
        self.rag.add_story_event(
            f"[{scene_tag}] Player: {player_action}\nDM: {narrative}",
            event_id=event_id,
        )

        return narrative, choices, turn_data, dice_result

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

    def _evaluate_npc_reactions(self, narrative, current_state, world):
        """
        Ask the LLM how tracked NPCs react to the narrative event independently.
        Only runs for social scenes or turns that already touched NPC relationships.
        Updates each changed NPC's affinity, state, and goal in the DB.
        """
        rels = current_state.relationships or {}
        if not rels:
            return
        # Only pass NPC dicts (skip legacy flat integers)
        npc_states = {
            name: data for name, data in rels.items()
            if isinstance(data, dict)
        }
        if not npc_states:
            return
        try:
            reactions = self.llm.evaluate_npc_reactions(
                event_summary=narrative[:500],
                npc_states=npc_states,
                language=current_state.language or 'English',
            )
            for npc_name, changes in reactions.items():
                if not isinstance(changes, dict):
                    continue
                world.update_relationship(
                    npc_name,
                    changes.get('affinity_delta', 0),
                    state=changes.get('state'),
                    goal=changes.get('goal'),
                )
        except Exception as e:
            print(f"NPC reaction step error: {e}")

    # ------------------------------------------------------------------
    # Internal helpers — prompts and memory
    # ------------------------------------------------------------------

    def _build_system_prompt(self, character, current_state):
        npc_context   = self._format_npc_state(current_state)
        world_context = self._format_world_setting(current_state)
        return (
            f"{world_context}"
            f"The player is {character.name}, a {character.race} {character.char_class}.\n"
            f"{config.get_world_setting(getattr(current_state, 'world_setting', None) or 'dnd5e')['term_map']['hp_name']}: "
            f"{character.hp}/{character.max_hp}  "
            f"{config.get_world_setting(getattr(current_state, 'world_setting', None) or 'dnd5e')['term_map']['mp_name']}: "
            f"{character.mp}/{character.max_mp}  "
            f"ATK: {character.atk}  DEF: {character.def_stat}.\n"
            f"Location: {current_state.current_location}.\n"
            f"World lore: {current_state.world_context}\n"
            f"Difficulty: {current_state.difficulty}\n"
            f"{npc_context}"
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
            if isinstance(data, dict):
                affinity = data.get('affinity', 0)
                state    = data.get('state', 'Neutral')
                goal     = data.get('goal', '')
                line = f"  - {name}: {state} ({affinity:+d})"
                if goal:
                    line += f", goal: {goal}"
                lines.append(line)
        return "\n".join(lines) + "\n"

    def _format_session_memory(self, current_state):
        """Format the last N turns from the sliding window as readable text."""
        memory = current_state.session_memory or []
        if not memory:
            return "(No prior turns in this session)"
        lines = []
        for entry in memory[-config.SESSION_MEMORY_WINDOW:]:
            lines.append(
                f"Turn {entry.get('turn', '?')}: "
                f"Player: {entry.get('player_action', '')} "
                f"→ {entry.get('outcome', '')}"
            )
        return "\n".join(lines)

    def _update_session_memory(self, current_state, player_action, narrative, outcome_label):
        """
        Append this turn to session memory and enforce the sliding window.

        When turns are trimmed (overflow), summarize them via LLM and store
        the summary as a 'chapter_summary' in world_lore RAG so long-term
        story continuity is never completely lost  (Section 3.1).
        """
        memory      = list(current_state.session_memory or [])
        turn_number = (current_state.turn_count or 0) + 1

        memory.append({
            "turn":          turn_number,
            "player_action": player_action,
            "narrative":     narrative[:200],   # trimmed to keep token budget bounded
            "outcome":       outcome_label,
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
        """
        action_type = intent.get('action_type', 'direct_action')
        entity_type = 'monster' if action_type == 'attack' else 'npc'

        stat_block = self.llm.generate_entity_stat_block(
            entity_name=entity_name,
            entity_type=entity_type,
            world_context=current_state.world_context,
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

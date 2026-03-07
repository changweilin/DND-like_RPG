import time
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
          on first encounter and cached in the game_rules RAG collection.

      TaskingAI D&D Game Master:
        — World context and basic rules are pre-seeded into RAG so the
          LLM can retrieve exact facts instead of hallucinating them.

    Turn flow — 8 steps:
      1. RAG retrieval (long-term semantic context)
      2. World lore seeding (first turn only)
      3. parse_intent — Guided Thinking → structured intent
      4. Dynamic entity stat block generation (new targets only)
      5. Dice roll + rule engine (deterministic)
      6. render_narrative — receives hard mechanical facts
      7. Apply mechanics (HP/MP/items/location/relationships)
      8. Update session memory + persist to RAG
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
        # The world_context stored in SQLite is injected into RAG so future
        # turns can retrieve relevant world facts via semantic search rather
        # than always including the entire blob in the prompt.
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
        # If the intent targets a named entity we haven't seen before, generate
        # its stat block once and store it in game_rules RAG for consistency.
        target = intent.get('target', '').strip()
        if target and not self.rag.entity_stat_block_exists(target):
            self._generate_and_store_stat_block(target, intent, current_state)

        # --- Step 5: Dice roll + rule engine (fully deterministic) ---
        dice_result   = None
        outcome_label = "NO_ROLL"

        if intent.get('requires_roll') and intent.get('dc', 0) > 0:
            skill    = intent.get('skill', '')
            modifier = char_logic.get_skill_modifier(skill) if skill else 0
            dice_result   = self.dice.roll_skill_check(dc=intent['dc'], modifier=modifier)
            outcome_label = _OUTCOME_LABELS[dice_result['outcome']]

        # --- Step 6: Render Narrative Event (LLM Phase 2) ---
        session_memory_text = self._format_session_memory(current_state)
        system_prompt       = self._build_system_prompt(character, current_state)

        # Build the structured outcome context the narrative renderer receives.
        # The LLM reads these as hard facts and writes prose around them.
        thought = intent.get('thought_process', '')
        outcome_parts = [f"Player action: {player_action}"]
        if thought:
            outcome_parts.append(f"Action analysis: {thought}")
        if dice_result:
            outcome_parts.append(
                f"Skill checked: {intent.get('skill', 'general')} vs DC {dice_result['dc']}"
            )
            outcome_parts.append(
                f"Dice roll: {dice_result['notation']} → "
                f"{dice_result['raw_roll']} + {dice_result['modifier']} "
                f"= {dice_result['total']} — {outcome_label}"
            )
        outcome_parts.append(f"Recent session history:\n{session_memory_text}")
        outcome_context = "\n".join(outcome_parts)

        turn_data = self.llm.render_narrative(system_prompt, outcome_context, rag_context)

        narrative = turn_data.get('narrative', "The DM stares blankly into space...")
        choices   = turn_data.get('choices', ["Look around", "Wait"])

        # --- Step 7: Apply deterministic mechanics ---
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

        # --- Step 8a: Update sliding-window session memory ---
        self._update_session_memory(current_state, player_action, narrative, outcome_label)

        # --- Step 8b: Persist Narrative Event to RAG long-term memory ---
        event_id = f"event_{character.id}_{current_state.id}_{int(time.time() * 1000)}"
        scene_tag = turn_data.get('scene_type', 'exploration').upper()
        self.rag.add_story_event(
            f"[{scene_tag}] Player: {player_action}\nDM: {narrative}",
            event_id=event_id,
        )

        return narrative, choices, turn_data, dice_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self, character, current_state):
        return (
            "You are a creative and strict Dungeon Master for a text RPG.\n"
            f"The player is {character.name}, a {character.race} {character.char_class}.\n"
            f"HP: {character.hp}/{character.max_hp}, MP: {character.mp}/{character.max_mp}, "
            f"ATK: {character.atk}, DEF: {character.def_stat}.\n"
            f"Location: {current_state.current_location}.\n"
            f"World: {current_state.world_context}\n"
            f"Difficulty: {current_state.difficulty}\n\n"
            f"CRITICAL: Write ALL narrative and choices EXCLUSIVELY in "
            f"{current_state.language or 'English'}.\n"
            "Do NOT invent dice rolls or mechanical outcomes — "
            "those are provided to you as hard structured facts."
        )

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
        """Append this turn to session memory and enforce the sliding window."""
        memory      = list(current_state.session_memory or [])
        turn_number = (current_state.turn_count or 0) + 1

        memory.append({
            "turn":          turn_number,
            "player_action": player_action,
            "narrative":     narrative[:200],   # trimmed to keep token budget bounded
            "outcome":       outcome_label,
        })

        if len(memory) > config.SESSION_MEMORY_WINDOW:
            memory = memory[-config.SESSION_MEMORY_WINDOW:]

        current_state.session_memory = memory
        current_state.turn_count     = turn_number
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
        # Split on sentence boundaries for better chunk retrieval granularity
        import re
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', lore) if s.strip()]
        chunk_size = 3  # group sentences into small chunks
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
        produce a rule-compliant stat block and store it in game_rules RAG so
        every subsequent turn that mentions this entity retrieves consistent stats.
        """
        action_type = intent.get('action_type', 'direct_action')
        entity_type = 'monster' if action_type == 'attack' else 'npc'

        stat_block = self.llm.generate_entity_stat_block(
            entity_name=entity_name,
            entity_type=entity_type,
            world_context=current_state.world_context,
        )

        # Format as human-readable text for RAG storage
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

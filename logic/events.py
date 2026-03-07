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
    Orchestrates one full game turn using a neuro-symbolic approach:

      Step 1 — LLM (Intent Parser):  player's natural language → structured intent
      Step 2 — DiceRoller:           execute any required skill check (real randomness)
      Step 3 — Rule Engine:          dice + DC → deterministic outcome label
      Step 4 — LLM (Narrative Gen):  structured outcome → atmospheric story text

    The LLM never sees or invents dice rolls. All mechanical truth comes from
    the rule engine and is injected into the narrative prompt as hard facts.
    """

    def __init__(self, llm_client, rag_system, db_session):
        self.llm = llm_client
        self.rag = rag_system
        self.session = db_session
        self.dice = DiceRoller()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_turn(self, player_action, current_state, character):
        """
        Run one full turn and return:
            narrative   (str)   — story text to display
            choices     (list)  — suggested next actions
            turn_data   (dict)  — full narrative dict (mechanics, etc.)
            dice_result (dict|None) — dice roll details, or None if no roll occurred
        """
        char_logic = CharacterLogic(self.session, character)
        world      = WorldManager(self.session, current_state)

        # --- Step 1: Retrieve long-term context from RAG ---
        rag_context = self.rag.retrieve_context(player_action)

        # --- Step 2: Parse intent (LLM Phase 1) ---
        game_context_summary = (
            f"Character: {character.name}, {character.race} {character.char_class}. "
            f"HP: {character.hp}/{character.max_hp}, MP: {character.mp}/{character.max_mp}. "
            f"Location: {current_state.current_location}."
        )
        intent = self.llm.parse_intent(player_action, game_context_summary)

        # --- Step 3: Dice roll + rule engine (fully deterministic) ---
        dice_result  = None
        outcome_label = "NO_ROLL"

        if intent.get('requires_roll') and intent.get('dc', 0) > 0:
            skill    = intent.get('skill', '')
            modifier = char_logic.get_skill_modifier(skill) if skill else 0
            dice_result   = self.dice.roll_skill_check(dc=intent['dc'], modifier=modifier)
            outcome_label = _OUTCOME_LABELS[dice_result['outcome']]

        # --- Step 4: Build outcome context for narrative rendering ---
        session_memory_text = self._format_session_memory(current_state)
        system_prompt       = self._build_system_prompt(character, current_state)

        if dice_result:
            outcome_context = (
                f"Player action: {player_action}\n"
                f"Skill checked: {intent.get('skill', 'general')} vs DC {dice_result['dc']}\n"
                f"Dice roll: {dice_result['notation']} → "
                f"{dice_result['raw_roll']} + {dice_result['modifier']} "
                f"= {dice_result['total']} — {outcome_label}\n"
                f"Recent session history:\n{session_memory_text}"
            )
        else:
            outcome_context = (
                f"Player action: {player_action}\n"
                f"Recent session history:\n{session_memory_text}"
            )

        # --- Step 5: Render narrative (LLM Phase 2) ---
        turn_data = self.llm.render_narrative(system_prompt, outcome_context, rag_context)

        narrative = turn_data.get('narrative', "The DM stares blankly into space...")
        choices   = turn_data.get('choices', ["Look around", "Wait"])

        # --- Step 6: Apply deterministic mechanics ---
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

        # --- Step 7: Update sliding-window session memory ---
        self._update_session_memory(current_state, player_action, narrative, outcome_label)

        # --- Step 8: Persist turn to RAG long-term memory ---
        event_id = f"event_{character.id}_{current_state.id}_{int(time.time() * 1000)}"
        self.rag.add_story_event(
            f"Player: {player_action}\nDM: {narrative}",
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
            "those are provided to you as structured facts."
        )

    def _format_session_memory(self, current_state):
        """Return the last N turns from the sliding window as readable text."""
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
        """Append this turn to the session memory and enforce the sliding window."""
        memory      = list(current_state.session_memory or [])
        turn_number = (current_state.turn_count or 0) + 1

        memory.append({
            "turn":          turn_number,
            "player_action": player_action,
            "narrative":     narrative[:200],   # cap to keep token budget bounded
            "outcome":       outcome_label,
        })

        if len(memory) > config.SESSION_MEMORY_WINDOW:
            memory = memory[-config.SESSION_MEMORY_WINDOW:]

        current_state.session_memory = memory
        current_state.turn_count     = turn_number
        self.session.commit()

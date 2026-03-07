import json
import re
import ollama
from engine.config import config

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _repair_json(text):
    """Strip markdown fences and extract the outermost JSON object."""
    text = re.sub(r'```(?:json)?', '', text).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text

def _validated_intent(data):
    """Merge LLM output onto safe defaults for an intent dict."""
    defaults = {
        "action_type": "direct_action",  # direct_action|skill_check|attack|magic|social|explore
        "requires_roll": False,
        "skill": "",
        "dc": 10,
        "target": "",
        "summary": "",
    }
    for k in defaults:
        if k in data:
            defaults[k] = data[k]
    return defaults

def _validated_narrative(data):
    """Merge LLM output onto safe defaults for a narrative dict."""
    defaults = {
        "narrative": "Something happens...",
        "choices": ["Look around", "Wait"],
        "damage_taken": 0,
        "hp_healed": 0,
        "mp_used": 0,
        "items_found": [],
        "location_change": "",
        "npc_relationship_changes": {},
    }
    for k in defaults:
        if k in data:
            defaults[k] = data[k]
    return defaults

# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, model_name=None):
        self.model = model_name or config.LLM_MODEL_NAME

    # ------------------------------------------------------------------
    # Phase 1 — intent parsing (LLM as Intent Parser)
    # ------------------------------------------------------------------

    def parse_intent(self, player_action, game_context):
        """
        Parse the player's natural language into a structured intent dict.

        The rule engine uses this to decide whether a dice roll is needed,
        which skill applies, and what DC to roll against — without the LLM
        ever touching random numbers.

        Returns a validated intent dict with keys:
            action_type, requires_roll, skill, dc, target, summary
        """
        system_prompt = (
            "You are the intent parser for a TRPG rule engine.\n"
            "Classify the player's action as structured JSON. "
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema:\n"
            '{\n'
            '  "action_type": "<direct_action|skill_check|attack|magic|social|explore>",\n'
            '  "requires_roll": <true|false>,\n'
            '  "skill": "<skill name or empty string>",\n'
            '  "dc": <integer difficulty class, 0 if no roll needed>,\n'
            '  "target": "<target entity or empty string>",\n'
            '  "summary": "<one-sentence plain-language summary>"\n'
            '}\n\n'
            f"Current game context:\n{game_context}"
        )
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": player_action},
                ],
                format='json',
            )
            raw = _repair_json(response.message.content)
            return _validated_intent(json.loads(raw))
        except Exception as e:
            print(f"Intent parsing error: {e}")
            return _validated_intent({"summary": player_action})

    # ------------------------------------------------------------------
    # Phase 2 — narrative rendering (LLM as Narrative Generator)
    # ------------------------------------------------------------------

    def render_narrative(self, system_prompt, outcome_context, rag_context=""):
        """
        Convert structured rule-engine outcome into atmospheric narrative.

        The LLM receives dice results and mechanical outcomes as facts —
        it only renders the story, never decides the mechanics.

        Returns a validated narrative dict with keys:
            narrative, choices, damage_taken, hp_healed, mp_used,
            items_found, location_change, npc_relationship_changes
        """
        json_schema = (
            '{\n'
            '  "narrative": "Atmospheric story description of what happened.",\n'
            '  "choices": ["Choice A", "Choice B", "Choice C"],\n'
            '  "damage_taken": 0,\n'
            '  "hp_healed": 0,\n'
            '  "mp_used": 0,\n'
            '  "items_found": [],\n'
            '  "location_change": "",\n'
            '  "npc_relationship_changes": {}\n'
            '}'
        )
        full_prompt = (
            f"{system_prompt}\n\n"
            f"Relevant Memory (RAG):\n{rag_context}\n\n"
            f"CRITICAL: Respond ONLY with valid JSON matching this schema exactly:\n{json_schema}"
        )
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_prompt},
                    {"role": "user", "content": outcome_context},
                ],
                format='json',
            )
            raw = _repair_json(response.message.content)
            return _validated_narrative(json.loads(raw))
        except Exception as e:
            print(f"Narrative rendering error: {e}")
            return _validated_narrative({
                "narrative": "The world holds its breath... (Error generating response)",
            })

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def generate_turn(self, system_prompt, user_action, context=""):
        """Legacy single-call wrapper. Delegates to render_narrative."""
        outcome = f"Player action: {user_action}"
        return self.render_narrative(system_prompt, outcome, context)

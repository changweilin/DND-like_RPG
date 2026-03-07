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
        # thought_process is filled FIRST — forces chain-of-thought reasoning
        # before the model commits to a classification (One Trillion and One Nights approach).
        "thought_process": "",
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
        # scene_type enables Waidrin-style structured narrative events:
        # the UI can apply different styling/icons per scene type.
        "scene_type": "exploration",   # combat | social | exploration | puzzle | rest
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

def _validated_stat_block(data, entity_name):
    """Merge LLM output onto safe defaults for an entity stat block."""
    defaults = {
        "name": entity_name,
        "type": "npc",          # npc | monster | boss | merchant | guard
        "hp": 20,
        "atk": 5,
        "def_stat": 5,
        "skills": [],
        "special_ability": "",
        "description": entity_name,
        "loot": [],
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
    # Phase 1 — intent parsing with Guided Thinking
    # (One Trillion and One Nights: Guided Thinking approach)
    # ------------------------------------------------------------------

    def parse_intent(self, player_action, game_context):
        """
        Phase 1: Parse the player's natural language into a structured intent dict.

        The schema puts "thought_process" FIRST so the model reasons through
        action feasibility and difficulty before committing to a classification.
        This is the Guided Thinking technique from "One Trillion and One Nights":
        the LLM evaluates feasibility, skill required, and DC before deciding
        requires_roll — making classification far more accurate.

        Returns a validated intent dict with keys:
            thought_process, action_type, requires_roll, skill, dc, target, summary
        """
        system_prompt = (
            "You are the intent parser for a TRPG rule engine.\n"
            "Think step-by-step FIRST (thought_process), then classify the action.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema — fill in ORDER shown (thought_process guides your classification):\n"
            '{\n'
            '  "thought_process": "Step-by-step: Is this feasible? What skill? How hard? (Trivial/Easy/Medium/Hard/VeryHard)",\n'
            '  "action_type": "<direct_action|skill_check|attack|magic|social|explore>",\n'
            '  "requires_roll": <true|false>,\n'
            '  "skill": "<acrobatics|athletics|arcana|perception|stealth|persuasion|medicine|intimidation|empty>",\n'
            '  "dc": <5|10|15|20|25 if roll needed, else 0>,\n'
            '  "target": "<target entity name or empty string>",\n'
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
    # Phase 2 — narrative rendering as structured event
    # (Waidrin: LLM generates structured Narrative Events, not chat)
    # ------------------------------------------------------------------

    def render_narrative(self, system_prompt, outcome_context, rag_context=""):
        """
        Phase 2: Convert structured rule-engine outcome into a Narrative Event.

        Inspired by Waidrin's approach: the output is a structured event dict,
        not a free-form chat message. scene_type enables the UI to apply
        different visual treatments per encounter type.

        The LLM receives dice results and mechanical outcomes as hard facts —
        it narrates the story, never decides the mechanics.

        Returns a validated narrative dict with keys:
            scene_type, narrative, choices, damage_taken, hp_healed, mp_used,
            items_found, location_change, npc_relationship_changes
        """
        json_schema = (
            '{\n'
            '  "scene_type": "<combat|social|exploration|puzzle|rest>",\n'
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
    # Entity stat block generation
    # (Inspired by Infinite Monster Engine: dynamic TRPG-compliant stat blocks)
    # ------------------------------------------------------------------

    def generate_entity_stat_block(self, entity_name, entity_type="npc", world_context=""):
        """
        Dynamically generate a stat block for an NPC or monster on first encounter.

        Called once per new entity; the result is stored in the game_rules RAG
        collection so subsequent turns retrieve consistent stats.
        This mirrors the Infinite Monster Engine's approach: define constraints
        (entity type, world context) and let the LLM produce rule-compliant stats.

        Returns a validated stat block dict.
        """
        system_prompt = (
            "You are a TRPG game master generating a concise stat block.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema:\n"
            '{\n'
            '  "name": "<entity name>",\n'
            '  "type": "<npc|monster|boss|merchant|guard>",\n'
            '  "hp": <integer 1-200>,\n'
            '  "atk": <integer 1-30>,\n'
            '  "def_stat": <integer 1-30>,\n'
            '  "skills": ["<skill1>", "<skill2>"],\n'
            '  "special_ability": "<one-line special ability or empty string>",\n'
            '  "description": "<one atmospheric sentence>",\n'
            '  "loot": ["<item1>", "<item2>"]\n'
            '}\n\n'
            f"World context: {world_context}"
        )
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Generate a stat block for: {entity_name} (type: {entity_type})"},
                ],
                format='json',
            )
            raw = _repair_json(response.message.content)
            return _validated_stat_block(json.loads(raw), entity_name)
        except Exception as e:
            print(f"Stat block generation error for {entity_name!r}: {e}")
            return _validated_stat_block({}, entity_name)

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def generate_turn(self, system_prompt, user_action, context=""):
        """Legacy single-call wrapper. Delegates to render_narrative."""
        outcome = f"Player action: {user_action}"
        return self.render_narrative(system_prompt, outcome, context)

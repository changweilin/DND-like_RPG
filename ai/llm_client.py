import json
import os
import re
import ollama
from engine.config import config


def _is_correct_language(text, language):
    """Heuristic: does text appear to be in the requested language?"""
    if not text or not language:
        return True
    lang_low = language.lower()
    cjk  = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    kana = sum(1 for c in text if '\u3040' <= c <= '\u30ff')
    ratio = (cjk + kana) / max(len(text), 1)
    if '中文' in lang_low or 'chinese' in lang_low:
        return ratio >= 0.08
    if '日本' in lang_low or 'japanese' in lang_low:
        return ratio >= 0.08
    # English / Spanish / default: should NOT be CJK-dominant
    return ratio < 0.05

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
        "choices": ["Look around", "Wait and observe", "Ask for information"],
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
    # Ensure at least 3 choices
    if len(defaults["choices"]) < 3:
        defaults["choices"] = (defaults["choices"]
                               + ["Look around", "Wait and observe", "Ask for information"]
                               )[:3]
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

def _detect_provider(model_id):
    """Look up the provider for a model ID in MODEL_PRESETS; default to ollama."""
    for preset in config.MODEL_PRESETS:
        if preset['id'] == model_id:
            return preset['provider']
    return 'ollama'

def _preset_for(model_id):
    """Return the full preset dict for a model ID, or an empty dict."""
    for preset in config.MODEL_PRESETS:
        if preset['id'] == model_id:
            return preset
    return {}

# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, model_name=None):
        self.model    = model_name or config.LLM_MODEL_NAME
        self.provider = _detect_provider(self.model)

    def switch_model(self, model_id, provider=None):
        """Hot-swap the active model without rebuilding shared state."""
        self.model    = model_id
        self.provider = provider or _detect_provider(model_id)

    # ------------------------------------------------------------------
    # Unified provider routing — _chat()
    # All public methods funnel through here so adding a new provider
    # requires changing only one place.
    # ------------------------------------------------------------------

    def _chat(self, messages, json_mode=False):
        """
        Route a chat request to the correct provider SDK.

        Args:
            messages  (list[dict]): OpenAI-style message list
                                    [{"role": "system"|"user"|"assistant", "content": str}]
            json_mode (bool):       Ask the provider for structured JSON output.

        Returns:
            str: raw response text from the model.
        """
        if self.provider == 'anthropic':
            return self._chat_anthropic(messages, json_mode)
        elif self.provider == 'google':
            return self._chat_google(messages, json_mode)
        elif self.provider == 'openai':
            return self._chat_openai(messages, json_mode)
        else:
            return self._chat_ollama(messages, json_mode)

    def _chat_ollama(self, messages, json_mode=False):
        kwargs = {'format': 'json'} if json_mode else {}
        response = ollama.chat(model=self.model, messages=messages, **kwargs)
        return response.message.content

    def _chat_openai(self, messages, json_mode=False):
        """
        Handles both OpenAI GPT models and xAI Grok (which uses an OpenAI-compatible API).
        For Grok, the preset supplies base_url and env_key=XAI_API_KEY.
        """
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")

        preset  = _preset_for(self.model)
        api_key = os.environ.get(preset.get('env_key', 'OPENAI_API_KEY'), '')
        base_url = preset.get('base_url')  # None for standard OpenAI, set for Grok

        client_kwargs = {'api_key': api_key}
        if base_url:
            client_kwargs['base_url'] = base_url
        client = openai.OpenAI(**client_kwargs)

        call_kwargs = {}
        if json_mode:
            call_kwargs['response_format'] = {"type": "json_object"}

        response = client.chat.completions.create(
            model=self.model, messages=messages, **call_kwargs
        )
        return response.choices[0].message.content

    def _chat_anthropic(self, messages, json_mode=False):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        client  = anthropic.Anthropic(api_key=api_key)

        # Anthropic separates system content from the messages array
        system_parts = []
        user_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_parts.append(m['content'])
            else:
                user_messages.append(m)

        system_text = "\n".join(system_parts)
        if json_mode:
            system_text += "\nCRITICAL: Respond ONLY with valid JSON, no markdown."

        response = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_text,
            messages=user_messages or [{"role": "user", "content": "Continue."}],
        )
        return response.content[0].text

    def _chat_google(self, messages, json_mode=False):
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError(
                "google-generativeai package not installed. Run: pip install google-generativeai"
            )

        api_key = os.environ.get('GOOGLE_API_KEY', '')
        genai.configure(api_key=api_key)

        # Convert OpenAI-style messages to Google format
        system_parts = []
        conversation = []
        for m in messages:
            if m['role'] == 'system':
                system_parts.append(m['content'])
            elif m['role'] == 'user':
                conversation.append({'role': 'user', 'parts': [m['content']]})
            elif m['role'] == 'assistant':
                conversation.append({'role': 'model', 'parts': [m['content']]})

        system_instruction = "\n".join(system_parts) or None

        gen_config = {}
        if json_mode:
            gen_config['response_mime_type'] = 'application/json'

        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_instruction,
            generation_config=genai.GenerationConfig(**gen_config) if gen_config else None,
        )
        if not conversation:
            conversation = [{'role': 'user', 'parts': ['Continue.']}]

        response = model.generate_content(conversation)
        return response.text

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
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": player_action},
                ],
                json_mode=True,
            )
            return _validated_intent(json.loads(_repair_json(raw)))
        except Exception as e:
            print(f"Intent parsing error: {e}")
            return _validated_intent({"summary": player_action})

    # ------------------------------------------------------------------
    # Phase 2 — narrative rendering as structured event
    # (Waidrin: LLM generates structured Narrative Events, not chat)
    # ------------------------------------------------------------------

    def render_narrative(self, system_prompt, outcome_context, rag_context="", language="English"):
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
            '  "narrative": "Atmospheric story description (MINIMUM 300 characters of vivid prose).",\n'
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
            "NARRATIVE RULES:\n"
            "  • narrative field MUST be at least 300 characters of vivid, immersive prose\n"
            "  • Write in the SAME language specified in the system prompt above\n"
            "  • choices MUST contain at least 3 distinct options\n"
            f"CRITICAL: Respond ONLY with valid JSON matching this schema exactly:\n{json_schema}"
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": full_prompt},
                    {"role": "user",   "content": outcome_context},
                ],
                json_mode=True,
            )
            result = _validated_narrative(json.loads(_repair_json(raw)))
            result = self._ensure_min_length(
                result, raw,
                base_messages=[
                    {"role": "system", "content": full_prompt},
                    {"role": "user",   "content": outcome_context},
                ],
                min_chars=300,
                combine=True,
                language=language,
            )
            result = self._localize_narrative(result, language)
            return result
        except Exception as e:
            print(f"Narrative rendering error: {e}")
            return _validated_narrative({
                "narrative": "The world holds its breath... (Error generating response)",
            })

    # ------------------------------------------------------------------
    # Internal helper: ensure narrative meets minimum length via one retry
    # ------------------------------------------------------------------

    def _ensure_min_length(self, result, raw, base_messages, min_chars,
                           combine=False, language="English"):
        """
        Two-pass quality check on the narrative field:
          Pass 1 — Too short: ask the model to CONTINUE from where it left off
                   (plain-text append; avoids JSON re-generation failures).
          Pass 2 — Wrong language: ask the model to TRANSLATE the result
                   (plain-text; avoids triggering a full rewrite).
        Both passes use plain-text responses so JSON parsing cannot fail.
        """
        narrative = result["narrative"]

        # Pass 1: too short → ask to continue, not rewrite
        if len(narrative) < min_chars:
            shortage = min_chars - len(narrative)
            continue_msg = (
                f"Continue the narrative from exactly where it ended. "
                f"Write approximately {shortage} more characters of vivid, "
                f"immersive prose. "
                f"Write EXCLUSIVELY in {language}. "
                "Return plain text only — no JSON, no markdown."
            )
            try:
                continuation = self._chat(
                    messages=base_messages + [
                        {"role": "assistant", "content": narrative},
                        {"role": "user",      "content": continue_msg},
                    ],
                    json_mode=False,
                ).strip()
                if continuation:
                    narrative = narrative + "\n\n" + continuation
            except Exception:
                pass

        # Pass 2: wrong language → ask to translate, not rewrite
        if not _is_correct_language(narrative, language):
            translate_msg = (
                f"The text below is not written in {language}. "
                f"Translate it into {language}, preserving every detail and atmosphere. "
                "Return the translated text only — no JSON, no commentary.\n\n"
                f"{narrative}"
            )
            try:
                translated = self._chat(
                    messages=[{"role": "user", "content": translate_msg}],
                    json_mode=False,
                ).strip()
                if translated:
                    narrative = translated
            except Exception:
                pass

        result["narrative"] = narrative
        return result

    # ------------------------------------------------------------------
    # Localisation helpers — translate generated text to target language
    # ------------------------------------------------------------------

    def _translate_text(self, text, language):
        """Translate a single string to language if it is not already correct."""
        if not text or _is_correct_language(text, language):
            return text
        try:
            return self._chat(
                messages=[{"role": "user", "content":
                    f"Translate the following text to {language}. "
                    "Return only the translation, no commentary:\n\n" + text}],
                json_mode=False,
            ).strip() or text
        except Exception:
            return text

    def _localize_narrative(self, result, language):
        """
        Batch-translate choices, items_found and location_change to language
        in a single LLM call (section-marker protocol).
        Only fires when at least one field is not already in the target language.
        """
        choices  = result.get("choices") or []
        items    = result.get("items_found") or []
        location = result.get("location_change") or ""

        item_strs = [
            (i if isinstance(i, str) else i.get('name', '')) for i in items
        ]

        needs = (
            any(s and not _is_correct_language(s, language) for s in choices)
            or any(s and not _is_correct_language(s, language) for s in item_strs)
            or (location and not _is_correct_language(location, language))
        )
        if not needs:
            return result

        parts = []
        if choices:
            parts.append("##CHOICES##\n" + "\n".join(
                f"{i + 1}. {c}" for i, c in enumerate(choices)
            ))
        if item_strs:
            parts.append("##ITEMS##\n" + "\n".join(
                f"{i + 1}. {s}" for i, s in enumerate(item_strs)
            ))
        if location:
            parts.append(f"##LOCATION##\n{location}")

        if not parts:
            return result

        prompt = (
            f"Translate the following text sections to {language}.\n"
            "Keep each section header (##CHOICES##, ##ITEMS##, ##LOCATION##) exactly.\n"
            "Keep numbered items numbered. Return only the translated sections.\n\n"
            + "\n\n".join(parts)
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()

            def _extract(tag, text):
                m = re.search(rf'##{tag}##\s*(.*?)(?=##\w+##|$)', text, re.DOTALL)
                return m.group(1).strip() if m else ""

            def _parse_numbered(text):
                lines = [re.sub(r'^\d+\.\s*', '', l).strip()
                         for l in text.splitlines() if l.strip()]
                return [l for l in lines if l]

            choices_raw = _extract("CHOICES", raw)
            if choices_raw:
                tr = _parse_numbered(choices_raw)
                if len(tr) >= len(choices):
                    result["choices"] = tr[:len(choices)]

            items_raw = _extract("ITEMS", raw)
            if items_raw and items:
                tr = _parse_numbered(items_raw)
                new_items = []
                for idx, orig in enumerate(items):
                    name = tr[idx] if idx < len(tr) else (
                        orig if isinstance(orig, str) else orig.get('name', ''))
                    if isinstance(orig, dict):
                        entry = dict(orig)
                        entry['name'] = name
                        new_items.append(entry)
                    else:
                        new_items.append(name)
                result["items_found"] = new_items

            location_raw = _extract("LOCATION", raw)
            if location_raw:
                result["location_change"] = location_raw

        except Exception as e:
            print(f"[_localize_narrative] {e}")

        return result

    def _localize_stat_block(self, stat_block, language):
        """
        Batch-translate the text fields of a stat block dict
        (description, special_ability, skills list, loot list).
        Fires only when at least one field is wrong language.
        """
        desc    = stat_block.get("description", "")
        special = stat_block.get("special_ability", "")
        skills  = stat_block.get("skills") or []
        loot    = stat_block.get("loot") or []

        needs = (
            (desc    and not _is_correct_language(desc,    language))
            or (special and not _is_correct_language(special, language))
            or any(s and not _is_correct_language(s, language) for s in skills)
            or any(s and not _is_correct_language(s, language) for s in loot)
        )
        if not needs:
            return stat_block

        parts = []
        if desc:
            parts.append(f"##DESCRIPTION##\n{desc}")
        if special:
            parts.append(f"##SPECIAL##\n{special}")
        if skills:
            parts.append("##SKILLS##\n" + "\n".join(
                f"{i + 1}. {s}" for i, s in enumerate(skills)
            ))
        if loot:
            parts.append("##LOOT##\n" + "\n".join(
                f"{i + 1}. {s}" for i, s in enumerate(loot)
            ))

        if not parts:
            return stat_block

        prompt = (
            f"Translate the following text sections to {language}.\n"
            "Keep each header (##DESCRIPTION##, ##SPECIAL##, ##SKILLS##, ##LOOT##) exactly.\n"
            "Keep numbered items numbered. Return only the translated sections.\n\n"
            + "\n\n".join(parts)
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()

            def _extract(tag, text):
                m = re.search(rf'##{tag}##\s*(.*?)(?=##\w+##|$)', text, re.DOTALL)
                return m.group(1).strip() if m else ""

            def _parse_numbered(text):
                lines = [re.sub(r'^\d+\.\s*', '', l).strip()
                         for l in text.splitlines() if l.strip()]
                return [l for l in lines if l]

            desc_raw = _extract("DESCRIPTION", raw)
            if desc_raw:
                stat_block["description"] = desc_raw

            special_raw = _extract("SPECIAL", raw)
            if special_raw:
                stat_block["special_ability"] = special_raw

            skills_raw = _extract("SKILLS", raw)
            if skills_raw and skills:
                tr = _parse_numbered(skills_raw)
                if tr:
                    stat_block["skills"] = tr[:len(skills)] if len(tr) >= len(skills) else tr

            loot_raw = _extract("LOOT", raw)
            if loot_raw and loot:
                tr = _parse_numbered(loot_raw)
                if tr:
                    stat_block["loot"] = tr[:len(loot)] if len(tr) >= len(loot) else tr

        except Exception as e:
            print(f"[_localize_stat_block] {e}")

        return stat_block

    # ------------------------------------------------------------------
    # Prologue generation — Turn 0 opening scene (≥ 1000 chars)
    # ------------------------------------------------------------------

    def generate_prologue(self, current_state, party):
        """
        Generate an immersive opening prologue of at least 1000 characters.

        The prologue sets the scene, introduces the party's arrival, and ends
        with 3+ branching choices so the player can immediately act.

        Args:
            current_state: GameState ORM object
            party:         list[Character] ORM objects

        Returns a validated narrative dict (same schema as render_narrative).
        """
        ws_id      = getattr(current_state, 'world_setting', None) or 'dnd5e'
        ws         = config.get_world_setting(ws_id)
        tm         = ws.get('term_map', {})
        language   = current_state.language or 'English'
        world_name = ws['name']
        location   = current_state.current_location or ws.get('starting_location', 'the starting area')
        difficulty = current_state.difficulty or 'Normal'
        dm_title   = tm.get('dm_title', 'Game Master')
        world_ctx  = current_state.world_context or ''

        party_lines = '\n'.join(
            f"  - {c.name} ({c.race} {c.char_class})"
            + (f": {c.personality}" if c.personality else "")
            for c in party
        )

        json_schema = (
            '{\n'
            '  "scene_type": "exploration",\n'
            '  "narrative": "Epic opening prologue — MINIMUM 1000 characters of atmospheric prose.",\n'
            '  "choices": ["Choice A", "Choice B", "Choice C"],\n'
            '  "damage_taken": 0, "hp_healed": 0, "mp_used": 0,\n'
            '  "items_found": [], "location_change": "", "npc_relationship_changes": {}\n'
            '}'
        )

        system_prompt = (
            f"You are a master {dm_title} opening a {world_name} campaign.\n"
            f"Write ALL text exclusively in {language}.\n"
            f"Setting: {world_name}. Starting location: {location}. Difficulty: {difficulty}.\n"
            f"Party arriving:\n{party_lines}\n"
            f"World lore:\n{world_ctx[:800]}\n\n"
            "PROLOGUE RULES:\n"
            "  • narrative MUST be at least 1000 characters of vivid, atmospheric prose\n"
            "  • Paint the world with rich sensory detail (sights, sounds, smells)\n"
            "  • Introduce the starting location, hint at upcoming dangers and mysteries\n"
            "  • End by presenting the party's immediate situation\n"
            "  • choices MUST contain exactly 3 distinct opening actions the party can take\n"
            f"CRITICAL: Respond ONLY with valid JSON matching this schema:\n{json_schema}"
        )

        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": "Begin the adventure with an epic prologue."},
                ],
                json_mode=True,
            )
            result = _validated_narrative(json.loads(_repair_json(raw)))
            result = self._ensure_min_length(
                result, raw,
                base_messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": "Begin the adventure with an epic prologue."},
                ],
                min_chars=1000,
                combine=True,
                language=language,
            )
            result = self._localize_narrative(result, language)
            return result
        except Exception as e:
            print(f"Prologue generation error: {e}")
            return _validated_narrative({
                "narrative": (
                    f"The adventure begins in {location}. "
                    "The air is thick with anticipation as your party arrives, "
                    "ready to face whatever challenges lie ahead in this realm."
                ),
            })

    # ------------------------------------------------------------------
    # Entity stat block generation
    # (Inspired by Infinite Monster Engine: dynamic TRPG-compliant stat blocks)
    # ------------------------------------------------------------------

    def generate_entity_stat_block(self, entity_name, entity_type="npc",
                                    world_context="", language="English"):
        """
        Dynamically generate a stat block for an NPC or monster on first encounter.

        Called once per new entity; the result is stored in the game_rules RAG
        collection so subsequent turns retrieve consistent stats.
        This mirrors the Infinite Monster Engine's approach: define constraints
        (entity type, world context) and let the LLM produce rule-compliant stats.

        Returns a validated stat block dict with all text fields in language.
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
            f"World context: {world_context}\n"
            f"CRITICAL: Write description, special_ability, skills, and loot "
            f"EXCLUSIVELY in {language}."
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Generate a stat block for: {entity_name} (type: {entity_type})"},
                ],
                json_mode=True,
            )
            stat_block = _validated_stat_block(json.loads(_repair_json(raw)), entity_name)
            stat_block = self._localize_stat_block(stat_block, language)
            return stat_block
        except Exception as e:
            print(f"Stat block generation error for {entity_name!r}: {e}")
            return _validated_stat_block({}, entity_name)

    # ------------------------------------------------------------------
    # NPC generative agent reactions  (Section 3.5)
    # ------------------------------------------------------------------

    def evaluate_npc_reactions(self, event_summary, npc_states, language):
        """
        After a social/NPC-involved turn, ask the LLM how each NPC reacts
        *independently* — their goals, moods, and relationships shift based on
        what just happened.  Only NPCs whose state changes are returned.

        Inspired by Park et al. (2023) Generative Agents: each NPC is treated
        as a tiny autonomous agent with a persistent goal and emotional state,
        not a static affinity integer.

        Returns a dict: {npc_name: {affinity_delta, state, goal}}
        Only NPCs whose state changes meaningfully are included.
        """
        npc_list = "\n".join(
            f"- {name}: affinity={d.get('affinity', 0):+d}, state={d.get('state', 'Neutral')}, "
            f"goal={d.get('goal', '')}"
            for name, d in npc_states.items()
        ) or "(no tracked NPCs)"

        system_prompt = (
            "You are simulating autonomous NPC reactions in a TRPG.\n"
            "Based on the event, decide how each NPC's emotional state and goal changes.\n"
            "Return ONLY NPCs whose state changes meaningfully — omit unchanged NPCs.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema:\n"
            '{\n'
            '  "NPC Name": {\n'
            '    "affinity_delta": <integer -30 to +30>,\n'
            '    "state": "<Friendly|Suspicious|Fearful|Hostile|Neutral|Grateful|Angry|...>",\n'
            '    "goal": "<updated short-term goal for this NPC>"\n'
            '  }\n'
            '}\n\n'
            f"Current NPC states:\n{npc_list}\n\n"
            f"CRITICAL: Write all goal text EXCLUSIVELY in {language or 'English'}."
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Event that just occurred:\n{event_summary}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"NPC reaction evaluation error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Memory summarization  (Section 3.1)
    # ------------------------------------------------------------------

    def summarize_memory_segment(self, turns, language):
        """
        When the session_memory sliding window overflows, the oldest turns are
        summarized into a single paragraph and stored in world_lore RAG as a
        'chapter summary' — so long-term story continuity is never truly lost.

        'turns' is a list of turn dicts:
            {turn, player_action, narrative, outcome}

        Returns a plain-text summary string.
        """
        lines = [
            f"Turn {t.get('turn', '?')}: {t.get('player_action', '')} → {t.get('outcome', '')}"
            for t in turns
        ]
        turns_text = "\n".join(lines)

        system_prompt = (
            "You are a historian summarizing past events in a TRPG campaign.\n"
            "Write a single concise paragraph (3-5 sentences) summarizing the story\n"
            "events listed below. Focus on narrative consequences, not mechanical details.\n"
            f"Write EXCLUSIVELY in {language or 'English'}. Return plain text only, no JSON."
        )
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Events to summarize:\n{turns_text}"},
                ],
                json_mode=False,
            ).strip()
        except Exception as e:
            print(f"Memory summarization error: {e}")
            return f"Earlier: {turns[-1].get('player_action', '')}..." if turns else ""

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def generate_turn(self, system_prompt, user_action, context=""):
        """Legacy single-call wrapper. Delegates to render_narrative."""
        outcome = f"Player action: {user_action}"
        return self.render_narrative(system_prompt, outcome, context)

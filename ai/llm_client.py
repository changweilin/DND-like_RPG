import json
import os
import re
import sys
from pathlib import Path
import ollama
from engine.config import config

# ── API 用量追蹤 ───────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from api_tracker import check_quota, record_call as _record_api
    _API_TRACKER = True
except ImportError:
    _API_TRACKER = False
    def check_quota(api, **kw): return True
    def _record_api(api): pass


_PLACEHOLDER_RE = re.compile(
    r'^(choice|option|選項|action|行動|分歧|第.個選項)\s*[a-z\d]?$',
    re.IGNORECASE,
)

def _coerce_choice(c):
    """Ensure a single choice entry is a plain string, not a dict or other type."""
    if isinstance(c, str):
        stripped = c.strip()
        # Guard against LLM returning a JSON object string like '{"text": "..."}'
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    return obj.get('text') or obj.get('action') or obj.get('choice') or stripped
            except (json.JSONDecodeError, ValueError):
                pass
        return c
    if isinstance(c, dict):
        return c.get('text') or c.get('action') or c.get('choice') or next(
            (v for v in c.values() if isinstance(v, str) and len(v) > 2), str(c)
        )
    return str(c)


_MIN_CHOICE_LEN = 3

def _is_valid_choice(c):
    """A choice is valid if it's non-empty, non-placeholder, and > 8 characters."""
    stripped = (c or '').strip()
    if not stripped:
        return False
    if _PLACEHOLDER_RE.match(stripped):
        return False
    if len(stripped) <= _MIN_CHOICE_LEN:
        return False
    return True


def _has_placeholder_choices(choices):
    """True if any choice fails validation (placeholder, too short, or empty)."""
    if not choices:
        return True
    return any(not _is_valid_choice(c) for c in choices)


def _is_correct_language(text, language):
    """
    Traditional (non-LLM) language detection using Unicode character analysis.

    Layer 1 — Unicode block counting (always runs, no dependencies):
      CJK Unified Ideographs U+4E00–U+9FFF
      CJK Extension A         U+3400–U+4DBF
      CJK Compatibility       U+F900–U+FAFF
      Hiragana + Katakana     U+3040–U+30FF
      Hangul Syllables        U+AC00–U+D7A3

    Layer 2 — langdetect statistical n-gram analysis (optional):
      Used for non-CJK languages (English, Spanish, French, …).
      Falls back gracefully if langdetect is not installed.
      Only applied when len(text) >= 20 to avoid unreliable short-text results.
    """
    if not text or not language:
        return True
    lang_low = language.lower()
    total = max(len(text), 1)

    # --- Layer 1: Unicode block counting ---
    cjk = sum(
        1 for c in text
        if ('\u4e00' <= c <= '\u9fff')   # CJK Unified Ideographs
        or ('\u3400' <= c <= '\u4dbf')   # CJK Extension A
        or ('\uf900' <= c <= '\ufaff')   # CJK Compatibility Ideographs
    )
    kana   = sum(1 for c in text if '\u3040' <= c <= '\u30ff')  # Hiragana + Katakana
    hangul = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')  # Hangul Syllables

    cjk_ratio    = cjk / total
    cjk_kana_ratio = (cjk + kana) / total

    if '中文' in lang_low or 'chinese' in lang_low:
        return cjk_ratio >= 0.80
    if '日本' in lang_low or 'japanese' in lang_low:
        return cjk_kana_ratio >= 0.80
    if '한국' in lang_low or 'korean' in lang_low:
        return hangul / total >= 0.80

    # For non-CJK languages: text must not be CJK-dominant
    if cjk_kana_ratio >= 0.05:
        return False

    # --- Layer 2: langdetect statistical check (optional, non-CJK only) ---
    _LANG_CODES = {
        'english': 'en', 'spanish': 'es', 'french': 'fr',
        'german': 'de', 'portuguese': 'pt', 'italian': 'it',
        'russian': 'ru', 'arabic': 'ar',
    }
    expected_code = next((v for k, v in _LANG_CODES.items() if k in lang_low), None)
    if expected_code and len(text) >= 20:
        try:
            from langdetect import detect
            return detect(text) == expected_code
        except Exception:
            pass  # langdetect not installed or detection failed — fall through

    return True  # cannot determine further; assume correct

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
        # names of NPCs / characters who appear in this scene
        "characters_present": [],
        # list of quest names the player completed this turn (matched against active quests)
        "quest_completed": [],
    }
    # String fields — coerce to str so callers can always call len() safely.
    # Empty / whitespace-only strings are treated as missing so defaults survive.
    _STR_FIELDS = {'narrative', 'scene_type', 'location_change'}
    for k in defaults:
        if k in data:
            v = data[k]
            if k in _STR_FIELDS:
                s = str(v).strip() if v is not None else ''
                if s:
                    defaults[k] = s
            else:
                defaults[k] = v
    # Ensure at least 3 choices, each a plain string
    if not isinstance(defaults["choices"], list):
        defaults["choices"] = []
    defaults["choices"] = [_coerce_choice(c) for c in defaults["choices"]]
    if len(defaults["choices"]) < 3:
        defaults["choices"] = (defaults["choices"]
                               + ["Look around", "Wait and observe", "Ask for information"]
                               )[:3]
    # Coerce list fields — LLM sometimes returns a comma-separated string instead of a list.
    for _list_key in ("characters_present", "items_found", "quest_completed"):
        v = defaults[_list_key]
        if isinstance(v, str):
            defaults[_list_key] = [s.strip() for s in v.split(',') if s.strip()]
        elif not isinstance(v, list):
            defaults[_list_key] = []
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

def _validated_npc_profile(data, display_name):
    """Merge LLM output onto safe defaults for an NPC profile dict."""
    defaults = {
        "proper_name":  display_name,   # set to a real name if display_name is a title
        "aliases":      [],
        "gender":       "",             # inferred from name and context
        "biography":    "",
        "personality":  "",             # MBTI type + description
        "traits":       "",             # appearance, build, intelligence, physique
        "health":       "Healthy",
        "action":       "",
        "goal":         "",
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
        # Cross-turn adaptive choice quality tracker.
        # Tracks recent choice issues (wrong language, too short, placeholder)
        # so that subsequent prompts can include stronger corrective hints.
        # Each entry: {"turn": int, "issues": list[str]}
        self._choice_quality_log = []

    def switch_model(self, model_id, provider=None):
        """Hot-swap the active model without rebuilding shared state."""
        self.model    = model_id
        self.provider = provider or _detect_provider(model_id)

    # ------------------------------------------------------------------
    # VRAM lifecycle — let external systems (e.g. ImageGenerator) ask
    # Ollama to unload / reload the LLM so GPU memory can be reused.
    # ------------------------------------------------------------------

    def unload_from_vram(self):
        if self.provider != 'ollama':
            return
        try:
            ollama.generate(model=self.model, prompt="", keep_alive=0)
            # Poll ollama.ps() to confirm the model is actually out of VRAM
            import time
            for _ in range(30):  # up to ~15 seconds
                time.sleep(0.5)
                try:
                    running = ollama.ps()
                    loaded = [m.model for m in running.models]
                    if not any(n.startswith(self.model) for n in loaded):
                        break
                except Exception:
                    time.sleep(1)
                    break
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"[LLM] Unloaded {self.model} from VRAM")
        except Exception as e:
            print(f"[LLM] Failed to unload {self.model}: {e}")

    def preload_to_vram(self):
        if self.provider != 'ollama':
            return
        try:
            ollama.generate(model=self.model, prompt="", keep_alive="5m")
            print(f"[LLM] Preloaded {self.model} into VRAM")
        except Exception as e:
            print(f"[LLM] Failed to preload {self.model}: {e}")

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

        preset   = _preset_for(self.model)
        env_key  = preset.get('env_key', 'OPENAI_API_KEY')
        api_key  = os.environ.get(env_key, '')
        if not api_key:
            raise RuntimeError(
                f"OpenAI API key not set. Please enter your key in the "
                f"LLM Model panel (environment variable: {env_key})."
            )
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

        preset  = _preset_for(self.model)
        env_key = preset.get('env_key', 'ANTHROPIC_API_KEY')
        api_key = os.environ.get(env_key, '')
        if not api_key:
            raise RuntimeError(
                f"Anthropic API key not set. Please enter your key in the "
                f"LLM Model panel (environment variable: {env_key})."
            )
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

        preset  = _preset_for(self.model)
        env_key = preset.get('env_key', 'GOOGLE_API_KEY')
        api_key = os.environ.get(env_key, '')
        if not api_key:
            raise RuntimeError(
                f"Google API key not set. Please enter your key in the "
                f"LLM Model panel (environment variable: {env_key})."
            )
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

        check_quota(self.model)
        _record_api(self.model)
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
            '  "characters_present": ["<REQUIRED: full name of every NPC or character who appears, speaks, or is mentioned in the narrative — include ALL of them>"],\n'
            '  "narrative": "Atmospheric story description (MINIMUM 300 characters of vivid prose).",\n'
            f'  "choices": ["<具體行動或事件，用{language}寫>", "<第二個選項>", "<第三個選項>"],\n'
            '  "damage_taken": 0,\n'
            '  "hp_healed": 0,\n'
            '  "mp_used": 0,\n'
            '  "items_found": [],\n'
            '  "location_change": "",\n'
            '  "npc_relationship_changes": {},\n'
            '  "quest_completed": []\n'
            '}'
        )
        # Build adaptive hint from recent choice quality failures
        adaptive_hint = self._build_choice_quality_hint(language)
        full_prompt = (
            f"{system_prompt}\n\n"
            f"Relevant Memory (RAG):\n{rag_context}\n\n"
            "NARRATIVE RULES (in order of priority):\n"
            "  1. [HIGHEST PRIORITY] narrative MUST be at least 300 characters of vivid,"
            " immersive prose with sensory details — this is the MOST important field\n"
            f"  2. Write EXCLUSIVELY in {language} — do NOT use any other language\n"
            "  3. choices: at least 3 distinct options grounded in session history"
            " (reference unresolved hooks, NPCs, or unexplored locations)\n"
            "  4. characters_present MUST list every named NPC / character who appears or speaks in the narrative\n"
            "  5. choices MUST explore directions NOT already taken — if CHOICES CONSTRAINT is present, do not repeat or paraphrase any listed action\n"
            f"{adaptive_hint}"
            f"CRITICAL: Respond ONLY with valid JSON matching this schema exactly:\n{json_schema}"
        )
        base_messages = [
            {"role": "system", "content": full_prompt},
            {"role": "user",   "content": outcome_context},
        ]
        # Step 1: call the LLM — failure here leaves raw empty
        try:
            raw = self._chat(messages=base_messages, json_mode=True)
        except Exception as e:
            print(f"Narrative LLM error: {e}")
            raw = ''

        # Step 2: parse JSON — failure here still lets safeguards run below
        try:
            result = _validated_narrative(json.loads(_repair_json(raw)))
        except Exception:
            # Malformed JSON — use raw text as narrative candidate so the
            # continuation-relay and language-translation passes can still fix it.
            result = _validated_narrative({'narrative': raw.strip()})

        # Step 3: safeguards always run regardless of parse outcome above
        result = self._ensure_min_length(
            result, raw,
            base_messages=base_messages,
            min_chars=300,
            combine=True,
            language=language,
        )
        result = self._localize_narrative(result, language)
        if _has_placeholder_choices(result.get('choices', [])):
            result = self._fix_placeholder_choices(result, result['narrative'], language)

        # Step 4: extract embedded choices from narrative text
        result = self._extract_embedded_choices(result)

        # Step 5: log choice quality for cross-turn adaptation
        self._log_choice_quality(result.get('choices', []), language)
        return result

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

        CJK adjustment: Chinese/Japanese characters carry ~3× more information
        per code-point than Latin characters, so the effective threshold is
        reduced to avoid unnecessary retries on quality CJK responses.
        """
        narrative = result["narrative"]
        # Safety net: narrative must be a str for len() to work
        if not isinstance(narrative, str):
            narrative = str(narrative) if narrative is not None else ""
            result["narrative"] = narrative

        # Adjust threshold for CJK languages — CJK characters carry ~2× more
        # information per code-point than Latin, but // 3 was too aggressive:
        # 100 CJK chars is only 1–2 sentences, not enough for immersive prose.
        # Using // 2 (= 150 for min_chars=300) ensures 3+ sentences minimum.
        lang_low = (language or '').lower()
        if '中文' in lang_low or 'chinese' in lang_low or '日本' in lang_low or 'japanese' in lang_low:
            effective_min = max(100, min_chars // 2)
        else:
            effective_min = min_chars

        # Pass 1: wrong language → translate first so relay only sees target language.
        # Original text is not stored or used as context after this point.
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

        # Pass 2a: full regeneration if narrative is too short for relay to work.
        # "Continue from where you left off" is unreliable when there's barely
        # any text — the LLM may ignore the stub and reply in English or produce
        # garbage.  Regenerate fully whenever narrative < effective_min // 3.
        regen_threshold = max(30, effective_min // 3)
        if len(narrative) < regen_threshold and len(narrative) < effective_min:
            regen_msg = (
                f"Your previous response had no narrative text. "
                f"Write at least {effective_min} characters of vivid, immersive "
                f"prose for this scene. Write EXCLUSIVELY in {language}. "
                "Return plain text only — no JSON, no markdown."
            )
            try:
                regen = self._chat(
                    messages=base_messages + [
                        {"role": "user", "content": regen_msg},
                    ],
                    json_mode=False,
                ).strip()
                # Strip JSON wrapper if the LLM ignores the plain-text instruction
                if regen and regen.startswith('{'):
                    try:
                        obj = json.loads(_repair_json(regen))
                        regen = obj.get('narrative', regen) if isinstance(obj, dict) else regen
                    except Exception:
                        pass
                if regen and _is_correct_language(regen, language):
                    narrative = regen
            except Exception:
                pass

        # Pass 2b: relay-continuation loop — length is measured on translated text only.
        # Only used when there is already some meaningful text to build on.
        MAX_RELAY = 3
        for _attempt in range(MAX_RELAY):
            if len(narrative) >= effective_min:
                break
            shortage = effective_min - len(narrative)
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
                # Strip JSON wrapper from continuation
                if continuation and continuation.startswith('{'):
                    try:
                        obj = json.loads(_repair_json(continuation))
                        continuation = obj.get('narrative', continuation) if isinstance(obj, dict) else continuation
                    except Exception:
                        pass
                if continuation and _is_correct_language(continuation, language):
                    narrative = narrative + "\n\n" + continuation
                elif not continuation:
                    break  # model returned empty — no point retrying
            except Exception:
                break  # network / model error — stop relay

        # Pass 2c: last-resort regeneration if relay loop didn't help enough.
        if len(narrative) < effective_min:
            fallback_msg = (
                f"Write a vivid, immersive narrative of at least {effective_min} characters "
                f"for this scene. Write EXCLUSIVELY in {language}. "
                "Return plain text only — no JSON, no markdown."
            )
            try:
                fallback = self._chat(
                    messages=base_messages + [
                        {"role": "user", "content": fallback_msg},
                    ],
                    json_mode=False,
                ).strip()
                if fallback and fallback.startswith('{'):
                    try:
                        obj = json.loads(_repair_json(fallback))
                        fallback = obj.get('narrative', fallback) if isinstance(obj, dict) else fallback
                    except Exception:
                        pass
                if fallback and len(fallback) > len(narrative):
                    narrative = fallback
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

    def _fix_placeholder_choices(self, result, narrative, language):
        """
        If choices are still generic placeholders after generation, ask the LLM
        to produce real choices derived from the narrative content.
        Returns result with choices replaced (or unchanged on failure).
        """
        prompt = (
            f"Based on the following story narrative, provide exactly 3 concrete "
            f"actions or decisions the player can take next. "
            f"Each choice MUST be a complete sentence longer than {_MIN_CHOICE_LEN} characters. "
            f"Write EXCLUSIVELY in {language}. "
            "Return ONLY a JSON array of 3 strings, e.g. [\"action1\", \"action2\", \"action3\"]. "
            "No markdown, no extra keys.\n\n"
            f"Narrative:\n{narrative[:800]}"
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                choices = json.loads(m.group(0))
                if isinstance(choices, list) and len(choices) >= 3:
                    coerced = [_coerce_choice(c) for c in choices[:5]]
                    valid   = [c for c in coerced if _is_valid_choice(c)]
                    if len(valid) >= 3:
                        result["choices"] = valid[:3]
        except Exception:
            pass
        return result

    # Regex patterns for embedded choices in narrative text:
    #   "1. ...", "1) ...", "1、...", "- ...", "• ..."
    _EMBEDDED_CHOICE_RE = re.compile(
        r'(?:^|\n)\s*'
        r'(?:\d+[.)、]\s*|[-•]\s+)'
        r'(.+)',
    )
    # A block of 2+ consecutive numbered/bulleted lines
    _CHOICE_BLOCK_RE = re.compile(
        r'((?:(?:^|\n)\s*(?:\d+[.)、]\s*|[-•]\s+).+){2,})',
    )

    def _extract_embedded_choices(self, result):
        """
        If the narrative text itself contains a numbered/bulleted list of options,
        extract those as choices and strip the list from the narrative.
        Keeps existing valid choices as supplements if embedded ones are < 3.
        """
        narrative = result.get('narrative', '')
        if not narrative:
            return result
        block_match = self._CHOICE_BLOCK_RE.search(narrative)
        if not block_match:
            return result
        block = block_match.group(0)
        extracted = []
        for m in self._EMBEDDED_CHOICE_RE.finditer(block):
            text = m.group(1).strip()
            if text and len(text) > _MIN_CHOICE_LEN:
                extracted.append(text)
        if len(extracted) < 2:
            return result
        # Strip the choice block from narrative
        cleaned = narrative[:block_match.start()] + narrative[block_match.end():]
        cleaned = cleaned.strip()
        if cleaned:
            result['narrative'] = cleaned
        # Use extracted choices, supplement with existing if needed
        existing = result.get('choices', [])
        final = extracted[:]
        for c in existing:
            if len(final) >= max(3, len(extracted)):
                break
            if c not in final and _is_valid_choice(c):
                final.append(c)
        # Ensure at least 3
        if len(final) < 3:
            for c in existing:
                if len(final) >= 3:
                    break
                if c not in final:
                    final.append(c)
        result['choices'] = final
        return result

    def _log_choice_quality(self, choices, language):
        """Record any choice quality issues for cross-turn adaptive prompting."""
        issues = []
        for c in (choices or []):
            if not _is_valid_choice(c):
                issues.append('placeholder')
            elif not _is_correct_language(c, language):
                issues.append('wrong_language')
            elif len(c.strip()) < 15:
                issues.append('too_short')
        if issues:
            self._choice_quality_log.append({'issues': issues})
        # Keep only the last 5 entries
        self._choice_quality_log = self._choice_quality_log[-5:]

    def _build_choice_quality_hint(self, language):
        """Build adaptive prompt hints based on recent choice quality failures."""
        if not self._choice_quality_log:
            return ""
        # Count recent issues across last 3 entries
        recent = self._choice_quality_log[-3:]
        all_issues = []
        for entry in recent:
            all_issues.extend(entry.get('issues', []))
        if not all_issues:
            return ""

        hints = []
        lang_fails = all_issues.count('wrong_language')
        short_fails = all_issues.count('too_short') + all_issues.count('placeholder')

        if lang_fails > 0:
            hints.append(
                f"  ⚠ LANGUAGE WARNING: Recent choices were in the WRONG language. "
                f"Every single choice string MUST be written in {language}. "
                f"Do NOT use English or any other language for choices."
            )
        if short_fails > 0:
            hints.append(
                f"  ⚠ LENGTH WARNING: Recent choices were too short or generic. "
                f"Each choice MUST be a specific, concrete action sentence of at least "
                f"15 characters — NOT placeholders like 'Option A' or '選項1'."
            )
        if not hints:
            return ""
        return "\n".join(hints) + "\n"

    def generate_diverse_choices(self, narrative, avoid_choices, count=3,
                                language="English", session_memory_text=""):
        """
        Generate action choices that are distinct from avoid_choices.
        Called by EventManager._filter_similar_choices when generated choices are
        too similar to recently taken or recently offered actions.
        session_memory_text provides recent story history so choices build on
        the ongoing narrative arc, not just the current turn.
        Returns a list of strings (may be empty on failure).
        """
        avoid_str = "\n".join(f"- {c}" for c in avoid_choices[:12] if c)
        memory_block = ""
        if session_memory_text:
            memory_block = (
                f"Recent story history (use this to generate choices that "
                f"advance the ongoing narrative):\n{session_memory_text}\n\n"
            )
        prompt = (
            f"Generate exactly {count} action choices for a TRPG player.\n"
            f"These choices are FORBIDDEN (too similar to recent actions — do NOT repeat or paraphrase them):\n"
            f"{avoid_str}\n\n"
            f"{memory_block}"
            f"Current scene:\n{narrative[:400]}\n\n"
            f"Requirements:\n"
            f"  • Each choice must be genuinely different in direction from all forbidden options\n"
            f"  • Each choice MUST be a complete sentence longer than {_MIN_CHOICE_LEN} characters\n"
            f"  • Choices should build on story threads from the history — reference unresolved"
            f" plot hooks, NPCs, or locations from past turns when possible\n"
            f"  • Write exclusively in {language}\n"
            f"Return ONLY a JSON array of {count} strings: [\"action1\", \"action2\", ...]"
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                result = json.loads(m.group(0))
                if isinstance(result, list):
                    return [_coerce_choice(c) for c in result
                            if c and _is_valid_choice(_coerce_choice(c))]
        except Exception as e:
            print(f"generate_diverse_choices error: {e}")
        return []

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
            '  "characters_present": ["<REQUIRED: full name of every NPC who appears or is mentioned in the prologue>"],\n'
            '  "narrative": "Epic opening prologue — MINIMUM 1000 characters of atmospheric prose.",\n'
            f'  "choices": ["<具體開場行動，用{language}寫>", "<第二個選項>", "<第三個選項>"],\n'
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
            f"  • Write EXCLUSIVELY in {language} — do NOT use any other language\n"
            "  • Paint the world with rich sensory detail (sights, sounds, smells)\n"
            "  • Introduce the starting location, hint at upcoming dangers and mysteries\n"
            "  • End by presenting the party's immediate situation\n"
            "  • choices MUST contain exactly 3 distinct opening actions the party can take\n"
            f"CRITICAL: Respond ONLY with valid JSON matching this schema:\n{json_schema}"
        )

        base_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": "Begin the adventure with an epic prologue."},
        ]
        # Step 1: call the LLM
        try:
            raw = self._chat(messages=base_messages, json_mode=True)
        except Exception as e:
            print(f"Prologue LLM error: {e}")
            raw = ''

        # Step 2: parse JSON
        try:
            result = _validated_narrative(json.loads(_repair_json(raw)))
        except Exception:
            result = _validated_narrative({'narrative': raw.strip()})

        # Step 3: safeguards always run
        result = self._ensure_min_length(
            result, raw,
            base_messages=base_messages,
            min_chars=1000,
            combine=True,
            language=language,
        )
        result = self._localize_narrative(result, language)
        if _has_placeholder_choices(result.get('choices', [])):
            result = self._fix_placeholder_choices(result, result['narrative'], language)
        result = self._extract_embedded_choices(result)
        return result

    # ------------------------------------------------------------------
    # Entity stat block generation
    # (Inspired by Infinite Monster Engine: dynamic TRPG-compliant stat blocks)
    # ------------------------------------------------------------------

    def generate_entity_stat_block(self, entity_name, entity_type="npc",
                                    world_context="", language="English",
                                    base_stats=None):
        """
        Generate the TEXT fields of a stat block for a newly encountered entity.

        Numeric stats (hp, atk, def_stat) are supplied by the caller via
        base_stats=(hp, atk, def_stat) — computed from the rule-engine lookup
        table in engine/intent_parser.get_entity_base_stats().  The LLM only
        writes description, special_ability, skills, and loot.

        Returns a validated stat block dict with all text fields in language.
        """
        hp, atk, def_stat = base_stats if base_stats else (20, 5, 5)

        system_prompt = (
            "You are a TRPG game master writing flavour text for an entity.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema — write ONLY the text fields shown; do NOT invent numeric stats:\n"
            '{\n'
            '  "name": "<entity name>",\n'
            '  "type": "<npc|monster|boss|merchant|guard>",\n'
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
                    {"role": "user",   "content": f"Generate flavour text for: {entity_name} (type: {entity_type})"},
                ],
                json_mode=True,
            )
            stat_block = _validated_stat_block(json.loads(_repair_json(raw)), entity_name)
        except Exception as e:
            print(f"Stat block generation error for {entity_name!r}: {e}")
            stat_block = _validated_stat_block({}, entity_name)

        # Inject rule-engine numeric stats — LLM output for these fields is discarded
        stat_block['hp']       = hp
        stat_block['atk']      = atk
        stat_block['def_stat'] = def_stat
        stat_block['type']     = entity_type

        stat_block = self._localize_stat_block(stat_block, language)
        return stat_block

    # ------------------------------------------------------------------
    # Character appearance generation — for the new-game creation form
    # ------------------------------------------------------------------

    def generate_character_appearance(self, race, gender, char_class, mbti,
                                       world_context="", language="English"):
        """
        Generate a 1-2 sentence physical appearance description for a new character.

        Uses race, gender, class, and MBTI personality to produce a contextual
        description that fits the world setting.  Called when the player clicks
        the 🎲 button in the character creation form.
        Returns "" on failure so the caller can fall back to the word-pool.
        """
        system_prompt = (
            "You are a TRPG character creator. Write a 1-2 sentence physical appearance "
            "description for a new character. Be specific about build, hair, eyes, skin, "
            "clothing, and any distinctive features that suit the race and class.\n"
            "Return ONLY the description — no labels, no JSON, no extra commentary.\n"
            f"Write EXCLUSIVELY in {language}.\n"
            f"World context: {world_context[:200]}"
        )
        user_msg = (
            f"Race: {race}, Gender: {gender}, Class: {char_class}, "
            f"Personality (MBTI): {mbti}"
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                json_mode=False,
            ).strip()
            return raw[:300] if len(raw) > 20 else ""
        except Exception as e:
            print(f"Character appearance generation error: {e}")
            return ""

    # ------------------------------------------------------------------
    # NPC profile generation — rich profile for newly encountered NPCs
    # ------------------------------------------------------------------

    def generate_npc_profile(self, display_name, world_context="", existing_rel=None, language="English"):
        """
        Generate a rich profile for an NPC encountered for the first time.

        display_name may be a title (e.g. "村長", "神秘旅人") or a proper name.
        If it is a title, the LLM generates a proper_name different from display_name.
        All text fields are written in language and must be consistent with world_context
        and any info already in existing_rel (e.g. state, goal) so plot is never contradicted.

        Returns a validated profile dict ready to be passed to WorldManager.register_npc().
        """
        existing_hint = ""
        if existing_rel and isinstance(existing_rel, dict):
            parts = []
            if existing_rel.get('state'):
                parts.append(f"current state: {existing_rel['state']}")
            if existing_rel.get('goal'):
                parts.append(f"known goal: {existing_rel['goal']}")
            if parts:
                existing_hint = "Known facts about this NPC (do NOT contradict): " + ", ".join(parts) + "\n"

        system_prompt = (
            "You are a TRPG game master creating a detailed NPC profile.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Determine whether the provided name is a TITLE/ALIAS or a PROPER NAME.\n"
            "If it is a title, generate a fitting proper_name consistent with the world.\n"
            "If it is already a proper name, set proper_name identical to the input name.\n\n"
            "Schema:\n"
            '{\n'
            '  "proper_name": "<full given name — same as input if already a proper name>",\n'
            '  "aliases": ["<title, honorific, or nickname used to refer to this NPC>"],\n'
            '  "gender": "<Male|Female|Non-binary|Unknown — infer from name and context>",\n'
            '  "biography": "<2-3 sentences of background and life history>",\n'
            '  "personality": "<one of the 16 MBTI types (INTJ/INTP/ENTJ/ENTP/INFJ/INFP/ENFJ/ENFP/ISTJ/ISFJ/ESTJ/ESFJ/ISTP/ISFP/ESTP/ESFP) + one-sentence description, e.g. ENFJ — warm and charismatic leader>",\n'
            '  "traits": "<physical appearance, build, intelligence, notable features — 1-2 sentences>",\n'
            '  "health": "<Healthy|Wounded|Exhausted|Ill|Dying — add a brief note if not Healthy>",\n'
            '  "action": "<what this NPC is currently doing in this scene>",\n'
            '  "goal": "<NPC\'s current short-term goal>"\n'
            '}\n\n'
            f"World context: {world_context[:400]}\n"
            f"{existing_hint}"
            f"CRITICAL: Write all text fields (biography, personality, traits, action, goal) "
            f"EXCLUSIVELY in {language}. All details must fit the game world setting."
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Generate NPC profile for: {display_name}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            profile = _validated_npc_profile(data, display_name)
            return self._localize_npc_profile(profile, language)
        except Exception as e:
            print(f"NPC profile generation error for {display_name!r}: {e}")
            return _validated_npc_profile({}, display_name)

    def _localize_npc_profile(self, profile, language):
        """
        Translate NPC profile text fields if they are not in the target language.
        Checks biography, personality, traits, action, goal — translates any that
        are in the wrong language in a single batched LLM call.
        """
        _PROFILE_TEXT_FIELDS = ['biography', 'personality', 'traits', 'action', 'goal']
        wrong = [
            f for f in _PROFILE_TEXT_FIELDS
            if profile.get(f) and not _is_correct_language(profile[f], language)
        ]
        if not wrong:
            return profile
        sections = "\n".join(
            f"##{f.upper()}##\n{profile[f]}" for f in wrong
        )
        prompt = (
            f"Translate the following NPC profile fields to {language}.\n"
            "Keep each section header (e.g. ##BIOGRAPHY##) exactly as-is.\n"
            "Return ONLY the translated sections, nothing else.\n\n"
            + sections
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()
            for f in wrong:
                tag = f.upper()
                m = re.search(rf'##{tag}##\s*(.*?)(?=##[A-Z]+##|$)', raw, re.DOTALL)
                if m:
                    translated = m.group(1).strip()
                    if translated:
                        profile[f] = translated
        except Exception as e:
            print(f"NPC profile localization error: {e}")
        return profile

    def _localize_org_profile(self, profile, language):
        """
        Translate organization profile text fields that are not in the target language.
        Checks description, history, founder, current_leader, headquarters, alignment.
        """
        _ORG_TEXT_FIELDS = ['description', 'history', 'founder', 'current_leader',
                            'headquarters', 'alignment', 'member_count']
        wrong = [
            f for f in _ORG_TEXT_FIELDS
            if profile.get(f) and not _is_correct_language(profile[f], language)
        ]
        if not wrong:
            return profile
        sections = "\n".join(
            f"##{f.upper()}##\n{profile[f]}" for f in wrong
        )
        prompt = (
            f"Translate the following organization profile fields to {language}.\n"
            "Keep each section header (e.g. ##DESCRIPTION##) exactly as-is.\n"
            "Return ONLY the translated sections, nothing else.\n\n"
            + sections
        )
        try:
            raw = self._chat(
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
            ).strip()
            for f in wrong:
                tag = f.upper()
                m = re.search(rf'##{tag}##\s*(.*?)(?=##[A-Z_]+##|$)', raw, re.DOTALL)
                if m:
                    translated = m.group(1).strip()
                    if translated:
                        profile[f] = translated
        except Exception as e:
            print(f"Org profile localization error: {e}")
        return profile

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

        Returns a dict: {npc_name: {state, goal, emotion, action}}
        affinity_delta is intentionally omitted — the rule engine injects it.
        Only NPCs whose state changes meaningfully are included.
        """
        npc_list = "\n".join(
            f"- {name}: affinity={d.get('affinity', 0):+d}, state={d.get('state', 'Neutral')}, "
            f"goal={d.get('goal', '')}, emotion={d.get('emotion', '')}, "
            f"personality={d.get('personality', '')}"
            for name, d in npc_states.items()
        ) or "(no tracked NPCs)"

        system_prompt = (
            "You are simulating autonomous NPC reactions in a TRPG.\n"
            "Based on the event, describe how each NPC's emotional state and goal changes.\n"
            "Return ONLY NPCs whose state changes meaningfully — omit unchanged NPCs.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema — write ONLY the text fields shown; do NOT include numeric deltas:\n"
            '{\n'
            '  "NPC Name": {\n'
            '    "state": "<Friendly|Suspicious|Fearful|Hostile|Neutral|Grateful|Angry|...>",\n'
            '    "goal": "<updated short-term goal for this NPC>",\n'
            '    "emotion": "<in-scene emotional state: frightened|excited|angry|sad|calm|curious|...>",\n'
            '    "action": "<what this NPC is visibly doing right now>"\n'
            '  }\n'
            '}\n\n'
            f"Current NPC states:\n{npc_list}\n\n"
            f"CRITICAL: Write all text fields EXCLUSIVELY in {language or 'English'}."
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
            if not isinstance(data, dict):
                return {}
            # Translate reaction fields if wrong language
            for npc_name, changes in data.items():
                if isinstance(changes, dict):
                    wrong = [
                        f for f in ('state', 'goal', 'emotion', 'action')
                        if changes.get(f) and not _is_correct_language(changes[f], language)
                    ]
                    if wrong:
                        sections = "\n".join(f"##{f.upper()}##\n{changes[f]}" for f in wrong)
                        try:
                            tr = self._chat(
                                messages=[{"role": "user", "content":
                                    f"Translate to {language}. Keep section headers.\n\n{sections}"}],
                                json_mode=False,
                            ).strip()
                            for f in wrong:
                                m = re.search(rf'##{f.upper()}##\s*(.*?)(?=##[A-Z]+##|$)', tr, re.DOTALL)
                                if m and m.group(1).strip():
                                    changes[f] = m.group(1).strip()
                        except Exception:
                            pass
            return data
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
    # Organization extraction / generation
    # ------------------------------------------------------------------

    def extract_organizations(self, narrative_text, world_context, language, turn_number=0):
        """
        Scan a narrative passage and return any organizations (governments,
        armies, guilds, cults, academies, mercenary companies, …) mentioned or
        implied in the text.  For each organization found, auto-generate any
        fields that the text does not supply, based on the world context.

        Returns a list of dicts, each matching the schema:
          {
            "name":           display name,
            "type":           category (government / army / guild / cult / academy /
                              mercenary / religious order / secret society / …),
            "founder":        founder name,
            "history":        2-3 sentence founding history and key events,
            "member_count":   rough size (text),
            "current_leader": current leader name,
            "headquarters":   main base or location,
            "alignment":      moral alignment hint,
            "description":    1-2 sentence flavour blurb,
          }
        Returns [] on failure.
        """
        json_schema = (
            '[\n'
            '  {\n'
            '    "name": "Iron Vanguard",\n'
            '    "type": "army",\n'
            '    "founder": "General Aldric Thorne",\n'
            '    "history": "Founded 200 years ago to repel the northern invasion. '
            'Survived three civil wars and now serves the crown.",\n'
            '    "member_count": "~8,000 soldiers",\n'
            '    "current_leader": "Commander Seraphine Voss",\n'
            '    "headquarters": "Ironhold Fortress",\n'
            '    "alignment": "Lawful Neutral",\n'
            '    "description": "An elite standing army renowned for discipline and '
            'unwavering loyalty to the throne."\n'
            '  }\n'
            ']'
        )

        system_prompt = (
            "You are a TRPG lore keeper. Analyse the narrative text below and identify "
            "every organization mentioned or strongly implied (governments, armies, guilds, "
            "religious orders, secret societies, academies, mercenary groups, noble houses "
            "acting as factions, etc.).\n"
            "For each organization:\n"
            "  • Extract details that appear explicitly in the text.\n"
            "  • Auto-generate any MISSING fields so they are plausible and consistent "
            "with the world context provided.\n"
            "  • Never fabricate an organization that is not present or implied.\n"
            f"Write ALL generated text exclusively in {language or 'English'}.\n"
            f"World context: {world_context[:600]}\n\n"
            f"CRITICAL: Respond ONLY with a valid JSON array matching this schema "
            f"(empty array [] if none found):\n{json_schema}"
        )

        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Narrative:\n{narrative_text[:2000]}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            if not isinstance(data, list):
                # LLM sometimes wraps in {"organizations": [...]}
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            data = v
                            break
                    else:
                        data = []
            result = []
            for item in data:
                if not isinstance(item, dict) or not item.get('name'):
                    continue
                result.append({
                    'name':           str(item.get('name', '')).strip(),
                    'type':           str(item.get('type', '')).strip(),
                    'founder':        str(item.get('founder', '')).strip(),
                    'history':        str(item.get('history', '')).strip(),
                    'member_count':   str(item.get('member_count', '')).strip(),
                    'current_leader': str(item.get('current_leader', '')).strip(),
                    'headquarters':   str(item.get('headquarters', '')).strip(),
                    'alignment':      str(item.get('alignment', '')).strip(),
                    'description':    str(item.get('description', '')).strip(),
                    'first_seen_turn': turn_number,
                })
            return result
        except Exception as e:
            print(f"Organization extraction error: {e}")
            return []

    def generate_organization_profile(self, org_name, world_context="", existing_org=None, language="English"):
        """
        Generate a rich profile for an organization that was registered with sparse data.

        Similar to generate_npc_profile — called as a back-fill when an org's
        description or history fields are empty.  Never overwrites existing fields.
        Returns a dict matching WorldManager.register_organization's schema.
        """
        existing_hint = ""
        if existing_org and isinstance(existing_org, dict):
            _all_fields = ('type', 'founder', 'current_leader', 'headquarters',
                           'alignment', 'description', 'history', 'member_count')
            known = [f"{f}: {existing_org[f]}" for f in _all_fields if existing_org.get(f)]
            empty = [f for f in _all_fields if not existing_org.get(f)]
            if known:
                existing_hint += "Known facts (do NOT contradict): " + "; ".join(known) + "\n"
            if empty:
                existing_hint += "Fields that need generating (fill these): " + ", ".join(empty) + "\n"

        system_prompt = (
            "You are a TRPG game master creating a detailed organization profile.\n"
            "Respond ONLY with valid JSON, no markdown.\n\n"
            "Schema:\n"
            '{\n'
            '  "type": "<government|army|guild|religious order|secret society|academy|mercenary company|noble house|other>",\n'
            '  "founder": "<name of the founder>",\n'
            '  "history": "<2-3 sentences of founding history and key events>",\n'
            '  "member_count": "<rough size estimate, e.g. ~500 members>",\n'
            '  "current_leader": "<current leader name and title>",\n'
            '  "headquarters": "<main base or location>",\n'
            '  "alignment": "<moral alignment, e.g. Lawful Neutral>",\n'
            '  "description": "<1-2 sentence flavour blurb summarising the organisation>"\n'
            '}\n\n'
            f"World context: {world_context[:400]}\n"
            f"{existing_hint}"
            f"CRITICAL: Write all text fields exclusively in {language}. "
            f"All details must be consistent with the game world."
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Generate organization profile for: {org_name}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            result = {
                'name':           org_name,
                'type':           str(data.get('type', '')).strip(),
                'founder':        str(data.get('founder', '')).strip(),
                'history':        str(data.get('history', '')).strip(),
                'member_count':   str(data.get('member_count', '')).strip(),
                'current_leader': str(data.get('current_leader', '')).strip(),
                'headquarters':   str(data.get('headquarters', '')).strip(),
                'alignment':      str(data.get('alignment', '')).strip(),
                'description':    str(data.get('description', '')).strip(),
            }
            return self._localize_org_profile(result, language)
        except Exception as e:
            print(f"Organization profile generation error for {org_name!r}: {e}")
            return {'name': org_name}

    def extract_characters(self, text, world_context="", language="English"):
        """
        Extract named NPC/character names mentioned in free text.

        Returns a list of name strings.  Organization names and generic
        titles that don't refer to a specific individual are excluded.
        """
        system_prompt = (
            "You are a TRPG lore keeper. Read the text and list every specific "
            "named NPC or individual character mentioned (NOT organization names, "
            "NOT generic roles like 'the king' unless tied to a proper name).\n"
            "Return ONLY a JSON array of name strings, e.g. [\"Ser Aldric\", \"Elder Morin\"].\n"
            "Return [] if no named characters are found.\n"
            f"World context: {world_context[:300]}"
        )
        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Text:\n{text[:2000]}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            if not isinstance(data, list):
                return []
            return [str(n).strip() for n in data if n and str(n).strip()]
        except Exception as e:
            print(f"Character extraction error: {e}")
            return []

    def extract_relations(self, narrative_text, known_orgs, known_chars,
                          language, turn_number=0):
        """
        Scan a narrative passage for relationships between entities (org↔org,
        org↔char/npc, char↔char).  Only entities already known to the game
        (listed in known_orgs / known_chars) are eligible as endpoints — this
        prevents the LLM from hallucinating new names.

        known_orgs  — list of org name strings (display names)
        known_chars — list of character/NPC name strings

        Returns a list of dicts:
          {
            "source_type":   'org' | 'char' | 'npc',
            "source_key":    identifier (name.lower() for org/npc, str(id) or name for char),
            "source_label":  display name,
            "target_type":   'org' | 'char' | 'npc',
            "target_key":    identifier,
            "target_label":  display name,
            "relation_type": short verb/noun (ally / rival / member / …),
            "strength":      int -100…+100,
            "description":   one-sentence flavour note,
          }
        Returns [] on failure or if nothing found.
        """
        if not known_orgs and not known_chars:
            return []

        org_list  = '\n'.join(f"  org:{n}"  for n in known_orgs)  or '  (none)'
        char_list = '\n'.join(f"  char:{n}" for n in known_chars) or '  (none)'

        json_schema = (
            '[\n'
            '  {\n'
            '    "source_type": "char",\n'
            '    "source_key": "ser aldric",\n'
            '    "source_label": "Ser Aldric",\n'
            '    "target_type": "org",\n'
            '    "target_key": "iron vanguard",\n'
            '    "target_label": "Iron Vanguard",\n'
            '    "relation_type": "member",\n'
            '    "strength": 60,\n'
            '    "description": "Ser Aldric is a sworn knight of the Iron Vanguard."\n'
            '  }\n'
            ']'
        )

        system_prompt = (
            "You are a TRPG lore keeper. Analyse the narrative text below and identify "
            "relationships between the KNOWN ENTITIES listed.\n\n"
            "KNOWN ORGANISATIONS:\n" + org_list + "\n\n"
            "KNOWN CHARACTERS / NPCs:\n" + char_list + "\n\n"
            "Rules:\n"
            "  • Only create edges between entities in the lists above. "
            "Do NOT invent new entity names.\n"
            "  • source_key / target_key must be the entity's name in lowercase.\n"
            "  • relation_type: use short English words "
            "(ally / rival / enemy / member / leader / founder / employs / "
            "patron / contractor / friend / family / mentor / romantic).\n"
            "  • strength: positive = positive bond, negative = hostile bond "
            "(-100 = mortal enemies, 0 = neutral, +100 = inseparable allies).\n"
            "  • Only include relationships that are explicitly stated or "
            "clearly implied in the text.\n"
            f"Write description exclusively in {language or 'English'}.\n\n"
            f"CRITICAL: Respond ONLY with a valid JSON array matching this schema "
            f"(empty array [] if none found):\n{json_schema}"
        )

        try:
            raw = self._chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Narrative:\n{narrative_text[:2000]}"},
                ],
                json_mode=True,
            )
            data = json.loads(_repair_json(raw))
            if not isinstance(data, list):
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            data = v
                            break
                    else:
                        data = []
            result = []
            valid_types = {'org', 'char', 'npc'}
            for item in data:
                if not isinstance(item, dict):
                    continue
                st = item.get('source_type', '').lower()
                tt = item.get('target_type', '').lower()
                sk = str(item.get('source_key', '')).lower().strip()
                tk = str(item.get('target_key', '')).lower().strip()
                rt = str(item.get('relation_type', '')).lower().strip()
                if not (st in valid_types and tt in valid_types and sk and tk and rt):
                    continue
                result.append({
                    'source_type':  st,
                    'source_key':   sk,
                    'source_label': str(item.get('source_label', sk)).strip(),
                    'target_type':  tt,
                    'target_key':   tk,
                    'target_label': str(item.get('target_label', tk)).strip(),
                    'relation_type': rt,
                    'strength':     max(-100, min(100, int(item.get('strength', 0)))),
                    'description':  str(item.get('description', '')).strip(),
                    'since_turn':   turn_number,
                })
            return result
        except Exception as e:
            print(f"Relation extraction error: {e}")
            return []

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def generate_turn(self, system_prompt, user_action, context=""):
        """Legacy single-call wrapper. Delegates to render_narrative."""
        outcome = f"Player action: {user_action}"
        return self.render_narrative(system_prompt, outcome, context)

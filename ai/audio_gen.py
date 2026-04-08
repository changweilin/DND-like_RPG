# ai/audio_gen.py
# AudioGenerator — scene-aware audio cue stub.
#
# Current state: stub only.  No real audio is generated.
# The interface is designed so a future implementation can swap in
# MusicGen / AudioLDM / Bark TTS without changing callers.
#
# Integration points (app.py):
#   1. Initialise once: st.session_state.audio_gen = AudioGenerator()
#   2. After each DM turn: audio_gen.on_scene_change(scene_type, combat_result, flee_result)
#   3. On Game Over:       audio_gen.play_cue('game_over')
#   4. On level-up:        audio_gen.play_cue('level_up')

# ---------------------------------------------------------------------------
# Scene-type → BGM theme mapping
# ---------------------------------------------------------------------------
_SCENE_BGM = {
    'combat':       'battle_intense',
    'social':       'tavern_ambient',
    'exploration':  'adventure_calm',
    'puzzle':       'mystery_tense',
    'rest':         'campfire_peaceful',
    'cinematic':    'cinematic_swell',
}

# One-shot SFX cues fired on specific game events
_EVENT_SFX = {
    'hit':          'sword_hit',
    'critical':     'critical_strike',
    'miss':         'sword_whoosh',
    'flee_success': 'footsteps_running',
    'flee_fail':    'grunt_pain',
    'level_up':     'fanfare_short',
    'game_over':    'defeat_sting',
    'loot_drop':    'coin_drop',
    'heal':         'heal_chime',
    'status_poison':'bubbling_liquid',
}


class AudioGenerator:
    """
    Scene-aware audio cue manager.

    Stub implementation: logs what would be played instead of producing
    real audio.  Future integration targets:
      - BGM:  MusicGen (Meta) or AudioLDM-2 for generative background music
      - SFX:  Bark TTS for voiced DM narration; pre-baked WAV for combat cues
      - UI:   HTML5 <audio> tags injected via st.html() or st.components.v1.html()

    All public methods return dicts describing the cue so the UI layer
    can decide how to present it (log, play via JS, or ignore).
    """

    def __init__(self):
        self._current_bgm = None    # currently playing BGM theme key
        self._cue_log     = []      # history of all cues fired this session
        self._enabled     = False   # True once a real backend is wired up

    @property
    def enabled(self):
        return self._enabled

    def on_scene_change(self, scene_type, combat_result=None,
                        flee_result=None, loot_xp=None):
        """
        Called after each DM turn.  Fires the appropriate BGM transition
        and any one-shot SFX implied by the combat/flee/loot outcome.

        Returns a list of cue dicts for optional UI rendering.
        """
        cues = []

        # BGM transition
        bgm_key = _SCENE_BGM.get(scene_type, _SCENE_BGM['exploration'])
        if bgm_key != self._current_bgm:
            cues.append(self._fire_bgm(bgm_key))

        # Combat SFX
        if combat_result:
            if combat_result.get('critical'):
                cues.append(self._fire_sfx('critical'))
            elif combat_result.get('hit'):
                cues.append(self._fire_sfx('hit'))
            else:
                cues.append(self._fire_sfx('miss'))

        # Flee SFX
        if flee_result:
            key = 'flee_success' if flee_result.get('fled') else 'flee_fail'
            cues.append(self._fire_sfx(key))

        # Loot / level-up SFX
        if loot_xp:
            if loot_xp.get('loot_dropped'):
                cues.append(self._fire_sfx('loot_drop'))
            if loot_xp.get('leveled_up'):
                cues.append(self._fire_sfx('level_up'))

        return cues

    def play_cue(self, event_key):
        """Fire a named one-shot SFX (e.g. 'game_over', 'heal')."""
        sfx = _EVENT_SFX.get(event_key)
        if sfx:
            return self._fire_sfx(event_key)
        return {}

    def _fire_bgm(self, theme_key):
        self._current_bgm = theme_key
        cue = {'type': 'bgm', 'theme': theme_key, 'file': f'{theme_key}.ogg'}
        self._cue_log.append(cue)
        return cue

    def _fire_sfx(self, event_key):
        sfx = _EVENT_SFX.get(event_key, event_key)
        cue = {'type': 'sfx', 'event': event_key, 'file': f'{sfx}.wav'}
        self._cue_log.append(cue)
        return cue

    # ------------------------------------------------------------------
    # Legacy stub methods (kept for backward compatibility)
    # ------------------------------------------------------------------

    def generate_bgm(self, theme, output_path):
        """Legacy stub — logs intent only."""
        cue = self._fire_bgm(theme)
        return cue

    def generate_sfx(self, description, output_path):
        """Legacy stub — logs intent only."""
        cue = {'type': 'sfx', 'event': description, 'file': output_path}
        self._cue_log.append(cue)
        return cue

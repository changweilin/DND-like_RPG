"""
engine/image_prompts.py — World-aware image prompt builder.

Builds SDXL-Turbo / Diffusers text-to-image prompts for:
  - Continent world maps       (build_map_prompt)
  - Character portraits        (build_portrait_prompt)
  - Cinematic game events      (classify_cinematic_event, build_cinematic_prompt)

Cinematic triggers (in priority order):
  1. battle_start   — scene_type transitions from non-combat → combat
  2. battle_end     — scene_type transitions from combat → non-combat
  3. plot_twist     — narrative contains dramatic keywords
  4. npc_event      — NPC relationship delta ≥ 20 (arrival / departure / betrayal)
  5. milestone      — turn_count is a multiple of IMAGE_GEN_MILESTONE_TURNS
  6. new_location   — turn_data['location_change'] is set
  - Character portraits   (build_portrait_prompt)

Pure data module — no Streamlit, no torch, no diffusers imports.
All style and world-aesthetic data lives here so it can be tested
without a GPU and imported freely from both engine and ui layers.
"""

# ---------------------------------------------------------------------------
# Image style presets
# ---------------------------------------------------------------------------
# Each preset drives the positive suffix (style modifier) and negative prompt.
# 'custom' has empty suffix — the user fills it via text_input.

IMAGE_STYLES = {
    'fantasy_art': {
        'name':     '奇幻插畫',
        'name_en':  'Fantasy Art',
        'suffix':   ('digital fantasy art, concept art, highly detailed illustration, '
                     'vibrant colors, artstation quality, dramatic lighting, professional'),
        'negative': 'photo, realistic, modern, blurry, low quality, watermark, text',
    },
    'watercolor': {
        'name':     '水彩畫',
        'name_en':  'Watercolor',
        'suffix':   ('watercolor painting, soft delicate washes, hand-painted, '
                     'gentle muted colors, wet-on-wet technique, artistic, transparent'),
        'negative': 'digital, harsh lines, photorealistic, dark, low quality',
    },
    'anime': {
        'name':     '動漫風格',
        'name_en':  'Anime',
        'suffix':   ('anime style illustration, manga art, vibrant cel-shaded, '
                     'crisp linework, highly detailed, studio quality'),
        'negative': 'western comic, realistic, photo, low quality, deformed',
    },
    'realistic': {
        'name':     '寫實風格',
        'name_en':  'Realistic',
        'suffix':   ('photorealistic, cinematic lighting, highly detailed, '
                     '8k uhd, sharp focus, professional photography'),
        'negative': 'cartoon, anime, painting, sketch, blurry, low quality',
    },
    'pixel_art': {
        'name':     '像素藝術',
        'name_en':  'Pixel Art',
        'suffix':   ('detailed pixel art, retro JRPG style, 16-bit aesthetic, '
                     'carefully crafted pixel illustration, nostalgic'),
        'negative': 'smooth gradients, realistic, photo, blurry, low quality',
    },
    'ink': {
        'name':     '墨水插畫',
        'name_en':  'Ink Drawing',
        'suffix':   ('detailed ink drawing, pen and ink illustration, '
                     'strong linework, cross-hatching, selective color accents'),
        'negative': 'digital, photo, blurry, low quality, watermark',
    },
    'custom': {
        'name':     '自定義',
        'name_en':  'Custom',
        'suffix':   '',   # user fills this via the custom_suffix text_input
        'negative': 'blurry, low quality, watermark, deformed',
    },
}

# ---------------------------------------------------------------------------
# World-setting map aesthetics
# (terrain desc, atmosphere desc, map-type label)
# ---------------------------------------------------------------------------
_WORLD_MAP_AESTHETICS = {
    'dnd5e': (
        'rolling hills, deep ancient forests, jagged mountains, scattered dungeon entrances, '
        'frontier towns and hamlets, winding rivers, coastal harbors',
        'heroic high fantasy, magical atmosphere, glowing rune stones, distant dragon silhouettes',
        'illustrated fantasy world map, cartographic parchment style, tolkien-esque continent',
    ),
    'pathfinder': (
        'inner sea continent, diverse biomes, ancient ruins, sprawling harbor cities, '
        'desert regions, northern frozen wastes, jungle interiors',
        'epic political fantasy, rich nation-state borders, adventurer trade routes',
        'illustrated pathfinder world map, political regions marked, aged ink-on-parchment',
    ),
    'warhammer_fantasy': (
        'old world empire cities, dark forests of chaos taint, northern wastes and chaos storms, '
        'river trading towns, dwarf holds in mountains, blighted marshes',
        'grimdark atmosphere, oppressive threatening sky, corruption spreading from the north',
        'dark fantasy map, old world cartography, sinister wilderness regions marked',
    ),
    'wh40k': (
        'hive city mega-structures, underhive slums, industrial wastelands, '
        'void station orbital platforms, toxic promethium refineries, ruined zones',
        'grimdark far future, industrial hellscape, toxic ochre atmosphere, gothic spires',
        'imperial sector star map, gothic architecture aesthetic, mechanicus technical overlay',
    ),
    'shadowrun': (
        'sprawling metropolitan districts, megacorp arcology towers, barrens slums, '
        'matrix access node hubs, gang territory borders, smuggling docks',
        'cyberpunk dystopia, rain-slicked neon streets, corporate logo lights, '
        'holo-billboard advertisements, perpetual night',
        'cyberpunk city district map, matrix overlay hexagons, neon highlights on dark map',
    ),
    'world_of_darkness': (
        'gothic city districts, elysium masquerade locations, haunted cemeteries, '
        'sewer warrens, cathedral spires, fog-filled parks at midnight',
        'gothic horror, eternal night, blood moon overhead, shadows and deep mystery',
        'gothic vampire masquerade city map, noir atmosphere, domain territories marked',
    ),
    'call_of_cthulhu': (
        'new england coastline, arkham town streets, miskatonic river delta, '
        'fog-shrouded rocky hills, old colonial architecture, lighthouse coast',
        'lovecraftian horror, 1920s atmosphere, creeping cosmic dread, ancient standing stones',
        '1920s new england town map, lovecraftian horror, aged parchment with hand annotations',
    ),
    'iron_kingdoms': (
        'immoren continent, mechanikal factory-cities, steam-vented ruins, '
        'blighted marshes, warjack assembly ports, arcane storm fronts',
        'full metal fantasy, steam and magic intertwined, warjack smoke plumes, arcane lightning',
        'iron kingdoms continent map, steampunk fantasy aesthetic, mechanikal detail overlay',
    ),
    'blades_in_the_dark': (
        'doskvol canal districts, lightning rail lines, leaky industrial waterways, '
        'gang territory borders, deathlands beyond city walls, ghost-lit sectors',
        'industrial gothic, perpetual supernatural darkness, electroplasmic lightning towers, '
        'smog and fog, flickering gas lamps',
        'doskvol city district map, gothic industrial zones, criminal faction territories',
    ),
    'hearts_of_wulin': (
        'jianghu martial world landscape, mist-covered mountain peaks, '
        'bamboo forest valleys, ancient temple complexes, river market towns, '
        'hidden sect strongholds',
        'wuxia aesthetic, flowing chi energy trails, classical chinese landscape beauty',
        'chinese ink wash continent map, jianghu locations in classical painting style',
    ),
    'l5r': (
        'rokugan empire provinces, celestial mountain ranges, shadow lands border, '
        'castle districts, sacred forest groves, elemental spirit realms beyond the veil',
        'japanese feudal fantasy, cherry blossom atmosphere, honor and duty, elemental spirits',
        'rokugan map, japanese feudal empire provinces, elemental rings aesthetic, mon crests',
    ),
    'deadlands': (
        'weird west territories, ghost rock mine claims, sun-baked desert badlands, '
        'frontier towns and rail stations, haunted canyons, native spirit lands',
        'weird west supernatural frontier, ghost rock steam-tech, undead outlaws, sepia tones',
        'weird west territory map, frontier cartography style, ghost rock deposits and rail lines',
    ),
    'mutant_year_zero': (
        'the ark ruins encampment, dead zones of radiation, mutant clan territories, '
        'old world ruin sites, scavenger trails, zone sectors',
        'post-apocalyptic wasteland, radiation haze, mutant settlements, grey-green toxic sky',
        'post-apocalyptic zone map, ark territory, ruin markers, survival zone designations',
    ),
    'gloomhaven': (
        'gloomhaven city harbor, dungeon entrance locations, ancient catacomb networks, '
        'demon-infested ruined citadels, frozen north peaks, dark forest regions',
        'dark tactical fantasy, gloomy overcast atmosphere, ancient evil awakening',
        'gloomhaven area map, dungeon locations, tactical rpg aesthetic, scenario map style',
    ),
}

# ---------------------------------------------------------------------------
# World-setting portrait aesthetics
# ---------------------------------------------------------------------------
_WORLD_PORTRAIT_AESTHETICS = {
    'dnd5e':             'classic D&D fantasy RPG character portrait, heroic fantasy equipment',
    'pathfinder':        'pathfinder RPG character, richly detailed fantasy adventuring gear',
    'warhammer_fantasy': 'warhammer fantasy character, grim and battle-worn, old world grit',
    'wh40k':             'warhammer 40k character, gothic sci-fi armor, imperial iconography',
    'shadowrun':         'shadowrun cyberpunk character, chrome augmentations, neon-lit shadows',
    'world_of_darkness': 'vampire the masquerade character, gothic elegant, masquerade fashion',
    'call_of_cthulhu':   '1920s investigator character, period suit or coat, lovecraftian dread',
    'iron_kingdoms':     'iron kingdoms character, steampunk armor, mechanikal augmentation',
    'blades_in_the_dark':'doskvol scoundrel, dark industrial gothic, criminal equipment',
    'hearts_of_wulin':   'wuxia martial artist, flowing silk robes, dynamic chi-charged pose',
    'l5r':               'rokugan clan samurai, traditional japanese armor, clan mon insignia',
    'deadlands':         'weird west character, frontier clothing, ghost-rock supernatural aura',
    'mutant_year_zero':  'post-apocalyptic mutant, visible mutation, scavenged patchwork armor',
    'gloomhaven':        'gloomhaven mercenary, dungeon-worn gear, tactical mercenary equipment',
}

# ---------------------------------------------------------------------------
# Race appearance descriptors
# ---------------------------------------------------------------------------
_RACE_DESCRIPTORS = {
    'Human':    'human, expressive face, diverse features',
    'Elf':      'elf with pointed ears, elegant refined features, lithe graceful build',
    'Dwarf':    'stocky dwarf, intricate braided beard, rugged weathered features',
    'Orc':      'orc with prominent tusks, powerful green-skinned build, fierce eyes',
    'Halfling': 'small halfling, cheerful round face, quick clever eyes, nimble frame',
}

# ---------------------------------------------------------------------------
# Class visual descriptors
# ---------------------------------------------------------------------------
_CLASS_VISUAL = {
    'warrior': ('heavy battle armor, shield or greatsword, '
                'battle-scarred, imposing combat stance'),
    'mage':    ('arcane robes, glowing staff or orb, '
                'magical energy aura, mysterious intense gaze'),
    'rogue':   ('dark leather armor, twin daggers, hood casting shadow, '
                'agile crouching stance, hidden in shadow'),
    'cleric':  ('divine vestments, holy symbol radiating light, '
                'healing energy glow, protective devoted stance'),
}

# ---------------------------------------------------------------------------
# Personality → expression mapping
# ---------------------------------------------------------------------------
_PERSONALITY_EXPRESSIONS = [
    (['brave', 'bold', 'fierce', 'fearless', '勇', '無畏'], 'determined confident expression'),
    (['wise', 'calm', 'sage', 'stoic', '智', '冷靜'],       'calm wise scholarly expression'),
    (['cunning', 'sly', 'clever', 'sharp', '狡', '機智'],   'cunning knowing smirk'),
    (['kind', 'gentle', 'compassion', 'heal', '善', '慈'],  'gentle compassionate warm expression'),
    (['fierce', 'angry', 'battle', 'war', '怒', '戰'],      'fierce battle-ready glare'),
    (['mysterious', 'dark', 'shadow', '神秘', '暗'],        'enigmatic shadowed expression'),
]


def _infer_expression(personality):
    """Map personality free text to a visual expression descriptor."""
    if not personality:
        return 'strong adventurer expression'
    pl = personality.lower()
    for keywords, expression in _PERSONALITY_EXPRESSIONS:
        if any(k in pl for k in keywords):
            return expression
    return 'resolute adventurer expression'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_map_prompt(ws, image_style='fantasy_art', custom_suffix=''):
    """
    Build an image generation prompt for a continent world map.

    Args:
        ws            (dict): World setting dict (from config.WORLD_SETTINGS).
        image_style   (str):  Key from IMAGE_STYLES.
        custom_suffix (str):  User-supplied extra suffix (overrides style suffix if non-empty).

    Returns:
        str — positive prompt.
    """
    ws_id = ws.get('id', 'dnd5e')
    terrain, atmosphere, map_label = _WORLD_MAP_AESTHETICS.get(
        ws_id,
        ('diverse forests, mountains, towns', 'fantasy atmosphere', 'illustrated world map'),
    )
    style = IMAGE_STYLES.get(image_style, IMAGE_STYLES['fantasy_art'])
    suf   = custom_suffix.strip() or style['suffix']

    return (
        f"{map_label}, "
        f"{terrain}, "
        f"{atmosphere}, "
        f"overhead birds-eye view, highly detailed, "
        f"{suf}"
    )


def build_portrait_prompt(char, ws, image_style='fantasy_art', custom_suffix=''):
    """
    Build an image generation prompt for a character portrait.

    Args:
        char          (obj):  Character with .name, .race, .char_class,
                              .appearance (str), .personality (str).
        ws            (dict): World setting dict.
        image_style   (str):  Key from IMAGE_STYLES.
        custom_suffix (str):  User-supplied extra suffix.

    Returns:
        str — positive prompt.
    """
    ws_id  = ws.get('id', 'dnd5e')
    cls    = (getattr(char, 'char_class', 'warrior') or 'warrior').lower().strip()
    race   = (getattr(char, 'race', 'Human') or 'Human').strip()

    race_desc   = _RACE_DESCRIPTORS.get(race, f'{race} character')
    class_desc  = _CLASS_VISUAL.get(cls, 'adventurer with equipment')
    ws_aes      = _WORLD_PORTRAIT_AESTHETICS.get(ws_id, 'fantasy character portrait')
    appearance  = (getattr(char, 'appearance', '') or '').strip()[:100]
    personality = (getattr(char, 'personality', '') or '').strip()
    expression  = _infer_expression(personality)

    style = IMAGE_STYLES.get(image_style, IMAGE_STYLES['fantasy_art'])
    suf   = custom_suffix.strip() or style['suffix']

    parts = [
        f"portrait of {race_desc}",
        class_desc,
        ws_aes,
    ]
    if appearance:
        parts.append(appearance)
    parts.append(expression)
    parts.append('character portrait, face close-up, dramatic lighting, highly detailed')
    if suf:
        parts.append(suf)

    return ', '.join(p for p in parts if p)


def get_map_negative_prompt(image_style='fantasy_art'):
    """Negative prompt for map generation."""
    style = IMAGE_STYLES.get(image_style, IMAGE_STYLES['fantasy_art'])
    return (style.get('negative', '') +
            ', characters, people, text overlay, watermark, blurry, low quality')


def get_portrait_negative_prompt(image_style='fantasy_art'):
    """Negative prompt for portrait generation."""
    style = IMAGE_STYLES.get(image_style, IMAGE_STYLES['fantasy_art'])
    return (style.get('negative', '') +
            ', map, landscape, multiple people, text, watermark, '
            'deformed face, extra limbs, blurry, low quality')


# ---------------------------------------------------------------------------
# Cinematic event detection
# ---------------------------------------------------------------------------

# Narrative keywords that signal a major plot moment
_PLOT_TWIST_KEYWORDS = [
    # English
    'betrayed', 'betrayal', 'reveals', 'revealed', 'revelation', 'shocking', 'suddenly',
    'dead', 'died', 'death', 'fallen', 'killed', 'murdered', 'slain', 'executed',
    'coronation', 'apocalypse', 'sacrifice', 'ambush', 'trap sprung', 'true identity',
    'secret revealed', 'final boss', 'prophecy', 'destined', 'chosen one',
    'portal opens', 'ancient evil', 'world-ending',
    # Chinese
    '背叛', '揭露', '震驚', '突然', '死亡', '陷阱', '真相', '犧牲',
    '暗殺', '末日', '預言', '命運', '覺醒', '轉折', '秘密', '身份',
]

# Human-readable Chinese labels for each event type
_CINEMATIC_LABELS = {
    'battle_start': '⚔️ 戰鬥開始',
    'battle_end':   '🏆 戰鬥結束',
    'plot_twist':   '🎭 劇情重大轉折',
    'npc_event':    '👤 重要 NPC 場面',
    'milestone':    '📸 冒險里程碑',
    'new_location': '🗺️ 新地點探索',
}


def classify_cinematic_event(turn_data, prev_scene_type, turn_count, narrative=''):
    """
    Decide whether this turn warrants a cinematic scene image.

    Args:
        turn_data       (dict): Narrative Event dict from EventManager.process_turn().
        prev_scene_type (str):  scene_type of the PREVIOUS DM turn; 'exploration' if none.
        turn_count      (int):  current game turn number (after increment).
        narrative       (str):  narrative text returned this turn.

    Returns:
        dict {'type': str, 'label': str} or None.
    """
    from engine.config import config  # avoid circular import at module level

    scene_type = turn_data.get('scene_type', 'exploration')

    # Priority 1 — combat boundary (highest dramatic impact)
    if scene_type == 'combat' and prev_scene_type != 'combat':
        return {'type': 'battle_start', 'label': _CINEMATIC_LABELS['battle_start']}
    if prev_scene_type == 'combat' and scene_type != 'combat':
        return {'type': 'battle_end',   'label': _CINEMATIC_LABELS['battle_end']}

    # Priority 2 — plot-twist keyword in narrative
    if narrative:
        n_lower = narrative.lower()
        for kw in _PLOT_TWIST_KEYWORDS:
            if kw in n_lower:
                return {'type': 'plot_twist', 'label': _CINEMATIC_LABELS['plot_twist']}

    # Priority 3 — significant NPC relationship shift
    npc_changes = turn_data.get('npc_relationship_changes') or {}
    for _npc, delta in npc_changes.items():
        if isinstance(delta, dict):
            delta_val = delta.get('affinity_delta', 0)
        else:
            delta_val = delta if isinstance(delta, (int, float)) else 0
        if abs(delta_val) >= 20:
            return {'type': 'npc_event', 'label': _CINEMATIC_LABELS['npc_event']}

    # Priority 4 — milestone turn
    milestone = getattr(config, 'IMAGE_GEN_MILESTONE_TURNS', 5)
    if milestone > 0 and turn_count > 0 and (turn_count % milestone == 0):
        return {
            'type':  'milestone',
            'label': f"{_CINEMATIC_LABELS['milestone']} (Turn {turn_count})",
        }

    # Priority 5 — major location change
    if turn_data.get('location_change'):
        return {'type': 'new_location', 'label': _CINEMATIC_LABELS['new_location']}

    return None


# ---------------------------------------------------------------------------
# Cinematic prompt builder
# ---------------------------------------------------------------------------

_CINEMATIC_TEMPLATES = {
    'battle_start': (
        'epic battle commencing, {char_desc}, {location}, {ws_aes}, {tone}, '
        'dynamic action pose, motion blur, dramatic clash of weapons, '
        'cinematic composition, {suf}'
    ),
    'battle_end': (
        'aftermath of epic battle, victorious heroes standing amid fallen foes, '
        '{char_desc}, {location}, {ws_aes}, {tone}, '
        'dramatic lighting, exhaustion and triumph, cinematic, {suf}'
    ),
    'plot_twist': (
        'dramatic revelation moment, {location}, {ws_aes}, {tone}, '
        'shocked faces, dramatic light beam from above, '
        'high-stakes emotional confrontation, cinematic cutscene, {suf}'
    ),
    'npc_event': (
        'dramatic NPC encounter, tense face-to-face meeting, '
        '{location}, {ws_aes}, {tone}, '
        'intense character interaction, cinematic framing, {suf}'
    ),
    'milestone': (
        'grand adventure panorama, {char_desc} on a heroic journey, '
        '{location}, {ws_aes}, {tone}, '
        'epic vista, sense of scale and wonder, highly detailed, {suf}'
    ),
    'new_location': (
        'first glimpse of {location}, establishing wide shot, '
        '{ws_aes}, {tone}, '
        'atmospheric depth, sense of awe and mystery, '
        'highly detailed environment, {suf}'
    ),
}


def build_cinematic_prompt(event_type, turn_data, char, ws,
                           image_style='fantasy_art', custom_suffix=''):
    """
    Build a text-to-image prompt for a cinematic game event.

    Args:
        event_type    (str):  Key from _CINEMATIC_TEMPLATES.
        turn_data     (dict): Narrative Event dict.
        char          (obj):  Active character (.race, .char_class).
        ws            (dict): World setting dict.
        image_style   (str):  Key from IMAGE_STYLES.
        custom_suffix (str):  User override suffix.

    Returns:
        str — positive prompt.
    """
    ws_id  = ws.get('id', 'dnd5e')
    style  = IMAGE_STYLES.get(image_style, IMAGE_STYLES['fantasy_art'])
    suf    = custom_suffix.strip() or style['suffix']
    ws_aes = _WORLD_PORTRAIT_AESTHETICS.get(ws_id, 'fantasy setting')
    tone   = ws.get('tone', 'fantasy adventure')[:80]

    cls  = (getattr(char, 'char_class', 'warrior') or 'warrior').lower().strip()
    race = (getattr(char, 'race', 'Human') or 'Human').strip()
    char_desc = f"{_RACE_DESCRIPTORS.get(race, race)}, {_CLASS_VISUAL.get(cls, 'adventurer')}"

    location = (turn_data.get('location_change') or '').strip() or 'a dramatic scene'

    template = _CINEMATIC_TEMPLATES.get(event_type, _CINEMATIC_TEMPLATES['milestone'])
    return template.format(
        char_desc=char_desc,
        location=location,
        ws_aes=ws_aes,
        tone=tone,
        suf=suf,
    )

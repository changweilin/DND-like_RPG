"""
engine/board.py — Pure (non-Streamlit) game-board logic.

Handles world-map position assignment for the interactive game board UI.
Keeping this separate from ui/app.py makes it testable without a running
Streamlit server.

Map layout (row 0 = top, row 4 = bottom):
  Row 0 — ✨ Otherworldly / Astral planes
  Row 1 — ⛰️ Wilderness / Mountains
  Row 2 — 🌲 Forests / Nature
  Row 3 — 🏘️ Towns / Settlements  ← starting zone
  Row 4 — 💀 Dungeons / Underground
"""

MAP_ROWS = 5
MAP_COLS = 8

# Keyword → (preferred_row, icon).  Checked in order — first match wins.
_LOC_KEYWORDS = [
    # Dungeon / Underground (row 4)
    (['dungeon', 'cavern', 'crypt', 'tomb', 'underground', 'sewer', 'mine',
      'abyss', 'pit', 'lair', 'vault', 'depths', 'catacomb'], 4, '💀'),
    (['cave', 'grotto', 'tunnel'], 4, '🦇'),

    # Settlements (row 3)
    (['castle', 'citadel', 'stronghold', 'palace', 'keep', 'fort'], 3, '🏰'),
    (['temple', 'shrine', 'church', 'monastery', 'cathedral'], 3, '⛪'),
    (['harbor', 'port', 'dock', 'wharf', 'pier'], 3, '⚓'),
    (['guild', 'academy', 'tower', 'library'], 3, '🏛️'),
    (['town', 'village', 'city', 'hamlet', 'settlement', 'inn',
      'tavern', 'market', 'bazaar', 'plaza', 'quarter', 'district'], 3, '🏘️'),

    # Forest / Nature (row 2)
    (['swamp', 'marsh', 'bog', 'fen', 'mire'], 2, '🌾'),
    (['jungle', 'rainforest', 'mangrove'], 2, '🌴'),
    (['forest', 'wood', 'grove', 'thicket', 'copse'], 2, '🌲'),
    (['plains', 'field', 'meadow', 'prairie', 'grassland'], 2, '🌾'),
    (['road', 'path', 'trail', 'bridge', 'crossing', 'ford'], 2, '🛤️'),
    (['river', 'lake', 'pond', 'stream', 'waterfall'], 2, '🌊'),

    # Wilderness / Mountains (row 1)
    (['desert', 'wasteland', 'badlands', 'dunes'], 1, '🏜️'),
    (['tundra', 'snowfield', 'glacier', 'ice'], 1, '❄️'),
    (['ruin', 'wreckage', 'remnant', 'rubble'], 1, '🏚️'),
    (['mountain', 'peak', 'cliff', 'highland', 'hill',
      'ridge', 'gorge', 'canyon', 'pass'], 1, '⛰️'),

    # Otherworldly (row 0)
    (['void', 'astral', 'ethereal', 'shadow realm', 'nexus',
      'plane', 'dimension', 'realm'], 0, '✨'),
    (['sky', 'cloud', 'celestial', 'heavens'], 0, '☁️'),
]


def detect_location_type(name):
    """
    Return (row, icon) for a location name based on keyword matching.

    row 0 = otherworldly, row 4 = underground.
    Default (no keyword match) → row 3 (settlement), icon '🗺️'.
    """
    name_lower = name.lower()
    for keywords, row, icon in _LOC_KEYWORDS:
        if any(k in name_lower for k in keywords):
            return row, icon
    return 3, '🗺️'


def assign_map_position(loc_name, existing_map, map_cols=MAP_COLS, map_rows=MAP_ROWS):
    """
    Return a (row, col, icon) grid position for loc_name.

    If loc_name already exists in existing_map, returns the stored entry.
    Otherwise finds the nearest free cell in the preferred row and returns it.
    Does NOT mutate existing_map — caller is responsible for storing the result.

    Args:
        loc_name    (str):  Location name to place.
        existing_map (dict): {name: {row, col, icon}} — already-placed locations.
        map_cols    (int):  Grid width.
        map_rows    (int):  Grid height.

    Returns:
        (row, col, icon) tuple.
    """
    if loc_name in existing_map:
        e = existing_map[loc_name]
        return e['row'], e['col'], e['icon']

    pref_row, icon = detect_location_type(loc_name)

    # Deterministic preferred column from name hash
    pref_col = abs(hash(loc_name)) % map_cols

    occupied = {(v['row'], v['col']) for v in existing_map.values()}

    # Search preferred row first, radiating outward from preferred_col
    for offset in range(map_cols):
        for sign in (1, -1):
            col = (pref_col + offset * sign) % map_cols
            if (pref_row, col) not in occupied:
                return pref_row, col, icon

    # All cells in preferred row occupied — try other rows (closest first)
    row_order = sorted(range(map_rows), key=lambda r: abs(r - pref_row))
    for row in row_order:
        for col in range(map_cols):
            if (row, col) not in occupied:
                return row, col, icon

    # Absolute last resort: overwrite preferred cell
    return pref_row, pref_col, icon


def build_map_html(world_map, player_positions, party, active_char_id, player_flags):
    """
    Build an HTML string for the interactive world-map grid.

    Args:
        world_map       (dict): {loc_name: {row, col, icon}}
        player_positions (dict): {char_id: {location, row, col}}
        party           (list): list of Character-like objects (need .id, .name, .hp)
        active_char_id  (int):  ID of the currently active character
        player_flags    (list): emoji flag per party slot

    Returns:
        str — complete HTML ready for st.markdown(unsafe_allow_html=True).
    """
    # Build grid array
    grid = [[None] * MAP_COLS for _ in range(MAP_ROWS)]
    for loc_name, pos in world_map.items():
        r, c = pos['row'], pos['col']
        if 0 <= r < MAP_ROWS and 0 <= c < MAP_COLS:
            grid[r][c] = {'name': loc_name, 'icon': pos['icon'], 'players': []}

    # Place player tokens on their cells
    for i, char in enumerate(party):
        pos = player_positions.get(char.id)
        if pos:
            r, c = pos['row'], pos['col']
            if 0 <= r < MAP_ROWS and 0 <= c < MAP_COLS and grid[r][c] is not None:
                flag      = player_flags[i] if i < len(player_flags) else '👤'
                is_active = (char.id == active_char_id)
                alive     = char.hp > 0
                grid[r][c]['players'].append({
                    'flag': flag, 'name': char.name,
                    'active': is_active, 'alive': alive,
                })

    row_labels = ['✨ 異界', '⛰️ 荒野', '🌲 自然', '🏘️ 城鎮', '💀 地下城']

    css = (
        "<style>"
        ".rpg-map{border-collapse:collapse;width:100%;table-layout:fixed;}"
        ".rpg-rlabel{font-size:0.68em;color:#555;writing-mode:vertical-rl;"
        " text-orientation:mixed;text-align:center;vertical-align:middle;"
        " padding:2px 3px;background:#0a0a0a;width:28px;white-space:nowrap;}"
        ".rpg-cell{border:1px solid #2a2a4a;text-align:center;"
        " vertical-align:middle;height:62px;background:#080814;"
        " font-size:0.72em;overflow:hidden;cursor:default;}"
        ".rpg-fog{background:#050510;color:#1e1e2e;font-size:1.4em;}"
        ".rpg-visited{background:#111128;}"
        ".rpg-active{background:#0a2408;border:2px solid #4caf50;}"
        ".rpg-has-players{background:#111e18;}"
        "@keyframes rpg-glow{0%,100%{border-color:#4caf50;box-shadow:0 0 4px #4caf50;}"
        " 50%{border-color:#80ff90;box-shadow:0 0 10px #80ff90;}}"
        ".rpg-active.rpg-has-players{animation:rpg-glow 1.8s ease-in-out infinite;}"
        ".rpg-icon{font-size:1.5em;line-height:1.1;display:block;}"
        ".rpg-name{font-size:0.66em;color:#8888aa;display:block;"
        " overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:92px;margin:0 auto;}"
        ".rpg-tokens{font-size:0.95em;display:block;line-height:1.1;margin-top:2px;}"
        ".rpg-dead-token{opacity:0.35;}"
        "</style>"
    )

    rows_html = ['<table class="rpg-map">']
    for r in range(MAP_ROWS):
        rows_html.append('<tr>')
        rows_html.append(f'<td class="rpg-rlabel">{row_labels[r]}</td>')
        for c in range(MAP_COLS):
            cell = grid[r][c]
            if cell is None:
                rows_html.append('<td class="rpg-cell rpg-fog">❓</td>')
            else:
                players   = cell['players']
                has_act   = any(p['active'] for p in players)
                has_plrs  = bool(players)
                classes   = ['rpg-cell', 'rpg-visited']
                if has_act:
                    classes.append('rpg-active')
                if has_plrs:
                    classes.append('rpg-has-players')

                short_name = (cell['name'][:11] + '…') if len(cell['name']) > 11 else cell['name']
                tokens_html = ''.join(
                    f'<span class="{"" if p["alive"] else "rpg-dead-token"}"'
                    f' title="{p["name"]}">{p["flag"]}</span>'
                    for p in players
                )

                rows_html.append(
                    f'<td class="{" ".join(classes)}">'
                    f'<span class="rpg-icon">{cell["icon"]}</span>'
                    f'<span class="rpg-name" title="{cell["name"]}">{short_name}</span>'
                    + (f'<span class="rpg-tokens">{tokens_html}</span>' if tokens_html else '')
                    + '</td>'
                )
        rows_html.append('</tr>')
    rows_html.append('</table>')

    return css + '\n'.join(rows_html)

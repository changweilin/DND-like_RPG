"""
engine/story_saver.py — Image and story persistence for Book Mode.

Saves generated images (continent maps, portraits, cinematic scenes) to a
per-save-file directory, paired with corresponding narrative text as JSON
sidecars.  Maintains a compressed story log for the 📕 Book Mode tab.

Directory layout:
  saves/{save_name}/
    images/
      map_turn0.png               ← continent map
      map_turn0.json              ← {event_type, turn, text, timestamp}
      portrait_{name}_turn0.png   ← character portrait
      portrait_{name}_turn0.json
      scene_{event_type}_turn{N}.png  ← cinematic / scene image
      scene_{event_type}_turn{N}.json
    story_log.json                ← [{page, turn, actor, action, narrative,
                                       image_path, label, scene_type}]
"""

import os
import json
import datetime
from engine.config import config


def get_save_dir(save_name):
    """Return the per-save folder path (creates it if needed)."""
    path = os.path.join(config.SAVE_DIR, save_name)
    os.makedirs(path, exist_ok=True)
    return path


def get_image_dir(save_name):
    """Return the images subfolder path (creates it if needed)."""
    path = os.path.join(get_save_dir(save_name), 'images')
    os.makedirs(path, exist_ok=True)
    return path


def save_image_with_text(save_name, image, text, turn, event_type):
    """
    Save a PIL Image to disk with a JSON sidecar containing the paired text.

    Args:
        save_name  (str):        Save file identifier (used as folder name).
        image      (PIL.Image):  Generated image to persist.
        text       (str):        Narrative / caption paired with this image.
        turn       (int):        Game turn number (0 for map/portraits).
        event_type (str):        e.g. 'map', 'portrait_Aria', 'battle_start'.

    Returns:
        str — absolute path to the saved PNG, or None on failure.
    """
    if image is None:
        return None
    try:
        img_dir   = get_image_dir(save_name)
        # Sanitise event_type for a safe filename
        safe_type = ''.join(c if (c.isalnum() or c == '_') else '_' for c in event_type)
        basename  = f"{safe_type}_turn{turn}"
        img_path  = os.path.join(img_dir, f"{basename}.png")
        meta_path = os.path.join(img_dir, f"{basename}.json")

        image.save(img_path, format='PNG')

        meta = {
            'event_type': event_type,
            'turn':       turn,
            'text':       text,
            'timestamp':  datetime.datetime.now().isoformat(),
            'image_file': os.path.basename(img_path),
        }
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return img_path
    except Exception as exc:
        print(f"[StorySaver] Failed to save image: {exc}")
        return None


FULL_PRESERVE_TURNS = 12  # last N pages stored with complete narrative


def compress_game_log(history):
    """
    Condense the in-memory history list into a compact story log.

    Each DM entry is paired with the preceding player action to form one page.
    The last FULL_PRESERVE_TURNS pages are stored with the full narrative text.
    Older pages are truncated to 300 chars to keep the log file manageable.

    Returns list of page dicts:
      {page, turn, actor, action, narrative, image_path, label, scene_type}
    """
    pages          = []
    page_no        = 0
    pending_action = None
    pending_actor  = None

    for item in history:
        if item.get('role') == 'player':
            pending_action = item.get('content', '')
            pending_actor  = item.get('actor', '')
        elif item.get('role') == 'dm':
            page_no   += 1
            narrative  = item.get('content', '')
            pages.append({
                'page':       page_no,
                'turn':       item.get('turn', page_no),
                'actor':      pending_actor or '',
                'action':     pending_action or '',
                'narrative':  narrative,        # full text — trimmed below if needed
                'image_path': item.get('image_path', ''),
                'label':      item.get('cinematic_label') or '',
                'scene_type': item.get('scene_type', 'exploration'),
            })
            pending_action = None
            pending_actor  = None

    # Truncate older pages to 300 chars; keep last FULL_PRESERVE_TURNS complete
    cutoff = max(0, len(pages) - FULL_PRESERVE_TURNS)
    for i in range(cutoff):
        nar = pages[i]['narrative']
        if len(nar) > 300:
            pages[i]['narrative'] = nar[:300] + '…'

    return pages


def restore_history_from_log(story_log, n=2):
    """
    Reconstruct the last n story pages as history entries suitable for
    st.session_state.history (player + dm dicts).

    Images are not in memory after a load, so image=None; image_path
    is preserved so Book Mode can still load the file from disk.

    Returns a flat list of history dicts ordered oldest → newest.
    """
    recent = story_log[-n:] if len(story_log) >= n else story_log
    history = []
    for page in recent:
        if page.get('action'):
            history.append({
                'role':    'player',
                'actor':   page.get('actor', ''),
                'content': page.get('action', ''),
            })
        history.append({
            'role':            'dm',
            'content':         page.get('narrative', ''),
            'choices':         [],
            'scene_type':      page.get('scene_type', 'exploration'),
            'dice_result':     None,
            'image':           None,          # PIL not persisted across sessions
            'image_path':      page.get('image_path', ''),
            'is_cinematic':    bool(page.get('label')),
            'cinematic_label': page.get('label', ''),
            'turn':            page.get('turn', 0),
        })
    return history


def save_game_log(save_name, compressed_log):
    """
    Write the compressed story log to saves/{save_name}/story_log.json.

    Returns the file path, or None on failure.
    """
    try:
        path = os.path.join(get_save_dir(save_name), 'story_log.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(compressed_log, f, ensure_ascii=False, indent=2)
        return path
    except Exception as exc:
        print(f"[StorySaver] Failed to save log: {exc}")
        return None


def load_story_log(save_name):
    """
    Load the story log for a save.

    Returns a list of page dicts, or [] if the file does not exist / is invalid.
    """
    path = os.path.join(get_save_dir(save_name), 'story_log.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        print(f"[StorySaver] Failed to load log: {exc}")
        return []

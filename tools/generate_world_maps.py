import argparse
import datetime
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.config import config
from engine.image_prompts import (
    MAP_TERRAIN_TYPES,
    build_map_prompt,
    get_map_negative_prompt,
)


WORLD_MAP_DIR = os.path.join(config.SAVE_DIR, "world_maps")
STATE_FILE = os.path.join(WORLD_MAP_DIR, "_batch_state.json")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_name(value):
    return "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in value)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _world_dir(world_id):
    path = os.path.join(WORLD_MAP_DIR, _safe_name(world_id))
    os.makedirs(path, exist_ok=True)
    return path


def _paths_for(world_id, terrain_type):
    base = f"{_safe_name(world_id)}_{_safe_name(terrain_type)}"
    out_dir = _world_dir(world_id)
    return (
        os.path.join(out_dir, f"{base}.png"),
        os.path.join(out_dir, f"{base}.json"),
    )


def _load_state():
    return _load_json(STATE_FILE, {
        "next_world_index": 0,
        "completed_worlds": [],
        "runs": [],
    })


def _save_state(state):
    _write_json(STATE_FILE, state)


def _world_complete(world_id):
    for terrain_type in MAP_TERRAIN_TYPES:
        img_path, _ = _paths_for(world_id, terrain_type)
        if not os.path.exists(img_path):
            return False
    return True


def _select_world(args, state):
    worlds = list(config.WORLD_SETTINGS)
    if args.world_id:
        for ws in worlds:
            if ws.get("id") == args.world_id:
                return ws, worlds.index(ws)
        raise SystemExit(f"Unknown world id: {args.world_id}")

    if not worlds:
        raise SystemExit("No world settings configured.")

    start = int(state.get("next_world_index") or 0) % len(worlds)
    for offset in range(len(worlds)):
        idx = (start + offset) % len(worlds)
        ws = worlds[idx]
        if args.force or not _world_complete(ws["id"]):
            return ws, idx

    return None, start


def _build_variant_metadata(ws, terrain_type, args, status, image_path="", error=""):
    terrain = MAP_TERRAIN_TYPES[terrain_type]
    prompt = build_map_prompt(
        ws,
        image_style=args.style,
        custom_suffix=args.custom_suffix,
        terrain_type=terrain_type,
    )
    negative = get_map_negative_prompt(args.style)
    return {
        "world_id": ws.get("id", ""),
        "world_name": ws.get("name", ""),
        "terrain_type": terrain_type,
        "terrain_name": terrain.get("name", terrain_type),
        "status": status,
        "image_path": image_path,
        "error": error,
        "width": args.width,
        "height": args.height,
        "image_style": args.style,
        "image_model": args.model or config.IMAGE_MODEL_NAME,
        "prompt": prompt,
        "negative_prompt": negative,
        "updated_at": _now(),
    }


def _save_prompt_sidecar(ws, terrain_type, args, status, image_path="", error=""):
    _, meta_path = _paths_for(ws["id"], terrain_type)
    meta = _build_variant_metadata(ws, terrain_type, args, status, image_path, error)
    _write_json(meta_path, meta)
    return meta


def _get_generator(args):
    from ai.image_gen import ImageGenerator

    generator = ImageGenerator()
    if args.model:
        generator.switch_model(args.model)
    return generator


def process_world(ws, args):
    os.makedirs(WORLD_MAP_DIR, exist_ok=True)
    generator = None
    if not args.prompt_only:
        try:
            generator = _get_generator(args)
        except Exception as exc:
            print(f"[world-maps] Image generator unavailable: {exc}")
            generator = None

    variants = []
    generated = 0
    skipped = 0
    failed = 0

    for terrain_type in MAP_TERRAIN_TYPES:
        img_path, _ = _paths_for(ws["id"], terrain_type)
        if os.path.exists(img_path) and not args.force:
            variants.append(_save_prompt_sidecar(ws, terrain_type, args, "skipped_existing", img_path))
            skipped += 1
            continue

        if args.prompt_only:
            variants.append(_save_prompt_sidecar(ws, terrain_type, args, "prompt_only"))
            continue

        if generator is None:
            variants.append(_save_prompt_sidecar(
                ws, terrain_type, args, "failed", error="image generator unavailable"
            ))
            failed += 1
            continue

        prompt = build_map_prompt(
            ws,
            image_style=args.style,
            custom_suffix=args.custom_suffix,
            terrain_type=terrain_type,
        )
        negative = get_map_negative_prompt(args.style)
        print(f"[world-maps] Generating {ws['id']} / {terrain_type}...")
        try:
            image = generator.generate_image(
                prompt,
                negative_prompt=negative,
                context_type="map",
                width=args.width,
                height=args.height,
            )
            if image is None:
                variants.append(_save_prompt_sidecar(
                    ws, terrain_type, args, "failed", error="generator returned no image"
                ))
                failed += 1
                continue
            image.save(img_path, format="PNG")
            variants.append(_save_prompt_sidecar(ws, terrain_type, args, "generated", img_path))
            generated += 1
        except Exception as exc:
            variants.append(_save_prompt_sidecar(ws, terrain_type, args, "failed", error=str(exc)))
            failed += 1

    if generator is not None:
        try:
            generator.unload_model()
        except Exception:
            pass

    manifest = {
        "world_id": ws.get("id", ""),
        "world_name": ws.get("name", ""),
        "completed": failed == 0 and not args.prompt_only,
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "prompt_only": args.prompt_only,
        "updated_at": _now(),
        "variants": variants,
    }
    _write_json(os.path.join(_world_dir(ws["id"]), "manifest.json"), manifest)
    return manifest


def update_progress(state, ws, world_index, manifest, args):
    worlds = list(config.WORLD_SETTINGS)
    completed = set(state.get("completed_worlds") or [])
    if manifest.get("completed") or _world_complete(ws["id"]):
        completed.add(ws["id"])
        state["next_world_index"] = (world_index + 1) % max(len(worlds), 1)
    elif args.force and args.world_id:
        state["next_world_index"] = (world_index + 1) % max(len(worlds), 1)
    else:
        state["next_world_index"] = world_index

    run_summary = {
        "world_id": ws.get("id", ""),
        "world_name": ws.get("name", ""),
        "generated": manifest.get("generated", 0),
        "skipped": manifest.get("skipped", 0),
        "failed": manifest.get("failed", 0),
        "prompt_only": manifest.get("prompt_only", False),
        "completed": manifest.get("completed", False),
        "updated_at": _now(),
    }
    runs = list(state.get("runs") or [])
    runs.append(run_summary)
    state["runs"] = runs[-50:]
    state["completed_worlds"] = sorted(completed)
    _save_state(state)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate four style-aware world map variants for one configured game world."
    )
    parser.add_argument("--world-id", default="", help="Specific GameConfig.WORLD_SETTINGS id to process.")
    parser.add_argument("--style", default="fantasy_art", help="Image style key from IMAGE_STYLES.")
    parser.add_argument("--custom-suffix", default="", help="Extra style suffix appended to every map prompt.")
    parser.add_argument("--model", default="", help="Override config.IMAGE_MODEL_NAME for this run.")
    parser.add_argument("--width", default=1024, type=int, help="Requested generated image width.")
    parser.add_argument("--height", default=1024, type=int, help="Requested generated image height.")
    parser.add_argument("--prompt-only", action="store_true", help="Write prompts and sidecars without generating images.")
    parser.add_argument("--force", action="store_true", help="Regenerate maps even when PNG files already exist.")
    return parser.parse_args()


def main():
    args = parse_args()
    state = _load_state()
    ws, world_index = _select_world(args, state)
    if ws is None:
        print("[world-maps] All configured worlds already have all four map variants.")
        return 0

    manifest = process_world(ws, args)
    update_progress(state, ws, world_index, manifest, args)

    print(
        "[world-maps] "
        f"{ws['id']}: generated={manifest['generated']} "
        f"skipped={manifest['skipped']} failed={manifest['failed']} "
        f"prompt_only={manifest['prompt_only']}"
    )
    if manifest.get("failed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

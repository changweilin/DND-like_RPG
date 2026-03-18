#!/usr/bin/env python3
"""
tools/download_models.py — 本地影像模型下載工具
=================================================

預先將 HuggingFace 上的 diffusers 影像模型下載到本機快取，
讓遊戲啟動時不需等待自動下載，也不會因網路逾時導致生成失敗。

Usage
-----
    # 列出所有可用模型及其下載狀態
    python tools/download_models.py --list

    # 下載預設模型（SDXL-Turbo）
    python tools/download_models.py

    # 下載指定模型 ID
    python tools/download_models.py --model "Lykon/dreamshaper-8"

    # 下載所有本地 diffusers 模型
    python tools/download_models.py --all

    # 驗證已下載模型的完整性（不重新下載）
    python tools/download_models.py --check

注意：只有 provider=diffusers 的模型需要下載。
      DALL·E 3 / Stability AI 是雲端 API，不需要下載。
"""

import argparse
import sys
import os

# Allow running from repo root or tools/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from engine.config import config

_DIFFUSERS_PROVIDER = 'diffusers'


def _local_presets():
    """Return only diffusers (local GPU) presets."""
    return [p for p in config.IMAGE_MODEL_PRESETS
            if p.get('provider', _DIFFUSERS_PROVIDER) == _DIFFUSERS_PROVIDER]


def _check_cached(model_id):
    """
    Return True if the model files are present in the HuggingFace cache.
    Uses huggingface_hub's scan_cache_dir for an accurate check.
    """
    try:
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == model_id:
                return True
        return False
    except Exception:
        return False


def _print_status(preset, cached):
    status = "✅ 已下載" if cached else "⬇️  未下載"
    vram   = f"{preset['vram_gb']} GB VRAM" if preset.get('vram_gb') else "—"
    print(f"  {status}  [{vram:>10}]  {preset['name']:<20}  {preset['id']}")


def cmd_list():
    """列出所有本地模型及下載狀態。"""
    print("\n本地 Diffusers 影像模型\n" + "=" * 60)
    presets = _local_presets()
    if not presets:
        print("（config 中沒有 diffusers 模型）")
        return
    for p in presets:
        cached = _check_cached(p['id'])
        _print_status(p, cached)
        print(f"            描述：{p.get('description', '')}")
    print()


def cmd_download(model_id):
    """下載指定模型到 HuggingFace 快取。"""
    # 確認 model_id 存在於 presets
    preset = config.get_image_preset(model_id)
    if preset['id'] != model_id:
        print(f"[錯誤] 找不到模型 '{model_id}'。請用 --list 查看可用 ID。")
        sys.exit(1)
    if preset.get('provider', _DIFFUSERS_PROVIDER) != _DIFFUSERS_PROVIDER:
        print(f"[跳過] '{model_id}' 是雲端 API 模型，不需要下載。")
        return

    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except ImportError as e:
        print(f"[錯誤] 缺少必要套件：{e}")
        print("請先安裝：pip install diffusers transformers accelerate torch")
        sys.exit(1)

    print(f"\n正在下載：{preset['name']} ({model_id})")
    print(f"需要 VRAM：{preset.get('vram_gb', '?')} GB（僅生成時使用）")
    print("正在從 HuggingFace 下載模型權重…（首次下載可能需要幾分鐘）\n")

    try:
        # fp16 variant first (smaller download), fall back to standard
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(
                model_id, torch_dtype=torch.float16, variant="fp16"
            )
        except Exception:
            pipe = AutoPipelineForText2Image.from_pretrained(
                model_id, torch_dtype=torch.float16
            )
        # Immediately unload — we only wanted to cache the weights
        del pipe
        print(f"\n✅ {preset['name']} 下載完成，已存入 HuggingFace 快取。")
    except Exception as e:
        print(f"\n[錯誤] 下載失敗：{e}")
        sys.exit(1)


def cmd_download_all():
    """下載所有本地 diffusers 模型。"""
    presets = _local_presets()
    if not presets:
        print("沒有可下載的本地模型。")
        return
    print(f"將下載 {len(presets)} 個本地模型…")
    for p in presets:
        if _check_cached(p['id']):
            print(f"[跳過] {p['name']} 已在快取中。")
        else:
            cmd_download(p['id'])


def cmd_check():
    """驗證各模型是否已在快取中，不執行下載。"""
    print("\n快取狀態檢查\n" + "=" * 60)
    presets = _local_presets()
    missing = []
    for p in presets:
        cached = _check_cached(p['id'])
        _print_status(p, cached)
        if not cached:
            missing.append(p)
    print()
    if missing:
        print(f"⬇️  尚未下載的模型（{len(missing)} 個）：")
        for p in missing:
            print(f"    python tools/download_models.py --model \"{p['id']}\"")
    else:
        print("✅ 所有本地模型均已下載。")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="下載並管理本地 diffusers 影像模型"
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument('--list',  action='store_true', help='列出所有模型及下載狀態')
    grp.add_argument('--all',   action='store_true', help='下載所有本地模型')
    grp.add_argument('--check', action='store_true', help='檢查快取狀態（不下載）')
    grp.add_argument('--model', metavar='MODEL_ID',  help='下載指定模型 ID')
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.all:
        cmd_download_all()
    elif args.check:
        cmd_check()
    elif args.model:
        cmd_download(args.model)
    else:
        # Default: download the configured default model
        default_id = config.IMAGE_MODEL_NAME
        print(f"未指定模型，使用預設：{default_id}")
        if _check_cached(default_id):
            print(f"✅ {default_id} 已在快取中，無需重新下載。")
        else:
            cmd_download(default_id)


if __name__ == '__main__':
    main()

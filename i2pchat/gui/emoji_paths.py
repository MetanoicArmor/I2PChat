"""Кэш путей к PNG Noto Emoji (manifest в noto_emoji/)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_GUI_DIR = Path(__file__).resolve().parent


def noto_emoji_root() -> Path:
    """Каталог noto_emoji: исходники или PyInstaller _MEIPASS."""
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        bundled = Path(meipass) / "i2pchat" / "gui" / "noto_emoji"
        if bundled.is_dir():
            return bundled
    return _GUI_DIR / "noto_emoji"


def _load_emoji_manifest_paths(root: Path) -> dict[str, Path]:
    mf = root / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        raw = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    root_resolved = root.resolve()
    out: dict[str, Path] = {}
    for key, rel in raw.items():
        if not isinstance(key, str) or not isinstance(rel, str):
            continue
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            continue
        p = (root / rel).resolve()
        try:
            p.relative_to(root_resolved)
        except ValueError:
            continue
        if p.is_file():
            out[key] = p
    return out


_paths_cache: Optional[dict[str, Path]] = None


def emoji_paths_cached() -> dict[str, Path]:
    global _paths_cache
    if _paths_cache is None:
        _paths_cache = _load_emoji_manifest_paths(noto_emoji_root())
    return _paths_cache

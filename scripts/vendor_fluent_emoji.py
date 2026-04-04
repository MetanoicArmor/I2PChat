#!/usr/bin/env python3
"""
Копирует PNG из локального клона microsoft/fluentui-emoji в i2pchat/gui/fluent_emoji/.

Для каждого ``assets/<name>/metadata.json`` читается поле ``glyph``; растр берётся из
подкаталога стиля (``3D``, ``Color``, …). См. https://github.com/microsoft/fluentui-emoji

Использование (из корня репозитория I2PChat)::

  python3 scripts/vendor_fluent_emoji.py /path/to/fluentui-emoji --style 3d

Лицензия апстрима: MIT (файл LICENSE в репозитории Fluent UI Emoji).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import unicodedata
from pathlib import Path

_STYLE_SUBDIRS: dict[str, str] = {
    "3d": "3D",
    "color": "Color",
    "flat": "Flat",
    "high_contrast": "High Contrast",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _strip_vs16(s: str) -> str:
    return "".join(c for c in s if ord(c) != 0xFE0F)


def _resolve_src_png(glyph_map: dict[str, Path], emoji: str) -> Path | None:
    nfc = unicodedata.normalize("NFC", emoji)
    p = glyph_map.get(nfc)
    if p is not None:
        return p
    no_vs = _strip_vs16(nfc)
    if no_vs != nfc:
        return glyph_map.get(no_vs)
    return None


def _unicode_hex_key(s: str) -> str:
    return "-".join(f"{ord(c):X}" for c in unicodedata.normalize("NFC", s))


def _style_png_dir(asset_dir: Path, style_subdir: str) -> Path | None:
    """Прямой ``<asset>/3D`` или вложенный ``<asset>/Default/3D`` (жесты со скинтонами)."""
    d = asset_dir / style_subdir
    if d.is_dir():
        return d
    nested = asset_dir / "Default" / style_subdir
    if nested.is_dir():
        return nested
    return None


def _build_glyph_map(fluent_root: Path, style_subdir: str) -> dict[str, Path]:
    assets = fluent_root / "assets"
    if not assets.is_dir():
        return {}
    out: dict[str, Path] = {}
    for meta_path in sorted(assets.glob("*/metadata.json")):
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        glyph = raw.get("glyph")
        if not isinstance(glyph, str) or not glyph:
            continue
        style_path = _style_png_dir(meta_path.parent, style_subdir)
        if style_path is None:
            continue
        pngs = sorted(style_path.glob("*.png"))
        if not pngs:
            continue
        png = pngs[0]
        key = unicodedata.normalize("NFC", glyph)
        prev = out.get(key)
        if prev is not None and prev.resolve() != png.resolve():
            print(
                f"warning: duplicate glyph {key!r} -> keeping {prev}, ignoring {png}",
                file=sys.stderr,
            )
            continue
        out[key] = png
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vendor Fluent UI Emoji PNGs into I2PChat fluent_emoji pack."
    )
    parser.add_argument(
        "fluent_root",
        type=Path,
        help="Path to cloned microsoft/fluentui-emoji repository root",
    )
    parser.add_argument(
        "--style",
        choices=list(_STYLE_SUBDIRS.keys()),
        default="3d",
        help="Raster style folder under each asset (default: 3d)",
    )
    args = parser.parse_args()
    fluent_root = args.fluent_root.resolve()
    style_subdir = _STYLE_SUBDIRS[args.style]
    glyph_map = _build_glyph_map(fluent_root, style_subdir)
    if not glyph_map:
        print(
            f"No PNGs found under {fluent_root}/assets/*/metadata.json + {style_subdir!r}",
            file=sys.stderr,
        )
        return 1

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    from i2pchat.gui.emoji_data import EMOJI_CHARS  # noqa: E402
    from i2pchat.gui.emoji_paths import fluent_emoji_root  # noqa: E402

    dest_root = fluent_emoji_root().resolve()
    png_dir = dest_root / "png"
    png_dir.mkdir(parents=True, exist_ok=True)
    for stale in png_dir.glob("*.png"):
        stale.unlink()

    manifest: dict[str, str] = {}
    missing: list[str] = []
    used_names: dict[str, str] = {}

    for emoji in EMOJI_CHARS:
        src = _resolve_src_png(glyph_map, emoji)
        if src is None:
            missing.append(emoji)
            continue
        key = _unicode_hex_key(unicodedata.normalize("NFC", emoji))
        fname = f"{key}.png"
        if fname in used_names and used_names[fname] != emoji:
            i = 1
            while f"{key}_{i}.png" in used_names:
                i += 1
            fname = f"{key}_{i}.png"
        used_names[fname] = emoji
        dest = png_dir / fname
        shutil.copy2(src, dest)
        manifest[emoji] = f"png/{fname}"

    manifest_path = dest_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Copied {len(manifest)} PNGs -> {png_dir}")
    if missing:
        print(f"Missing ({len(missing)}):", ", ".join(missing[:20]), file=sys.stderr)
        if len(missing) > 20:
            print(f"... and {len(missing) - 20} more", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

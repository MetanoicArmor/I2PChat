#!/usr/bin/env python3
"""
Копирует 3D PNG из локального клона microsoft/fluentui-emoji в i2pchat/gui/fluent_emoji/.
Ищет файлы в `<asset>/3D/*_3d.png` или `<asset>/Default/3D/*.png` (эмодзи со скинтонами).

Использование (из корня репозитория I2PChat):
  python3 scripts/vendor_fluent_emoji.py /path/to/fluentui-emoji

Сеть не нужна, если репозиторий уже клонирован. Лицензия Fluent Emoji: MIT.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import unicodedata
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _unicode_hex_key(s: str) -> str:
    return "-".join(f"{ord(c):X}" for c in s)


def _storage_hex_key(s: str) -> str:
    """Имя файла без U+FE0F (variation selector) — короче и без суффикса -FE0F."""
    nfc = unicodedata.normalize("NFC", s)
    return "-".join(f"{ord(c):X}" for c in nfc if ord(c) != 0xFE0F)


def _parse_metadata_unicode(raw: str) -> str:
    parts = raw.strip().split()
    return "-".join(p.upper() for p in parts if p)


def _strip_fe0f(s: str) -> str:
    return "".join(c for c in s if ord(c) != 0xFE0F)


def _find_3d_png(asset_dir: Path) -> Path | None:
    """Fluent layout: либо `<asset>/3D/*_3d.png`, либо `<asset>/Default/3D/*.png` (скинтоны)."""
    top = asset_dir / "3D"
    if top.is_dir():
        pngs = sorted(top.glob("*_3d.png"))
        if pngs:
            return pngs[0]
    nested = asset_dir / "Default" / "3D"
    if nested.is_dir():
        pngs = sorted(nested.glob("*.png"))
        if pngs:
            return pngs[0]
    return None


def build_indexes(
    assets_root: Path,
) -> tuple[dict[str, Path], dict[str, Path]]:
    """glyph (NFC) -> png, unicode-key -> png."""
    by_glyph: dict[str, Path] = {}
    by_unicode: dict[str, Path] = {}
    if not assets_root.is_dir():
        return by_glyph, by_unicode
    for asset_dir in sorted(assets_root.iterdir()):
        if not asset_dir.is_dir():
            continue
        meta_path = asset_dir / "metadata.json"
        if not meta_path.is_file():
            continue
        png = _find_3d_png(asset_dir)
        if png is None:
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        glyph = data.get("glyph")
        if isinstance(glyph, str) and glyph:
            g = unicodedata.normalize("NFC", glyph)
            by_glyph[g] = png
            by_glyph.setdefault(_strip_fe0f(g), png)
        uni = data.get("unicode")
        if isinstance(uni, str) and uni.strip():
            key = _parse_metadata_unicode(uni)
            if key:
                by_unicode[key] = png
    return by_glyph, by_unicode


def resolve_png(
    emoji: str,
    by_glyph: dict[str, Path],
    by_unicode: dict[str, Path],
) -> Path | None:
    nfc = unicodedata.normalize("NFC", emoji)
    candidates = [
        nfc,
        _strip_fe0f(nfc),
        unicodedata.normalize("NFD", nfc),
        unicodedata.normalize("NFC", _strip_fe0f(nfc)),
    ]
    for c in candidates:
        if c in by_glyph:
            return by_glyph[c]
    keys = [_unicode_hex_key(nfc), _unicode_hex_key(_strip_fe0f(nfc))]
    for k in keys:
        if k in by_unicode:
            return by_unicode[k]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Vendor Fluent 3D emoji PNGs into I2PChat.")
    parser.add_argument(
        "fluent_root",
        type=Path,
        help="Path to cloned fluentui-emoji repository root",
    )
    args = parser.parse_args()
    fluent_root = args.fluent_root.resolve()
    assets = fluent_root / "assets"
    if not assets.is_dir():
        print(f"Not a fluentui-emoji repo (missing assets): {assets}", file=sys.stderr)
        return 1

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    from i2pchat.gui.emoji_data import EMOJI_CHARS  # noqa: E402

    dest_root = repo_root / "i2pchat" / "gui" / "fluent_emoji"
    png_dir = dest_root / "png"
    png_dir.mkdir(parents=True, exist_ok=True)

    by_glyph, by_unicode = build_indexes(assets)
    manifest: dict[str, str] = {}
    missing: list[str] = []
    used_names: dict[str, str] = {}

    for emoji in EMOJI_CHARS:
        src = resolve_png(emoji, by_glyph, by_unicode)
        if src is None or not src.is_file():
            missing.append(emoji)
            continue
        key = _storage_hex_key(emoji)
        fname = f"{key}.png"
        if fname in used_names and used_names[fname] != emoji:
            # крайне редко; добавить суффикс
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

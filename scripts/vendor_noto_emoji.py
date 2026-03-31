#!/usr/bin/env python3
"""
Копирует PNG 128px из локального клона googlefonts/noto-emoji в i2pchat/gui/noto_emoji/.

Ищет файлы в ``<noto-root>/png/128/emoji_u{hex}_{hex...}.png`` (hex в нижнем регистре),
как в апстриме: https://github.com/googlefonts/noto-emoji

Использование (из корня репозитория I2PChat):
  python3 scripts/vendor_noto_emoji.py /path/to/noto-emoji

Растровые ассеты и инструменты в noto-emoji в основном под Apache-2.0 (см. LICENSE в том репо).
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


def _noto_basename_candidates(glyph: str) -> list[str]:
    """Имена файлов без .png в порядке приоритета."""
    nfc = unicodedata.normalize("NFC", glyph)
    names: list[str] = []

    def u_name(s: str) -> str:
        return "emoji_u" + "_".join(f"{ord(c):x}" for c in s)

    names.append(u_name(nfc))
    no_vs = "".join(c for c in nfc if ord(c) != 0xFE0F)
    if no_vs != nfc:
        names.append(u_name(no_vs))
    return names


def _resolve_src_png(size_dir: Path, glyph: str) -> Path | None:
    for base in _noto_basename_candidates(glyph):
        p = size_dir / f"{base}.png"
        if p.is_file():
            return p
    return None


def _unicode_hex_key(s: str) -> str:
    return "-".join(f"{ord(c):X}" for c in unicodedata.normalize("NFC", s))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vendor Noto Emoji PNGs (128px) into I2PChat noto_emoji pack."
    )
    parser.add_argument(
        "noto_root",
        type=Path,
        help="Path to cloned googlefonts/noto-emoji repository root",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=128,
        help="Subdirectory under png/ (default: 128)",
    )
    args = parser.parse_args()
    noto_root = args.noto_root.resolve()
    size_dir = noto_root / "png" / str(args.size)
    if not size_dir.is_dir():
        print(f"Missing Noto png dir: {size_dir}", file=sys.stderr)
        return 1

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    from i2pchat.gui.emoji_data import EMOJI_CHARS  # noqa: E402
    from i2pchat.gui.emoji_paths import noto_emoji_root  # noqa: E402

    dest_root = noto_emoji_root().resolve()
    png_dir = dest_root / "png"
    png_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, str] = {}
    missing: list[str] = []
    used_names: dict[str, str] = {}

    for emoji in EMOJI_CHARS:
        src = _resolve_src_png(size_dir, emoji)
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

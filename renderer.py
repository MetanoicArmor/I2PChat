from __future__ import annotations

from typing import Iterable, List

from PIL import Image


def _load_image(path: str, max_width: int = 80) -> Image.Image:
    """Load image and downscale to a reasonable width for text rendering."""
    img = Image.open(path).convert("L")
    w, h = img.size
    if w > max_width:
        ratio = max_width / float(w)
        img = img.resize((max_width, max(int(h * ratio))), Image.LANCZOS)
    return img


def render_bw(path: str) -> List[str]:
    """
    Render image as simple black/white ASCII art.

    Returns list of strings, each string is a text row.
    """
    img = _load_image(path)
    # Simple threshold
    img = img.point(lambda v: 0 if v < 128 else 255, mode="1")

    chars = {0: "█", 255: " "}
    pixels = img.load()
    w, h = img.size

    lines: List[str] = []
    for y in range(h):
        row_chars: List[str] = []
        for x in range(w):
            row_chars.append(chars[255 if pixels[x, y] else 0])
        lines.append("".join(row_chars).rstrip())
    return lines


def render_braille(path: str) -> List[str]:
    """
    Render image using braille unicode characters (2x4 pixel blocks).

    Returns list of strings, each string is a text row.
    """
    img = _load_image(path)
    w, h = img.size
    # Ensure dimensions are multiples of 2x4
    w_aligned = w - (w % 2)
    h_aligned = h - (h % 4)
    if w_aligned <= 0 or h_aligned <= 0:
        return []
    img = img.crop((0, 0, w_aligned, h_aligned))
    img = img.point(lambda v: 0 if v < 128 else 1, mode="1")
    pixels = img.load()
    w, h = img.size

    def cell_to_braille(cx: int, cy: int) -> str:
        # Braille dot layout (2x4):
        # (0,0) dot1, (0,1) dot2, (0,2) dot3, (0,3) dot7
        # (1,0) dot4, (1,1) dot5, (1,2) dot6, (1,3) dot8
        offsets = [
            (0, 0, 0),  # dot 1 -> bit 0
            (0, 1, 1),  # dot 2 -> bit 1
            (0, 2, 2),  # dot 3 -> bit 2
            (1, 0, 3),  # dot 4 -> bit 3
            (1, 1, 4),  # dot 5 -> bit 4
            (1, 2, 5),  # dot 6 -> bit 5
            (0, 3, 6),  # dot 7 -> bit 6
            (1, 3, 7),  # dot 8 -> bit 7
        ]
        value = 0
        for dx, dy, bit in offsets:
            if pixels[cx + dx, cy + dy] == 0:  # "black" dot
                value |= 1 << bit
        if value == 0:
            return " "
        return chr(0x2800 + value)

    lines: List[str] = []
    for cy in range(0, h, 4):
        row_chars: List[str] = []
        for cx in range(0, w, 2):
            row_chars.append(cell_to_braille(cx, cy))
        lines.append("".join(row_chars).rstrip())
    return lines


from pathlib import Path

from PIL import Image


def make_icon() -> None:
    root = Path(__file__).parent
    src = root / "image.png"
    out_1024 = root / "icon-1024.png"

    if not src.exists():
        raise SystemExit(f"source image not found: {src}")

    img = Image.open(src).convert("RGBA")
    w, h = img.size

    # делаем квадратную обрезку по центру
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top + side
    img = img.crop((left, top, right, bottom))

    # ресайз до 1024×1024 и сохранение
    img = img.resize((1024, 1024), Image.LANCZOS)
    img.save(out_1024)
    print("saved", out_1024)


if __name__ == "__main__":
    make_icon()


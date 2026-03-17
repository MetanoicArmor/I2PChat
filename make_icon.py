from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image


def make_icon() -> None:
    root = Path(__file__).parent
    src = root / "image.png"
    out_icon = root / "icon.png"

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
    img.save(out_icon)
    print("saved", out_icon)

    # Windows ICO
    out_ico = root / "i2pchat.ico"
    img.save(
        out_ico,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("saved", out_ico)

    # macOS ICNS (через iconutil)
    out_icns = root / "I2PChat.icns"
    iconset = root / "I2PChat.iconset"
    iconset.mkdir(exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
        img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")

    iconutil = shutil.which("iconutil")
    if iconutil:
        subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(out_icns)],
            check=True,
        )
        print("saved", out_icns)
    else:
        print("skip icns: iconutil not found")

    for p in iconset.glob("*.png"):
        p.unlink(missing_ok=True)
    iconset.rmdir()


if __name__ == "__main__":
    try:
        make_icon()
    except Exception as e:
        raise SystemExit(f"make_icon failed: {e}") from e


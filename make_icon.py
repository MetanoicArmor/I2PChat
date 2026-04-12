from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image


def make_icon() -> None:
    root = Path(__file__).parent
    # Default source: image.png. Pass another file (e.g. image2.png) as argv[1] to override.
    src_name = sys.argv[1] if len(sys.argv) > 1 else "image.png"
    src = root / src_name
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
    # Один квадратный мастер: все размеры считаем из него отдельно. Цепочка
    # «мастер → 1024 → 16/32/…» даёт лишнее размытие по альфе и на macOS в Dock
    # может выглядеть как светлая/серая «подложка» по контуру иконки.
    master = img.crop((left, top, right, bottom))
    resample = Image.Resampling.LANCZOS

    def downscale(side: int) -> Image.Image:
        return master.resize((side, side), resample)

    icon_1024 = downscale(1024)
    icon_1024.save(out_icon)
    print("saved", out_icon)

    # Windows ICO
    out_ico = root / "i2pchat.ico"
    icon_1024.save(
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
        downscale(s).save(iconset / f"icon_{s}x{s}.png")
        downscale(s * 2).save(iconset / f"icon_{s}x{s}@2x.png")

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


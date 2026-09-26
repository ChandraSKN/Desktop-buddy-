"""Turn the source character image into sprite.png: trim the empty transparent border
and scale it down so the app loads a small, crisp image.

Usage: python make_sprite.py [source.png]   (default: assets/Hero_image.png)"""

import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "assets" / "Hero_image.png"
OUT = HERE / "sprite.png"
TARGET_HEIGHT = 640   # ~2.5x the on-screen size, so it stays sharp on HiDPI screens

im = Image.open(SRC).convert("RGBA")
# Ignore the faint glow pixels (alpha <= 40) when finding the character's bounds.
bbox = im.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
if bbox is None:
    sys.exit(f"{SRC} has no visible (non-transparent) pixels")
im = im.crop(bbox)
width = round(im.width * TARGET_HEIGHT / im.height)
im.resize((width, TARGET_HEIGHT), Image.LANCZOS).save(OUT)
print(f"Wrote {OUT} ({width}x{TARGET_HEIGHT}) from {SRC.name}, cropped to {bbox}")

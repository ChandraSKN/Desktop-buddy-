"""Pack the frames written by render_frames.py into assets/model3d/<anim>_<yaw>.png strips
plus a manifest.json that model3d.py reads. Strips from earlier runs stay in the manifest,
so the sitting frames (sit_frames.py) can be packed on their own.

    .venv/bin/python blender/pack_frames.py /path/to/frames"""

import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image

YAWS = [-0.95, -0.5, -0.22, 0.0, 0.22, 0.5, 0.95]    # keep in sync with render_frames.py

src = Path(sys.argv[1])
dst = Path(__file__).resolve().parent.parent / "assets" / "model3d"
dst.mkdir(parents=True, exist_ok=True)

groups = defaultdict(dict)
for f in src.glob("*.png"):
    m = re.fullmatch(r"(\w+)_(\d+)_(\d+)\.png", f.name)
    if m:
        groups[(m[1], int(m[2]))][int(m[3])] = f

old = dst / "manifest.json"
manifest = json.loads(old.read_text()) if old.exists() else {}
manifest["yaws"] = YAWS
manifest["anims"] = defaultdict(dict, manifest.get("anims", {}))
for (anim, yi), frames in sorted(groups.items()):
    ims = [Image.open(frames[i]) for i in sorted(frames)]
    w, h = ims[0].size
    sheet = Image.new("RGBA", (w * len(ims), h))
    for i, im in enumerate(ims):
        sheet.paste(im, (i * w, 0))
    name = f"{anim}_{yi}.png"
    sheet.save(dst / name, optimize=True)
    manifest["anims"][anim][str(yi)] = {"file": name, "frames": len(ims)}
    manifest["frame_size"] = [w, h]

# the chair layer and where it sits in each pull frame (from sit_frames.py)
meta = src / "sit_meta.json"
if meta.exists():
    info = json.loads(meta.read_text())
    shutil.copy(src / info["chair"]["file"], dst / info["chair"]["file"])
    manifest["chair"] = info["chair"]
    manifest["sit_yaw_index"] = info["yaw_index"]

if ("idle", 3) in groups:
    # lowest opaque row of the front-facing idle frame = where the soles touch the ground
    idle = Image.open(dst / manifest["anims"]["idle"]["3"]["file"])
    bbox = idle.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    manifest["ground_y"] = bbox[3]
    manifest["top_y"] = bbox[1]
old.write_text(json.dumps(manifest, indent=1))
print(f"{len(groups)} strips -> {dst}; ground_y={manifest['ground_y']} top_y={manifest['top_y']}")

"""Subtle expressions for the existing model; geometry and identity stay unchanged.

BUDDY_FRAMES=/tmp/buddy_human blender -b blender/buddy_model.blend --python blender/human_frames.py
.venv/bin/python blender/pack_frames.py /tmp/buddy_human

Adds idle blink shapes, an occasional relaxed glance, and blinking variants of each
seated frame. The original idle, walking, talking, and chair transition strips stay intact.
"""

import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_frames as rf
import sit_frames as sf

OUT = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_human")
ONLY = os.environ.get("BUDDY_HUMAN_ONLY", "")
FEATURES = ("eye", "iris", "pupil", "shine", "lid")
rest = {f"BB_{name}.{side}": [v.co.copy() for v in bpy.data.objects[f"BB_{name}.{side}"].data.vertices]
        for side in ("L", "R") for name in FEATURES}


def eyelids(closed):
    for side in ("L", "R"):
        eye = rest[f"BB_eye.{side}"]
        low, high = min(v.z for v in eye), max(v.z for v in eye)
        centre = low + (high - low) * 0.40
        opening = 1 - closed * 0.98
        for name in FEATURES:
            obj = bpy.data.objects[f"BB_{name}.{side}"]
            obj.hide_render = name == "shine" and closed > 0.7
            points = rest[obj.name]
            for vertex, original in zip(obj.data.vertices, points):
                point = original.copy()
                if name == "lid":
                    point.z -= (high - centre) * closed
                else:
                    point.z = centre + (original.z - centre) * opening
                vertex.co = point
            obj.data.update()


def glance(frame):
    rf.pose_idle()
    t = frame / 16
    # Ease in, hold briefly, settle back. Small head movement, feet remain planted.
    amount = math.sin(math.pi * t) ** 2
    rf.rot("head", rf.Z, -0.10 * amount)
    rf.rot("head", rf.Y, 0.035 * amount)
    rf.rot("spine", rf.Y, -0.012 * amount)
    rf.rot("forearm.L", rf.X, -0.025 * amount)
    eyelids(0)


def render(anim, frame, pose):
    if ONLY and anim not in ONLY.split(","):
        return
    rf.render_one(os.path.join(OUT, f"{anim}_2_{frame:02d}.png"), pose, rf.YAWS[2])


def main():
    os.makedirs(OUT, exist_ok=True)
    for i, amount in enumerate((0.0, 0.55, 1.0)):
        def pose(amount=amount):
            rf.pose_idle()
            eyelids(amount)
        render("blink", i, pose)
    for i in range(17):
        render("glance", i, lambda i=i: glance(i))
    for anim, amount in (("seated_half", 0.55), ("seated_closed", 1.0)):
        for i in range(sf.SEATED_FRAMES):
            def pose(i=i, amount=amount):
                sf.pose_seated(2 * math.pi * i / sf.SEATED_FRAMES)
                eyelids(amount)
            render(anim, i, pose)
    eyelids(0)
    rf.reset_pose()
    print("HUMAN_FRAMES_DONE", OUT)


if __name__ == "__main__":
    main()

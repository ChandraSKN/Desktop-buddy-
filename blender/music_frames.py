"""Render a loop of Buddy wearing headphones and dancing to music.

    BUDDY_FRAMES=/tmp/buddy_music blender -b blender/buddy_model.blend --python blender/music_frames.py
    .venv/bin/python blender/pack_frames.py /tmp/buddy_music

BUDDY_ONLY="0,3,6" renders just some frames."""
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_frames as a

out = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_music_frames")
os.makedirs(out, exist_ok=True)
coll = a.collection()
root = bpy.data.objects.new("Headphones", None)
coll.objects.link(root)
root.parent = a.rig
mat = a.toon("HeadsetNavy", "#253451", "#111827", "#536788")
accent = a.toon("HeadsetBlue", "#668cff", "#354eb0", "#9db6ff")
for side in (-1, 1):
    a.part(coll, root, f"earcup{side}", "box", (0.075, 0.15, 0.19),
           (side * 0.205, 0.0, 1.73), mat, bevel=0.025)
    a.part(coll, root, f"accent{side}", "box", (0.015, 0.10, 0.12),
           (side * 0.248, -0.005, 1.73), accent, bevel=0.015)
curve = bpy.data.curves.new("Headband", "CURVE")
curve.dimensions = "3D"
curve.bevel_depth = 0.022
curve.bevel_resolution = 3
spline = curve.splines.new("POLY")
spline.points.add(24)
for i, point in enumerate(spline.points):
    t = math.pi * i / 24
    point.co = (0.212 * math.cos(t), 0.025, 1.75 + 0.24 * math.sin(t), 1)
band = bpy.data.objects.new("Headband", curve)
coll.objects.link(band)
band.parent = root
curve.materials.append(mat)
a.rig.rotation_euler = (0, 0, a.YAWS[2])
head_rest = a.bone("head").bone.matrix_local.inverted()
legs = a.bone("thigh.L").bone.length + a.bone("shin.L").bone.length
ONLY = {int(x) for x in os.environ.get("BUDDY_ONLY", "").split(",") if x}


def move(name, offset):
    pb = a.bone(name)
    pb.location = pb.bone.matrix_local.to_3x3().inverted() @ Vector(offset)


def pose_dance(t):
    """One 24-frame bar at 12 fps: four knee bounces (120 bpm), a hip sway left and right,
    fists pumping in turn and the head nodding on the beat. Feet stay planted."""
    a.reset_pose()
    bounce = 0.5 - 0.5 * math.cos(4 * t)          # 0 standing .. 1 knees bent, per beat
    sway = math.sin(t)
    pump = math.sin(2 * t)
    bend = 0.30 * bounce
    for side in ("L", "R"):
        a.rot(f"thigh.{side}", a.X, -bend)
        a.rot(f"shin.{side}", a.X, 2 * bend)
        a.rot(f"foot.{side}", a.X, -bend)
    move("hips", (0.045 * sway, 0, -legs * (1 - math.cos(bend))))
    a.rot("hips", a.Y, -0.06 * sway)
    a.rot("spine", a.Y, 0.10 * sway)
    a.rot("spine", a.Z, 0.12 * sway)
    a.rot("spine", a.X, 0.05 * bounce)
    a.rot("head", a.X, 0.16 * bounce - 0.04)
    a.rot("head", a.Y, -0.07 * sway)
    for side, x, phase in (("L", 1, pump), ("R", -1, -pump)):
        a.rot(f"upper_arm.{side}", a.Y, -0.22 * x)
        a.rot(f"upper_arm.{side}", a.X, -0.25 - 0.30 * max(0.0, phase))
        a.rot(f"forearm.{side}", a.X, -1.35 - 0.35 * max(0.0, phase))


for f in range(24):
    if ONLY and f not in ONLY:
        continue
    pose_dance(2 * math.pi * f / 24)
    a.update()
    root.matrix_parent_inverse = Matrix.Identity(4)
    root.matrix_basis = a.bone("head").matrix @ head_rest
    a.update()
    a.render_to(os.path.join(out, f"music_2_{f:02d}.png"))
print("MUSIC_FRAMES_DONE")

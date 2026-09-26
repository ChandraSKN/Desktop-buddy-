"""Pose the BuddyRig and render sprite frames (run inside Blender after build_model.py).

Frames go to OUT_DIR as <anim>_<yaw index>_<frame>.png; pack_frames.py turns them into
the sprite sheets in assets/model3d/. Renders can be done in chunks: jobs() lists every
frame and render(jobs[a:b]) renders a slice."""

import math
import os

import bpy
from mathutils import Quaternion, Vector

OUT_DIR = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_frames")
YAWS = [-0.95, -0.5, -0.22, 0.0, 0.22, 0.5, 0.95]   # + turns him toward screen-right
WAVE_YAWS = [1, 2, 3, 4, 5]                           # indices into YAWS
WALK_FRAMES = 16
WAVE_FRAMES = 12

X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
rig = bpy.data.objects["BuddyRig"]


def reset_pose():
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)


def rot(name, axis, angle):
    """Rotate a bone about an axis given in the character's rest space."""
    pb = rig.pose.bones[name]
    local = pb.bone.matrix_local.to_3x3().inverted() @ Vector(axis)
    pb.rotation_quaternion = Quaternion(local, angle) @ pb.rotation_quaternion


def move(name, offset):
    pb = rig.pose.bones[name]
    pb.location = pb.bone.matrix_local.to_3x3().inverted() @ Vector(offset)


def relaxed_arms(out=0.07, bend=0.14):
    for s, x in (("L", 1), ("R", -1)):
        rot(f"upper_arm.{s}", Y, -out * x)
        rot(f"forearm.{s}", X, -bend)


def pose_idle():
    reset_pose()
    relaxed_arms()


def pose_walk(t):
    reset_pose()
    s, c = math.sin(t), math.cos(t)
    stride = 0.42
    for side, sign, lift in (("L", -1, max(0.0, c)), ("R", 1, max(0.0, -c))):
        thigh = sign * stride * s
        knee = 0.95 * lift ** 1.4
        rot(f"thigh.{side}", X, thigh)
        rot(f"shin.{side}", X, knee)
        rot(f"foot.{side}", X, -(thigh + knee) * 0.85 + 0.25 * lift)
    move("hips", (0, 0, -0.84 * (1 - math.cos(stride * s)) + 0.012 * abs(c)))
    rot("hips", Z, -0.05 * s)
    rot("spine", Z, 0.09 * s)
    rot("spine", X, -0.03)                       # slight forward lean
    rot("head", Z, -0.05 * s)
    for side, x, swing in (("L", 1, s), ("R", -1, -s)):
        rot(f"upper_arm.{side}", Y, -0.07 * x)
        rot(f"upper_arm.{side}", X, 0.4 * swing)
        rot(f"forearm.{side}", X, -(0.22 + 0.3 * max(0.0, -swing)))


def pose_wave(t):
    reset_pose()
    relaxed_arms()
    rig.pose.bones["upper_arm.L"].rotation_quaternion = (1, 0, 0, 0)
    rig.pose.bones["forearm.L"].rotation_quaternion = (1, 0, 0, 0)
    rot("upper_arm.L", Y, -0.85)
    rot("upper_arm.L", X, -0.25)
    rot("forearm.L", Y, -1.55 - 0.32 * math.sin(t))
    hand = rig.pose.bones["hand.L"].bone
    rot("hand.L", (hand.tail_local - hand.head_local).normalized(), math.pi / 2)
    rot("head", Y, 0.07)
    rot("spine", Y, -0.03)


def jobs():
    out = []
    for yi in range(len(YAWS)):
        out.append(("idle", yi, 0, pose_idle))
        for f in range(WALK_FRAMES):
            out.append(("walk", yi, f, lambda f=f: pose_walk(2 * math.pi * f / WALK_FRAMES)))
    for yi in WAVE_YAWS:
        for f in range(WAVE_FRAMES):
            out.append(("wave", yi, f, lambda f=f: pose_wave(2 * math.pi * f / WAVE_FRAMES)))
    return out


def render_one(path, pose, yaw):
    pose()
    rig.rotation_euler = (0, 0, yaw)
    bpy.context.view_layer.update()
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render(batch):
    os.makedirs(OUT_DIR, exist_ok=True)
    for anim, yi, f, pose in batch:
        render_one(os.path.join(OUT_DIR, f"{anim}_{yi}_{f:02d}.png"), pose, YAWS[yi])
    reset_pose()
    rig.rotation_euler = (0, 0, 0)
    return len(batch)

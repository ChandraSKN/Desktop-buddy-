"""Mouth shapes for talking (run with buddy_model.blend loaded).

    BUDDY_FRAMES=/tmp/buddy_talk blender -b blender/buddy_model.blend --python blender/talk_frames.py
    .venv/bin/python blender/pack_frames.py /tmp/buddy_talk

Renders the idle pose at the idle turn angle (yaw index 2) with the mouth at four
openings, closed → wide: talk_2_00 … talk_2_03. Frame 0 matches idle_2, so the app can
switch between idle and talking without a jump. The app picks the opening from the
loudness of his voice, 25 times a second.

The mouth opens downward like a dropping jaw, so its top edge stays under the moustache;
a strip of teeth shows once it's open."""

import os
import sys

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_frames as rf  # noqa: E402  (poses and render settings from the walk/idle frames)

OUT_DIR = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_talk_frames")
YI = 2
# (height, width) of the mouth relative to closed; closed is a thin line
OPENINGS = [(1.0, 1.0), (2.6, 0.95), (4.2, 0.9), (5.8, 0.84)]

mouth = bpy.data.objects["BB_mouth"]
rest = [v.co.copy() for v in mouth.data.vertices]
centre = sum(rest, Vector()) / len(rest)
height = max(v.z for v in rest) - min(v.z for v in rest)


def make_teeth():
    """A flat white ellipse in the mouth's own mesh space, with the mouth's transform, so it
    follows the head exactly like the mouth does."""
    old = bpy.data.objects.get("BB_teeth")
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.0)
    w = (max(v.x for v in rest) - min(v.x for v in rest)) / 2 * 0.62
    bmesh.ops.scale(bm, vec=Vector((w, 0.0006, height * 0.75)), verts=bm.verts)
    me = bpy.data.meshes.new("BB_teeth")
    bm.to_mesh(me)
    bm.free()
    teeth = bpy.data.objects.new("BB_teeth", me)
    mouth.users_collection[0].objects.link(teeth)
    me.materials.append(bpy.data.materials["BB_EyeWhite"])
    teeth.parent = mouth.parent
    teeth.parent_type = mouth.parent_type
    teeth.parent_bone = mouth.parent_bone
    teeth.matrix_parent_inverse = mouth.matrix_parent_inverse.copy()
    teeth.matrix_basis = mouth.matrix_basis.copy()
    return teeth, [v.co.copy() for v in me.vertices]


def set_opening(teeth, teeth_rest, h, w):
    top = max(v.z for v in rest)
    for v, r in zip(mouth.data.vertices, rest):
        z = top - (top - r.z) * h                     # the top edge stays put
        v.co = Vector((centre.x + (r.x - centre.x) * w, r.y, z))
    mouth.data.update()
    # upper teeth: a strip along the top of the opening, just in front of the dark mouth
    # (behind it they only poked through at the thin edges, as a ring)
    teeth.hide_render = h < 2
    front = min(r.y for r in rest)
    at = Vector((centre.x, front - 0.0004, top - height * 0.85))
    for v, r in zip(teeth.data.vertices, teeth_rest):
        v.co = r + at
    teeth.data.update()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    teeth, teeth_rest = make_teeth()
    rf.pose_idle()
    rf.rig.rotation_euler = (0, 0, rf.YAWS[YI])
    bpy.context.view_layer.update()
    for i, (h, w) in enumerate(OPENINGS):
        set_opening(teeth, teeth_rest, h, w)
        bpy.context.scene.render.filepath = os.path.join(OUT_DIR, f"talk_{YI}_{i:02d}.png")
        bpy.ops.render.render(write_still=True)
    set_opening(teeth, teeth_rest, 1.0, 1.0)
    rf.reset_pose()
    rf.rig.rotation_euler = (0, 0, 0)
    print(f"TALK_FRAMES_DONE {len(OPENINGS)} -> {OUT_DIR}")


main()

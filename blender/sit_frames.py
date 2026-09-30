"""Chair + sitting animations for the Blender character (run with buddy_model.blend loaded).

    blender -b blender/buddy_model.blend --python blender/sit_frames.py

Builds a toon-shaded folding wooden chair, then renders at the idle turn angle (yaw index 2):
  pull_2_NN   he brings in a folded chair, opens it and guides it behind him
  sit_2_NN    he sits down on it
  seated_2_NN a gentle seated idle loop
and the chair on its own layer (chair.png) plus sit_meta.json, which holds where the chair
is on screen in every pull frame. The app draws the chair first and the character on top
(he is always in front of it). foldchair_2_NN holds the articulated chair at each pull frame.
Frames go to BUDDY_FRAMES; pack_frames.py merges them into assets/model3d/."""

import json
import math
import os
import sys

import bpy
from mathutils import Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_sit_frames")
ONLY = os.environ.get("BUDDY_ONLY")      # e.g. "pull:0,10,27" to render a few test frames
YAWS = [-0.95, -0.5, -0.22, 0.0, 0.22, 0.5, 0.95]
YI = 2                                    # he sits at the idle angle
PULL_FRAMES, SIT_FRAMES, SEATED_FRAMES = 28, 14, 16
PX_PER_UNIT = 160                         # 360 px tall / 2.25 world units
CHAIR_W, CHAIR_H = 480, 360

X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
scene = bpy.context.scene
rig = bpy.data.objects["BuddyRig"]
cam = scene.camera
LIGHT_DIR = Vector((-0.45, -0.75, 0.55)).normalized()


# ---------------------------------------------------------------- chair
def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c) + (1.0,)


def toon(name, lit, shadow, highlight):
    """Same cel ramp as build_model.toon()."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(emit.outputs[0], out.inputs["Surface"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = LIGHT_DIR
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    remap = nt.nodes.new("ShaderNodeMapRange")
    remap.inputs["From Min"].default_value = -1
    remap.inputs["From Max"].default_value = 1
    nt.links.new(dot.outputs["Value"], remap.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = "CONSTANT"
    cr.elements[0].position, cr.elements[0].color = 0.0, srgb(shadow)
    cr.elements[1].position, cr.elements[1].color = 0.52, srgb(lit)
    cr.elements.new(0.94).color = srgb(highlight)
    nt.links.new(remap.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emit.inputs["Color"])
    return m


# chair space: origin on the floor under the seat centre, front is -Y (like the character)
SEAT_W, SEAT_D, SEAT_TOP = 0.52, 0.42, 0.47
LEG = 0.048
BACK_Y = SEAT_D / 2 - LEG / 2
GRIP = Vector((SEAT_W / 2 - 0.035, BACK_Y, 1.10))     # near end of the top rail
SEATED_AT = Vector((0.0, 0.30, 0.0))                  # chair position when he sits
PULL_FROM = Vector((-0.64, 0.12, 0.0))                # to his right (screen-left)


def build_chair():
    coll = bpy.data.collections.get("BuddyChair")
    if coll:
        for ob in list(coll.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    else:
        coll = bpy.data.collections.new("BuddyChair")
        scene.collection.children.link(coll)
    wood = toon("BB_ChairWood", "#9a6438", "#673f22", "#bb8150")
    seat_mat = toon("BB_ChairSeat", "#b0773f", "#76492a", "#cf965e")
    outline = bpy.data.materials["BB_Outline"]

    root = bpy.data.objects.new("BuddyChair", None)
    coll.objects.link(root)
    root.parent = rig

    def box(name, centre, size, mat):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
        ob = bpy.context.active_object
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        coll.objects.link(ob)
        ob.name = "Chair_" + name
        ob.scale = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        ob.location = centre
        ob.data.materials.append(mat)
        ob.data.materials.append(outline)
        bev = ob.modifiers.new("Bevel", "BEVEL")
        bev.width, bev.segments = min(0.012, min(size) * 0.3), 2
        so = ob.modifiers.new("Outline", "SOLIDIFY")
        so.thickness, so.offset = 0.0075, 1
        so.use_flip_normals, so.use_rim, so.material_offset = True, False, 1
        ob.parent = root
        return ob

    hx, hy = SEAT_W / 2 - LEG / 2 - 0.01, SEAT_D / 2 - LEG / 2
    box("seat", (0, 0, SEAT_TOP - 0.028), (SEAT_W, SEAT_D, 0.056), seat_mat)
    for sx in (-1, 1):
        box(f"front_leg{sx}", (sx * hx, -hy, (SEAT_TOP - 0.05) / 2), (LEG, LEG, SEAT_TOP - 0.05), wood)
        box(f"back_leg{sx}", (sx * hx, hy, 1.14 / 2), (LEG, LEG, 1.14), wood)
        box(f"side_rung{sx}", (sx * hx, 0, 0.16), (0.026, SEAT_D - LEG, 0.03), wood)
    box("front_rung", (0, -hy, 0.22), (SEAT_W - LEG - 0.02, 0.026, 0.03), wood)
    box("top_rail", (0, BACK_Y, 1.07), (SEAT_W + 0.02, 0.05, 0.13), wood)
    box("mid_rail", (0, BACK_Y, 0.84), (SEAT_W - 0.06, 0.035, 0.06), wood)
    for sx in (-0.33, 0.0, 0.33):
        box(f"slat{sx}", (sx * SEAT_W / 2, BACK_Y, 0.95), (0.04, 0.03, 0.16), wood)
    # A real folding mechanism: the seat pivots up at its rear hinge, and
    # the front leg assembly tucks against the back frame.
    for name, pivot, names in (
        ("SeatHinge", (0, hy, SEAT_TOP - 0.028), ["seat"]),
        ("LegHinge", (0, hy, SEAT_TOP - 0.05),
         ["front_leg-1", "front_leg1", "front_rung", "side_rung-1", "side_rung1"]),
    ):
        hinge = bpy.data.objects.new(name, None)
        coll.objects.link(hinge)
        hinge.parent = root
        hinge.location = pivot
        for part in names:
            ob = bpy.data.objects["Chair_" + part]
            ob.parent = hinge
            ob.location -= Vector(pivot)
    return root, coll


def unfold_chair(u):
    opened = smooth((u - 0.22) / 0.40)
    bpy.data.objects["SeatHinge"].rotation_euler.x = -1.48 * (1 - opened)
    bpy.data.objects["LegHinge"].rotation_euler.x = 0.72 * (1 - opened)


# ---------------------------------------------------------------- posing helpers
def reset_pose():
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)


def rot(name, axis, angle):
    pb = rig.pose.bones[name]
    local = pb.bone.matrix_local.to_3x3().inverted() @ Vector(axis)
    pb.rotation_quaternion = Quaternion(local, angle) @ pb.rotation_quaternion


def move(name, offset):
    pb = rig.pose.bones[name]
    pb.location = pb.bone.matrix_local.to_3x3().inverted() @ Vector(offset)


def update():
    bpy.context.view_layer.update()


# Planar angles. Rotating about Y turns a hanging bone (0,0,-1) to (-sin a, 0, -cos a);
# about X, to (0, sin a, -cos a). Both add up, so aiming = rotating by the difference.
def ang_y(v):
    return math.atan2(-v.x, -v.z)


def ang_x(v):
    return math.atan2(v.y, -v.z)


def aim(name, axis, want):
    v = rig.pose.bones[name].vector
    rot(name, axis, want - (ang_y(v) if axis == Y else ang_x(v)))
    update()


def two_bone(root, target, l1, l2, plane, bend):
    """Planar two-bone IK. Returns the upper and lower bone angles (see ang_x/ang_y).
    bend picks the elbow/knee side: a vector the joint should lean toward."""
    if plane == Y:
        to2 = lambda v: (v.x, v.z)
        ang = lambda d: math.atan2(-d[0], -d[1])
        dirv = lambda a: (-math.sin(a), -math.cos(a))
    else:
        to2 = lambda v: (v.y, v.z)
        ang = lambda d: math.atan2(d[0], -d[1])
        dirv = lambda a: (math.sin(a), -math.cos(a))
    r, t, b = to2(root), to2(target), to2(bend)
    d = (t[0] - r[0], t[1] - r[1])
    dist = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, math.hypot(*d)))
    base = ang(d)
    alpha = math.acos(max(-1, min(1, (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist))))
    best = None
    for a1 in (base + alpha, base - alpha):
        u = dirv(a1)
        e = (r[0] + l1 * u[0], r[1] + l1 * u[1])
        score = (e[0] - r[0]) * b[0] + (e[1] - r[1]) * b[1]
        if best is None or score > best[0]:
            best = (score, a1, e)
    _, a1, e = best
    a2 = ang((t[0] - e[0], t[1] - e[1]))
    return a1, a2


def bone(name):
    return rig.pose.bones[name]


def relaxed_arm(s, out=0.07, bend=0.14):
    x = 1 if s == "L" else -1
    rot(f"upper_arm.{s}", Y, -out * x)
    rot(f"forearm.{s}", X, -bend)


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return a + (b - a) * u


# ---------------------------------------------------------------- the three animations
REST_HAND = None


def rest_hand():
    global REST_HAND
    if REST_HAND is None:
        reset_pose()
        relaxed_arm("R")
        update()
        REST_HAND = bone("hand.R").tail.copy()
    return REST_HAND.copy()


def arm_reach(target, lean_bend=Vector((-0.4, 0, -1))):
    """Point the right arm at target (armature space, frontal plane)."""
    ua, fa, hd = bone("upper_arm.R"), bone("forearm.R"), bone("hand.R")
    l2 = fa.bone.length + hd.bone.length
    a1, a2 = two_bone(ua.head, target, ua.bone.length, l2, Y, ua.head + lean_bend)
    aim("upper_arm.R", Y, a1)
    aim("forearm.R", Y, a2)
    aim("hand.R", Y, a2)


def chair_at_pull(u):
    """Chair position (character space) during the pull, u in [0, 1]."""
    # Bring a folded chair from beyond the screen edge, open it on the floor,
    # then guide it all the way behind the hips without a free slide.
    if u < 0.22:
        return Vector((-1.65, 0.12, 0)).lerp(PULL_FROM, smooth(u / 0.22))
    return PULL_FROM.lerp(SEATED_AT, smooth((u - 0.62) / 0.30))


def pose_pull(u):
    reset_pose()
    relaxed_arm("L")
    # how much he is turned toward the chair: in while reaching, out as he lets go
    env = smooth(u / 0.20) * (1 - smooth((u - 0.88) / 0.12))
    rot("spine", Y, -0.14 * env)          # lean toward his right
    rot("spine", Z, -0.18 * env)          # and turn his shoulders to it
    rot("hips", Z, -0.06 * env)
    rot("head", Z, -0.45 * env)
    rot("head", Y, 0.08 * env)
    update()
    grip = chair_at_pull(u) + GRIP
    grip.y = 0
    hand0 = rest_hand()
    if u < 0.22:
        target = hand0.lerp(grip, smooth(u / 0.22))
    elif u < 0.92:
        target = grip
    else:
        target = grip.lerp(hand0, smooth((u - 0.92) / 0.08))
    arm_reach(target)
    # The other hand presses the seat down as the hinge opens.
    opening = smooth((u - 0.20) / 0.12) * (1 - smooth((u - 0.62) / 0.18))
    rot("upper_arm.L", X, -0.65 * opening)
    rot("upper_arm.L", Y, 0.32 * opening)
    rot("forearm.L", X, -0.8 * opening)
    update()


def seated_legs(hip, foot_y, spread=0.0):
    """Hips at hip (y, z); ankles planted at foot_y; knees forward; feet flat."""
    move("hips", (0, hip[0], hip[1] - 0.93))
    update()
    for s in ("L", "R"):
        th, sh, ft = bone(f"thigh.{s}"), bone(f"shin.{s}"), bone(f"foot.{s}")
        ankle = Vector((th.head.x, foot_y, ft.bone.head_local.z))
        a1, a2 = two_bone(th.head, ankle, th.bone.length, sh.bone.length, X,
                          th.head + Vector((0, -1, 0.2)))
        aim(f"thigh.{s}", X, a1)
        aim(f"shin.{s}", X, a2)
        aim(f"foot.{s}", X, ang_x(ft.bone.tail_local - ft.bone.head_local))
        rot(f"thigh.{s}", Z, spread * (1 if s == "L" else -1))    # knees a little apart
    update()


def pose_sit(u):
    reset_pose()
    k = smooth((u - 0.12) / 0.80)
    hip = (lerp(0.0, 0.25, k), lerp(0.93, 0.57, k))
    lean = 0.34 * math.sin(math.pi * min(1.0, u * 1.05)) - 0.04 * k   # forward as he lowers
    rot("spine", X, lean)
    rot("head", X, -0.6 * lean)
    seated_legs(hip, -0.10 * smooth(u / 0.18), 0.14 * k)
    for s, x in (("L", 1), ("R", -1)):
        rot(f"upper_arm.{s}", Y, -0.07 * x + 0.03 * x * k)     # hands come in onto the thighs
        rot(f"upper_arm.{s}", X, -0.25 * k)
        rot(f"forearm.{s}", X, -0.14 - 0.9 * k)
        rot(f"forearm.{s}", Y, 0.12 * x * k)
    update()


def pose_seated(t):
    pose_sit(1.0)
    rot("head", Z, 0.13 * math.sin(t))
    rot("head", X, 0.04 * math.sin(2 * t))
    rot("spine", Z, 0.03 * math.sin(t))
    update()


# ---------------------------------------------------------------- rendering
def set_frame(w, h, ortho):
    r = scene.render
    r.resolution_x, r.resolution_y = w, h
    cam.data.ortho_scale = ortho
    cam.data.shift_y = -0.045 / ortho       # same vertical framing as the 240x360 frames


def show(chair_coll, chair, character):
    chair_coll.hide_render = not chair
    for ob in bpy.data.collections["BuddyModel"].objects:
        ob.hide_render = not character


def render_to(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def screen_offset(delta_char):
    """Pixel offset on screen (x right, y down) of a move in character space."""
    world = rig.matrix_world.to_3x3() @ delta_char
    m = cam.matrix_world.to_3x3()
    right, up = m.col[0], m.col[1]
    return [round(world.dot(right) * PX_PER_UNIT, 2), round(-world.dot(up) * PX_PER_UNIT, 2)]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    chair, chair_coll = build_chair()
    rig.rotation_euler = (0, 0, YAWS[YI])
    update()

    jobs = []
    for f in range(PULL_FRAMES):
        jobs.append(("pull", f, lambda f=f: pose_pull(f / (PULL_FRAMES - 1))))
    for f in range(SIT_FRAMES):
        jobs.append(("sit", f, lambda f=f: pose_sit(f / (SIT_FRAMES - 1))))
    for f in range(SEATED_FRAMES):
        jobs.append(("seated", f, lambda f=f: pose_seated(2 * math.pi * f / SEATED_FRAMES)))
    if ONLY:
        anim, frames = ONLY.split(":")
        keep = {int(x) for x in frames.split(",")}
        jobs = [j for j in jobs if j[0] == anim and j[1] in keep]

    # the chair on its own, where he sits on it
    chair.location = SEATED_AT
    unfold_chair(1.0)
    show(chair_coll, chair=True, character=False)
    set_frame(CHAIR_W, CHAIR_H, 2.25 * CHAIR_W / 360)
    render_to(os.path.join(OUT_DIR, "chair.png"))

    for f in range(PULL_FRAMES):
        u = f / (PULL_FRAMES - 1)
        chair.location = chair_at_pull(u)
        unfold_chair(u)
        update()
        render_to(os.path.join(OUT_DIR, f"foldchair_{YI}_{f:02d}.png"))
    unfold_chair(1.0)
    show(chair_coll, chair=False, character=True)
    set_frame(240, 360, 2.25)
    for anim, f, pose in jobs:
        pose()
        render_to(os.path.join(OUT_DIR, f"{anim}_{YI}_{f:02d}.png"))
    if os.environ.get("BUDDY_PREVIEW"):     # test composite with chair + character together
        show(chair_coll, chair=True, character=True)
        set_frame(CHAIR_W, CHAIR_H, 2.25 * CHAIR_W / 360)
        for anim, f, pose in jobs:
            pose()
            if anim == "pull":
                chair.location = chair_at_pull(f / (PULL_FRAMES - 1))
            else:
                chair.location = SEATED_AT
            unfold_chair(f / (PULL_FRAMES - 1) if anim == "pull" else 1.0)
            update()
            render_to(os.path.join(OUT_DIR, f"preview_{anim}_{f:02d}.png"))
        set_frame(240, 360, 2.25)

    meta = {
        "yaw_index": YI,
        "chair": {"file": "chair.png", "fold_strip": "foldchair_2.png", "frame_size": [CHAIR_W, CHAIR_H],
                  # where the chair is in each pull frame, relative to where he sits on it
                  "pull_offsets": [screen_offset(chair_at_pull(f / (PULL_FRAMES - 1)) - SEATED_AT)
                                   for f in range(PULL_FRAMES)]},
    }
    with open(os.path.join(OUT_DIR, "sit_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    reset_pose()
    rig.rotation_euler = (0, 0, 0)
    print(f"SIT_FRAMES_DONE {len(jobs)} frames -> {OUT_DIR}")


if __name__ == "__main__":
    main()

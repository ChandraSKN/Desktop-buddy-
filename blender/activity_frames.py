"""Props + loops for what Buddy is busy with (run with buddy_model.blend loaded).

    BUDDY_FRAMES=/tmp/buddy_act blender -b blender/buddy_model.blend --python blender/activity_frames.py
    .venv/bin/python blender/pack_frames.py /tmp/buddy_act

At the idle turn angle (yaw index 2), like talking and sitting:
  film_2_NN   recording a meeting: a handheld camcorder held up in both hands, lens toward
              you, flip-out screen toward him, red REC light blinking; a gentle sway
  write_2_NN  writing the minutes: a clipboard in his left hand, head down, the pen
              scribbling along a line of the paper

The props are built here and parented to the rig; each frame the arms are posed with a
small 3D two-bone IK so the hands land on the props. BUDDY_ONLY="film:0,3" renders just
some frames."""

import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

OUT_DIR = os.environ.get("BUDDY_FRAMES", "/tmp/buddy_act_frames")
ONLY = os.environ.get("BUDDY_ONLY")
YAWS = [-0.95, -0.5, -0.22, 0.0, 0.22, 0.5, 0.95]
YI = 2
FILM_FRAMES, WRITE_FRAMES = 12, 16
CAM_SCALE = 1.35
EYECUP = Vector((0.0, 0.125, 0.035))              # camera space: back of the camera, on top
RIGHT_EYE = Vector((-0.06, -0.165, 1.725))        # rig space, from the model's BB_eye.R

X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
scene = bpy.context.scene
rig = bpy.data.objects["BuddyRig"]
LIGHT_DIR = Vector((-0.45, -0.75, 0.55)).normalized()


# ---------------------------------------------------------------- materials / props
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


def flat(name, colour):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = srgb(colour)
    nt.links.new(emit.outputs[0], out.inputs["Surface"])
    return m


def collection():
    coll = bpy.data.collections.get("BuddyProps")
    if coll:
        for ob in list(coll.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    else:
        coll = bpy.data.collections.new("BuddyProps")
        scene.collection.children.link(coll)
    return coll


def part(coll, parent, name, kind, size, at, mat, outline=True, rot=None, bevel=0.006):
    """A box or cylinder, in the parent's space; size is (x, y, z) extent."""
    if kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.5, depth=1, location=(0, 0, 0))
    else:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    ob = bpy.context.active_object
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    ob.name = "Prop_" + name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    ob.data.materials.append(mat)
    if bevel and kind == "box":
        bev = ob.modifiers.new("Bevel", "BEVEL")
        bev.width, bev.segments = min(bevel, min(size) * 0.3), 2
    if outline:
        ob.data.materials.append(bpy.data.materials["BB_Outline"])
        so = ob.modifiers.new("Outline", "SOLIDIFY")
        so.thickness, so.offset = 0.006, 1
        so.use_flip_normals, so.use_rim, so.material_offset = True, False, 1
    ob.parent = parent
    ob.location = at
    if rot:
        ob.rotation_euler = rot
    return ob


def build_camera(coll):
    """Camcorder, local space: lens along -Y (toward you), top +Z, his side +Y."""
    body = toon("BB_CamBody", "#3a3f4b", "#23262e", "#5a6273")
    trim = toon("BB_CamTrim", "#8a93a6", "#5c6475", "#b8c0d0")
    glass = toon("BB_CamGlass", "#27415f", "#16263a", "#6f9cc9")
    root = bpy.data.objects.new("Prop_camera", None)
    coll.objects.link(root)
    root.parent = rig
    part(coll, root, "cam_body", "box", (0.12, 0.2, 0.11), (0, 0.0, 0), body)
    part(coll, root, "cam_lens", "cyl", (0.085, 0.085, 0.08), (0, -0.13, 0.0), trim, rot=(math.pi / 2, 0, 0))
    part(coll, root, "cam_glass", "cyl", (0.06, 0.06, 0.01), (0, -0.172, 0.0), glass, outline=False,
         rot=(math.pi / 2, 0, 0))
    part(coll, root, "cam_handle", "box", (0.05, 0.14, 0.03), (0, 0.01, 0.072), body)
    # flip-out screen folded shut against his left side (+X); he looks through the eyepiece
    part(coll, root, "cam_flip", "box", (0.012, 0.11, 0.075), (0.066, 0.02, 0.0), body)
    # viewfinder: a rubber eyecup at the back, on top, that he holds to his eye
    part(coll, root, "cam_eyecup", "box", (0.05, 0.045, 0.045), EYECUP - Vector((0, 0.02, 0)), trim)
    part(coll, root, "cam_eyecup_rim", "box", (0.055, 0.012, 0.05), EYECUP, body, bevel=0.004)
    rec = part(coll, root, "cam_rec", "cyl", (0.022, 0.022, 0.012), (0.035, -0.07, 0.058),
               flat("BB_RecOn", "#ff2d3a"), outline=False)
    return root, rec


def build_clipboard(coll):
    """Clipboard, local space: board in the XZ plane, paper facing -Y, clip at +Z."""
    board = toon("BB_Board", "#a9713d", "#744a25", "#c98f55")
    paper = flat("BB_Paper", "#fbfbf6")
    ink = flat("BB_Ink", "#5b6b8c")
    metal = toon("BB_Clip", "#b9c0cc", "#7d8594", "#e6ebf2")
    root = bpy.data.objects.new("Prop_clipboard", None)
    coll.objects.link(root)
    root.parent = rig
    part(coll, root, "board", "box", (0.30, 0.014, 0.40), (0, 0, 0), board)
    part(coll, root, "paper", "box", (0.26, 0.004, 0.34), (0, -0.009, -0.02), paper, outline=False, bevel=0)
    part(coll, root, "clip", "box", (0.12, 0.03, 0.05), (0, -0.012, 0.18), metal)
    # lines already written: short grey strokes of varied length
    for i, length in enumerate((0.19, 0.16, 0.2, 0.12, 0.17, 0.08)):
        part(coll, root, f"line{i}", "box", (length, 0.002, 0.008),
             (-0.105 + length / 2, -0.0115, 0.1 - i * 0.045), ink, outline=False, bevel=0)
    pen_mat = toon("BB_Pen", "#2f5bd3", "#1d3a8c", "#6f93f0")
    pen = bpy.data.objects.new("Prop_pen", None)
    coll.objects.link(pen)
    pen.parent = rig
    part(coll, pen, "pen_barrel", "cyl", (0.022, 0.022, 0.15), (0, 0, 0.085), pen_mat)
    part(coll, pen, "pen_tip", "cyl", (0.01, 0.01, 0.02), (0, 0, 0.005),
         toon("BB_PenTip", "#d9dde4", "#9aa1ad", "#ffffff"), outline=False)
    return root, pen


# ---------------------------------------------------------------- posing
def reset_pose():
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)


def update():
    bpy.context.view_layer.update()


def bone(name):
    return rig.pose.bones[name]


def rot(name, axis, angle):
    """Rotate about an axis in the character's rest space (as in render_frames.py)."""
    pb = bone(name)
    local = pb.bone.matrix_local.to_3x3().inverted() @ Vector(axis)
    pb.rotation_quaternion = Quaternion(local, angle) @ pb.rotation_quaternion


def point(name, want):
    """Turn a bone (as posed now) so it points along want (armature space)."""
    update()
    pb = bone(name)
    cur = (pb.tail - pb.head).normalized()
    axis, angle = cur.rotation_difference(want.normalized()).to_axis_angle()
    local = pb.matrix.to_3x3().normalized().inverted() @ axis
    pb.rotation_quaternion = pb.rotation_quaternion @ Quaternion(local, angle)
    update()


def reach(side, wrist, hand_dir, pole):
    """Two-bone IK: forearm tail (the wrist) at `wrist`, elbow bent toward `pole`, hand
    pointing along hand_dir. All in armature space."""
    update()
    ua, fa = bone(f"upper_arm.{side}"), bone(f"forearm.{side}")
    s = ua.head.copy()
    l1, l2 = ua.bone.length, fa.bone.length
    d = wrist - s
    dist = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, d.length))
    n = d.normalized()
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    p = pole - s
    p = (p - n * p.dot(n)).normalized()
    elbow = s + n * a + p * h
    point(f"upper_arm.{side}", elbow - s)
    point(f"forearm.{side}", s + n * dist - elbow)
    point(f"hand.{side}", hand_dir)


def place(ob, at, rot_m, scale=1.0):
    """Put a prop root at `at` with rotation rot_m (3x3), both in rig space."""
    ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.matrix_basis = Matrix.Translation(at) @ rot_m.to_4x4() @ Matrix.Scale(scale, 4)


def turn(yaw=0.0, pitch=0.0, roll=0.0):
    return (Matrix.Rotation(yaw, 3, "Z") @ Matrix.Rotation(pitch, 3, "X") @ Matrix.Rotation(roll, 3, "Y"))


def pose_film(t, cam, rec, on):
    """Camcorder up at his face, the eyepiece at his right eye, lens toward you; a small
    handheld wobble."""
    reset_pose()
    rot("head", Y, -0.05)                         # cheek leaning into the camera
    rot("head", Z, 0.03 * math.sin(t))
    update()
    m = turn(yaw=0.28 + 0.03 * math.sin(t), pitch=0.02 * math.sin(2 * t))   # lens a bit toward screen-right
    wobble = Vector((0.006 * math.sin(t), 0.0, 0.004 * math.sin(2 * t)))
    eye = RIGHT_EYE + wobble
    centre = eye - m @ (EYECUP * CAM_SCALE) + Vector((0, -0.005, 0))
    place(cam, centre, m, CAM_SCALE)
    # right hand wrapped round his right side (-X) of the body, elbow out and down;
    # left hand under the lens, holding it up
    reach("R", centre + m @ (Vector((-0.1, 0.02, -0.02)) * CAM_SCALE), m @ Vector((0.2, -1, 0.1)),
          Vector((-1.0, 0.1, -0.4)))
    reach("L", centre + m @ (Vector((0.03, -0.07, -0.1)) * CAM_SCALE), m @ Vector((-0.6, -0.6, 0.45)),
          Vector((0.5, 0.0, -0.9)))
    rec.hide_render = not on


# the pen writes along one line of the paper, then hops back: board-space (x, z) points
BOARD_AT = Vector((0.06, -0.33, 1.02))
BOARD_TURN = turn(yaw=0.05, pitch=-0.95)          # tipped back so the paper faces up at him


def pen_path(u):
    """Tip position on the paper (board space) at u in [0, 1): a wavy line left to right."""
    x = -0.09 + 0.17 * u
    z = -0.10 + 0.012 * math.sin(u * 2 * math.pi * 7)
    return Vector((x, -0.013, z))


def pose_write(t, board, pen):
    reset_pose()
    u = (t / (2 * math.pi)) % 1.0
    rot("spine", X, 0.10)
    rot("head", X, 0.42 + 0.02 * math.sin(2 * t))   # looking down at the page
    rot("head", Z, 0.12 - 0.12 * u)                  # following the pen
    update()
    place(board, BOARD_AT, BOARD_TURN)
    # left hand holds the board's lower left corner (his left = +X)
    reach("L", BOARD_AT + BOARD_TURN @ Vector((0.13, 0.02, -0.14)), BOARD_TURN @ Vector((-0.6, 0, 0.8)),
          Vector((0.7, 0.3, -0.4)))
    tip = BOARD_AT + BOARD_TURN @ pen_path(u)
    lift = 0.012 * max(0.0, math.sin(u * 2 * math.pi * 7 + 1.2))   # tiny lifts between words
    up = (BOARD_TURN @ Vector((-0.05, -0.55, 0.35))).normalized()
    tip = tip + up * lift
    pen_dir = (Vector((-0.35, 0.25, 0.9))).normalized()             # pen leans back toward his hand
    place(pen, tip, Vector((0, 0, 1)).rotation_difference(pen_dir).to_matrix())
    reach("R", tip + pen_dir * 0.13 + Vector((-0.02, 0.03, 0)), (tip - (tip + pen_dir * 0.13)),
          Vector((-0.7, 0.2, -0.3)))


# ---------------------------------------------------------------- rendering
def render_to(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    coll = collection()
    cam, rec = build_camera(coll)
    board, pen = build_clipboard(coll)
    rig.rotation_euler = (0, 0, YAWS[YI])
    update()

    def only(objs):
        for ob in coll.objects:
            ob.hide_render = True
        for root in objs:
            root.hide_render = False
            for child in root.children_recursive:
                child.hide_render = False

    jobs = []
    for f in range(FILM_FRAMES):
        t = 2 * math.pi * f / FILM_FRAMES
        jobs.append(("film", f, [cam], lambda t=t, f=f: pose_film(t, cam, rec, on=(f // 3) % 2 == 0)))
    for f in range(WRITE_FRAMES):
        t = 2 * math.pi * f / WRITE_FRAMES
        jobs.append(("write", f, [board, pen], lambda t=t: pose_write(t, board, pen)))
    if ONLY:
        anim, frames = ONLY.split(":")
        keep = {int(x) for x in frames.split(",")}
        jobs = [j for j in jobs if j[0] == anim and j[1] in keep]

    for anim, f, props, pose in jobs:
        only(props)
        pose()
        update()
        render_to(os.path.join(OUT_DIR, f"{anim}_{YI}_{f:02d}.png"))
    only([])
    reset_pose()
    rig.rotation_euler = (0, 0, 0)
    print(f"ACTIVITY_FRAMES_DONE {len(jobs)} frames -> {OUT_DIR}")


if __name__ == "__main__":
    main()

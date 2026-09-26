"""Build the Desktop Buddy 3D character in Blender (run inside Blender).

A toon-shaded, rigged model of the character in assets/Hero_image.png: short black hair,
moustache, dark suit over an open-collar white shirt, black belt and dress shoes.
Every part is a separate mesh parented to a bone of the "BuddyRig" armature, so poses
are rigid and cheap. Shading is cel-style: a fixed light direction dotted with the normal
feeds a constant colour ramp, and each part has an inverted-hull outline.

Front of the character is -Y, up is +Z, the soles of the shoes are at z=0.
Run render_frames.py afterwards to produce the sprite frames the app uses."""

import math

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

COLL = "BuddyModel"
LIGHT_DIR = Vector((-0.45, -0.75, 0.55)).normalized()
OUTLINE_W = 0.0075
HEAD_SCALE = 1.2          # cartoon proportions: a bigger head, scaled about the neck
HEAD_PIVOT = Vector((0, 0, 1.5))
SH = 0.028                # extra shoulder width (muscular build): arms sit further out


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c) + (1.0,)


# ---------------------------------------------------------------- scene reset
old = bpy.data.collections.get(COLL)
if old:
    for ob in list(old.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
            elif isinstance(data, bpy.types.MetaBall):
                bpy.data.metaballs.remove(data)
    bpy.data.collections.remove(old)
for name in ("Cube", "Light"):          # Blender's default startup objects
    ob = bpy.data.objects.get(name)
    if ob:
        bpy.data.objects.remove(ob, do_unlink=True)

scene = bpy.context.scene
coll = bpy.data.collections.new(COLL)
scene.collection.children.link(coll)


# ---------------------------------------------------------------- materials
def toon(name, lit, shadow, highlight=None, flat=False, cull=False):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if not m.use_nodes:   # always on in Blender 5+
        m.use_nodes = True
    m.use_backface_culling = cull
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(emit.outputs[0], out.inputs["Surface"])
    if flat:
        emit.inputs["Color"].default_value = srgb(lit)
        return m
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = LIGHT_DIR
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = "CONSTANT"
    cr.elements[0].position = 0.0
    cr.elements[0].color = srgb(shadow)
    cr.elements[1].position = 0.52          # dot > ~0.04 is lit
    cr.elements[1].color = srgb(lit)
    if highlight:
        e = cr.elements.new(0.94)
        e.color = srgb(highlight)
    remap = nt.nodes.new("ShaderNodeMapRange")   # dot in [-1, 1] -> [0, 1]
    remap.inputs["From Min"].default_value = -1
    remap.inputs["From Max"].default_value = 1
    nt.links.new(dot.outputs["Value"], remap.inputs["Value"])
    nt.links.new(remap.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emit.inputs["Color"])
    return m


OUTLINE = toon("BB_Outline", "#0b0908", None, flat=True, cull=True)
M = {
    "skin": toon("BB_Skin", "#b97a4a", "#8a5532", "#c98b58"),
    "beard": toon("BB_Beard", "#8e5d3c", "#6a4128"),
    "hair": toon("BB_Hair", "#1b1817", "#0c0a0a", "#312c2a"),
    "jacket": toon("BB_Jacket", "#2c3337", "#191e21"),
    "trousers": toon("BB_Trousers", "#252a2e", "#15181b"),
    "shirt": toon("BB_Shirt", "#eeeae2", "#b4b8c9"),
    "belt": toon("BB_Belt", "#1d1d1d", "#101010", "#3a3a3a"),
    "buckle": toon("BB_Buckle", "#77776f", "#4a4a45", "#b0b0a8"),
    "shoe": toon("BB_Shoe", "#191919", "#0b0b0b", "#555a63"),
    "button": toon("BB_Button", "#d9d6cf", "#a9a7a0"),
    "jbutton": toon("BB_JacketButton", "#15181a", "#0b0c0d"),
    "badge": toon("BB_Badge", "#c7743c", "#9a5528"),
    "eyewhite": toon("BB_EyeWhite", "#f6f3ec", None, flat=True),
    "iris": toon("BB_Iris", "#3d2617", None, flat=True),
    "pupil": toon("BB_Pupil", "#0d0806", None, flat=True),
    "shine": toon("BB_Shine", "#ffffff", None, flat=True),
    "line": toon("BB_Line", "#120d0b", None, flat=True),
    "mouth": toon("BB_Mouth", "#5a2e22", None, flat=True),
}


# ---------------------------------------------------------------- armature
BONES = [  # name, head, tail, parent
    ("root", (0, 0, 0), (0, 0, 0.25), None),
    ("hips", (0, 0, 0.93), (0, 0, 1.05), "root"),
    ("spine", (0, 0, 1.05), (0, 0, 1.45), "hips"),
    ("head", (0, 0, 1.47), (0, 0, 1.85), "spine"),
]
for s, x in (("L", 1), ("R", -1)):
    BONES += [
        (f"upper_arm.{s}", ((0.20 + SH) * x, 0, 1.40), ((0.235 + SH) * x, 0, 1.13), "spine"),
        (f"forearm.{s}", ((0.235 + SH) * x, 0, 1.13), ((0.252 + SH) * x, 0, 0.89), f"upper_arm.{s}"),
        (f"hand.{s}", ((0.252 + SH) * x, 0, 0.89), ((0.258 + SH) * x, 0, 0.78), f"forearm.{s}"),
        (f"thigh.{s}", (0.095 * x, 0, 0.93), (0.10 * x, 0, 0.50), "hips"),
        (f"shin.{s}", (0.10 * x, 0, 0.50), (0.10 * x, 0, 0.09), f"thigh.{s}"),
        (f"foot.{s}", (0.10 * x, 0, 0.09), (0.10 * x, -0.12, 0.03), f"shin.{s}"),
    ]

arm_data = bpy.data.armatures.new("BuddyRig")
rig = bpy.data.objects.new("BuddyRig", arm_data)
coll.objects.link(rig)
rig.show_in_front = True
for ob in bpy.context.selected_objects:
    ob.select_set(False)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
for name, h, t, parent in BONES:
    eb = arm_data.edit_bones.new(name)
    eb.head, eb.tail = h, t
    eb.roll = 0
    if parent:
        eb.parent = arm_data.edit_bones[parent]
        eb.use_connect = False
bpy.ops.object.mode_set(mode="OBJECT")
bpy.context.view_layer.update()


# ---------------------------------------------------------------- mesh helpers
def orient_outward(bm):
    centre = sum((v.co for v in bm.verts), Vector()) / len(bm.verts)
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()


def loft(rings, segs=24, arc=None, caps=True):
    """rings: (z, rx, ry[, cx, cy]) ellipses stacked along +Z. arc: (start, end) angles in
    degrees for an open surface (0 deg = +X, -90 deg = front)."""
    bm = bmesh.new()
    layers = []
    for ring in rings:
        z, rx, ry = ring[:3]
        cx, cy = ring[3:5] if len(ring) > 3 else (0, 0)
        if arc:
            a0, a1 = arc(z) if callable(arc) else arc
            angles = [math.radians(a0 + (a1 - a0) * i / segs) for i in range(segs + 1)]
        else:
            angles = [2 * math.pi * i / segs for i in range(segs)]
        layers.append([bm.verts.new((cx + rx * math.cos(a), cy + ry * math.sin(a), z)) for a in angles])
    n = len(layers[0])
    for lo, hi in zip(layers, layers[1:]):
        for i in range(n if not arc else n - 1):
            j = (i + 1) % n
            bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    if caps and not arc:
        bm.faces.new(layers[0])
        bm.faces.new(layers[-1])
    bm.normal_update()
    orient_outward(bm)
    return bm


def ellipsoid(radii, segs=20, rings=12):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    bmesh.ops.scale(bm, vec=Vector(radii), verts=bm.verts)
    return bm


def place(bm, loc=(0, 0, 0), rot=(0, 0, 0)):
    from mathutils import Euler
    mat = Matrix.Translation(Vector(loc)) @ Euler(rot).to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=bm.verts)
    return bm


def along(bm, a, b):
    """Map a loft built along +Z (0..1) onto the segment a->b."""
    a, b = Vector(a), Vector(b)
    d = b - a
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    mat = Matrix.Translation(a) @ rot @ Matrix.Diagonal((1, 1, d.length, 1))
    # keep radii unscaled: scale only local Z
    bmesh.ops.transform(bm, matrix=mat, verts=bm.verts)
    return bm


def part(name, bm, mat, bone, outline=True, subdiv=1, smooth=True):
    if bone == "head" and name != "neck":
        mat_s = (Matrix.Translation(HEAD_PIVOT) @ Matrix.Scale(HEAD_SCALE, 4)
                 @ Matrix.Translation(-HEAD_PIVOT))
        bmesh.ops.transform(bm, matrix=mat_s, verts=bm.verts)
    me = bpy.data.meshes.new("BB_" + name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    ob = bpy.data.objects.new("BB_" + name, me)
    coll.objects.link(ob)
    me.materials.append(mat)
    if subdiv:
        sd = ob.modifiers.new("Subdivision", "SUBSURF")
        sd.levels = sd.render_levels = subdiv
    if outline:
        me.materials.append(OUTLINE)
        so = ob.modifiers.new("Outline", "SOLIDIFY")
        so.thickness = outline if isinstance(outline, float) else OUTLINE_W
        so.offset = 1
        so.use_flip_normals = True
        so.use_rim = False
        so.material_offset = 1
    world = ob.matrix_world.copy()
    ob.parent = rig
    ob.parent_type = "BONE"
    ob.parent_bone = bone
    bpy.context.view_layer.update()
    ob.matrix_world = world
    return ob


# ---------------------------------------------------------------- body
# Legs, shoes
for s, x in (("L", 1), ("R", -1)):
    part(f"thigh.{s}", along(loft([(0, .102, .104), (.45, .09, .092), (.9, .076, .08),
                                    (1.0, .06, .064), (1.04, .03, .03)]),
                             (0.095 * x, 0, 0.99), (0.10 * x, 0, 0.47)), M["trousers"], f"thigh.{s}")
    part(f"shin.{s}", along(loft([(0, .045, .045), (.06, .072, .075), (.5, .068, .07),
                                  (.92, .07, .072), (1.0, .074, .076)]),
                            (0.10 * x, 0, 0.53), (0.10 * x, 0, 0.075)), M["trousers"], f"shin.{s}")
    shoe = ellipsoid((0.055, 0.128, 0.052))
    place(shoe, (0.10 * x, -0.045, 0.046))
    for v in shoe.verts:
        v.co.z = max(v.co.z, 0.004)
    part(f"shoe.{s}", shoe, M["shoe"], f"foot.{s}")

part("pelvis", loft([(0.78, .17, .1), (0.84, .19, .115), (0.92, .196, .12), (1.0, .19, .118),
                     (1.02, .15, .09)]), M["trousers"], "hips")
part("belt", loft([(0.975, .199, .124), (1.025, .196, .122)], segs=32), M["belt"], "hips", subdiv=0)
part("buckle", place(ellipsoid((0.028, 0.008, 0.02), 12, 8), (0, -0.124, 1.0)), M["buckle"], "hips",
     outline=0.004, subdiv=0)

# Torso: shirt underneath, jacket with an opening down the front
SHIRT = [(0.9, .183, .112), (1.05, .186, .116), (1.2, .214, .13), (1.36, .232, .134),
         (1.44, .2, .11), (1.5, .095, .07)]


def shirt_front(z):
    for (z0, _, y0), (z1, _, y1) in zip(SHIRT, SHIRT[1:]):
        if z0 <= z <= z1:
            return -(y0 + (y1 - y0) * (z - z0) / (z1 - z0)) - 0.002
    return -SHIRT[-1][2]
part("shirt", loft(SHIRT), M["shirt"], "spine")

JACKET = [(0.84, .212, .134), (0.93, .206, .13), (1.03, .205, .13), (1.12, .22, .14),
          (1.25, .245, .15), (1.36, .268, .152), (1.43, .25, .132), (1.48, .13, .09)]
GAP = [(0.84, 38), (0.93, 22), (1.06, 3), (1.12, 9), (1.25, 19), (1.36, 27), (1.43, 33), (1.48, 44)]


def gap(z):
    for (z0, g0), (z1, g1) in zip(GAP, GAP[1:]):
        if z0 <= z <= z1:
            g = g0 + (g1 - g0) * (z - z0) / (z1 - z0)
            break
    else:
        g = GAP[-1][1]
    return (-90 + g, 270 - g)


part("jacket", loft(JACKET, segs=40, arc=gap), M["jacket"], "spine")
# jacket front button and shirt buttons
part("jacket_button", place(ellipsoid((0.016, 0.008, 0.016), 12, 8), (0.026, -0.14, 1.075)),
     M["jbutton"], "spine", outline=0.003, subdiv=0)
for z in (1.13, 1.23, 1.33):
    part(f"shirt_button_{z}", place(ellipsoid((0.009, 0.004, 0.009), 10, 6), (0, shirt_front(z), z)),
         M["button"], "spine", outline=0.002, subdiv=0)
part("badge", place(ellipsoid((0.022, 0.004, 0.008), 10, 6), (0.15, -0.152, 1.35), (0, 0, 0.35)),
     M["badge"], "spine", outline=False, subdiv=0)
# open shirt collar
for x in (1, -1):
    part(f"collar.{'L' if x > 0 else 'R'}",
         place(ellipsoid((0.05, 0.012, 0.03), 14, 8), (0.056 * x, -0.074, 1.475), (0.45, 0, -0.6 * x)),
         M["shirt"], "spine", outline=0.004)
part("neck", loft([(1.4, .068, .066), (1.58, .062, .06)]), M["skin"], "head")

# Arms
for s, x in (("L", 1), ("R", -1)):
    part(f"shoulder.{s}", place(ellipsoid((0.088, 0.084, 0.08)), ((0.2 + SH) * x, 0, 1.395)),
         M["jacket"], f"upper_arm.{s}")
    part(f"upper_arm.{s}", along(loft([(0, .082, .08), (.42, .08, .078), (.8, .068, .066), (1, .062, .062)]),
                                 ((0.2 + SH) * x, 0, 1.40), ((0.235 + SH) * x, 0, 1.13)), M["jacket"], f"upper_arm.{s}")
    part(f"elbow.{s}", place(ellipsoid((0.063, 0.063, 0.063)), ((0.235 + SH) * x, 0, 1.13)),
         M["jacket"], f"forearm.{s}")
    part(f"forearm.{s}", along(loft([(0, .062, .062), (.3, .066, .064), (.75, .056, .055), (1, .056, .056)]),
                               ((0.235 + SH) * x, 0, 1.13), ((0.252 + SH) * x, 0, 0.9)), M["jacket"], f"forearm.{s}")
    part(f"cuff.{s}", along(loft([(0, .047, .047), (1, .045, .045)]),
                            ((0.252 + SH) * x, 0, 0.915), ((0.254 + SH) * x, 0, 0.875)), M["shirt"], f"forearm.{s}",
         outline=0.004)
    # palm faces the body (-x for the left hand); four fingers and a thumb forward
    hand = []
    hand.append(("palm", place(ellipsoid((0.024, 0.047, 0.05)), ((0.256 + SH) * x, -0.004, 0.835)), 0.006))
    for i, fy in enumerate((-0.032, -0.011, 0.01, 0.03)):
        length = (0.03, 0.036, 0.034, 0.027)[i]
        hand.append((f"finger{i}", place(ellipsoid((0.014, 0.0115, length), 12, 8),
                                         ((0.257 + SH) * x, fy, 0.79 - length * 0.5)), 0.004))
    hand.append(("thumb", place(ellipsoid((0.014, 0.015, 0.032)), ((0.246 + SH) * x, -0.045, 0.832),
                                (0.45, 0, 0)), 0.004))
    wrist = Matrix.Translation(((0.252 + SH) * x, 0, 0.89))
    grow = wrist @ Matrix.Scale(1.2, 4) @ wrist.inverted()
    for pname, bm, ow in hand:
        bmesh.ops.transform(bm, matrix=grow, verts=bm.verts)
        part(f"{pname}.{s}", bm, M["skin"], f"hand.{s}", outline=ow)

# ---------------------------------------------------------------- head
HC = Vector((0, 0, 1.68))
HR = Vector((0.128, 0.132, 0.165))
head = ellipsoid(tuple(HR), 28, 18)
for v in head.verts:
    zn = v.co.z / HR.z
    v.co.x *= 1 - 0.28 * max(0.0, -zn) ** 1.3      # narrower jaw
    v.co.y *= 1 - 0.12 * max(0.0, -zn)
place(head, tuple(HC))
part("head", head, M["skin"], "head")


def face_y(x, z, lift=0.0):
    """Front surface of the head at (x, z)."""
    zn = (z - HC.z) / HR.z
    rx = HR.x * (1 - 0.28 * max(0.0, -zn) ** 1.3)
    ry = HR.y * (1 - 0.12 * max(0.0, -zn))
    k = 1 - (x / rx) ** 2 - zn ** 2
    return -ry * math.sqrt(max(0.0, k)) - lift


def feature(name, radii, x, z, lift, mat, rot=(0, 0, 0), outline=False, segs=16):
    yaw = math.asin(max(-0.9, min(0.9, x / HR.x))) * 0.9   # follow the face curvature
    bm = ellipsoid(radii, segs, 8)
    place(bm, (x, face_y(x, z, lift), z), (rot[0], rot[1], rot[2] + yaw))
    return part(name, bm, mat, "head", outline=outline, subdiv=0)


for s, x in (("L", 1), ("R", -1)):
    ex = 0.05 * x
    feature(f"eye.{s}", (0.030, 0.008, 0.037), ex, 1.688, -0.002, M["eyewhite"])
    feature(f"iris.{s}", (0.021, 0.006, 0.027), ex - 0.002 * x, 1.684, 0.004, M["iris"])
    feature(f"pupil.{s}", (0.011, 0.005, 0.014), ex - 0.002 * x, 1.683, 0.007, M["pupil"])
    feature(f"shine.{s}", (0.0065, 0.004, 0.007), ex - 0.009 * x + 0.004, 1.695, 0.01, M["shine"])
    feature(f"lid.{s}", (0.036, 0.008, 0.0065), ex, 1.724, 0.0, M["line"])
    feature(f"brow.{s}", (0.042, 0.012, 0.0115), 0.056 * x, 1.752, 0.002, M["hair"], rot=(0, 0.1 * x, 0))
    feature(f"moustache.{s}", (0.043, 0.014, 0.0125), 0.033 * x, 1.604, 0.004, M["hair"],
            rot=(0, 0.28 * x, 0))
    part(f"ear.{s}", place(ellipsoid((0.016, 0.03, 0.044)), (0.126 * x, 0.004, 1.672)),
         M["skin"], "head")
feature("nose", (0.018, 0.022, 0.026), 0, 1.642, -0.008, M["skin"], outline=0.004)
feature("mouth", (0.024, 0.004, 0.0032), 0, 1.578, 0.004, M["mouth"])

# Light beard: a thin stubble shell over the jaw, chin and cheeks, up to the sideburns.
bbm = ellipsoid(tuple(HR), 40, 24)
cut = []
for v in bbm.verts:
    n = Vector((v.co.x / HR.x, v.co.y / HR.y, v.co.z / HR.z))
    zn = n.z
    v.co.x *= 1 - 0.28 * max(0.0, -zn) ** 1.3      # same jaw as the head
    v.co.y *= 1 - 0.12 * max(0.0, -zn)
    if HC.z + v.co.z > 1.607 + 0.042 * min(1.0, abs(n.x) / 0.75) or n.y > 0.3:
        cut.append(v)
        continue
    v.co += v.co.normalized() * 0.0035
bmesh.ops.delete(bbm, geom=cut, context="VERTS")
place(bbm, tuple(HC))
part("beard", bbm, M["beard"], "head", outline=False)

# Short side-swept haircut: a shell over the head with a parting on his left; the hair
# is combed across and forward from it, tapered at the sides and back.
PART_X = 0.4          # side parting, on his left
HAIRLINE = [(-1.0, 1.785), (-0.5, 1.772), (-0.22, 1.705), (0.25, 1.685), (0.6, 1.60), (1.0, 1.59)]


def hairline(n):
    """Lowest z the hair reaches at head direction n (n.y: -1 front .. 1 back)."""
    ny, ax = n.y, abs(n.x)
    for (a, za), (b, zb) in zip(HAIRLINE, HAIRLINE[1:]):
        if a <= ny <= b:
            z = za + (zb - za) * (ny - a) / (b - a)
            break
    else:
        z = HAIRLINE[-1][1]
    if ny < -0.25:                                   # recede a little at the temples
        z += 0.03 * min(1.0, max(0.0, (ax - 0.25) / 0.35))
        # the swept fringe drapes lower over the forehead, away from the part
        z -= 0.02 * min(1.0, max(0.0, (0.3 - n.x) / 0.6)) * min(1.0, (-ny - 0.25) / 0.4)
    if ax > 0.8 and -0.55 < ny < -0.05:              # short sideburns in front of the ears
        z = min(z, 1.645)
    return z


hbm = ellipsoid(tuple(HR), 48, 28)
cut = []
for v in hbm.verts:
    n = Vector((v.co.x / HR.x, v.co.y / HR.y, v.co.z / HR.z))
    line = hairline(n)
    if HC.z + v.co.z < line:
        cut.append(v)
        continue
    top = max(0.0, n.z)
    thick = 0.006 + 0.048 * top ** 2 + 0.005 * max(0.0, n.y) + 0.016 * max(0.0, -n.y) * top
    d = n.x - PART_X
    sweep = min(1.0, max(0.0, -d / 0.35))                  # 0 at the part .. 1 across the head
    thick += 0.014 * top * sweep                            # volume pushed over from the part
    if n.z > 0.15 and n.y < 0.55:
        thick *= 1 - 0.7 * math.exp(-(d / 0.07) ** 2)      # the parting line
    thick *= 0.35 + 0.65 * min(1.0, (HC.z + v.co.z - line) / 0.07)   # taper into the scalp
    v.co += v.co.normalized() * thick
    v.co.y -= 0.014 * top ** 3                      # a little forward
    v.co.x -= 0.024 * top ** 1.5 * (0.35 + 0.65 * sweep) * (1 if d < 0 else 0.4)   # swept sideways
bmesh.ops.delete(hbm, geom=cut, context="VERTS")
place(hbm, tuple(HC))
part("hair", hbm, M["hair"], "head")

# ---------------------------------------------------------------- camera and render settings
cam = bpy.data.objects.get("Camera")
if cam is None:
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"
cam.data.ortho_scale = 2.25
cam.location = (0, -8, 1.9)
cam.rotation_euler = (math.radians(84), 0, 0)
cam.data.shift_y = -0.02

r = scene.render
r.engine = "BLENDER_EEVEE"
r.resolution_x, r.resolution_y, r.resolution_percentage = 240, 360, 100
r.film_transparent = True
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGBA"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.eevee.taa_render_samples = 16
if scene.world is None:
    scene.world = bpy.data.worlds.new("World")

result = {"parts": len(coll.objects), "bones": len(arm_data.bones)}

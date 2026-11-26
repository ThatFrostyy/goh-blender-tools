"""Edge cases for the from-scratch tools: unit factor, mirrored meshes, modifiers, volume reduction, validation messages,
track belt deformation, export stop on errors."""
import bpy, sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
from mathutils import Vector

bpy.ops.wm.read_factory_settings(use_empty=True)
fails = []
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

arm = create_rig("edge", "tank")

# ---- centimetre model with a mirror modifier and a negative scale
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0)); o = bpy.context.active_object; o.name = "cm_part"
o.scale = (-300, 100, 50)               # 3 m x 1 m x 0.5 m in centimetres, mirrored on X
o.location = (0, 0, 80)
bpy.context.view_layer.update()
adopt_mesh(o, arm, "body", size_factor=0.01)
xs = [v.co.x for v in o.data.vertices]; ys = [v.co.y for v in o.data.vertices]; zs = [v.co.z for v in o.data.vertices]
check(abs((max(xs) - min(xs)) - 60.0) < 1e-3, "300 cm -> 60 game units (3 m): %.3f" % (max(xs) - min(xs)))
check(abs((max(zs) - min(zs)) - 10.0) < 1e-3, "50 cm -> 10 game units")
bpy.context.view_layer.update()
# faces still point outwards after the mirrored transform
o.data.calc_normals_split() if hasattr(o.data, "calc_normals_split") else None
inside = 0
cen = sum((v.co for v in o.data.vertices), Vector()) / len(o.data.vertices)
for p in o.data.polygons:
    if (p.center - cen).dot(p.normal) < 0: inside += 1
check(inside == 0, "mirrored mesh keeps outward normals (%d inside-out faces)" % inside)

# ---- modifiers are applied
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.4, location=(0, 0, 1.2)); s = bpy.context.active_object; s.name = "dome"
s.modifiers.new("sub", "SUBSURF").levels = 1
adopt_mesh(s, arm, "turret")
check(len(s.modifiers) == 0 and len(s.data.vertices) > 482, "subdivision applied on attach (%d vertices)" % len(s.data.vertices))

# ---- volume from a very dense mesh stays under the limit and is convex-valid
bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, radius=0.5, location=(0, 0, 0.5)); d = bpy.context.active_object; d.name = "dense"
v = generate_volume(arm, [d], "body", "dense1", "HULL", max_faces=500)
v.data.calc_loop_triangles()
check(0 < len(v.data.loop_triangles) <= 500, "hull of a 8000-vertex sphere reduced to %d triangles (limit 500)" % len(v.data.loop_triangles))
vb = generate_volume(arm, [d], "body", "box1", "BOX")
vb.data.calc_loop_triangles()
check(len(vb.data.loop_triangles) == 12 and len(vb.data.vertices) == 8, "box volume has 8 vertices / 12 triangles")
# volume size = mesh size (1 m sphere -> 20 units)
zs = [p.co.z for p in v.data.vertices]
check(abs((max(zs) - min(zs)) - 20.0) < 0.5, "sphere hull height %.2f units (~20)" % (max(zs) - min(zs)))
# regenerating a volume with the same name replaces it
n_before = len([ob for ob in bpy.data.objects if ob.name.startswith("vol_dense1")])
generate_volume(arm, [d], "body", "dense1", "HULL", max_faces=300)
check(len([ob for ob in bpy.data.objects if ob.name.startswith("vol_dense1")]) == n_before == 1, "same volume name replaces the old object")
check(len([x for x in json.loads(arm["goh_volumes_json"]) if x["name"] == "dense1"]) == 1, "volume list has one entry for it")

# ---- validation messages
msgs = lambda: [(l, t) for l, t in validate_model(arm)]
pb = arm.data.bones.get("gun_rot")
check(not any("missing bone" in t for l, t in msgs()), "full template has no missing-bone error")
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT"); arm.data.edit_bones.remove(arm.data.edit_bones["gun"]); bpy.ops.object.mode_set(mode="OBJECT")
check(any(l == "error" and 'missing bone "gun"' in t for l, t in msgs()), "removing the gun bone is reported as an error")
check(bool(check_export(arm)), "check_export returns the error so the export dialog can stop")
try:
    export_mdl(arm, os.path.join(_env.OUT, "edge_blocked"), "edge", confirm=lambda problems: False)
    check(False, "export with errors and 'stop' answer should be cancelled")
except GohExportCancelled:
    check(True, "export with errors is cancelled when the user answers stop")

# ---- texture checks
img = bpy.data.images.new("npot", 100, 60)
m = bpy.data.materials.new("npot_m"); m.use_nodes = True
tn = m.node_tree.nodes.new("ShaderNodeTexImage"); tn.image = img
o.data.materials.append(m)
check(not any("power-of-two" in t for l, t in msgs()), "non power-of-two textures are fine (the game ships 1600x1600 and 3072x3072 ones)")
bpy.ops.mesh.primitive_cube_add(); big = bpy.context.active_object; big.name = "nomat"
adopt_mesh(big, arm, "body")
check(any("mesh nomat has no material" in t for l, t in msgs()), "mesh without a material is warned about")

# ---- unattached and wrongly framed meshes
bpy.ops.mesh.primitive_cube_add(); loose = bpy.context.active_object; loose.name = "loose"
loose.parent = arm
check(any("loose is not attached" in t for l, t in msgs()), "mesh parented to the rig but not a bone is an error")
adopt_mesh(loose, arm, "body")
check(not any("loose" in t for l, t in msgs() if l == "error"), "…and fine once attached")
loose.location.x += 1.0; bpy.context.view_layer.update()
check(any("loose sits in a different frame" in t for l, t in msgs()), "moving an attached mesh object is flagged (exporter ignores transforms)")

# ---- track belt follows the suspension
bpy.context.view_layer.update()
tl = bpy.data.objects["trackl"]
dg = bpy.context.evaluated_depsgraph_get()
def belt_top(deformed):
    me = tl.evaluated_get(dg).to_mesh() if deformed else tl.data
    pts = [(tl.matrix_world @ v.co) for v in me.vertices]
    r = max(p.z for p in pts if abs(p.x - (tl.matrix_world @ Vector((0, 0, 0))).x - 0.8) < 0.3) if False else None
    return pts
base = [p.copy() for p in belt_top(False)]
sus = arm.pose.bones["suspension_l3"]
sus.location = Vector((0, 0, 0)); sus.location.z = 0.0
# the prismatic bones slide along their own axis: move 0.1 m = 2 units along Z of the bone frame
sus.location = Vector((0, 0, 2.0)); bpy.context.view_layer.update(); dg = bpy.context.evaluated_depsgraph_get()
me = tl.evaluated_get(dg).to_mesh(); moved = [tl.matrix_world @ v.co for v in me.vertices]
diff = max((a - b).length for a, b in zip(base, moved))
check(diff > 0.02, "belt vertices follow a moving suspension bone (max move %.3f m)" % diff)
check(sum(1 for a, b in zip(base, moved) if (a - b).length > 1e-4) < len(base) * 0.7, "…but only the vertices near it")

print("FAILED: %s" % fails if fails else "SCRATCH EDGE OK")
sys.exit(1 if fails else 0)

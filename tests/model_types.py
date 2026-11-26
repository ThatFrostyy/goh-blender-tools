"""Model types and the problems found porting outside models (car, human skin, helmet, weapon):
car template + car clips, headwear template, skinned part swap on a game human (frame, check, left-out old part, weights),
renamed item names, mixed-winding meshes, clips played as bone sequences."""
import bpy, bmesh, sys, os, json, re, struct, shutil
import numpy as np
from mathutils import Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME
fails = []
def check(ok, what):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok: fails.append(what)

def ply_winding(path):
    """share of triangles wound counter-clockwise around their stored normals (the game's files: 0)"""
    b = open(path, "rb").read(); j = b.find(b"VERT"); nv, st, _fl = struct.unpack("<IHH", b[j + 4:j + 12]); vs = b[j + 12:j + 12 + nv * st]
    skinned = b.find(b"SKIN", 0, 64) >= 0
    no = 20 if skinned else 12
    p = np.array([struct.unpack_from("<3f", vs, i * st) for i in range(nv)]); n = np.array([struct.unpack_from("<3f", vs, i * st + no) for i in range(nv)])
    i = b.find(b"INDX"); k = struct.unpack("<I", b[i + 4:i + 8])[0]; t = np.array(struct.unpack_from("<%dH" % k, b, i + 8)).reshape(-1, 3)
    a, b_, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    return float(np.mean(np.sum(np.cross(b_ - a, c - a) * (n[t[:, 0]] + n[t[:, 1]] + n[t[:, 2]]), 1) > 0))

def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.active_object; o.name = name; o.scale = size
    img = bpy.data.images.get("t") or bpy.data.images.new("t", 64, 64)
    m = bpy.data.materials.get("m") or bpy.data.materials.new("m"); m.use_nodes = True
    if not any(n.type == "TEX_IMAGE" for n in m.node_tree.nodes):
        tn = m.node_tree.nodes.new("ShaderNodeTexImage"); tn.image = img
        m.node_tree.links.new(tn.outputs["Color"], next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Base Color"])
    o.data.materials.append(m)
    return o

# ------------------------------------------------------------------ 1. car template
print("CAR")
fresh()
arm = create_rig("test_car", "car")
need = ("basis", "body", "engine", "engine_hood", "doorleft1", "doorright1", "steer", "pivot_front", "steerl1", "suspension_l1", "wheell1",
        "wheelr2", "susp_front", "driver", "commander", "emit1", "emit2", "fxstop1", "fxsmoke1", "headlightl", "fuel", "transmission")
check(all(n in arm.data.bones for n in need), "car rig has the game's car bones")
for o, b in ((box("hull", (4.0, 1.7, 0.8), (0, 0, 0.7)), "body"), (box("door", (1.2, 0.08, 0.6), (-0.2, 0.85, 0.7)), "doorleft1"),
             (box("door_r", (1.2, 0.08, 0.6), (-0.2, -0.85, 0.7)), "doorright1"), (box("hood", (1.2, 1.4, 0.05), (1.3, 0, 1.0)), "engine_hood"),
             (box("wheel", (0.64, 0.22, 0.64), (1.2, 0.77, 0.32)), "wheell1")):
    adopt_mesh(o, arm, b, report=lambda s: None)
generate_volume(arm, [bpy.data.objects["hull"]], "body", "body1", report=lambda s: None)
clips = bake_car_clips(arm, report=lambda s: None)
check({"doorleft1", "doorright1", "engine_r", "body_r", "hit", "start", "stop"} <= {a.name for a in clips}, "car clips baked")
issues = validate_model(arm)
check(not [t for l, t in issues if l == "error"], "car check without errors %s" % [t for l, t in issues if l == "error"])
out = os.path.join(_env.OUT, "mt_car"); shutil.rmtree(out, ignore_errors=True)
export_mdl(arm, out, "test_car", report=lambda s: None)
mdl = open(os.path.join(out, "test_car.mdl")).read()
check(re.search(r'\{bone "basis"\s*\{parameters "steermax=32;wheelradius=0.32;"\}\s*\{orientation\s*1 0 0\s*0 -1 0\s*0 0 1', mdl) is not None, "mirrored root with steermax / wheelradius")
check('{sequence "doorleft1" {speed 0.3} {events {1 "open_left"}}}' in mdl, "door sequence with the game's event")
check('{sequence "repair" {file "engine_r.anm"}' in mdl and 'sequence "engine_r"' not in mdl, "engine_r only as the engine's repair sequence")
check('{bone revolute "wheell1"' in mdl and '"id=wheelleft1;radius=0.32;"' in mdl and '{bone prizmatic "suspension_l1"' in mdl, "wheels, suspension")
d = open(os.path.join(out, "test_car.def")).read()
check('(include "/properties/car.ext")' in d and '{base "doorleft1"}' in d and '"mobility_car"' in d, "starter car .def")
check(all(ply_winding(os.path.join(out, f)) == 0.0 for f in os.listdir(out) if f.endswith(".ply")), "car meshes wound like the game's")
fresh()
arm2 = import_mdl(os.path.join(out, "test_car.mdl"), game_dir=GAME, report=lambda s: None)
check("wheelr2" in arm2.data.bones and {"doorleft1", "engine_r"} <= {a.name for a in bpy.data.actions}, "car re-imports with its clips")

# ------------------------------------------------------------------ 2. headwear template
print("HEADWEAR")
fresh()
arm = create_rig("test_helmet", "headwear")
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.13, location=(0, 0, 0.12)); sp = bpy.context.active_object; sp.name = "shell"
sp.data.materials.append(bpy.data.materials.new("m"))
adopt_mesh(sp, arm, "ger_headwear", report=lambda s: None)
out = os.path.join(_env.OUT, "mt_helmet"); shutil.rmtree(out, ignore_errors=True)
export_mdl(arm, out, "test_helmet", report=lambda s: None)
d = open(os.path.join(out, "test_helmet.def")).read()
check('{item "test_helmet"}' in d and "stuff_small.ext" in d and "heavy_headgear" in d, "starter helmet item .def")
mdl = open(os.path.join(out, "test_helmet.mdl")).read()
check(re.search(r'\{bone "basis"\s*\{orientation\s*1 0 0\s*0 -1 0\s*0 0 1\s*\}\s*\{bone "ger_headwear"', mdl) is not None, "helmet root like the game's")

# ------------------------------------------------------------------ 3. skinned part swap on a game human
print("SKIN")
fresh()
d = extract_model_dir("entity/humanskin/[civilians]/civ_01", GAME)
arm = import_mdl(d + "/civ_01_var1.mdl", game_dir=GAME, report=lambda s: None)
heads = [o for o in arm.children_recursive if o.type == "MESH" and o.name.startswith("civ_head_1")]
old = heads[0]
bb_old = [old.matrix_world @ Vector(c) for c in old.bound_box]
# "a head from another program": a world-space copy without the importer's data, every second face reversed
me = bpy.data.meshes.new_from_object(old.evaluated_get(bpy.context.evaluated_depsgraph_get())); me.transform(old.matrix_world)
for k in list(me.keys()): del me[k]
bm = bmesh.new(); bm.from_mesh(me); bm.faces.ensure_lookup_table()
bmesh.ops.reverse_faces(bm, faces=list(bm.faces)[::2]); bm.to_mesh(me); bm.free()
new = bpy.data.objects.new("new_head", me); bpy.context.scene.collection.objects.link(new)
new.vertex_groups.clear()
for m in old.data.materials: new.data.materials.append(m)
transfer_weights(new, old, report=lambda s: None)
check(set(g.name for g in new.vertex_groups) <= set(arm.data.bones.keys()) and len(new.vertex_groups) >= 1, "weights copied onto the rig's bones")
replace_part(arm, old, new, report=lambda s: None)
issues = validate_model(arm)
check(not [t for l, t in issues if "not in the frame" in t], "skinned part in the skin frame (no false frame error)")
out = os.path.join(_env.OUT, "mt_skin"); shutil.rmtree(out, ignore_errors=True)
export_mdl(arm, out, "test_civ", report=lambda s: None)
mdl = open(os.path.join(out, "test_civ.mdl")).read()
check("civ_head_1.ply" not in mdl and "civ_head_1_lod1.ply" not in mdl and "new_head.ply" in mdl, "replaced head left out with its detail levels")
check(not os.path.exists(os.path.join(out, "civ_head_1.ply")), "old head file not copied")
check(ply_winding(os.path.join(out, "new_head.ply")) == 0.0, "mixed-winding mesh written like the game's")
fresh()
arm = import_mdl(os.path.join(out, "test_civ.mdl"), game_dir=GAME, report=lambda s: None)
h = next(o for o in arm.children_recursive if o.type == "MESH" and "new_head" in (o.get("goh_ply") or o.name))
bb_new = [h.matrix_world @ Vector(c) for c in h.bound_box]
z_old = (min(p.z for p in bb_old), max(p.z for p in bb_old)); z_new = (min(p.z for p in bb_new), max(p.z for p in bb_new))
print("   head z before %.3f..%.3f, after %.3f..%.3f" % (z_old + z_new))
check(abs(z_old[0] - z_new[0]) < 0.01 and abs(z_old[1] - z_new[1]) < 0.01, "new head sits where the old one was (not upside down)")

# ------------------------------------------------------------------ 4. renamed item
print("ITEM RENAME")
fresh()
d = extract_model_dir("entity/inventory/-weapon/-small_arms/thompson_m1928a1_30", GAME)
arm = import_mdl(d + "/thompson_m1928a1_30.mdl", game_dir=GAME, report=lambda s: None)
out = os.path.join(_env.OUT, "mt_item"); shutil.rmtree(out, ignore_errors=True)
export_mdl(arm, out, "my_gun", report=lambda s: None)
d = open(os.path.join(out, "my_gun.def")).read()
check('{item "my_gun"}' in d and '{extension "my_gun.mdl"}' in d, "renamed item names itself")

print("MODEL TYPES: %d failure(s)" % len(fails))
if fails: sys.exit(1)

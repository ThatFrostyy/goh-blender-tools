"""Model from scratch: rig template, meshes made outside the game rig, attach to bones, generated collision volumes,
check report, export, import the export again and compare size / bones / volumes."""
import bpy, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME

bpy.ops.wm.read_factory_settings(use_empty=True)
arm = create_rig("test_tank", "tank")
print("bones:", len(arm.data.bones))

def cube(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object; o.name = name; o.scale = size
    return o

# a "downloaded" tank in metres, centred on the world origin, with an unapplied scale and a UV + material with a texture
hull = cube("hull", (4.0, 2.0, 0.9), (0, 0, 0.8))
tur = cube("turret", (1.6, 1.4, 0.6), (-0.2, 0, 1.55))
gun = cube("barrel", (2.4, 0.12, 0.12), (1.4, 0, 1.6))
img = bpy.data.images.new("tank_tex", 128, 128)
mat = bpy.data.materials.new("tank_mat"); mat.use_nodes = True
tn = mat.node_tree.nodes.new("ShaderNodeTexImage"); tn.image = img
b = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"); mat.node_tree.links.new(tn.outputs["Color"], b.inputs["Base Color"])
for o in (hull, tur, gun): o.data.materials.append(mat)

hull.parent = arm; hull.matrix_parent_inverse = arm.matrix_world.inverted()      # plain child of the rig, not on a bone: the exporter would drop it
issues = validate_model(arm)
print("BEFORE attaching:"); [print("  ", l, t) for l, t in issues]
assert any(l == "error" and "not attached" in t for l, t in issues)

# baked clips: gun recoil must move the gun back along -X by 0.22 m (4.4 units) at its peak
clips = bake_clips(arm)
assert sorted(a.name for a in clips) == ["fire", "hit", "spring", "start", "stop"], [a.name for a in clips]
fire = bpy.data.actions["fire"]
fcx = next(fc for fc in _action_fcurves(fire) if fc.data_path == 'pose.bones["gun"].location' and fc.array_index == 0)
vals = [fcx.evaluate(f) for f in range(1, 36)]
print("fire gun x:", [round(v, 2) for v in vals[:8]], "... end", round(vals[-1], 3))
assert abs(min(vals) + 4.4) < 1e-3 and abs(vals[0]) < 1e-6 and abs(vals[-1]) < 0.05

for o, bone in ((hull, "body"), (tur, "turret"), (gun, "gun")):
    adopt_mesh(o, arm, bone)
generate_volume(arm, [hull], "body", "body1", "HULL")
generate_volume(arm, [tur], "turret", "turret1", "BOX")
generate_volume(arm, [gun], "gun", "barrel1", "HULL")
issues = validate_model(arm)
print("AFTER:"); [print("  ", l, t) for l, t in issues]
assert not [1 for l, t in issues if l == "error"], "errors left"

# the tank must keep its size (4 m hull = 80 game units) and sit where it was modelled
import mathutils
def world_bbox(o):
    pts = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]
lo, hi = world_bbox(hull)
print("hull world bbox (m):", [round(x, 3) for x in lo], [round(x, 3) for x in hi])
assert abs((hi[0] - lo[0]) - 4.0) < 1e-3 and abs(lo[2] - 0.35) < 1e-3

out = os.path.join(_env.OUT, "scratch_tank"); import shutil; shutil.rmtree(out, ignore_errors=True)
written = export_mdl(arm, out, "scratch_tank", write_report=True)
print("written:", sorted(os.path.basename(w) for w in written))
assert os.path.isfile(os.path.join(out, "trackl.ply")) and os.path.isfile(os.path.join(out, "trackr.ply")), "track belts"
for a in ("fire", "hit", "start", "stop", "spring"): assert os.path.isfile(os.path.join(out, a + ".anm")), a
for need in ("scratch_tank.mdl", "scratch_tank.def", "hull.ply", "turret.ply", "barrel.ply", "body1.vol", "turret1.vol", "barrel1.vol", "tank_tex.dds", "goh_check.txt"):
    assert os.path.isfile(os.path.join(out, need)), "missing " + need
mdl = open(os.path.join(out, "scratch_tank.mdl")).read()
for s in ('{bone prizmatic "suspension_l3"', '{bone revolute "wheelr4"', '"id=trackleft;radius=0.32;"', '{VolumeView "trackl.ply"}', '{sequence "fire" {speed 0.7}}'):
    assert s in mdl, "mdl lacks " + s
assert '{bone revolute "turret"' in mdl and '{polyhedron "body1.vol"}' in mdl and 'VolumeView "hull.ply"' in mdl, "mdl content"
d = open(os.path.join(out, "scratch_tank.def")).read()
assert '{extension "scratch_tank.mdl"}' in d and '{volume "body1"' in d, "def content"

# import the export again
n_bones = len(arm.data.bones)
for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
arm2 = import_mdl(os.path.join(out, "scratch_tank.mdl"), game_dir=GAME, report=lambda s: None)
meshes = [o for o in arm2.children_recursive if o.type == "MESH" and "goh_volume" not in o.keys()]
vols = [o for o in arm2.children_recursive if "goh_volume" in o.keys()]
print("re-imported:", len(arm2.data.bones), "bones,", len(meshes), "meshes,", len(vols), "volumes")
extra = [b.name for b in arm2.data.bones if "goh_from_def" in arm2.pose.bones[b.name].keys()]; print("bones added by the .def:", extra)
assert len(arm2.data.bones) - len(extra) == n_bones and len(meshes) == 5 and len(vols) == 3
h = next(o for o in meshes if o.name.startswith("hull"))
lo, hi = world_bbox(h)
print("hull after round trip (m):", [round(x, 3) for x in lo], [round(x, 3) for x in hi])
assert abs((hi[0] - lo[0]) - 4.0) < 1e-3 and abs(lo[2] - 0.35) < 1e-3 and abs((hi[2] - lo[2]) - 0.9) < 1e-3
tl = next(o for o in meshes if o.name.startswith("trackl"))
print("trackl re-imported: skinned groups", sorted(g.name for g in tl.vertex_groups), "verts", len(tl.data.vertices))
assert len(tl.vertex_groups) == 6 and any(m.type == "ARMATURE" for m in tl.modifiers)
acts = {a.name for a in bpy.data.actions}
print("re-imported clips:", sorted(acts)); assert {"fire", "hit", "start", "stop", "spring"} <= acts
print(open(os.path.join(out, "goh_check.txt")).read())
print("FROM SCRATCH OK")

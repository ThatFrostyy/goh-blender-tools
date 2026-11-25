"""
Import a random sample of game models from one pack and report failures / warnings / timing.

  blender --background --factory-startup --python tests/import_batch.py -- <pack> <count> <seed>
  pack = vehicle | construction | entity | flora | landscape | inventory | humanskin
"""
import bpy, sys, time, random, os, zipfile, traceback, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME

args = sys.argv[sys.argv.index("--") + 1:]
pakname, n_models, seed = args[0], int(args[1]), int(args[2])
pak = {"vehicle": "entity/-vehicle.pak", "construction": "entity/construction.pak", "entity": "entity/entity.pak", "flora": "entity/flora.pak",
       "landscape": "entity/landscape.pak", "inventory": "entity/inventory.pak", "humanskin": "entity/humanskin.pak"}[pakname]
z = zipfile.ZipFile(GAME + "/resource/" + pak)
dirs = collections.defaultdict(list)
for n in z.namelist():
    if n.endswith(".mdl"): dirs[n.rsplit("/", 1)[0]].append(n.rsplit("/", 1)[1])
keys = sorted(dirs); random.Random(seed).shuffle(keys)


def wipe():
    for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections): bpy.data.collections.remove(c)
    for d in (bpy.data.meshes, bpy.data.actions, bpy.data.materials, bpy.data.armatures, bpy.data.images, bpy.data.texts, bpy.data.cameras, bpy.data.lights, bpy.data.curves):
        for x in list(d): d.remove(x)
    _img_cache.clear(); _mat_cache.clear()


rows = []
for d in keys[:n_models]:
    wipe(); t0 = time.time(); msgs = []
    try:
        folder = extract_model_dir("entity/" + d, GAME)
        arm = import_mdl(os.path.join(folder, sorted(dirs[d])[0]), game_dir=GAME, report=msgs.append)
        warn = [m for m in msgs if "WARNING" in m]
        vis = [o for o in arm.children if o.type == "MESH" and o.display_type != "WIRE" and not o.hide_viewport]
        rows.append(("OK" if not warn else "WARN", d[-50:], len(arm.data.bones), len(vis), round(time.time() - t0, 1), warn[:1]))
    except Exception:
        rows.append(("FAIL", d[-50:], 0, 0, round(time.time() - t0, 1), [traceback.format_exc().strip().split("\n")[-1][:120]]))
print("RESULT", pakname, dict(collections.Counter(r[0] for r in rows)))
for r in rows:
    if r[0] != "OK": print("  ", r)
print("  slowest:", sorted(((r[4], r[1]) for r in rows), reverse=True)[:3])

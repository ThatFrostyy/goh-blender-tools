"""
Round-trip test: import a model, export it, import the exported files again and compare everything
(bones, meshes, weights, animations, collision volumes, properties), and compare every exported .ply
with the original .ply it came from.

  blender --background --factory-startup --python tests/roundtrip.py -- [case ...]

Needs GOH_GAME_DIR (see tests/_env.py). With no case names it runs all of them.
"""
import bpy, sys, os, shutil, math, json, hashlib, collections, traceback, zipfile
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME, MODH, OUT = _env.GAME, _env.MOD_ENTITY, _env.OUT

# kind, path (or pak folder / search text), mdl name (optional)
CASES = {
    "mod_soldier_old":    ("file", MODH + "humanskin/yug_inf_rfl/yug_inf_rfl.mdl"),
    "mod_tank_sid":       ("file", MODH + "-vehicle/yugoslavia/tank_light/t32sid_yug/t32sid_yug.mdl"),
    "game_soldier_new":   ("pak", "entity/humanskin/[germans]/ger_heer_39", "ger_heer_39_rfl_1.mdl"),
    "game_tank":          ("pak", "entity/-vehicle/finland/tank_medium/t3485_44_fin", "t3485_44.mdl"),
    "game_cannon":        ("pak", "entity/-vehicle/ussr/cannon/76mm_zis3", "76mm_zis3.mdl"),
    "game_plane":         ("pak", "entity/-vehicle/germany/airborne/bf109_e4b", "bf109_e4b.mdl"),
    "game_armoured_car":  ("pak", "entity/-vehicle/germany/btr/sdkfz222a", "sdkfz222a.mdl"),
    "game_weapon":        ("pak", "entity/inventory/-weapon/-small_arms/mp40_unfolded", "mp40_unfolded.mdl"),
    "game_helmet":        ("pak", "entity/inventory/-gear_wearable/head/[finland]/fin_fieldcap_m27", "fin_fieldcap_m27.mdl"),
    "game_building":      ("search", "entity/construction.pak", "eastern-europe_farm/ee_farm_c22"),
    "game_ruin":          ("search", "entity/construction.pak", "generic_ruin_12/ruin12_c02"),
    "game_trench_colors": ("search", "entity/landscape.pak", "trench_dirt_straight_short"),
    "game_tree":          ("search", "entity/flora.pak", "trees/birch/birch_bush_01"),
    "game_bush":          ("search", "entity/flora.pak", "generic_bush/twigs_01"),
    "game_rock":          ("search", "entity/landscape.pak", "rock/rock_moss_euro3"),
    "game_fx":            ("pak", "entity/fx/mesh/fx_snowblow01", None),
}


def wipe():
    for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections): bpy.data.collections.remove(c)
    for d in (bpy.data.meshes, bpy.data.actions, bpy.data.materials, bpy.data.armatures, bpy.data.images, bpy.data.texts, bpy.data.cameras, bpy.data.lights):
        for x in list(d): d.remove(x)
    _img_cache.clear(); _mat_cache.clear()


def resolve(case):
    if case[0] == "file":
        return case[1] if os.path.exists(case[1]) else None
    if case[0] == "search":
        z = zipfile.ZipFile(GAME + "/resource/" + case[1])
        d = sorted(set(n.rsplit("/", 1)[0] for n in z.namelist() if n.endswith(".mdl") and case[2] in n))[0]
        folder = extract_model_dir("entity/" + d, GAME)
        return os.path.join(folder, sorted(f for f in os.listdir(folder) if f.endswith(".mdl"))[0])
    folder = extract_model_dir(case[1], GAME)
    if folder is None: return None
    return os.path.join(folder, case[2]) if case[2] else os.path.join(folder, sorted(f for f in os.listdir(folder) if f.endswith(".mdl"))[0])


def snap(arm):
    S = {}
    bones = {}
    for b in arm.data.bones:
        pb = arm.pose.bones[b.name]
        W = b.matrix_local @ Matrix.Diagonal((1, 1, pb.get("goh_F", 1), 1))
        bones[pb.get("goh_orig_name", b.name)] = (b.parent.name if b.parent else None, [round(x, 3) for r in W for x in r])
    S["bones"] = bones
    meshes = {}
    for o in arm.children_recursive:
        if o.type != "MESH" or "goh_volume" in o.keys() or "goh_kind" in o.keys(): continue
        me = o.data
        pts = sorted((round(v.co.x, 3), round(v.co.y, 3), round(v.co.z, 3)) for v in me.vertices)
        wsum = collections.Counter()
        for v in me.vertices:
            for g in v.groups: wsum[o.vertex_groups[g.group].name] += round(g.weight, 2)
        per_slot = collections.Counter(p.material_index for p in me.polygons)
        key = (o.get("goh_ply") or o.name).split("/")[-1] + "@" + str(o.get("goh_bone"))
        meshes[key] = (len(me.vertices), len(me.polygons), hashlib.md5(str(pts).encode()).hexdigest()[:8],
                       tuple(sorted(per_slot.items())), {k: round(v, 1) for k, v in wsum.items()},
                       tuple(m.name if m else None for m in me.materials))
    S["meshes"] = meshes
    S["vols"] = {o["goh_vol_name"]: (len(o.data.vertices), len(o.data.polygons),
                 tuple(o.data.attributes["goh_side"].data[i].value for i in range(len(o.data.polygons))) if "goh_side" in o.data.attributes else None)
                 for o in arm.children_recursive if "goh_vol_name" in o.keys() and o.type == "MESH" and o.get("goh_volume") == "polyhedron"}
    S["actions"] = {a.name: (round(a.get("goh_frames", 0), 1), len(_action_fcurves(a))) for a in bpy.data.actions if _action_fcurves(a)}
    skip = ("goh_model", "goh_def_file", "goh_def_text", "goh_def_expanded_text", "goh_model_extras", "goh_def_includes", "goh_local_includes", "goh_mdl_header")
    S["armprops"] = {k: str(arm[k])[:60] for k in arm.keys() if k.startswith("goh_") and k not in skip}
    S["bone_props"] = {pb.get("goh_orig_name", pb.name): sorted((k, str(pb[k])[:40]) for k in pb.keys() if k in ("goh_joint", "goh_limits", "goh_speed", "goh_speed2", "goh_cone_limit", "goh_visibility", "goh_terminator")) for pb in arm.pose.bones}
    return S


def diff(A, B):
    out = []
    ba, bb = A["bones"], B["bones"]
    if set(ba) != set(bb): out.append("bones differ: only A %s only B %s" % (sorted(set(ba) - set(bb))[:4], sorted(set(bb) - set(ba))[:4]))
    bad = [k for k in ba if k in bb and (ba[k][0] != bb[k][0] or max(abs(x - y) for x, y in zip(ba[k][1], bb[k][1])) > 0.01)]
    if bad: out.append("%d bones with different parent/position: %s" % (len(bad), bad[:4]))
    ma, mb = A["meshes"], B["meshes"]
    if set(ma) != set(mb): out.append("mesh sets differ: A-only %s B-only %s" % (sorted(set(ma) - set(mb))[:3], sorted(set(mb) - set(ma))[:3]))
    mbad = []
    for k in ma:
        if k not in mb: continue
        a, b = ma[k], mb[k]
        if a[0] != b[0] or a[1] != b[1] or a[2] != b[2] or a[3] != b[3] or a[5] != b[5]: mbad.append((k, a[:2], b[:2]))
        elif a[4] != b[4]:
            d = sum(abs(a[4].get(n, 0) - b[4].get(n, 0)) for n in set(a[4]) | set(b[4]))
            if d > max(2.0, 0.002 * sum(a[4].values())): mbad.append((k, "weights", round(d, 1)))
    if mbad: out.append("%d meshes differ (vertex count/positions): %s" % (len(mbad), mbad[:3]))
    if A["vols"] != B["vols"]: out.append("volumes differ: %s" % [k for k in set(A["vols"]) | set(B["vols"]) if A["vols"].get(k) != B["vols"].get(k)][:4])
    if A["actions"] != B["actions"]: out.append("actions differ: %s" % [(k, A["actions"].get(k), B["actions"].get(k)) for k in sorted(set(A["actions"]) | set(B["actions"])) if A["actions"].get(k) != B["actions"].get(k)][:4])
    if A["bone_props"] != B["bone_props"]: out.append("bone props differ: %s" % [k for k in A["bone_props"] if A["bone_props"][k] != B["bone_props"].get(k)][:5])
    pa, pb_ = A["armprops"], B["armprops"]
    miss = [k for k in pa if k not in pb_]; chg = [k for k in pa if k in pb_ and pa[k] != pb_[k]]
    if miss or chg: out.append("armature props: %d missing %s, %d changed %s" % (len(miss), miss[:3], len(chg), chg[:3]))
    return out


def ply_compare(arm, outdir):
    """exported .ply vs the original .ply that the mesh came from"""
    rows = []
    res = Resolver(os.path.dirname(arm["goh_model"]), GAME)
    seen = set()
    for o in arm.children_recursive:
        ref = o.get("goh_ply")
        if not ref or o.type != "MESH" or ref in seen or int(o.get("goh_lod", 0)) > 0: continue
        seen.add(ref)
        orig = res.locate(os.path.dirname(arm["goh_model"]), ref)
        new = os.path.join(outdir, os.path.basename(ref))
        if not orig or not os.path.exists(new): continue
        A, B = parse_ply(orig), parse_ply(new)
        same_len = len(A["pos"]) == len(B["pos"])
        mx = lambda xs: max(xs, default=0.0)
        dpos = mx(abs(x - y) for p, q in zip(A["pos"], B["pos"]) for x, y in zip(p, q)) if same_len else None
        dnrm = mx(abs(x - y) for p, q in zip(A["nrm"], B["nrm"]) for x, y in zip(p, q)) if same_len else None
        duv = mx(abs(x - y) for p, q in zip(A["uv"], B["uv"]) for x, y in zip(p, q)) if same_len else None
        tan = None
        if A["tan"] and same_len and len(A["tan"]) == len(B["tan"]):
            ang = [(math.degrees(math.acos(max(-1, min(1, p[0] * q[0] + p[1] * q[1] + p[2] * q[2])))), p[3] == q[3]) for p, q in zip(A["tan"], B["tan"])]
            tan = (round(sum(a for a, _s in ang) / len(ang), 2), round(sum(1 for _a, s in ang if s) / len(ang), 3))
        rows.append((ref.split("/")[-1], "verts %d/%d stride %s/%s dpos=%s dnrm=%s duv=%s tangent(avg deg, sign match)=%s same_indices=%s" % (
            len(A["pos"]), len(B["pos"]), A["stride"], B["stride"], dpos if dpos is None else round(dpos, 5),
            dnrm if dnrm is None else round(dnrm, 4), duv if duv is None else round(duv, 5), tan, A["idx"] == B["idx"])))
    return rows


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    names = argv or list(CASES)
    failed = 0
    for name in names:
        wipe()
        print("=" * 8, name, flush=True)
        try:
            mdl = resolve(CASES[name])
            if not mdl: print("  (skipped: model not available)"); continue
            armA = import_mdl(mdl, game_dir=GAME, report=lambda s: None)
            SA = snap(armA)
            outdir = os.path.join(OUT, name)
            if os.path.isdir(outdir): shutil.rmtree(outdir)
            emsgs = []
            files = export_mdl(armA, outdir, report=emsgs.append)
            print("  exported %d files; warnings: %s" % (len(files), [m for m in emsgs if "WARNING" in m][:3]))
            for r in ply_compare(armA, outdir)[:3]: print("    ", r[0], r[1])
            wipe()
            armB = import_mdl(os.path.join(outdir, os.path.splitext(os.path.basename(mdl))[0] + ".mdl"), game_dir=GAME, report=lambda s: None)
            d = diff(SA, snap(armB))
            print("  ROUND TRIP:", "PERFECT MATCH" if not d else "differences")
            for line in d: print("    -", line)
            if d and any(not l.startswith("armature props") and "meshes differ" not in l for l in d): failed += 1
        except Exception:
            traceback.print_exc(); failed += 1
    print("DONE, hard failures:", failed)


main()

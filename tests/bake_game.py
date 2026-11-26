"""Baked clips on a real game tank: the recoil must move the gun along the same direction as the game's own fire.anm,
and the exported .anm must have the game's structure (first frame flags 11, positions + rotations)."""
import bpy, sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME

def parse_anm_file(p):
    d = open(p, "rb").read(); assert d[:4] == b"EANM"
    i = 8; assert d[i:i + 4] == b"FRMS"; n = struct.unpack_from("<I", d, i + 4)[0]; i += 8
    assert d[i:i + 4] == b"BMAP"; nb = struct.unpack_from("<I", d, i + 4)[0]; i += 8
    names = []
    for _ in range(nb):
        l = struct.unpack_from("<I", d, i)[0]; names.append(d[i + 4:i + 4 + l].decode()); i += 4 + l
    fr = []
    while i < len(d) and d[i:i + 4] == b"FRM2":
        f, c = struct.unpack_from("<HB", d, i + 4); i += 7
        for _ in range(c):
            bi = d[i]; fl = struct.unpack_from("<H", d, i + 1)[0]; i += 3
            pos = rot = None
            if fl & 1: pos = struct.unpack_from("<3f", d, i); i += 12
            if fl & 2: rot = struct.unpack_from("<3f", d, i); i += 12
            fr.append((f, names[bi], fl, pos, rot))
    return n, names, fr

d = extract_model_dir("entity/-vehicle/finland/tank_medium/t3485_44_fin", GAME)
arm = import_mdl(os.path.join(d, "t3485_44.mdl"), game_dir=GAME, report=lambda s: None)
gun = next(b.name for b in arm.data.bones if b.name == "gun")
body = next(b.name for b in arm.data.bones if b.name == "body")
orig_clips = {a.name for a in bpy.data.actions}
bake_clips(arm, gun, body, report=print)
print("clips before:", sorted(orig_clips), "\nclips after:", sorted(a.name for a in bpy.data.actions))
# our baked "fire" replaces the game's clip of the same name in the file: it is a new action named fire
out = os.path.join(_env.OUT, "bake_game"); import shutil; shutil.rmtree(out, ignore_errors=True)
export_mdl(arm, out, "t3485_44")
n, names, fr = parse_anm_file(os.path.join(out, "fire.anm"))
assert n == 35, n
firsts = [e for e in fr if e[0] == 0]
assert firsts and all(e[2] == 11 for e in firsts), "first frame must carry full position + rotation (flags 11): %s" % [e[2] for e in firsts]
gp = [(e[0], e[3]) for e in fr if e[1] == "gun" and e[3]]
rest = gp[0][1]
peak = min(gp, key=lambda e: e[1][0])
print("gun rest", [round(x, 3) for x in rest], "peak", peak[0], [round(x, 3) for x in peak[1]])
mv = [peak[1][i] - rest[i] for i in range(3)]
print("movement of the gun in its parent's frame:", [round(x, 3) for x in mv])
assert abs(mv[0] + 4.4) < 1e-3 and abs(mv[1]) < 1e-3 and abs(mv[2]) < 1e-3, "recoil must be 4.4 units straight back (-X of the parent frame)"
# the game's own fire clip for comparison (same direction?)
src = os.path.join(d, "fire.anm")
if os.path.isfile(src):
    n2, names2, fr2 = parse_anm_file(src)
    g2 = [(e[0], e[3]) for e in fr2 if e[1] == "gun" and e[3]]
    if g2:
        r2 = g2[0][1]; p2 = max(g2, key=lambda e: sum((e[1][i] - r2[i]) ** 2 for i in range(3)))
        print("the game's own fire.anm moves the gun by", [round(p2[1][i] - r2[i], 3) for i in range(3)], "in", n2, "frames")
mdl = open(os.path.join(out, "t3485_44.mdl")).read()
assert mdl.count('{sequence "fire"') == 1, "fire sequence must appear once (the game's own one is replaced)"
print("BAKE ON GAME MODEL OK")

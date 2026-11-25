"""
DDS writer test: encode real game textures with our DXT writer, load the original and the re-encoded file in Blender and compare.

  blender --background --factory-startup --python tests/dds.py
"""
import bpy, sys, os, struct, zipfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
import numpy as np

GAME = _env.GAME
z = zipfile.ZipFile(GAME + "/resource/texture/common/_vehicle.pak")
names = z.namelist()
want = ["_vehicle/tracks/pz35t_track.dds",          # DXT5 power of two
        "_vehicle/pz35t_details.dds",               # DXT1
        "_vehicle/opel_blitz_shield_bp.dds"]        # DXT5, 768x768 (not a power of two)
tmp = os.path.join(_env.OUT, "dds"); os.makedirs(tmp, exist_ok=True)
worst = 0
for n in want:
    if n not in names:
        print("skip", n); continue
    src = os.path.join(tmp, "orig_" + os.path.basename(n)); open(src, "wb").write(z.read(n))
    head = z.read(n)[:128]
    h, w, _p, _d, mips = struct.unpack_from("<5I", head, 12); fourcc = head[84:88]
    a = bpy.data.images.load(src); a.colorspace_settings.name = "Non-Color"
    t0 = time.time()
    dst = os.path.join(tmp, "ours_" + os.path.basename(n))
    image_to_dds(a, dst)
    dt = time.time() - t0
    b = bpy.data.images.load(dst); b.colorspace_settings.name = "Non-Color"
    pa = np.empty(a.size[0] * a.size[1] * 4, np.float32); a.pixels.foreach_get(pa)
    pb = np.empty(b.size[0] * b.size[1] * 4, np.float32); b.pixels.foreach_get(pb)
    mean = float(np.abs(pa - pb).mean()); mx = float(np.abs(pa - pb).max())
    oh = open(dst, "rb").read(128)
    h2, w2, _p2, _d2, mips2 = struct.unpack_from("<5I", oh, 12)
    print("%-40s %4dx%-4d orig %s mips=%d | ours %s mips=%d | size %d (orig %d) | mean err %.4f max %.3f | %.1fs" % (
        os.path.basename(n), w, h, fourcc.decode(), mips, oh[84:88].decode(), mips2, os.path.getsize(dst), len(z.read(n)), mean, mx, dt))
    assert (w, h) == (w2, h2) and a.size[:] == b.size[:], "size changed"
    assert mean < 0.03, "texture colours differ too much (colour space or orientation problem?)"
    worst = max(worst, mean)
print("OK, worst mean error %.4f" % worst)

"""
Gates of Hell (Call to Arms) model tools for Blender - import AND export, one file.

IMPORT  (File > Import > Gates of Hell model (.mdl))
  * .mdl skeleton -> real Armature (every bone, joint types + limits as constraints, properties as custom props)
  * .ply meshes   -> multi-material meshes, skinned meshes (vertex groups), LODs, vertex colours, second UV set
  * .anm          -> Actions (+ the ~1600 shared soldier animations from properties.pak)
  * .mtl          -> materials,  .vol -> collision volumes,  .def/.ext -> armour, mobility, seats, weapons ... (macros resolved)
EXPORT  (File > Export > Gates of Hell model (.mdl))
  * writes .mdl, .ply, .anm, .mtl, .vol, .def (+ local .ext) back in the game's formats; an unchanged import
    round-trips (positions/UVs exact, normals within 0.001, tangents ~1 degree, bones/animations/properties identical)

Game files are found next to the model, in the mod / game resource folders, or inside the game's .pak archives
(extracted on demand into a cache folder, see CACHE_DIR / env var GOH_CACHE_DIR).

From Blender's Python console / headless:
  exec(open(r".../goh_tools.py").read())
  arm = import_mdl(r".../model.mdl", anim_library="basic")
  export_mdl(arm, r".../out_folder")

Units: game units are scaled by 0.05 on the armature object (1 game unit = 5 cm). The game is Z-up, +X forward like Blender.
"""
bl_info = {
    "name": "Gates of Hell model tools (.mdl import / export)",
    "author": "Claude / Frostyy",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import / Export > Gates of Hell model (.mdl)",
    "description": "Import and export Call to Arms: Gates of Hell models (.mdl .ply .anm .mtl .vol .def) with rig, animation, materials",
    "category": "Import-Export",
}

import bpy, os, re, math, struct, zipfile, glob, tempfile, json, collections
from mathutils import Matrix, Vector, Quaternion

SCALE = 0.05
def _default_cache_dir():
    env = os.environ.get("GOH_CACHE_DIR")
    if env: return env
    try: return os.path.join(bpy.utils.user_resource("CONFIG"), "goh_tools_cache")
    except Exception: return os.path.join(tempfile.gettempdir(), "goh_tools_cache")


CACHE_DIR = _default_cache_dir()          # files extracted from the game's .pak archives are cached here


# =====================================================================================================
#  File resolution (loose files, mod/game resource folders, .pak archives)
# =====================================================================================================
_GLOBAL_INDEX = {}      # game_dir -> (index, by_basename)
_ZIPS = {}              # pak path -> open ZipFile (opening a big pak's directory is slow, do it once)


def _zip(pak):
    z = _ZIPS.get(pak)
    if z is None:
        z = _ZIPS[pak] = zipfile.ZipFile(pak)
    return z


class Resolver:
    """Finds files: loose (next to the model, mod/game resource folders) and inside the game's .pak archives.
    Extracted files are cached under  cache/files/<virtual path>  so relative references (../x_source/a.ply) keep working."""

    def __init__(self, model_dir, game_dir=None):
        self.model_dir = os.path.abspath(model_dir)
        p = self.model_dir
        self.resource_dirs = []
        while True:
            parent = os.path.dirname(p)
            if os.path.basename(p).lower() == "resource":
                self.resource_dirs.append(p)
            if parent == p:
                break
            p = parent
        # game folder = ancestor that has resource/gamelogic.pak
        self.game_dir = game_dir
        if not self.game_dir:
            p = self.model_dir
            while True:
                if os.path.exists(os.path.join(p, "resource", "gamelogic.pak")):
                    self.game_dir = p; break
                parent = os.path.dirname(p)
                if parent == p: break
                p = parent
        if self.game_dir:
            self.resource_dirs.append(os.path.join(self.game_dir, "resource"))
        # extra resource folders (e.g. your mod's, when the model lives somewhere else): env var GOH_EXTRA_RESOURCE, ';' separated
        for extra in os.environ.get("GOH_EXTRA_RESOURCE", "").split(";"):
            if extra.strip() and os.path.isdir(extra.strip()): self.resource_dirs.insert(0, extra.strip())
        self._index = None

    # ---- pak index: lower-case virtual path ("entity/humanskin/x/y.ply") -> (pak, name, virtual)
    def _pak_index(self):
        if self._index is not None:
            return self._index
        key0 = os.path.abspath(self.game_dir).lower() if self.game_dir else ""
        if key0 in _GLOBAL_INDEX:
            self._index, self._by_base = _GLOBAL_INDEX[key0]
            return self._index
        self._index = {}
        self._by_base = {}
        if self.game_dir:
            root = os.path.join(self.game_dir, "resource")
            for pak in glob.glob(os.path.join(glob.escape(self.game_dir), "resource", "**", "*.pak"), recursive=True):
                try:
                    z = zipfile.ZipFile(pak)
                except Exception:
                    continue
                base = os.path.relpath(os.path.dirname(pak), root).replace("\\", "/")
                base = "" if base == "." else base + "/"
                for n in z.namelist():
                    if n.endswith("/"): continue
                    self._index[(base + n).lower()] = (pak, n, base + n)
                    self._index.setdefault(("*/" + n).lower(), (pak, n, base + n))
                    self._by_base.setdefault(n.rsplit("/", 1)[-1].lower(), []).append(((base + n).lower(), (pak, n, base + n)))
        _GLOBAL_INDEX[key0] = (self._index, self._by_base)
        return self._index

    def _extract(self, pak, name, virtual):
        out = os.path.join(CACHE_DIR, "files", virtual.replace("/", os.sep))
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(_zip(pak).read(name))
        return out

    def vdir(self, path):
        """virtual (game) directory of a disk directory: below cache/files/ or below a 'resource' folder."""
        p = os.path.abspath(path).replace("\\", "/")
        c = os.path.join(CACHE_DIR, "files").replace("\\", "/")
        if p.lower().startswith(c.lower() + "/"):
            return p[len(c) + 1:]
        m = re.search(r"/resource/(.*)$", p, re.I)
        return m.group(1) if m else None

    def locate(self, base_dir, rel):
        """file relative to base_dir: on disk, else (same virtual path) inside the game's paks."""
        rel = rel.replace("\\", "/")
        p = os.path.normpath(os.path.join(base_dir, rel))
        if os.path.isfile(p): return p
        v = self.vdir(base_dir)
        if v is not None:
            import posixpath
            key = posixpath.normpath(v + "/" + rel).lower()
            hit = self._pak_index().get(key)
            if hit: return self._extract(*hit)
        return None

    def find(self, rel, exts=("",), extra_dirs=()):
        """rel like 'pz3j' or '_vehicle/tracks/x' (already stripped of '$/'). returns disk path or None."""
        rel = rel.replace("\\", "/").lstrip("/")
        cands = [rel + e for e in exts]
        # 1. next to the referencing file(s) / model (and a couple of parents)
        dirs = list(extra_dirs) + [self.model_dir]
        for d0 in dirs:
            d = d0
            for _ in range(3):
                for c in cands:
                    p = self.locate(d, c)
                    if p: return p
                d = os.path.dirname(d)
        # 2. resource dirs (loose): resource/texture/**/<rel>
        for rd in self.resource_dirs:
            for c in cands:
                for p in (os.path.join(rd, "texture", c), os.path.join(rd, c)):
                    if os.path.isfile(p): return p
                for p in glob.glob(os.path.join(glob.escape(rd), "texture", "**", glob.escape(c)), recursive=True):
                    if os.path.isfile(p): return p
        # 3. paks (texture folders etc.): exact tail match, then any suffix match
        idx = self._pak_index()
        for c in cands:
            for key in ("texture/" + c, c):
                hit = idx.get(("*/" + key).lower())
                if hit: return self._extract(*hit)
        for c in cands:
            low = "/" + c.lower()
            for k, v in self._by_base.get(c.rsplit("/", 1)[-1].lower(), []):
                if k.endswith(low): return self._extract(*v)
        return None


# =====================================================================================================
#  .mtl
# =====================================================================================================
def strip_comments(s):
    out = []
    for line in s.split("\n"):
        q = False
        for k, ch in enumerate(line):
            if ch == '"': q = not q
            elif ch == ";" and not q:
                line = line[:k]; break
        out.append(line)
    return "\n".join(out)


def tokenize(s):
    return re.findall(r'\{|\}|"[^"]*"|[^\s{}"]+', strip_comments(s))


def parse_nodes(toks, i=0):
    nodes = []
    while i < len(toks):
        t = toks[i]
        if t == "}":
            return nodes, i + 1
        if t == "{":
            i += 1; head = []
            while i < len(toks) and toks[i] not in ("{", "}"):
                head.append(toks[i]); i += 1
            kids, i = parse_nodes(toks, i)
            nodes.append((head, kids))
        else:
            i += 1
    return nodes, i


def read_text(p):
    return open(p, encoding="utf-8", errors="replace").read()


def parse_mtl(path):
    """returns dict(kind, diffuse, bump, specular, color, blend)"""
    nodes, _ = parse_nodes(tokenize(read_text(path)))
    res = {"kind": "simple", "diffuse": None, "bump": None, "specular": None, "color": None, "blend": None, "extra": []}
    for head, kids in nodes:
        if head and head[0] == "material":
            if len(head) > 1: res["kind"] = head[1]
            for h, _k in kids:
                if not h: continue
                k = h[0].lower(); v = " ".join(x.strip('"') for x in h[1:])
                if k in ("diffuse", "bump", "specular", "blend"): res[k] = v
                elif k == "color":
                    try: res["color"] = [float(x) for x in v.split()[:4]] if v else None
                    except ValueError: res["color_raw"] = v; res["color"] = None
                else: res["extra"].append([h[0], v])        # diffuse1 / bump1 / lightmaps ... kept as they are
    return res


# =====================================================================================================
#  .ply / .vol
# =====================================================================================================
def _pstring(d, p):
    n = d[p]
    return d[p + 1:p + 1 + n].decode("latin-1"), p + 1 + n


def ply_layout(stride, a, d, skinned):
    """vertex layout from the MESH header flags:  a&0x10 normal, a&0x40 packed colour, a&0x200 second uv set, d&0x100 tangent.
       Order: position, [normal], [colour], uv, [uv2], [tangent]; skinned vertices: position, weight, 4 bone idx, normal, uv, [tangent]."""
    if skinned:
        return {"w": 12, "idx": 16, "n": 20, "uv": 32, "tan": 40 if stride >= 56 else None, "col": None, "uv2": None, "end": stride}
    L = {"n": None, "col": None, "uv": None, "uv2": None, "tan": None}
    o = 12
    if a & 0x10: L["n"] = o; o += 12
    if a & 0x40: L["col"] = o; o += 4
    L["uv"] = o; o += 8
    if a & 0x200: L["uv2"] = o; o += 8
    if d & 0x100: L["tan"] = o; o += 16
    L["end"] = o
    if o > stride:      # unknown combination: fall back to the common 32/48 byte layouts
        L = {"n": 12, "col": None, "uv": 24, "uv2": None, "tan": 32 if stride >= 48 else None, "end": min(stride, 48)}
    return L


def parse_ply(path):
    d = open(path, "rb").read()
    if d[:4] != b"EPLY":
        raise ValueError("not an EPLY file: " + path)
    p = 8 + 24 if d[4:8] == b"BNDS" else 4
    skin = []; meshes = []; verts = None; indices = None
    have_skin = False
    while p + 4 <= len(d):
        tag = d[p:p + 4]; p += 4
        if tag == b"SKIN":
            n = struct.unpack_from("<I", d, p)[0]; p += 4
            for _ in range(n):
                s, p = _pstring(d, p); skin.append(s)
            have_skin = True
        elif tag == b"MESH":
            a, b, c, dd = struct.unpack_from("<4I", d, p); p += 16
            # optional 4-byte colour before the material name
            def ok(q):
                n = d[q]
                return 0 < n < 120 and d[q + 1:q + 1 + n].lower().endswith(b".mtl")
            col = None
            if not ok(p) and ok(p + 4):
                col = d[p:p + 4].hex(); p += 4
            name, p = _pstring(d, p)
            palette = []
            if have_skin:
                cnt = d[p]; palette = list(d[p + 1:p + 1 + cnt]); p += 1 + cnt
            meshes.append({"first": b, "count": c, "mtl": name, "palette": palette, "a": a, "d": dd, "color": col})
        elif tag == b"VERT":
            n, stride, flags = struct.unpack_from("<IHH", d, p); p += 8
            verts = (n, stride, flags, p); p += n * stride
        elif tag == b"INDX":
            cnt = struct.unpack_from("<I", d, p)[0]; p += 4
            indices = struct.unpack_from("<%dH" % cnt, d, p); p += cnt * 2
        else:
            break
    if verts is None or indices is None:
        raise ValueError("ply without VERT/INDX: " + path)
    n, stride, flags, base = verts
    pos = []; nrm = []; uv = []; skinw = []; tan = []; col = []; uv2 = []
    _a0 = meshes[0].get("a", 274) if meshes else 274
    _d0 = meshes[0].get("d", 0x704) if meshes else 0x704
    lay = ply_layout(stride, _a0, _d0, have_skin and stride >= 40)
    for k in range(n):
        o = base + k * stride
        pos.append(struct.unpack_from("<3f", d, o))
        if have_skin and stride >= 40:   # skinned: pos, weight, bone idx x4, normal, uv [, tangent]
            w0 = struct.unpack_from("<f", d, o + 12)[0]
            idx = d[o + 16:o + 20]
            skinw.append((w0, idx))
            nrm.append(struct.unpack_from("<3f", d, o + 20))
            uv.append(struct.unpack_from("<2f", d, o + 32))
            if stride >= 56: tan.append(struct.unpack_from("<4f", d, o + 40))
        else:
            nrm.append(struct.unpack_from("<3f", d, o + lay["n"]) if lay["n"] is not None else (0.0, 0.0, 1.0))
            uv.append(struct.unpack_from("<2f", d, o + lay["uv"]))
            if lay["uv2"] is not None: uv2.append(struct.unpack_from("<2f", d, o + lay["uv2"]))
            if lay["col"] is not None: col.append(struct.unpack_from("<i", d, o + lay["col"])[0])
            if lay["tan"] is not None: tan.append(struct.unpack_from("<4f", d, o + lay["tan"]))
    if not meshes:
        meshes = [{"first": 0, "count": len(indices) // 3, "mtl": None, "palette": []}]
    return {"pos": pos, "nrm": nrm, "uv": uv, "skinw": skinw, "skin": skin, "meshes": meshes, "idx": indices,
            "stride": stride, "tan": tan, "have_skin": have_skin, "col": col, "uv2": uv2, "layout": lay}


def parse_vol(path):
    d = open(path, "rb").read()
    n = struct.unpack_from("<I", d, 8)[0]
    pos = [struct.unpack_from("<3f", d, 12 + i * 12) for i in range(n)]
    p = 12 + n * 12
    tris = []; sides = []
    if d[p:p + 4] == b"INDX":
        cnt = struct.unpack_from("<I", d, p + 4)[0]
        idx = struct.unpack_from("<%dH" % cnt, d, p + 8)
        tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, cnt - 2, 3)]
        p += 8 + cnt * 2
        if d[p:p + 4] == b"SIDE":
            sc = struct.unpack_from("<I", d, p + 4)[0]
            sides = list(d[p + 8:p + 8 + sc])
    return pos, tris, sides


# =====================================================================================================
#  .anm
# =====================================================================================================
def parse_anm(path):
    """returns (bone_names, frames) ; frames = [(frame, [(bone_index, pos|None, quat|None)])]"""
    d = open(path, "rb").read()
    if d[:4] != b"EANM": raise ValueError("not an EANM file")
    i = d.find(b"BMAP") + 4
    n = struct.unpack_from("<I", d, i)[0]; i += 4
    names = []
    for _ in range(n):
        ln = struct.unpack_from("<I", d, i)[0]; i += 4
        names.append(d[i:i + ln].decode("latin-1")); i += ln
    frames = []
    while i + 4 <= len(d) and d[i:i + 4] == b"FRM2":
        i += 4
        fr = struct.unpack_from("<H", d, i)[0]; i += 2
        cnt = d[i]; i += 1
        ents = []
        for _ in range(cnt):
            b = d[i]; i += 1
            flags = struct.unpack_from("<H", d, i)[0]; i += 2
            pos = quat = None
            if flags & 1:
                pos = struct.unpack_from("<3f", d, i); i += 12
            if flags & 2:
                x, y, z = struct.unpack_from("<3f", d, i); i += 12
                w2 = 1.0 - (x * x + y * y + z * z)
                quat = Quaternion((math.sqrt(max(w2, 0.0)), x, y, z))
            ents.append((b, pos, quat))
        frames.append((fr, ents))
    return names, frames


# =====================================================================================================
#  .mdl
# =====================================================================================================
def _f(v, n=None):
    out = []
    for x in v:
        try: out.append(float(x.strip('"')))
        except ValueError: break
    return out if n is None else out[:n]


def node_matrix(kids):
    """local transform of a bone/volume node (row-vector convention in file -> column matrix)."""
    R = None; t = Vector((0, 0, 0))
    for head, _ in kids:
        if not head: continue
        k = head[0].lower(); v = head[1:]
        if k == "position" and len(_f(v)) >= 3:
            t = Vector(_f(v, 3))
        elif k == "matrix34" and len(_f(v)) >= 12:
            f = _f(v, 12); R = Matrix((f[0:3], f[3:6], f[6:9])); t = Vector(f[9:12])
        elif k == "matrix" and len(_f(v)) >= 16:
            f = _f(v, 16); R = Matrix((f[0:3], f[4:7], f[8:11])); t = Vector(f[12:15])
        elif k == "matrix" and len(_f(v)) >= 12:
            f = _f(v, 12); R = Matrix((f[0:3], f[3:6], f[6:9])); t = Vector(f[9:12])
        elif k == "orientation" and len(_f(v)) >= 9:
            f = _f(v, 9); R = Matrix((f[0:3], f[3:6], f[6:9]))
    if R is None: R = Matrix.Identity(3)
    return Matrix.Translation(t) @ R.transposed().to_4x4()


class BoneNode:
    def __init__(self, name, parent, local, btype=None):
        self.name = name; self.parent = parent; self.local = local; self.btype = btype
        self.world = (parent.world if parent else Matrix.Identity(4)) @ local
        self.children = []; self.views = []
        self.params = {}          # parsed {parameters "k=v;..."}
        self.limits = None        # (min, max)  degrees for revolute, metres for prizmatic
        self.cone = None          # socket (ball) joints: max angle from the rest direction, degrees
        self.speed = None; self.speed2 = None
        self.visibility = None
        self.terminator = False
        self.sequences = []       # dicts: name, file, speed, resume, autostart, events
        self.uvanims = []         # (name, {'shiftu': (a,b,step,mode), 'shiftv': ...})
        self.extra = {}           # anything else (key -> text)
        self.raw_params = None    # the original {parameters "..."} text
        self.from_def = False     # bone added by the .def, not present in the .mdl
        if parent: parent.children.append(self)


def _parse_params(s):
    d = {}
    for part in s.split(";"):
        part = part.strip()
        if not part: continue
        if "=" in part:
            k, v = part.split("=", 1); d[k.strip().lower()] = v.strip()
        else:
            d[part.strip().lower()] = True
    return d


def _parse_sequence(h3, k3):
    seq = {"name": h3[1].strip('"') if len(h3) > 1 else "seq", "file": None, "speed": None, "resume": False,
           "autostart": False, "events": []}
    for h4, k4 in k3:
        if not h4: continue
        k = h4[0].lower()
        if k == "file" and len(h4) > 1: seq["file"] = h4[1].strip('"')
        elif k == "speed" and _f(h4[1:2]): seq["speed"] = _f(h4[1:2])[0]
        elif k == "resume": seq["resume"] = True
        elif k == "autostart": seq["autostart"] = True
        elif k == "events":
            for h5, _ in k4:
                v = _f(h5[:1]) if h5 else []
                if v and len(h5) > 1: seq["events"].append((v[0], h5[1].strip('"')))
            # some files write {events {2 "x"}{46 "y"}} where the child head is '2 "x"' (handled above)
    return seq


def _parse_shape2d(kids):
    """obb2 / polygon2 / circle2 children -> dict"""
    out = {}
    for h, k in kids:
        if not h: continue
        key = h[0].lower()
        if key in ("obb2", "polygon2", "circle2"):
            shp = {"type": key, "verts": [], "center": None, "extent": None, "axis": None, "radius": None}
            for h2, _ in k:
                if not h2: continue
                k2 = h2[0].lower(); v = _f(h2[1:])
                if k2 == "center" and len(v) >= 2: shp["center"] = v[:2]
                elif k2 == "extent" and len(v) >= 2: shp["extent"] = v[:2]
                elif k2 == "axis" and len(v) >= 2: shp["axis"] = v[:2]
                elif k2 == "radius" and v: shp["radius"] = v[0]
                elif k2 == "vertex" and len(v) >= 2: shp["verts"].append(v[:2])
            out.setdefault("shapes", []).append(shp)
        elif key == "rotate":
            out["rotate"] = out.get("rotate", 0) + 1
    return out


class Model:
    pass


def parse_mdl(path):
    _txt = read_text(path)
    nodes, _ = parse_nodes(tokenize(_txt))
    M = Model()
    M.header = []
    for _l in _txt.split("\n"):
        if _l.lstrip().startswith(";"): M.header.append(_l.rstrip("\r"))
        elif _l.strip(): break
    M.bones = []; M.volumes = []; M.seqs = []; M.previews = []; M.obstacles = []; M.extras = {}

    def walk(items, parent):
        for head, kids in items:
            if not head: continue
            h0 = head[0].lower()
            if h0 == "bone":
                name = head[-1].strip('"')
                btype = head[1].lower() if len(head) > 2 else None
                if btype == "prizmatic": btype = "prismatic"
                b = BoneNode(name, parent, node_matrix(kids), btype); M.bones.append(b)
                for h2, k2 in kids:
                    if not h2: continue
                    k = h2[0].lower()
                    if k == "volumeview":
                        b.views.append((h2[1].strip('"'), 0))
                        for h3, _k3 in k2:
                            if h3: b.extra["view_" + h3[0].lower()] = " ".join(x.strip('"') for x in h3[1:])
                    elif k == "lodview":
                        li = 0
                        for h3, _k3 in k2:
                            if h3 and h3[0].lower() == "volumeview":
                                b.views.append((h3[1].strip('"'), li)); li += 1
                    elif k == "visibility":
                        b.visibility = _f(h2[1:2])[0] if _f(h2[1:2]) else 0
                    elif k == "parameters":
                        b.raw_params = " ".join(x.strip('"') for x in h2[1:])
                        b.params.update(_parse_params(b.raw_params))
                    elif k == "limits":
                        v = _f(h2[1:])
                        if len(v) >= 2: b.limits = (v[0], v[1])
                        elif len(v) == 1: b.cone = v[0]            # ball ("socket") joints: one cone angle
                    elif k == "speed":
                        v = _f(h2[1:2]); b.speed = v[0] if v else None
                    elif k == "speed2":
                        v = _f(h2[1:2]); b.speed2 = v[0] if v else None
                    elif k == "terminator":
                        b.terminator = True
                    elif k == "animation":
                        for h3, k3 in k2:
                            if not h3: continue
                            k3n = h3[0].lower()
                            if k3n == "sequence":
                                b.sequences.append(_parse_sequence(h3, k3))
                            elif k3n == "uv":
                                d = {}
                                for h4, _ in k3:
                                    if h4 and h4[0].lower() in ("shiftu", "shiftv"):
                                        v = _f(h4[1:4]); mode = h4[-1] if len(h4) > 4 else ""
                                        if len(v) >= 3: d[h4[0].lower()] = (v[0], v[1], v[2], mode)
                                b.uvanims.append((h3[1].strip('"') if len(h3) > 1 else "uv", d))
                    elif k not in ("bone", "matrix34", "matrix", "orientation", "position", "volumeview"):
                        b.extra[k] = " ".join(x.strip('"') for x in h2[1:])
                walk(kids, b)
            elif h0 == "skeleton":
                for h2, k2 in kids:
                    if h2 and h2[0].lower() == "animation":
                        for h3, k3 in k2:
                            if h3 and h3[0].lower() == "sequence":
                                M.seqs.append(_parse_sequence(h3, k3))
                walk(kids, parent)
            elif h0 == "volume":
                vol = {"name": head[1].strip('"') if len(head) > 1 else "vol", "kind": None, "arg": None, "bone": None,
                       "local": node_matrix(kids)}
                for h2, _k in kids:
                    if not h2: continue
                    k = h2[0].lower()
                    if k in ("polyhedron", "box", "cylinder", "sphere"):
                        vol["kind"] = k; vol["arg"] = [x.strip('"') for x in h2[1:]]
                    elif k == "bone":
                        vol["bone"] = h2[1].strip('"')
                M.volumes.append(vol)
            elif h0 in ("obstacle", "area"):
                d = _parse_shape2d(kids); d["kind"] = h0; d["name"] = head[1].strip('"') if len(head) > 1 else h0
                M.obstacles.append(d)
            elif h0 == "extender" and len(head) > 1 and head[1].strip('"').lower() == "preview":
                for h2, k2 in kids:
                    if not h2: continue
                    pv = {"name": h2[0].strip('"'), "camera": None, "lights": []}
                    for h3, k3 in k2:
                        if not h3: continue
                        if h3[0].lower() == "camera":
                            cam = {}
                            for h4, _ in k3:
                                if h4: cam[h4[0].lower()] = _f(h4[1:])
                            pv["camera"] = cam
                        elif h3[0].lower() == "lights":
                            for h4, k4 in k3:
                                lt = {"name": h4[0].strip('"') if h4 else "L"}
                                for h5, _ in k4:
                                    if not h5: continue
                                    kk = h5[0].lower()
                                    if kk == "type": lt["type"] = h5[1].lower() if len(h5) > 1 else "point"
                                    elif kk in ("position", "direction", "color", "diffuse", "ambient"):
                                        lt[kk] = h5[1:]
                                pv["lights"].append(lt)
                    M.previews.append(pv)
            elif h0 in ("ambient", "diffuse"):
                M.extras[h0] = " ".join(head[1:])
            else:
                M.extras[h0] = " ".join(x.strip('"') for x in head[1:])

    walk(nodes, None)
    return M


# =====================================================================================================
#  Materials
# =====================================================================================================
_img_cache = {}
_mat_cache = {}


def load_image(path, noncolor=False):
    if not path: return None
    key = (path, noncolor)
    if key in _img_cache: return _img_cache[key]
    try:
        im = bpy.data.images.load(path, check_existing=True)
        if noncolor: im.colorspace_settings.name = "Non-Color"
    except Exception as e:
        print("[goh] image load failed", path, e); im = None
    _img_cache[key] = im
    return im


def _input(bsdf, *names):
    for n in names:
        if n in bsdf.inputs: return bsdf.inputs[n]
    return None


def make_material(mtl_path, res):
    name = os.path.splitext(os.path.basename(mtl_path))[0]
    key = os.path.normpath(mtl_path).lower()
    if key in _mat_cache and _mat_cache[key].name in bpy.data.materials: return _mat_cache[key]
    info = parse_mtl(mtl_path)
    mat = bpy.data.materials.new(name); _mat_cache[key] = mat
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.65
    exts = (".dds", ".tga", ".png", ".jpg", "")

    def tex(ref, noncolor=False):
        if not ref: return None
        rel = ref[2:] if ref.startswith("$/") else ref
        pth = res.find(rel, exts, extra_dirs=[os.path.dirname(mtl_path)])
        return load_image(pth, noncolor)

    def tex_node(im, x, y):
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = im; t.location = (x, y); return t

    diff = tex(info["diffuse"])
    if info["kind"] == "water" and not diff:
        bsdf.inputs["Base Color"].default_value = (0.05, 0.18, 0.25, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.05
        bsdf.inputs["Alpha"].default_value = 0.75
        try: mat.blend_method = "BLEND"
        except Exception: pass
        try: mat.surface_render_method = "BLENDED"
        except Exception: pass
    if diff:
        t = tex_node(diff, -600, 200)
        col = info.get("color")
        if col and len(col) >= 3 and min(col[:3]) < 250:
            mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"; mix.location = (-300, 200)
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs["B"].default_value = (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0, 1.0)
            nt.links.new(t.outputs["Color"], mix.inputs["A"])
            nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
        else:
            nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if info["blend"] in ("test", "blend"):
            nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
            try:
                mat.blend_method = "CLIP" if info["blend"] == "test" else "BLEND"
                mat.alpha_threshold = 0.5
            except Exception: pass
            try: mat.surface_render_method = "DITHERED" if info["blend"] == "test" else "BLENDED"
            except Exception: pass
    bump = tex(info["bump"], True)
    if bump:
        t = tex_node(bump, -600, -150)
        nm = nt.nodes.new("ShaderNodeNormalMap"); nm.location = (-300, -150); nm.inputs["Strength"].default_value = 0.8
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    spec = tex(info["specular"], True)
    if spec:
        t = tex_node(spec, -600, -450)
        inp = _input(bsdf, "Specular IOR Level", "Specular")
        if inp is not None:
            bw = nt.nodes.new("ShaderNodeRGBToBW"); bw.location = (-300, -450)
            mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.location = (-150, -450)
            mul.inputs[1].default_value = 0.25          # game spec maps are very strong; keep the look close to in-game
            nt.links.new(t.outputs["Color"], bw.inputs["Color"])
            nt.links.new(bw.outputs["Val"], mul.inputs[0])
            nt.links.new(mul.outputs["Value"], inp)
    mat["goh_mtl"] = os.path.basename(mtl_path)
    for k, v in info.items():
        if k == "extra":
            if v: mat["goh_extra"] = json.dumps(v)
        elif v is not None and k != "color": mat["goh_" + k] = str(v)
    if info["color"]: mat["goh_color"] = info["color"]
    return mat


# =====================================================================================================
#  Mesh building
# =====================================================================================================
def build_mesh(ply_path, res, name):
    P = parse_ply(ply_path)
    me = bpy.data.meshes.new(name)
    tris = []; mat_of = []
    mats = []
    for mi, m in enumerate(P["meshes"]):
        a, c = m["first"], m["count"]
        for t in range(a, a + c):
            tris.append((P["idx"][t * 3], P["idx"][t * 3 + 1], P["idx"][t * 3 + 2])); mat_of.append(mi)
        mt = None
        if m["mtl"]:
            mp = res.find(m["mtl"], ("",), extra_dirs=[os.path.dirname(ply_path)])
            if mp: mt = make_material(mp, res)
        mats.append(mt)
    me.from_pydata(P["pos"], [], tris)
    for mt in mats:
        me.materials.append(mt)
    for poly, mi in zip(me.polygons, mat_of):
        poly.material_index = mi; poly.use_smooth = True
    uvl = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li, vi in zip(poly.loop_indices, poly.vertices):
            u, v = P["uv"][vi]; uvl.data[li].uv = (u, 1.0 - v)
    if P["col"]:
        att = me.attributes.new("goh_color32", "INT", "POINT"); att.data.foreach_set("value", P["col"])
    if P["uv2"]:
        uvl2 = me.uv_layers.new(name="UV2")
        for poly in me.polygons:
            for li, vi in zip(poly.loop_indices, poly.vertices):
                u, v = P["uv2"][vi]; uvl2.data[li].uv = (u, 1.0 - v)
        me.uv_layers.active_index = 0
    me["goh_stride"] = P["stride"]
    me["goh_have_skin"] = bool(P["have_skin"])
    me["goh_skin"] = json.dumps(P["skin"])
    me["goh_mesh_hdr"] = json.dumps([{"a": m["a"], "d": m["d"], "color": m["color"], "mtl": m["mtl"], "palette": m["palette"]} if "a" in m else {"mtl": m["mtl"]} for m in P["meshes"]])
    me.update()
    try:
        if P["layout"].get("n") is not None: me.normals_split_custom_set_from_vertices([Vector(n).normalized() if Vector(n).length > 1e-6 else Vector((0, 0, 1)) for n in P["nrm"]])
        if hasattr(me, "use_auto_smooth"): me.use_auto_smooth = True
    except Exception as e:
        pass
    return me, P


def box_mesh(name, dims):
    me = bpy.data.meshes.new(name)
    x, y, z = [d / 2 for d in dims]
    v = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    f = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    me.from_pydata(v, [], f); me.update(); return me


def cylinder_mesh(name, r, h, seg=16):
    me = bpy.data.meshes.new(name)
    v = []; f = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        v.append((r * math.cos(a), r * math.sin(a), -h / 2)); v.append((r * math.cos(a), r * math.sin(a), h / 2))
    for i in range(seg):
        j = (i + 1) % seg
        f.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    f.append(tuple(2 * i for i in reversed(range(seg)))); f.append(tuple(2 * i + 1 for i in range(seg)))
    me.from_pydata(v, [], f); me.update(); return me


def extract_model_dir(virtual_dir, game_dir, dest=None):
    """Extract a model folder out of the game's .pak files, e.g.
         extract_model_dir("entity/-vehicle/ussr/tank_medium/t34_85", game_dir)
       returns the folder on disk (cache/files/...) - then import_mdl(<folder>/<name>.mdl).
       Files the model references in other folders (../x_source/...) are fetched automatically on import."""
    r = Resolver(os.path.join(game_dir, "resource"), game_dir)
    vd = virtual_dir.replace("\\", "/").strip("/")
    low = vd.lower(); n = 0
    for key, (pak, name, virtual) in r._pak_index().items():
        if key.startswith("*/"): continue
        if key.startswith(low + "/") and "/" not in key[len(low) + 1:]:
            r._extract(pak, name, virtual); n += 1
    return os.path.join(CACHE_DIR, "files", vd.replace("/", os.sep)) if n else None


# =====================================================================================================
#  SDL (.def / .ext / .inc): parser, macro expander, printer
#    {block head... children}      (call "name" key(val) ... args pos1 pos2)      (define "name" body...)
#    (include "file" key(val))     (* a b) arithmetic      %name / %0 parameters
# =====================================================================================================
class SAtom:
    __slots__ = ("v", "q")
    def __init__(self, v, q=False): self.v = v; self.q = q
    def __repr__(self): return ('"%s"' % self.v) if self.q else self.v


class SBlock:
    __slots__ = ("items",)
    def __init__(self, items): self.items = items
    @property
    def head(self):
        out = []
        for i in self.items:
            if isinstance(i, SAtom): out.append(i.v)
            else: break
        return out
    @property
    def name(self):
        h = self.head
        return h[0].lower() if h else ""
    def kids(self):
        return [i for i in self.items if isinstance(i, (SBlock, SCall, SNamed))]
    def blocks(self, name=None):
        return [i for i in self.items if isinstance(i, SBlock) and (name is None or i.name == name.lower())]


class SCall:
    __slots__ = ("items",)
    def __init__(self, items): self.items = items
    @property
    def name(self):
        return self.items[0].v.lower() if self.items and isinstance(self.items[0], SAtom) else ""


class SNamed:
    __slots__ = ("key", "items")
    def __init__(self, key, items): self.key = key; self.items = items


_SDL_DELIM = set('{}()";')


def sdl_parse(text):
    """-> list of top-level items"""
    s = text; n = len(s); i = 0
    toks = []          # (kind, value, adjacent_to_prev)
    prev_end = -1
    while i < n:
        c = s[i]
        if c in " \t\r\n\f\v\x00" or c == "﻿":
            i += 1; continue
        if c == ";":
            while i < n and s[i] != "\n": i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] != '"': j += 1
            toks.append(("str", s[i + 1:j], i == prev_end)); i = j + 1; prev_end = i; continue
        if c in "{}()":
            toks.append((c, c, i == prev_end)); i += 1; prev_end = i; continue
        j = i
        while j < n and s[j] not in " \t\r\n\f\v\x00" and s[j] not in _SDL_DELIM: j += 1
        toks.append(("atom", s[i:j], i == prev_end)); i = j; prev_end = j
    pos = [0]

    def parse_list(close):
        items = []
        while pos[0] < len(toks):
            k, v, adj = toks[pos[0]]
            if k == close:
                pos[0] += 1; return items
            if k in ("}", ")"):          # stray / mismatched closer: end this list
                pos[0] += 1; return items
            pos[0] += 1
            if k == "{":
                items.append(SBlock(parse_list("}")))
            elif k == "(":
                items.append(SCall(parse_list(")")))
            elif k == "str":
                items.append(SAtom(v, True))
            else:
                # atom directly followed by '(' -> named argument  key(...)
                if pos[0] < len(toks) and toks[pos[0]][0] == "(" and toks[pos[0]][2] and not re.match(r"^[-+]?[\d.]+$", v) and v not in ("*", "/", "+", "-"):
                    pos[0] += 1
                    items.append(SNamed(v, parse_list(")")))
                else:
                    items.append(SAtom(v))
        return items
    return parse_list(None)


def _atom_text(a):
    return a.v if isinstance(a, SAtom) else None


def sdl_print(items, indent=0, out=None):
    """serialize a parsed/expanded tree back to SDL text"""
    top = out is None
    out = [] if out is None else out
    pad = "\t" * indent
    for it in items:
        if isinstance(it, SAtom):
            out.append(pad + (('"%s"' % it.v) if it.q else it.v))
        elif isinstance(it, SBlock):
            head = []; rest = []
            for x in it.items:
                if isinstance(x, SAtom) and not rest: head.append(('"%s"' % x.v) if x.q else x.v)
                else: rest.append(x)
            if rest:
                out.append(pad + "{" + " ".join(head)); sdl_print(rest, indent + 1, out); out.append(pad + "}")
            else:
                out.append(pad + "{" + " ".join(head) + "}")
        elif isinstance(it, SCall):
            out.append(pad + "(" + _inline(it.items) + ")")
        elif isinstance(it, SNamed):
            out.append(pad + it.key + "(" + _inline(it.items) + ")")
    return "\n".join(out) + "\n" if top else out


def _inline(items):
    parts = []
    for x in items:
        if isinstance(x, SAtom): parts.append(('"%s"' % x.v) if x.q else x.v)
        elif isinstance(x, SNamed): parts.append(x.key + "(" + _inline(x.items) + ")")
        elif isinstance(x, SCall): parts.append("(" + _inline(x.items) + ")")
        elif isinstance(x, SBlock):
            parts.append("{" + _inline(x.items) + "}")
    return " ".join(parts)


class SdlEnv:
    """macro environment shared across includes"""
    def __init__(self, loader):
        self.defines = {}
        self.loader = loader          # loader(current_dir, relpath) -> (text, new_dir) | None
        self.errors = []
        self.included = []            # (relpath) for reporting


def _num(a):
    try: return float(a.v)
    except Exception: return None


def _fmt(x):
    return str(int(x)) if abs(x - round(x)) < 1e-9 and abs(x) < 1e15 else ("%.6g" % x)


def _subst_atom(a, params):
    """-> list of items replacing atom a"""
    v = a.v
    if "%" not in v: return [a]
    m = re.fullmatch(r"%(\w+)", v)
    if m and m.group(1) in params:
        return [copy_item(x) for x in params[m.group(1)]]
    def rep(mm):
        key = mm.group(1)
        if key in params and params[key] and isinstance(params[key][0], SAtom):
            return params[key][0].v
        return mm.group(0)
    return [SAtom(re.sub(r"%(\w+)", rep, v), a.q)]


def copy_item(x):
    if isinstance(x, SAtom): return SAtom(x.v, x.q)
    if isinstance(x, SBlock): return SBlock([copy_item(i) for i in x.items])
    if isinstance(x, SCall): return SCall([copy_item(i) for i in x.items])
    if isinstance(x, SNamed): return SNamed(x.key, [copy_item(i) for i in x.items])
    return x


def _subst(items, params):
    out = []
    for x in items:
        if isinstance(x, SAtom): out.extend(_subst_atom(x, params))
        elif isinstance(x, SBlock): out.append(SBlock(_subst(x.items, params)))
        elif isinstance(x, SCall): out.append(SCall(_subst(x.items, params)))
        elif isinstance(x, SNamed): out.append(SNamed(x.key, _subst(x.items, params)))
    return out


def sdl_expand(items, env, cur_dir, params=None, depth=0):
    """expand includes / defines / macro calls / arithmetic. returns new item list."""
    params = params or {}
    out = []
    if depth > 60:
        env.errors.append("macro depth exceeded"); return list(items)
    for x in items:
        if isinstance(x, SAtom):
            out.extend(_subst_atom(x, params) if params else [x])
        elif isinstance(x, SBlock):
            out.append(SBlock(sdl_expand(x.items, env, cur_dir, params, depth + 1)))
        elif isinstance(x, SNamed):
            out.append(SNamed(x.key, sdl_expand(x.items, env, cur_dir, params, depth + 1)))
        elif isinstance(x, SCall):
            out.extend(_expand_call(x, env, cur_dir, params, depth))
    return out


def _expand_call(call, env, cur_dir, params, depth):
    raw = call.items
    # define: keep the body unexpanded (parameters are substituted at call time)
    if raw and isinstance(raw[0], SAtom) and raw[0].v.lower() == "define":
        if len(raw) > 1 and isinstance(raw[1], SAtom):
            env.defines[raw[1].v.lower()] = raw[2:]
        return []
    items = sdl_expand(raw, env, cur_dir, params, depth + 1)
    if not items or not isinstance(items[0], SAtom):
        return [SCall(items)]
    name = items[0].v; low = name.lower()
    rest = items[1:]
    # arithmetic
    if name in ("*", "/", "+", "-") and not items[0].q:
        vals = []
        for r in rest:
            vals.append(_num(r) if isinstance(r, SAtom) else None)
        if vals and all(v is not None for v in vals):
            try:
                acc = vals[0]
                for v in vals[1:]:
                    acc = acc * v if name == "*" else acc / v if name == "/" else acc + v if name == "+" else acc - v
                return [SAtom(_fmt(acc))]
            except ZeroDivisionError:
                pass
        return [SCall(items)]
    if low == "include":
        if rest and isinstance(rest[0], SAtom):
            got = env.loader(cur_dir, rest[0].v)
            if got is None:
                env.errors.append("include not found: " + rest[0].v)
                return [SCall(items)]
            text, new_dir, label = got
            env.included.append(label)
            inc_params = dict(params)
            for r in rest[1:]:
                if isinstance(r, SNamed): inc_params[r.key] = r.items
            return sdl_expand(sdl_parse(text), env, new_dir, inc_params, depth + 1)
        return [SCall(items)]
    if low in env.defines:
        body = env.defines[low]
        p = {}; pos = 0; after_args = False
        for r in rest:
            if isinstance(r, SAtom) and r.v == "args" and not r.q:
                after_args = True; continue
            if isinstance(r, SNamed) and not after_args:
                p[r.key] = r.items
            else:
                p[str(pos)] = [r]; pos += 1
        inst = _subst(body, p)
        return sdl_expand(inst, env, cur_dir, {}, depth + 1)
    return [SCall(items)]


def sdl_find(items, *path):
    """first nested block matching the name path, e.g. sdl_find(tree, 'chassis', 'fuelbag')"""
    cur = items
    for nm in path:
        nxt = None
        for x in cur:
            if isinstance(x, SBlock) and x.name == nm.lower():
                nxt = x.items; break
        if nxt is None: return None
        cur = nxt
    return cur


def sdl_values(block):
    """head atoms after the block name as strings"""
    return block.head[1:]


def sdl_flatten(items, prefix="", out=None, depth=0, limit=400):
    """flatten nested blocks to {path: 'value text'} (for custom properties)"""
    out = {} if out is None else out
    if depth > 6: return out
    for x in items:
        if len(out) >= limit: break
        if isinstance(x, SBlock):
            key = x.name
            if not key: continue
            h = x.head
            label = key + ("_" + h[1].strip() if len(h) > 1 and not re.match(r"^[-+\d.]+$", h[1]) and depth > 0 and len(h) == 2 and key in ("volume", "bone", "place", "component", "setting", "visor", "weapon") else "")
            val = " ".join(h[1:])
            path = (prefix + "_" if prefix else "") + label
            if val and not (len(h) == 2 and key in ("volume", "bone", "place", "component", "setting", "visor")):
                out[path] = val
            sdl_flatten(x.items, path, out, depth + 1, limit)
        elif isinstance(x, SNamed):
            path = (prefix + "_" if prefix else "") + x.key
            out[path] = _inline(x.items)
        elif isinstance(x, SCall):
            nm = x.name
            if nm:
                path = (prefix + "_" if prefix else "") + "call_" + nm
                out[path] = _inline(x.items[1:])
    return out


# =====================================================================================================
#  .def / .ext  ->  properties on the rig (armour, mobility, seats, weapons, tags, components ...)
# =====================================================================================================
class DefData:
    pass


def _read(p):
    return open(p, encoding="utf-8", errors="replace").read()


def make_sdl_loader(res):
    def loader(cur_dir, rel):
        rel = rel.replace("\\", "/")
        if rel.startswith("/"):
            r = rel[1:]
            for rd in res.resource_dirs:
                p = os.path.join(rd, r)
                if os.path.isfile(p): return _read(p), os.path.dirname(p), p
            hit = res._pak_index().get(r.lower())
            if hit:
                p = res._extract(*hit); return _read(p), os.path.dirname(p), p
            return None
        p = res.locate(cur_dir, rel)
        if p: return _read(p), os.path.dirname(p), p
        return None
    return loader


def find_def_files(model_dir, mdl_name):
    """.def files of the folder that use this .mdl as their {extension}"""
    out = []
    try:
        files = [f for f in os.listdir(model_dir) if f.lower().endswith(".def")]
    except OSError:
        return out
    stem = os.path.splitext(mdl_name)[0].lower()
    for f in files:
        try: txt = _read(os.path.join(model_dir, f))
        except OSError: continue
        m = re.search(r'\{\s*extension\s+"([^"]+)"', txt, re.I)
        if m and m.group(1).lower() == mdl_name.lower():
            out.append(f)
    if not out:
        out = [f for f in files if os.path.splitext(f)[0].lower() == stem]
    out.sort(key=lambda f: os.path.splitext(f)[0].lower() != stem)
    return out


def load_def(res, model_dir, mdl_name, report=print):
    files = find_def_files(model_dir, mdl_name)
    if not files:
        used = set()
        try:
            for f in os.listdir(model_dir):
                if f.lower().endswith(".def"):
                    mm = re.search(r'\{\s*extension\s+"([^"]+)"', _read(os.path.join(model_dir, f)), re.I)
                    if mm: used.add(mm.group(1))
        except OSError:
            pass
        if used and mdl_name.lower() not in {u.lower() for u in used}:
            report("[goh] WARNING: no .def in this folder uses %s; the game loads %s instead." % (mdl_name, ", ".join(sorted(used))))
        return None
    D = DefData()
    D.path = os.path.join(model_dir, files[0]); D.all_files = files
    D.text = _read(D.path)
    D.env = SdlEnv(make_sdl_loader(res))
    tree = sdl_expand(sdl_parse(D.text), D.env, model_dir)
    D.tree = tree
    root = None
    for x in tree:
        if isinstance(x, SBlock) and x.name in ("game_entity", "actor", "entity"):
            root = x.items; break
    D.root = root if root is not None else tree
    for e in D.env.errors: report("[goh] def: " + e)
    m = re.search(r'\{\s*extension\s+"([^"]+)"', D.text, re.I)
    D.extension = m.group(1) if m else None
    if D.extension and D.extension.lower() != mdl_name.lower():
        report("[goh] WARNING: the game does not load %s for this entity - %s says it uses %s. Edit that file instead, "
               "or your changes will not show up in game (an exported copy of an unused .mdl can break placed units)." % (mdl_name, os.path.basename(D.path), D.extension))
    return D


def _blk_node(b):
    head = [('"%s"' % a.v) if a.q else a.v for a in b.items if isinstance(a, SAtom)]
    return (head, [_blk_node(k) for k in b.items if isinstance(k, SBlock)])


def def_bone_overrides(D, bones, report=print):
    """{bone "x" {limits a b}{speed2 n}...} in the .def overrides the .mdl; {Skeleton {bone ...}} may add bones."""
    by_name = {b.name: b for b in bones}
    added = 0

    def walk(items, parent):
        nonlocal added
        for x in items:
            if not isinstance(x, SBlock): continue
            if x.name == "bone" and len(x.head) >= 2:
                nm = x.head[-1]
                b = by_name.get(nm)
                kids = [_blk_node(k) for k in x.items if isinstance(k, SBlock)]
                if b is None:
                    par = parent or (bones[0] if bones else None)
                    btype = x.head[1].lower() if len(x.head) > 2 else None
                    b = BoneNode(nm, par, node_matrix(kids), "prismatic" if btype == "prizmatic" else btype)
                    b.from_def = True
                    bones.append(b); by_name[nm] = b; added += 1
                for k in x.items:
                    if not isinstance(k, SBlock): continue
                    h = k.head; kn = k.name
                    if kn == "limits":
                        v = _f(h[1:])
                        if len(v) >= 2: b.limits = (v[0], v[1])
                        elif len(v) == 1: b.cone = v[0]
                    elif kn == "speed":
                        v = _f(h[1:2]); b.speed = v[0] if v else b.speed
                    elif kn == "speed2":
                        v = _f(h[1:2]); b.speed2 = v[0] if v else b.speed2
                    elif kn == "parameters":
                        b.params.update(_parse_params(" ".join(h[1:])))
                    elif kn == "visibility":
                        v = _f(h[1:2]); b.visibility = v[0] if v else b.visibility
                    elif kn not in ("bone", "position", "matrix34", "matrix", "orientation", "volumeview", "lodview"):
                        b.extra["def_" + kn] = " ".join(h[1:])
                walk(x.items, b)
            elif x.name == "skeleton":
                walk(x.items, parent)

    walk(D.root, None)
    return added


_HANDLED_TOP = {"volume", "bone", "skeleton", "placer", "boarder", "weaponry", "perks", "armor", "sensor", "extension"}


_ACCUMULATE = ("props", "tags", "able", "perk_able")


def _merge(dst, src):
    """later definitions win (the .def comes after the shared .ext files); set-like keys (props, tags, able...) are unions"""
    for k, v in src.items():
        accumulate = k.split("_")[-1] in _ACCUMULATE or k in _ACCUMULATE
        if accumulate and k in dst and v not in dst[k].split(" "):
            dst[k] = (dst[k] + " " + v).strip()
        else:
            dst[k] = v


def apply_def_scene(D, arm, bones, bone_by_orig, vol_objs, coll, report=print):
    root = D.root
    n_props = 0

    def setp(holder, key, val):
        nonlocal n_props
        _set_prop(holder, key, val); n_props += 1

    # ---- raw text blocks (for round trip / reading) -------------------------------------------
    base = os.path.splitext(os.path.basename(D.path))[0]
    try:
        t = bpy.data.texts.new(base + ".def"); t.write(D.text); arm["goh_def_text"] = t.name
        local_inc = []
        for lab in D.env.included:
            if os.path.isfile(lab) and os.path.dirname(os.path.abspath(lab)) == os.path.dirname(os.path.abspath(D.path)):
                t2 = bpy.data.texts.new(os.path.basename(lab)); t2.write(_read(lab)); local_inc.append(t2.name)
        arm["goh_local_includes"] = json.dumps(local_inc)
        te = bpy.data.texts.new(base + ".expanded.sdl")
        te.write("; .def with all includes and macros resolved (generated by goh_importer)\n" + sdl_print(D.root))
        arm["goh_def_expanded_text"] = te.name
    except Exception as e:
        report("[goh] def texts: %s" % e)
    arm["goh_def_file"] = D.path
    arm["goh_def_includes"] = " | ".join(os.path.basename(x) if os.path.isfile(x) else x for x in D.env.included)

    # ---- armature level: every block that is not handled specially -----------------------------
    flat = {}
    for x in root:
        if isinstance(x, SBlock) and x.name not in _HANDLED_TOP and x.name and not x.name.startswith("#"):
            _merge(flat, sdl_flatten([x], limit=150))
        elif isinstance(x, SCall) and x.name:
            _merge(flat, {"call_" + x.name: _inline(x.items[1:])})
    for k, v in flat.items():
        setp(arm, "goh_" + k, v)

    # ---- chassis / weaponry / sensor flattened too (kept under their own prefix) ---------------
    # armour settings: one compact line  (volume pattern: blast koef / pierce koef)
    sets = []
    for x in root:
        if isinstance(x, SBlock) and x.name == "armor":
            for s in x.blocks("setting"):
                if len(s.head) < 2: continue
                b = sdl_find(s.items, "threshold", "blast", "koef"); pz = sdl_find(s.items, "threshold", "pierce", "koef")
                vols_ = [" ".join(v.head[1:]) for v in s.blocks("volumes")]
                kb = " ".join(sdl_values(SBlock([SAtom("x")] + b))) if b else "?"
                kp = " ".join(sdl_values(SBlock([SAtom("x")] + pz))) if pz else "?"
                sets.append("%s[%s]=%s/%s" % (s.head[1], ",".join(vols_), kb, kp))
        elif isinstance(x, SBlock) and x.name == "armor_damage_modifier":
            setp(arm, "goh_armor_damage_modifier", " ".join(x.head[1:]))
    if sets: setp(arm, "goh_armor_settings", " ; ".join(sets))
    d = {}
    for x in root:
        if isinstance(x, SBlock) and x.name == "sensor":
            _merge(d, sdl_flatten([x], limit=120))
    for k, v in d.items(): setp(arm, "goh_" + k, v)

    # ---- volumes: armour thickness, tags, able ... --------------------------------------------
    vols = {}
    for x in root:
        if isinstance(x, SBlock) and x.name == "volume" and len(x.head) > 1 and "%" not in x.head[1]:
            vols.setdefault(x.head[1], {})
            _merge(vols[x.head[1]], sdl_flatten(x.items, "", {}, 0, 80))
    n_vol = 0
    for nm, d in vols.items():
        ob = vol_objs.get(nm)
        if ob is None: continue          # macro boilerplate for volumes this model does not have
        for k, v in d.items(): _set_prop(ob, "goh_" + k, v)
        try: ob["goh_orig_json"] = json.dumps(d)
        except Exception: pass
        n_vol += 1
    arm["goh_volumes_described"] = len(vols)
    arm["goh_volumes_matched"] = n_vol

    # ---- components (hit points) --------------------------------------------------------------
    for x in root:
        if isinstance(x, SBlock) and x.name == "perks":
            for c in x.blocks("component"):
                if len(c.head) < 2: continue
                hp = sdl_find(c.items, "health", "hp")
                health = sdl_find(c.items, "health")
                if health:
                    d = sdl_flatten(health, "", {}, 0, 20)
                    for k, v in d.items(): setp(arm, "goh_component_%s_%s" % (c.head[1], k), v)
            d = {}
            for k in x.kids():
                if isinstance(k, SBlock) and k.name != "component":
                    _merge(d, sdl_flatten([k], "perk", {}, 0, 30))
            for k, v in d.items(): setp(arm, "goh_" + k, v)

    # ---- seats / places / doors -----------------------------------------------------------------
    doors = {}
    for x in root:
        if isinstance(x, SBlock) and x.name == "boarder":
            for l in x.blocks("link"):
                h = l.head
                if len(h) >= 3: doors[h[2]] = {"door": h[1], "anm": " ".join(" ".join(a.head[1:]) for a in l.blocks("anm"))}
    for x in root:
        if isinstance(x, SBlock) and x.name == "placer":
            for pl in x.blocks("place"):
                if len(pl.head) < 2: continue
                pname = pl.head[1]
                d = sdl_flatten(pl.items, "", {}, 0, 20)
                bonename = d.get("linkbone")
                b = bone_by_orig.get(bonename) if bonename else None
                holder = arm.pose.bones.get(b.bname) if b is not None else None
                for k, v in d.items():
                    if holder is not None: setp(holder, "goh_seat_" + k, v)
                    else: setp(arm, "goh_place_%s_%s" % (pname, k), v)
                if holder is not None:
                    setp(holder, "goh_seat_place", pname)
                    for k, v in doors.get(pname, {}).items(): setp(holder, "goh_seat_" + k, v)

    # ---- weapon places -----------------------------------------------------------------------------
    for x in root:
        if isinstance(x, SBlock) and x.name == "weaponry":
            top = sdl_flatten([k for k in x.items if isinstance(k, SBlock) and k.name != "place"], "weaponry", {}, 0, 40)
            for k, v in top.items(): setp(arm, "goh_" + k, v)
            for pl in x.blocks("place"):
                if len(pl.head) < 2: continue
                pname = pl.head[1]
                d = sdl_flatten(pl.items, "", {}, 0, 40)
                for k in pl.kids():
                    if isinstance(k, SCall): d["call_" + k.name] = _inline(k.items[1:])
                b = bone_by_orig.get(pname)
                holder = arm.pose.bones.get(b.bname) if b is not None else None
                for k, v in d.items():
                    if holder is not None: setp(holder, "goh_weapon_" + k, v)
                    else: setp(arm, "goh_weapon_%s_%s" % (pname, k), v)
                if holder is not None: setp(holder, "goh_weapon_place", pname)

    # ---- sensors (visors) on their bones ---------------------------------------------------------------
    for x in root:
        if isinstance(x, SBlock) and x.name == "sensor":
            for v in x.blocks("visor"):
                bl = v.blocks("bone")
                if not bl or len(bl[0].head) < 2: continue
                b = bone_by_orig.get(bl[0].head[1])
                if b is None: continue
                holder = arm.pose.bones.get(b.bname)
                if holder is not None:
                    setp(holder, "goh_visor", v.head[1] if len(v.head) > 1 else "")
                    for k, val in sdl_flatten(v.items, "", {}, 0, 10).items(): setp(holder, "goh_visor_" + k, val)

    unresolved = sorted(set(x.name for x in root if isinstance(x, SCall) and x.name))
    if unresolved: arm["goh_unresolved_macros"] = ", ".join(unresolved)
    report("[goh] def: %d properties, %d volumes described (%d matched collision objects), %d includes%s" % (
        n_props, len(vols), n_vol, len(D.env.included), (", unresolved: " + ", ".join(unresolved)) if unresolved else ""))
    return n_props


# =====================================================================================================
#  Main import
# =====================================================================================================
def _reflection_fix(m):
    """F such that det(m @ F) > 0 (right-multiplied local z flip when m mirrors)."""
    return Matrix.Diagonal((1, 1, -1, 1)) if m.to_3x3().determinant() < 0 else Matrix.Identity(4)


def _orthonormal(m):
    """proper rotation + translation from a matrix (drops scale)."""
    loc = m.to_translation()
    r = m.to_3x3().normalized()
    return Matrix.Translation(loc) @ r.to_4x4()


def _set_prop(holder, key, value):
    if len(key) > 63:
        import zlib
        key = key[:53] + "_" + ("%08x" % zlib.crc32(key.encode("utf-8")))
    try:
        holder[key] = value
    except Exception:
        try: holder[key] = str(value)
        except Exception: pass


def _shape2d_mesh(name, shp):
    me = bpy.data.meshes.new(name)
    v = []; e = []
    if shp["type"] == "obb2" and shp["center"] and shp["extent"]:
        cx, cy = shp["center"]; ex, ey = shp["extent"]
        ax, ay = shp["axis"] or (1.0, 0.0)
        l = math.hypot(ax, ay) or 1.0; ax, ay = ax / l, ay / l
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            v.append((cx + sx * ex * ax - sy * ey * ay, cy + sx * ex * ay + sy * ey * ax, 0.0))
        e = [(0, 1), (1, 2), (2, 3), (3, 0)]
    elif shp["type"] == "polygon2" and len(shp["verts"]) >= 2:
        v = [(x, y, 0.0) for x, y in shp["verts"]]
        e = [(i, (i + 1) % len(v)) for i in range(len(v))]
    elif shp["type"] == "circle2" and shp["radius"]:
        cx, cy = shp["center"] or (0.0, 0.0); r = shp["radius"]
        for i in range(24):
            a = 2 * math.pi * i / 24; v.append((cx + r * math.cos(a), cy + r * math.sin(a), 0.0))
        e = [(i, (i + 1) % 24) for i in range(24)]
    if not v: return None
    me.from_pydata(v, e, []); me.update(); return me


def _add_uv_scroll(ob, uvanims):
    """Texture scrolling ({uv "run" {shiftV a b step}}): Mapping node + driver on the offset."""
    for slot_i, mat in enumerate(list(ob.data.materials)):
        if mat is None or not mat.use_nodes: continue
        mat = mat.copy(); ob.data.materials[slot_i] = mat
        nt = mat.node_tree
        texs = [n for n in nt.nodes if n.type == "TEX_IMAGE"]
        if not texs: continue
        tc = nt.nodes.new("ShaderNodeTexCoord"); mp = nt.nodes.new("ShaderNodeMapping")
        nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
        for t in texs: nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        for _nm, d in uvanims:
            for key, idx in (("shiftu", 0), ("shiftv", 1)):
                if key not in d: continue
                lo, hi, step, _mode = d[key]
                rng = (hi - lo) or 1.0
                fc = mp.inputs["Location"].driver_add("default_value", idx)
                fc.driver.type = "SCRIPTED"
                fc.driver.expression = "%g + ((frame * %g) %% %g)" % (lo, step, rng)
        mat["goh_uv_scroll"] = str([(n, d) for n, d in uvanims])


def _make_preview(base, coll, arm, previews, scale):
    pcoll = bpy.data.collections.new(base + "_preview"); coll.children.link(pcoll)
    n = 0
    for pv in previews:
        cam = pv["camera"]
        if cam and "origin" in cam and "lookat" in cam:
            cd = bpy.data.cameras.new("%s_cam_%s" % (base, pv["name"]))
            if cam.get("fov"): cd.angle = math.radians(cam["fov"][0])
            co = bpy.data.objects.new(cd.name, cd); pcoll.objects.link(co)
            o = Vector(cam["origin"][:3]) * scale; t = Vector(cam["lookat"][:3]) * scale
            co.location = o
            q = (t - o).to_track_quat("-Z", "Y")
            if cam.get("horizon"): q = q @ Quaternion((0, 0, 1), cam["horizon"][0])
            co.rotation_euler = q.to_euler()
            co["goh_preview"] = pv["name"]; n += 1
        for lt in pv["lights"]:
            typ = lt.get("type", "point")
            if typ.startswith("dir"):
                ld = bpy.data.lights.new("%s_sun_%s_%s" % (base, pv["name"], lt["name"]), "SUN"); ld.energy = 2.0
                lo = bpy.data.objects.new(ld.name, ld); pcoll.objects.link(lo)
                d = Vector(_f(lt.get("direction", ["0", "0", "-1"]), 3) or (0, 0, -1))
                if d.length > 1e-6: lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
            else:
                ld = bpy.data.lights.new("%s_pt_%s_%s" % (base, pv["name"], lt["name"]), "POINT"); ld.energy = 1000.0
                lo = bpy.data.objects.new(ld.name, ld); pcoll.objects.link(lo)
                p = _f(lt.get("position", ["0", "0", "0"]), 3) or (0, 0, 0)
                lo.location = Vector(p) * scale
            n += 1
    return n


def import_mdl(mdl_path, collection=None, import_collision=True, import_anims=True, import_enumerators=True, import_lods=False,
               apply_speed=True, joint_limits=True, import_previews=True, import_obstacles=True, import_def=True,
               anim_library=None,
               game_dir=None, scale=SCALE, report=print):
    mdl_path = os.path.abspath(mdl_path)
    model_dir = os.path.dirname(mdl_path)
    base = os.path.splitext(os.path.basename(mdl_path))[0]
    res = Resolver(model_dir, game_dir)
    M = parse_mdl(mdl_path)
    bones, volumes = M.bones, M.volumes
    D = None
    if import_def:
        try:
            D = load_def(res, model_dir, os.path.basename(mdl_path), report)
            if D:
                add = def_bone_overrides(D, bones, report)
                report("[goh] .def: %s (+%d bones from .def)" % (os.path.basename(D.path), add))
        except Exception as e:
            import traceback; traceback.print_exc()
            report("[goh] WARNING .def failed: %s" % e); D = None
    report("[goh] %s: %d bones, %d volumes" % (base, len(bones), len(volumes)))
    scene = bpy.context.scene
    coll = collection or bpy.data.collections.new(base)
    if collection is None: scene.collection.children.link(coll)

    # ---------------- armature
    arm_data = bpy.data.armatures.new(base + "_armature")
    arm = bpy.data.objects.new(base + "_rig", arm_data)
    coll.objects.link(arm)
    arm.scale = (scale, scale, scale)
    arm.show_in_front = True
    arm_data.display_type = "STICK"
    bpy.context.view_layer.objects.active = arm
    for o in bpy.context.selected_objects: o.select_set(False)
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    uniq = {}
    for b in bones:
        n = b.name
        if n in uniq:
            uniq[n] += 1; n = "%s.%03d" % (n, uniq[n])
        else:
            uniq[n] = 0
        b.bname = n
        b.F = _reflection_fix(b.world)
        b.B = _orthonormal(b.world @ b.F)

    for b in bones:
        eb = arm_data.edit_bones.new(b.bname)
        length = 3.0
        eb.head = (0, 0, 0); eb.tail = (0, length, 0)
        eb.matrix = b.B          # sets head + orientation (keeps length)
        if b.parent is not None:
            eb.parent = arm_data.edit_bones[b.parent.bname]
    bpy.ops.object.mode_set(mode="OBJECT")
    bone_by_orig = {}
    for b in bones: bone_by_orig.setdefault(b.name, b)

    arm["goh_mdl_header"] = "\n".join(M.header)
    arm["goh_game_dir"] = res.game_dir or ""
    arm["goh_seqs_json"] = json.dumps(M.seqs)
    arm["goh_extras_json"] = json.dumps({k: v for k, v in M.extras.items() if k == "extension"})
    arm["goh_obstacles_json"] = json.dumps(M.obstacles)
    arm["goh_previews_json"] = json.dumps(M.previews)
    def _l12(m):
        R_ = m.to_3x3().transposed(); t_ = m.to_translation()
        return [R_[0][0], R_[0][1], R_[0][2], R_[1][0], R_[1][1], R_[1][2], R_[2][0], R_[2][1], R_[2][2], t_.x, t_.y, t_.z]
    arm["goh_volumes_json"] = json.dumps([{"name": v["name"], "kind": v["kind"], "arg": v["arg"], "bone": v["bone"], "local": _l12(v["local"])} for v in M.volumes])
    # bone properties (joint type, limits, speeds, parameters ...)
    for b in bones:
        pb = arm.pose.bones.get(b.bname)
        if not pb: continue
        pb.rotation_mode = "QUATERNION"
        if b.btype: pb["goh_joint"] = b.btype
        if b.limits: pb["goh_limits"] = list(b.limits)
        if b.cone is not None: pb["goh_cone_limit"] = b.cone
        if b.speed2 is not None and b.btype == "revolute": pb["goh_turn_speed_deg_per_s"] = b.speed2
        if b.speed is not None: pb["goh_speed"] = b.speed
        if b.speed2 is not None: pb["goh_speed2"] = b.speed2
        if b.terminator: pb["goh_terminator"] = True
        if b.visibility is not None: pb["goh_visibility"] = b.visibility
        for k, v in b.params.items(): _set_prop(pb, "goh_param_" + k, v)
        for k, v in b.extra.items(): _set_prop(pb, "goh_" + k, v)
        if b.visibility == 0: pb.bone.hide = True
        # data the exporter needs to write this node back exactly
        R = b.local.to_3x3().transposed(); tr = b.local.to_translation()
        pb["goh_local"] = [R[0][0], R[0][1], R[0][2], R[1][0], R[1][1], R[1][2], R[2][0], R[2][1], R[2][2], tr.x, tr.y, tr.z]
        pb["goh_F"] = -1 if b.F[2][2] < 0 else 1
        if b.bname != b.name: pb["goh_orig_name"] = b.name
        if b.views: pb["goh_views"] = json.dumps(b.views)
        if b.sequences: pb["goh_sequences"] = json.dumps(b.sequences)
        if b.uvanims: pb["goh_uvanims"] = json.dumps(b.uvanims)
        if b.extra: pb["goh_extra_json"] = json.dumps(b.extra)
        if b.raw_params: pb["goh_parameters_raw"] = b.raw_params
        if b.from_def: pb["goh_from_def"] = True

    # ---------------- meshes
    meshes_done = 0
    problems = []
    mesh_by_bone = {}
    for b in bones:
        for view, lod in b.views:
            if lod > 0 and not import_lods: continue
            ply = res.locate(model_dir, view) or res.find(view, ("",))
            if not ply: problems.append("missing " + view); continue
            if os.path.basename(view).lower().startswith("enumerator") and not import_enumerators:
                continue
            try:
                me, P = build_mesh(ply, res, os.path.splitext(os.path.basename(view))[0])
            except Exception as e:
                problems.append("%s: %s" % (view, e)); continue
            ob = bpy.data.objects.new(os.path.splitext(os.path.basename(view))[0] + ("" if b.name == "" else ""), me)
            coll.objects.link(ob)
            ARM_S = arm.matrix_world.copy()
            if P["skinw"] and P["skin"]:
                # skinned: vertex groups, armature modifier
                ob.parent = arm
                ob.matrix_basis = b.world
                vg = {}
                vert_pal = {}
                for mi, m in enumerate(P["meshes"]):
                    for t in range(m["first"], m["first"] + m["count"]):
                        for k in range(3): vert_pal.setdefault(P["idx"][t * 3 + k], m["palette"])
                bone_names = arm.data.bones
                # skin bone names refer to the original (non-uniquified) names
                for vi, (w0, idx) in enumerate(P["skinw"]):
                    pal = vert_pal.get(vi, [])
                    def gb(local):
                        # palette value 0 = model root (no bone); values 1.. index the SKIN name list
                        g = pal[local] if pal and local < len(pal) else local
                        return P["skin"][g - 1] if 1 <= g <= len(P["skin"]) else None
                    pairs = []
                    n0 = gb(idx[0]); n1 = gb(idx[1]) if idx[1] != idx[0] else None
                    if n0:
                        pairs.append((n0, w0 if n1 else 1.0))
                    if n1 and w0 < 0.9999:
                        pairs.append((n1, 1.0 - w0))
                    for nm, w in pairs:
                        bb = bone_by_orig.get(nm)
                        if bb is None or bb.bname not in bone_names: continue
                        if bb.bname not in vg: vg[bb.bname] = ob.vertex_groups.new(name=bb.bname)
                        vg[bb.bname].add([vi], max(min(w, 1.0), 0.0), "REPLACE")
                mod = ob.modifiers.new("Armature", "ARMATURE"); mod.object = arm
            else:
                ob.parent = arm; ob.parent_type = "BONE"; ob.parent_bone = b.bname
                # parent matrix at rest = arm * bone.matrix_local * T(0, length, 0)
                bone = arm.data.bones[b.bname]
                parent_rest = ARM_S @ bone.matrix_local @ Matrix.Translation((0, bone.length, 0))
                ob.matrix_parent_inverse = parent_rest.inverted()
                ob.matrix_basis = ARM_S @ b.world
            ob["goh_ply"] = view; ob["goh_bone"] = b.name; ob["goh_lod"] = lod
            if lod > 0 or b.visibility == 0:
                ob.hide_viewport = True; ob.hide_render = True
            mesh_by_bone.setdefault(b.bname, []).append(ob)
            meshes_done += 1
    report("[goh] meshes imported: %d" % meshes_done)

    # texture scrolling
    for b in bones:
        if b.uvanims:
            for ob in mesh_by_bone.get(b.bname, []):
                try: _add_uv_scroll(ob, b.uvanims)
                except Exception as e: problems.append("uv scroll %s: %s" % (b.name, e))

    # ---------------- collision volumes
    vol_objs = {}
    if import_collision and volumes:
        vcoll = bpy.data.collections.new(base + "_collision"); coll.children.link(vcoll)
        n_vol = 0
        for v in volumes:
            try:
                if v["kind"] == "polyhedron":
                    vp = res.locate(model_dir, v["arg"][0])
                    if not vp: continue
                    pos, tris, sides = parse_vol(vp)
                    me = bpy.data.meshes.new(v["name"]); me.from_pydata(pos, [], tris); me.update()
                    if sides and len(sides) == len(me.polygons):
                        att = me.attributes.new("goh_side", "INT", "FACE"); att.data.foreach_set("value", sides)
                elif v["kind"] == "box":
                    me = box_mesh(v["name"], [float(x) for x in v["arg"][:3]])
                elif v["kind"] == "cylinder":
                    me = cylinder_mesh(v["name"], float(v["arg"][0]), float(v["arg"][1]))
                else:
                    continue
                ob = bpy.data.objects.new("vol_" + v["name"], me)
                vcoll.objects.link(ob)
                ob.display_type = "WIRE"; ob.hide_render = True
                bn = bone_by_orig.get(v["bone"])
                if bn is None: bn = bones[0]
                ob.parent = arm; ob.parent_type = "BONE"; ob.parent_bone = bn.bname
                bone = arm.data.bones[bn.bname]
                ARM_S = arm.matrix_world.copy()
                ob.matrix_parent_inverse = (ARM_S @ bone.matrix_local @ Matrix.Translation((0, bone.length, 0))).inverted()
                ob.matrix_basis = ARM_S @ bn.world @ v["local"]
                ob["goh_volume"] = v["kind"]
                ob["goh_vol_name"] = v["name"]; ob["goh_vol_bone"] = v["bone"] or ""
                ob["goh_vol_arg"] = json.dumps(v["arg"]); ob["goh_vol_file"] = v["arg"][0] if v["kind"] == "polyhedron" and v["arg"] else ""
                vol_objs[v["name"]] = ob
                n_vol += 1
            except Exception as e:
                problems.append("volume %s: %s" % (v["name"], e))
        report("[goh] collision volumes: %d" % n_vol)

    # ---------------- .def / .ext properties
    if D is not None:
        try:
            apply_def_scene(D, arm, bones, bone_by_orig, vol_objs, coll, report)
        except Exception as e:
            import traceback; traceback.print_exc()
            problems.append(".def properties: %s" % e)

    # ---------------- obstacles / areas (2D shapes used for pathing, selection, ...)
    if import_obstacles and M.obstacles:
        ocoll = bpy.data.collections.new(base + "_obstacles"); coll.children.link(ocoll)
        n_ob = 0
        for d in M.obstacles:
            for si, shp in enumerate(d.get("shapes", [])):
                me = _shape2d_mesh("%s_%s_%d" % (d["kind"], d["name"], si), shp)
                if me is None: continue
                ob = bpy.data.objects.new(me.name, me); ocoll.objects.link(ob)
                ob.parent = arm; ob.display_type = "WIRE"; ob.hide_render = True
                ob["goh_kind"] = d["kind"]; ob["goh_name"] = d["name"]
                if d.get("rotate"): ob["goh_rotate"] = d["rotate"]
                n_ob += 1
        report("[goh] obstacle/area shapes: %d" % n_ob)

    # ---------------- preview camera / lights
    if import_previews and M.previews:
        n = _make_preview(base, coll, arm, M.previews, scale)
        report("[goh] preview cameras/lights: %d" % n)

    # ---------------- animations
    actions = []
    viol = set()
    if import_anims:
        todo = []   # (action name, path, meta)
        seen = set()
        for sq in M.seqs:
            p = os.path.join(model_dir, sq["file"] or (sq["name"] + ".anm"))
            if os.path.exists(p) and p not in seen: todo.append((sq["name"], p, sq)); seen.add(p)
        for b in bones:
            for sq in b.sequences:
                p = os.path.join(model_dir, sq["file"] or (sq["name"] + ".anm"))
                if os.path.exists(p) and p not in seen:
                    todo.append((os.path.splitext(sq["file"])[0] if sq["file"] else sq["name"], p, sq)); seen.add(p)
        for p in glob.glob(os.path.join(glob.escape(model_dir), "*.anm")):
            if p not in seen: todo.append((os.path.splitext(os.path.basename(p))[0], p, None)); seen.add(p)
        if not arm.animation_data: arm.animation_data_create()
        for nm, p, meta in todo:
            try:
                act = import_anm(arm, bones, bone_by_orig, p, nm, meta, apply_speed, viol)
                if act: actions.append(act)
            except Exception as e:
                problems.append("anm %s: %s" % (os.path.basename(p), e))
        arm.animation_data.action = None
        report("[goh] actions: %s" % [a.name for a in actions])

    if anim_library:
        try:
            reg = load_anim_registry(res, M.extras.get("extension") or "/properties/animation/human/human_anm.ext")
            import_anim_library(arm, bones, bone_by_orig, res, anim_library, report, registry=reg)
        except Exception as e:
            problems.append("animation library: %s" % e)

    # ---------------- joint limits -> constraints
    n_con = 0
    if joint_limits:
        for b in bones:
            if b.btype == "socket" and b.cone:
                pbs = arm.pose.bones.get(b.bname)
                if pbs is not None:
                    c = pbs.constraints.new("LIMIT_ROTATION"); c.name = "GoH socket cone limit"; c.owner_space = "LOCAL"
                    c.use_limit_x = c.use_limit_y = True
                    c.min_x = c.min_y = -math.radians(b.cone); c.max_x = c.max_y = math.radians(b.cone)
                    c.mute = b.bname in viol
                    pbs["goh_limit_note"] = "ball joint: cone of +-%g deg about local X/Y (approximation)" % b.cone
                    n_con += 1
        for b in bones:
            pb = arm.pose.bones.get(b.bname)
            if pb is None or not b.limits or b.btype not in ("revolute", "prismatic"): continue
            lo, hi = b.limits
            muted = b.bname in viol
            if b.btype == "revolute":
                if b.F[2][2] < 0: lo, hi = -hi, -lo      # mirrored node -> local Z flipped
                c = pb.constraints.new("LIMIT_ROTATION"); c.name = "GoH revolute limit"
                c.owner_space = "LOCAL"
                c.use_limit_x = c.use_limit_y = True; c.min_x = c.max_x = c.min_y = c.max_y = 0.0
                c.use_limit_z = True; c.min_z = math.radians(lo); c.max_z = math.radians(hi)
            else:
                # prizmatic joints slide along the local Y axis; limits are metres (20 game units = 1 m)
                c = pb.constraints.new("LIMIT_LOCATION"); c.name = "GoH prismatic limit"
                c.owner_space = "LOCAL"
                c.use_min_x = c.use_max_x = c.use_min_z = c.use_max_z = True
                c.min_x = c.max_x = c.min_z = c.max_z = 0.0
                c.use_min_y = c.use_max_y = True; c.min_y = lo * 20.0; c.max_y = hi * 20.0
            c.mute = muted
            if muted: pb["goh_limit_muted_reason"] = "imported animation leaves these limits"
            n_con += 1
        report("[goh] joint limit constraints: %d (muted because animations exceed them: %d)" % (n_con, len(viol)))

    arm["goh_model"] = mdl_path
    if M.extras: arm["goh_model_extras"] = str(M.extras)
    for pr in problems: report("[goh] WARNING " + pr)
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    return arm


def _norm_anim(name):
    n = name.replace("\\", "/").strip().lower()
    return n[:-4] if n.endswith(".anm") else n


def load_anim_registry(res, ext_path):
    """{sequence "name" {file "dir\\x"}{speed n}{smooth n}{resume}{events {11 "leg_left"}...}} entries of an animation .ext
       (e.g. /properties/animation/human/human_anm.ext). Returns {"by_name": {...}, "by_file": {...}}; a 'file' may be one .anm or a
       folder of variants."""
    reg = {"by_name": {}, "by_file": {}}
    try:
        loader = make_sdl_loader(res)
        got = loader(res.model_dir, ext_path)
        if not got: return reg
        env = SdlEnv(loader)
        tree = sdl_expand(sdl_parse(got[0]), env, got[1])
    except Exception as e:
        print("[goh] animation registry failed:", e); return reg

    def walk(items):
        for x in items:
            if not isinstance(x, SBlock): continue
            if x.name == "sequence" and len(x.head) > 1:
                d = {"name": x.head[1], "props": {}, "events": [], "resume": False, "speed": None, "file": None}
                for k in x.items:
                    if not isinstance(k, SBlock): continue
                    if k.name == "file" and len(k.head) > 1: d["file"] = k.head[1]
                    elif k.name == "speed":
                        v = _f(k.head[1:2]); d["speed"] = v[0] if v else None
                    elif k.name == "resume": d["resume"] = True
                    elif k.name == "events":
                        for e in k.blocks():
                            fr = _f(e.head[:1])
                            if fr and len(e.head) > 1: d["events"].append((fr[0], e.head[1]))
                    else:
                        d["props"][k.name] = " ".join(k.head[1:])
                reg["by_name"][_norm_anim(x.head[1])] = d
                if d["file"]: reg["by_file"].setdefault(_norm_anim(d["file"]), d)
            walk(x.items)
    walk(tree)
    return reg


def _reg_lookup(registry, nm):
    if not registry: return None
    bf, bn = registry.get("by_file", {}), registry.get("by_name", {})
    if nm in bf: return bf[nm]
    parts = nm.split("/")
    for i in range(len(parts) - 1, 0, -1):            # the file entry may be a folder of variants
        if "/".join(parts[:i]) in bf: return bf["/".join(parts[:i])]
    if nm in bn: return bn[nm]
    stem = parts[-1]
    return bn.get(stem)


def import_anim_library(arm, bones, bone_by_orig, res, which, report=print, apply_speed=True, folder="properties/animation/human/", registry=None,
                        min_match=0.6, per_prefix=3, max_total=400):
    """Import shared animations from the game's properties.pak (e.g. the 1600 human animations).
       which: "basic"  - a handful per category (idle / walk / run / crawl / die / lie / squat / stand / flinch ...)
              "all"    - everything that fits this skeleton
              "<regex>" - names (without .anm) matching the regular expression
       Only animations whose bones mostly exist on this armature are imported. Actions are called  lib_<name>."""
    idx = res._pak_index()
    entries = []
    f0 = folder.lower()
    for key, (pak, name, virtual) in idx.items():
        if key.startswith("*/") or not key.startswith(f0) or not key.endswith(".anm"): continue
        entries.append((os.path.splitext(key[len(f0):])[0], (pak, name, virtual)))    # rel path incl. sub folders
    entries.sort()
    # loose files in a mod's resource/properties folder override / extend
    for rd in res.resource_dirs:
        d = os.path.join(rd, folder.replace("/", os.sep))
        if os.path.isdir(d):
            for r0, _ds, fs in os.walk(d):
                for fn in sorted(fs):
                    if fn.lower().endswith(".anm"):
                        rel = os.path.relpath(os.path.join(r0, fn), d).replace("\\", "/").lower()
                        entries.append((os.path.splitext(rel)[0], os.path.join(r0, fn)))
    if which == "basic":
        # a soldier's everyday animations only (not vehicle / gun crews, cinematics, ...): a few per category
        ok_dirs = ("idle", "locomotion", "death", "flinch", "cover_stand", "throw", "paratroopers")
        ok_pre = ("idle", "walk", "run", "crawl", "die", "lie", "squat", "stand", "flinch", "fight", "swim", "cover", "jog", "sprint")
        chosen = []; cnt = {}
        for nm, src in entries:
            if "/" in nm:
                cat = nm.split("/")[0]
                if cat not in ok_dirs: continue
                cat = "/".join(nm.split("/")[:2]) if cat in ("death", "locomotion") else cat
            else:
                cat = re.split(r"[_\-]", nm)[0]
                if cat not in ok_pre: continue
            if cnt.get(cat, 0) < per_prefix:
                chosen.append((nm, src)); cnt[cat] = cnt.get(cat, 0) + 1
    elif which == "all":
        chosen = entries
    else:
        rx = re.compile(which, re.I)
        chosen = [(nm, s) for nm, s in entries if rx.search(nm)]
    if not arm.animation_data: arm.animation_data_create()
    n = 0; skipped = 0
    names_on_rig = set(bone_by_orig)
    for nm, src in chosen[:max_total]:
        try:
            path = src if isinstance(src, str) else res._extract(*src)
            bn, _frames = parse_anm(path)
            hit = sum(1 for b in bn if b in names_on_rig)
            if not bn or hit / float(len(bn)) < min_match or hit == 0:
                skipped += 1; continue
            meta = None
            rd = _reg_lookup(registry, nm)
            if rd:
                meta = {"props": dict(rd["props"], sequence=rd["name"]), "speed": rd["speed"], "resume": rd["resume"], "events": rd["events"]}
            act = import_anm(arm, bones, bone_by_orig, path, ("lib_" + nm.replace("/", "."))[:60], meta, apply_speed, None)
            if act: act["goh_library"] = folder; n += 1
        except Exception as e:
            skipped += 1
    arm.animation_data.action = None
    report("[goh] animation library: %d imported, %d skipped (bones not on this rig)" % (n, skipped))
    return n


def _fcurve_factory(act, owner):
    """create F-curves directly (about 1000x faster than keyframe_insert); works with slotted actions (4.4+) and legacy ones."""
    try:
        if hasattr(act, "slots"):
            from bpy_extras import anim_utils
            slot = act.slots.new(id_type="OBJECT", name=owner.name)
            cb = anim_utils.action_ensure_channelbag_for_slot(act, slot)
            return lambda path, idx, grp: cb.fcurves.new(path, index=idx, group_name=grp)
    except Exception as e:
        print("[goh] slotted action API failed, using legacy:", e)
    return lambda path, idx, grp: act.fcurves.new(path, index=idx, action_group=grp)


def import_anm(arm, bones, bone_by_orig, path, action_name, meta=None, apply_speed=True, viol=None, fps=30):
    names, frames = parse_anm(path)
    act = bpy.data.actions.new(action_name)
    act.use_fake_user = True
    speed = (meta or {}).get("speed") or 1.0
    k = (1.0 / speed) if (apply_speed and speed > 0) else 1.0
    cur = {}
    for n in names:
        b = bone_by_orig.get(n)
        if b is None: continue
        l = b.local
        # animation rotations are proper rotations: for a mirrored node the stored quaternion belongs to (local @ flipZ)
        floc = Matrix.Diagonal((1, 1, -1, 1)) if l.to_3x3().determinant() < 0 else Matrix.Identity(4)
        cur[n] = [l.to_translation(), _rot_of(l @ floc)]
    keys = {}            # bone name -> [(frame, loc, quat)]
    prevq = {}
    maxf = 0.0
    for fr, ents in frames:
        touched = []
        for bi, pos, quat in ents:
            if bi >= len(names): continue
            n = names[bi]
            if n not in cur: continue
            if pos is not None: cur[n][0] = Vector(pos)
            if quat is not None: cur[n][1] = quat
            if n not in touched: touched.append(n)
        fno = 1 + fr * k
        for n in touched:
            b = bone_by_orig[n]
            if b.bname not in arm.pose.bones: continue
            L = Matrix.Translation(cur[n][0]) @ cur[n][1].to_matrix().to_4x4()
            floc = Matrix.Diagonal((1, 1, -1, 1)) if b.local.to_3x3().determinant() < 0 else Matrix.Identity(4)
            G = floc @ b.F                       # local mirror fix * world mirror fix (both are diag(1,1,+-1), they commute)
            basis = G @ (b.local @ floc).inverted() @ L @ G
            loc, rot, _s = basis.decompose()
            pq = prevq.get(b.bname)
            if pq is not None and pq.dot(rot) < 0: rot = Quaternion((-rot.w, -rot.x, -rot.y, -rot.z))   # keep interpolation on the short arc
            prevq[b.bname] = rot
            keys.setdefault(b.bname, []).append((fno, loc, rot))
            maxf = max(maxf, fno)
            if viol is not None and b.limits and b.btype in ("revolute", "prismatic"):
                if b.btype == "revolute":
                    e = rot.to_euler(); lo, hi = b.limits
                    if b.F[2][2] < 0: lo, hi = -hi, -lo
                    if abs(e.x) > 0.03 or abs(e.y) > 0.03 or e.z < math.radians(lo) - 0.03 or e.z > math.radians(hi) + 0.03:
                        viol.add(b.bname)
                else:
                    lo, hi = b.limits[0] * 20.0, b.limits[1] * 20.0
                    if abs(loc.x) > 0.05 or abs(loc.z) > 0.05 or loc.y < lo - 0.05 or loc.y > hi + 0.05:
                        viol.add(b.bname)
    mk = _fcurve_factory(act, arm)
    for bname, ks in keys.items():
        n = len(ks)
        for ch in range(7):
            path_ = 'pose.bones["%s"].%s' % (bname, "location" if ch < 3 else "rotation_quaternion")
            idx = ch if ch < 3 else ch - 3
            fc = mk(path_, idx, bname)
            fc.keyframe_points.add(n)
            co = []
            for fno, loc, rot in ks:
                v = loc[idx] if ch < 3 else rot[idx]
                co.append(float(fno)); co.append(float(v))
            fc.keyframe_points.foreach_set("co", co)
            fc.keyframe_points.foreach_set("interpolation", [1] * n)      # LINEAR
            fc.update()
    act["goh_frames"] = maxf
    act["goh_source"] = os.path.basename(path)
    if meta:
        if meta.get("speed") is not None: act["goh_speed"] = meta["speed"]
        act["goh_speed_applied"] = bool(apply_speed)
        act["goh_resume"] = bool(meta.get("resume")); act["goh_autostart"] = bool(meta.get("autostart"))
        for pk, pv in (meta.get("props") or {}).items(): _set_prop(act, "goh_" + pk, pv)
        for ef, en in meta.get("events", []):
            act.pose_markers.new(en).frame = int(round(1 + ef * k))
    return act


def _rot_of(m):
    return m.to_3x3().to_quaternion()


# =====================================================================================================
#  EXPORT  (Blender -> .mdl / .ply / .anm / .mtl / .vol / .def)
# =====================================================================================================
MAX_SKIN_BONES = 22          # the game's skinned meshes never use more than 22 bones (palette size)


def _n(v):
    """number as text, short but lossless enough for game data"""
    if abs(v) < 1e-9: return "0"
    s = "%.7g" % v
    return s


def _action_fcurves(act):
    try:
        fcs = act.fcurves
        if fcs is not None and len(fcs): return list(fcs)
    except Exception:
        pass
    out = []
    try:
        for layer in act.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
    except Exception:
        pass
    return out


def _l12_to_matrix(v):
    """12 floats (file rows R, translation) -> column-convention 4x4 local matrix"""
    R = Matrix(((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8])))
    return Matrix.Translation(Vector(v[9:12])) @ R.transposed().to_4x4()


def _matrix_to_l12(m):
    R = m.to_3x3().transposed(); t = m.to_translation()
    return [R[0][0], R[0][1], R[0][2], R[1][0], R[1][1], R[1][2], R[2][0], R[2][1], R[2][2], t.x, t.y, t.z]


def _close(a, b, eps=1e-4):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def _xform_lines(v12, root, indent):
    """{Position} / {orientation} / {matrix34} text for a node"""
    pad = "\t" * indent
    R = v12[:9]; t = v12[9:]
    ident = _close(R, [1, 0, 0, 0, 1, 0, 0, 0, 1], 1e-6)
    zero_t = all(abs(x) < 1e-9 for x in t)
    if ident and zero_t: return []
    if ident: return [pad + "{Position %s}" % " ".join(_n(x) for x in t)]
    rows = ["%s\t%s" % (pad, " ".join(_n(x) for x in R[i * 3:i * 3 + 3])) for i in range(3)]
    if root and zero_t:
        return [pad + "{orientation"] + rows + [pad + "}"]
    return [pad + "{matrix34"] + rows + ["%s\t%s" % (pad, " ".join(_n(x) for x in t)), pad + "}"]


# ---------------------------------------------------------------------------------- .dds
def _np():
    import numpy as np
    return np


def _resize_axis(a, n, axis):
    """linear resample along one axis (used for mip levels of odd sizes)"""
    np = _np()
    m = a.shape[axis]
    if m == n: return a
    src = (np.arange(n) + 0.5) * m / n - 0.5
    i0 = np.clip(np.floor(src).astype(int), 0, m - 1); i1 = np.clip(i0 + 1, 0, m - 1)
    f = (src - np.floor(src)).reshape([-1 if d == axis else 1 for d in range(a.ndim)])
    return np.take(a, i0, axis=axis) * (1 - f) + np.take(a, i1, axis=axis) * f


def _mip_chain(rgba):
    np = _np()
    levels = [rgba]
    h, w = rgba.shape[:2]
    while w > 1 or h > 1:
        nh, nw = max(h // 2, 1), max(w // 2, 1)
        cur = levels[-1]
        if h % 2 == 0 and w % 2 == 0 and h > 1 and w > 1:
            nxt = (cur[0::2, 0::2] + cur[1::2, 0::2] + cur[0::2, 1::2] + cur[1::2, 1::2]) * 0.25
        else:
            nxt = _resize_axis(_resize_axis(cur, nh, 0), nw, 1)
        levels.append(nxt); h, w = nh, nw
    return levels


def _blocks(level):
    """(h, w, 4) float 0..255 -> (n_blocks, 16, 4), edges padded to a multiple of 4"""
    np = _np()
    h, w = level.shape[:2]
    ph, pw = (4 - h % 4) % 4, (4 - w % 4) % 4
    if ph or pw: level = np.pad(level, ((0, ph), (0, pw), (0, 0)), mode="edge")
    H, W = level.shape[:2]
    b = level.reshape(H // 4, 4, W // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    return b


def _rgb565(c):
    np = _np()
    c = np.clip(np.rint(c), 0, 255).astype(np.int32)
    return ((c[:, 0] * 31 + 127) // 255 << 11) | ((c[:, 1] * 63 + 127) // 255 << 5) | ((c[:, 2] * 31 + 127) // 255)


def _unrgb565(v):
    np = _np()
    r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
    return np.stack([r * 255.0 / 31, g * 255.0 / 63, b * 255.0 / 31], axis=1)


def _dxt_color_blocks(b):
    """b: (n,16,4) -> (n, 8 bytes) colour part of DXT1/DXT5 (always the 4-colour mode)"""
    np = _np()
    rgb = b[:, :, :3]
    mn = rgb.min(axis=1); mx = rgb.max(axis=1)
    inset = (mx - mn) / 16.0
    hi = mx - inset; lo = mn + inset
    c0 = _rgb565(hi); c1 = _rgb565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    flat = c0 == c1                                  # c0 == c1 would select the 3-colour mode: nudge so index 0..3 stay valid
    p0 = _unrgb565(c0); p1 = _unrgb565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3.0, (p0 + 2 * p1) / 3.0], axis=1)         # (n,4,3)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(axis=3)                   # (n,16,4)
    idx = d.argmin(axis=2).astype(np.uint32)                                           # (n,16)
    packed = np.zeros(len(b), dtype=np.uint32)
    for i in range(16): packed |= idx[:, i] << (2 * i)
    out = np.zeros((len(b), 8), dtype=np.uint8)
    out[:, 0] = c0 & 255; out[:, 1] = (c0 >> 8) & 255; out[:, 2] = c1 & 255; out[:, 3] = (c1 >> 8) & 255
    for k in range(4): out[:, 4 + k] = (packed >> (8 * k)) & 255
    return out


def _dxt5_alpha_blocks(b):
    np = _np()
    a = b[:, :, 3]
    a0 = np.clip(np.rint(a.max(axis=1)), 0, 255).astype(np.int32); a1 = np.clip(np.rint(a.min(axis=1)), 0, 255).astype(np.int32)
    swap = a0 < a1
    a0, a1 = np.where(swap, a1, a0), np.where(swap, a0, a1)
    pal = np.zeros((len(b), 8)); pal[:, 0] = a0; pal[:, 1] = a1
    for i in range(1, 7): pal[:, 1 + i] = ((7 - i) * a0 + i * a1) / 7.0           # 8-value mode (a0 > a1)
    eq = a0 == a1
    d = np.abs(a[:, :, None] - pal[:, None, :])
    idx = d.argmin(axis=2).astype(np.uint64)
    bits = np.zeros(len(b), dtype=np.uint64)
    for i in range(16): bits |= idx[:, i] << np.uint64(3 * i)
    out = np.zeros((len(b), 8), dtype=np.uint8)
    out[:, 0] = a0; out[:, 1] = a1
    for k in range(6): out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
    return out


def encode_dds(rgba, mips=True):
    """rgba: float array (h, w, 4) in 0..1, top row first.  DXT1 when fully opaque, DXT5 otherwise (what the game's textures use)."""
    np = _np()
    a = np.asarray(rgba, dtype=np.float64) * 255.0
    h, w = a.shape[:2]
    alpha = bool((a[:, :, 3] < 254.5).any())
    levels = _mip_chain(a) if mips else [a]
    data = bytearray()
    first_size = 0
    for li, lv in enumerate(levels):
        b = _blocks(lv)
        col = _dxt_color_blocks(b)
        blk = np.concatenate([_dxt5_alpha_blocks(b), col], axis=1) if alpha else col
        raw = blk.tobytes()
        if li == 0: first_size = len(raw)
        data += raw
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips else 0)
    caps = 0x1000 | ((0x8 | 0x400000) if mips else 0)
    hdr = b"DDS " + struct.pack("<7I", 124, flags, h, w, first_size, 0, len(levels) if mips else 0) + b"\x00" * 44
    hdr += struct.pack("<2I4s5I", 32, 4, b"DXT5" if alpha else b"DXT1", 0, 0, 0, 0, 0)
    hdr += struct.pack("<5I", caps, 0, 0, 0, 0)
    return bytes(hdr) + bytes(data)


def image_to_dds(img, path, mips=True):
    """write a Blender image as a game .dds (DXT1/DXT5 with mipmaps)"""
    np = _np()
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    rgba = np.flipud(buf.reshape(h, w, 4))             # Blender stores the bottom row first
    open(path, "wb").write(encode_dds(rgba, mips))
    return path


# ---------------------------------------------------------------------------------- .mtl
def write_mtl(path, mat):
    kind = mat.get("goh_kind") or ("bump" if any(n.type == "NORMAL_MAP" for n in mat.node_tree.nodes) else "simple") if mat.use_nodes else "simple"
    L = ["{material %s" % kind]
    for key in ("diffuse", "bump", "specular"):
        v = mat.get("goh_" + key)
        if v: L.append('\t{%s "%s"}' % (key, v))
    if mat.get("goh_extra"):
        for k, v in json.loads(mat["goh_extra"]): L.append('	{%s "%s"}' % (k, v))
    col = mat.get("goh_color")
    if col is not None:
        L.append('\t{color "%s"}' % " ".join(str(int(round(x))) for x in col))
    elif mat.get("goh_color_raw"):
        L.append('\t{color "%s"}' % mat["goh_color_raw"])
    bl = mat.get("goh_blend")
    if bl: L.append("\t{blend %s}" % bl)
    L.append("}")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


def _tex_stem(img):
    return img.get("goh_name") or os.path.splitext(os.path.basename(img.filepath or img.name))[0] or img.name


def _save_new_texture(mat, out_dir, report):
    """textures made or changed in Blender -> .dds next to the model (DXT1 / DXT5 with mipmaps, like the game's own).
       A material without game texture names gets them from its image; a changed image (unsaved edit, or custom property
       goh_export on the image) is written under its name (custom property goh_name overrides the file name)."""
    if not mat.use_nodes: return []
    out = []
    fresh = not any(mat.get("goh_" + k) for k in ("diffuse", "bump", "specular"))
    for n in mat.node_tree.nodes:
        if n.type != "TEX_IMAGE" or not n.image: continue
        img = n.image
        changed = img.is_dirty or bool(img.get("goh_export"))
        if not (fresh or changed): continue
        links = [l.to_node for o in n.outputs for l in o.links]
        kind = "bump" if any(x.type == "NORMAL_MAP" for x in links) else ("specular" if any(x.type == "RGB_TO_BW" for x in links) else "diffuse")
        if fresh and kind == "specular" and not any(x.type in ("RGB_TO_BW",) for x in links): continue
        stem = _tex_stem(img)
        try:
            image_to_dds(img, os.path.join(out_dir, stem + ".dds"))
            if fresh or changed: mat["goh_" + kind] = img.get("goh_ref") or stem
            out.append(os.path.join(out_dir, stem + ".dds"))
            report("[goh] saved texture %s.dds (%dx%d)" % (stem, img.size[0], img.size[1]))
        except Exception as e:
            report("[goh] WARNING texture %s not saved: %s" % (img.name, e))
    return out


# ---------------------------------------------------------------------------------- .vol
def write_vol(path, obj):
    me = obj.data
    me.calc_loop_triangles()
    nv = len(me.vertices)
    out = bytearray(b"EVLM" + b"VERT" + struct.pack("<I", nv))
    for v in me.vertices: out += struct.pack("<3f", *v.co)
    tris = [(t.vertices[0], t.vertices[1], t.vertices[2], t.polygon_index) for t in me.loop_triangles]
    out += b"INDX" + struct.pack("<I", len(tris) * 3)
    for a, b, c, _p in tris: out += struct.pack("<3H", a, b, c)
    att = me.attributes.get("goh_side")
    sides = [1] * len(tris)
    if att is not None and len(att.data) == len(me.polygons):
        sides = [max(0, min(255, att.data[pi].value)) for *_x, pi in tris]
    out += b"SIDE" + struct.pack("<I", len(tris)) + bytes(sides)
    open(path, "wb").write(bytes(out))


# ---------------------------------------------------------------------------------- .ply
def _default_hdr(skinned, layout_a, mat):
    bump = bool(mat and mat.get("goh_bump")); spec = bool(mat and mat.get("goh_specular")); color = mat is not None and mat.get("goh_color") is not None
    if skinned:
        return {"a": 4376, "d": 3860 if color else 3349}
    return {"a": layout_a, "d": 0x404 | (0x100 if bump else 0) | (0x200 if spec else 0)}


def write_ply(path, obj, arm, report=print):
    """write one mesh object as a game .ply. Vertex order of imported meshes is kept, so an unchanged mesh round-trips."""
    me = obj.data
    me.calc_loop_triangles()
    if not me.loop_triangles:
        raise ValueError("mesh %s has no triangles" % obj.name)
    uvl = me.uv_layers.get("UVMap") or me.uv_layers.active or (me.uv_layers[0] if me.uv_layers else None)
    uvl2 = me.uv_layers.get("UV2")
    try: normals = [c.vector for c in me.corner_normals]
    except Exception:
        me.calc_normals_split(); normals = [l.normal for l in me.loops]
    # ---- skin data
    arm_bones = {b.name: b for b in arm.data.bones}
    def oname(bn):
        pb = arm.pose.bones.get(bn)
        return pb.get("goh_orig_name", bn) if pb else bn
    gnames = {g.index: g.name for g in obj.vertex_groups if g.name in arm_bones}
    skinned = bool(gnames) and any(m.type == "ARMATURE" for m in obj.modifiers)
    # tangents: Blender's sign is the game's sign for skinned meshes and the opposite for rigid ones (verified on the game files)
    tangents = None
    if uvl is not None:
        try:
            me.calc_tangents(uvmap=uvl.name)
            sg = 1.0 if skinned else -1.0
            tangents = [(l.tangent[0], l.tangent[1], l.tangent[2], sg * l.bitangent_sign) for l in me.loops]
        except Exception as e:
            report("[goh] tangents for %s could not be computed (%s)" % (obj.name, e))
    skin_names = json.loads(me.get("goh_skin", "[]")) if skinned else []
    infl = {}
    if skinned:
        for v in me.vertices:
            ws = sorted(((g.weight, oname(gnames[g.group])) for g in v.groups if g.group in gnames and g.weight > 1e-6), reverse=True)[:2]
            s = sum(w for w, _n_ in ws)
            infl[v.index] = [(w / s, nm) for w, nm in ws] if s > 0 else []
        used = []
        for v in infl.values():
            for _w, nm in v:
                if nm not in skin_names: skin_names.append(nm)
                if nm not in used: used.append(nm)
        glob_idx = {nm: i + 1 for i, nm in enumerate(skin_names)}
        palette = [0] + sorted(glob_idx[nm] for nm in used)
        if len(palette) > MAX_SKIN_BONES:
            report("[goh] WARNING %s uses %d bones; the game's own skinned meshes use at most %d - split the mesh" % (obj.name, len(palette), MAX_SKIN_BONES))
        local = {g: i for i, g in enumerate(palette)}
    # ---- unique vertices (order: by Blender vertex index, so imported meshes keep their order)
    by_vert = collections.defaultdict(list)
    for t in me.loop_triangles:
        for li in t.loops: by_vert[me.loops[li].vertex_index].append(li)
    verts = []; loop_to_idx = {}; tacc = []
    for vi in sorted(by_vert):
        mine = []          # exported vertices made from this Blender vertex: (index, normal, uv, uv2)
        for li in sorted(set(by_vert[vi])):
            nrm = normals[li]
            uv = uvl.data[li].uv if uvl is not None else (0.0, 0.0)
            u2 = uvl2.data[li].uv if uvl2 is not None else (0.0, 0.0)
            idx = None
            for j, n0, uv0, u20 in mine:      # same vertex with (nearly) the same normal + uv -> one exported vertex
                if ((n0[0] - nrm[0]) ** 2 + (n0[1] - nrm[1]) ** 2 + (n0[2] - nrm[2]) ** 2 < 4e-4 and abs(uv0[0] - uv[0]) < 2e-4
                        and abs(uv0[1] - uv[1]) < 2e-4 and abs(u20[0] - u2[0]) < 2e-4 and abs(u20[1] - u2[1]) < 2e-4):
                    idx = j; break
            if idx is None:
                idx = len(verts); mine.append((idx, (nrm[0], nrm[1], nrm[2]), (uv[0], uv[1]), (u2[0], u2[1])))
                verts.append((vi, tuple(nrm), (uv[0], 1.0 - uv[1]), None, (u2[0], 1.0 - u2[1]))); tacc.append([0.0, 0.0, 0.0, 0.0])
            loop_to_idx[li] = idx
            if tangents:
                tg = tangents[li]; a = tacc[idx]
                a[0] += tg[0]; a[1] += tg[1]; a[2] += tg[2]; a[3] += tg[3]
    for i, (vi, nrm, uv, _tg, u2) in enumerate(verts):
        a = tacc[i]
        if tangents:
            L_ = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) or 1.0
            verts[i] = (vi, nrm, uv, (a[0] / L_, a[1] / L_, a[2] / L_, 1.0 if a[3] >= 0 else -1.0), u2)
        else:
            verts[i] = (vi, nrm, uv, (1.0, 0.0, 0.0, -1.0), u2)
    if len(verts) > 65535:
        raise ValueError("%s has %d vertices; the format allows 65535 per mesh" % (obj.name, len(verts)))
    # ---- triangles grouped by material slot
    slots = max(len(me.materials), 1)
    groups = [[] for _ in range(slots)]
    for t in me.loop_triangles:
        groups[min(t.material_index, slots - 1)].append(tuple(loop_to_idx[l] for l in t.loops))
    hdrs = json.loads(me.get("goh_mesh_hdr", "[]"))
    col_att = me.attributes.get("goh_color32")
    default_a = 0x112 | (0x40 if col_att is not None else 0) | (0x200 if uvl2 is not None else 0)
    slot_hdr = {}
    for si, g in enumerate(groups):
        if not g: continue
        mat = me.materials[si] if si < len(me.materials) else None
        slot_hdr[si] = hdrs[si] if si < len(hdrs) and "a" in hdrs[si] else _default_hdr(skinned, default_a, mat)
    first_hdr = next(iter(slot_hdr.values()))
    stride_in = me.get("goh_stride")
    if skinned:
        stride = 40 if stride_in == 40 else 56
        L = ply_layout(stride, first_hdr["a"], first_hdr["d"], True)
    else:
        L = ply_layout(1 << 30, first_hdr["a"], first_hdr["d"], False)
        stride = max(L["end"], stride_in if (stride_in and stride_in >= L["end"] and stride_in <= L["end"] + 16) else 0)
    # ---- bytes
    pts = [me.vertices[v[0]].co for v in verts]
    mn = [min(p[i] for p in pts) for i in range(3)]; mx = [max(p[i] for p in pts) for i in range(3)]
    out = bytearray(b"EPLY" + b"BNDS" + struct.pack("<6f", *mn, *mx))
    if skinned:
        out += b"SKIN" + struct.pack("<I", len(skin_names))
        for nm in skin_names:
            b = nm.encode("latin-1", "replace"); out += bytes([len(b)]) + b
    first = 0; tri_all = []
    for si, g in enumerate(groups):
        if not g: continue
        mat = me.materials[si] if si < len(me.materials) else None
        hdr = slot_hdr[si]
        mtl = (mat.get("goh_mtl") if mat else None) or ((mat.name if mat else "default") + ".mtl")
        if mat and not mat.get("goh_mtl") and not mtl.lower().endswith(".mtl"): mtl += ".mtl"
        out += b"MESH" + struct.pack("<4I", hdr["a"], first, len(g), hdr["d"])
        col = hdr.get("color") if "color" in hdr else None
        if col is None and "color" not in hdr and mat is not None and mat.get("goh_color") is not None:
            col = bytes(int(max(0, min(255, round(x)))) for x in (list(mat["goh_color"]) + [255])[:4]).hex()
        if col: out += bytes.fromhex(col)
        b = mtl.encode("latin-1", "replace"); out += bytes([len(b)]) + b
        if skinned: out += bytes([len(palette)]) + bytes(palette)
        first += len(g); tri_all.extend(g)
    out += b"VERT" + struct.pack("<IHH", len(verts), stride, 7)
    for vi, nrm, uv, tg, u2 in verts:
        co = me.vertices[vi].co
        if skinned:
            inf = infl.get(vi, [])
            w0 = inf[0][0] if inf else 1.0
            i0 = local[glob_idx[inf[0][1]]] if inf else 0
            i1 = local[glob_idx[inf[1][1]]] if len(inf) > 1 else 0
            if len(inf) == 1: w0 = 1.0
            out += struct.pack("<3ff4B3f2f", co[0], co[1], co[2], w0, i0, i1, 0, 0, nrm[0], nrm[1], nrm[2], uv[0], uv[1])
            if stride >= 56: out += struct.pack("<4f", *tg)
        else:
            rec = struct.pack("<3f", co[0], co[1], co[2])
            if L["n"] is not None: rec += struct.pack("<3f", nrm[0], nrm[1], nrm[2])
            if L["col"] is not None: rec += struct.pack("<i", col_att.data[vi].value if col_att is not None else -1)
            rec += struct.pack("<2f", uv[0], uv[1])
            if L["uv2"] is not None: rec += struct.pack("<2f", u2[0], u2[1])
            if L["tan"] is not None: rec += struct.pack("<4f", *tg)
            out += rec + b"\x00" * (stride - len(rec))
    out += b"INDX" + struct.pack("<I", len(tri_all) * 3)
    for a, b2, c in tri_all: out += struct.pack("<3H", a, b2, c)
    open(path, "wb").write(bytes(out))
    try: me.free_tangents()
    except Exception: pass
    return {"verts": len(verts), "tris": len(tri_all), "skinned": skinned, "stride": stride}


# ---------------------------------------------------------------------------------- .anm
def write_anm(path, act, arm, report=print):
    pat = re.compile(r'pose\.bones\["(.+)"\]\.(location|rotation_quaternion)$')
    by_bone = collections.OrderedDict()
    for fc in _action_fcurves(act):
        m = pat.match(fc.data_path)
        if not m: continue
        by_bone.setdefault(m.group(1), {})[(m.group(2), fc.array_index)] = fc
    speed = act.get("goh_speed") if act.get("goh_speed_applied") else None
    k = float(speed) if speed else 1.0           # file frame = (blender frame - 1) * speed
    ents = []        # (file_frame, bone_index, flags, pos|None, quat|None)
    names = []
    for bname, ch in by_bone.items():
        pb = arm.pose.bones.get(bname)
        if pb is None or "goh_local" not in pb.keys(): continue
        names.append(pb.get("goh_orig_name", bname))
        bi = len(names) - 1
        L0 = _l12_to_matrix(list(pb["goh_local"]))
        floc = Matrix.Diagonal((1, 1, -1, 1)) if L0.to_3x3().determinant() < 0 else Matrix.Identity(4)
        G = floc @ Matrix.Diagonal((1, 1, pb.get("goh_F", 1), 1))
        frames = sorted({round(kp.co[0], 4) for fc in ch.values() for kp in fc.keyframe_points})
        prev_pos = prev_rot = None
        for f in frames:
            loc = Vector([ch[("location", i)].evaluate(f) if ("location", i) in ch else 0.0 for i in range(3)])
            q = Quaternion([ch[("rotation_quaternion", i)].evaluate(f) if ("rotation_quaternion", i) in ch else (1.0 if i == 0 else 0.0) for i in range(4)])
            q.normalize()
            basis = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
            La = (L0 @ floc) @ G @ basis @ G
            pos = La.to_translation(); rot = La.to_3x3().to_quaternion(); rot.normalize()
            if rot.w < 0: rot = Quaternion((-rot.w, -rot.x, -rot.y, -rot.z))
            ff = int(round((f - 1.0) * k))
            flags = 3 | (8 if prev_pos is None else 0)
            ents.append((ff, bi, flags, tuple(pos), (rot.x, rot.y, rot.z)))
            prev_pos = pos; prev_rot = rot
    if not ents:
        return False
    frames = collections.OrderedDict()
    for e in sorted(ents, key=lambda e: (e[0], e[1])): frames.setdefault(e[0], []).append(e)
    out = bytearray(b"EANM" + bytes([1, 0, 6, 0]) + b"FRMS" + struct.pack("<I", max(frames) + 1) + b"BMAP" + struct.pack("<I", len(names)))
    for nm in names:
        b = nm.encode("latin-1", "replace"); out += struct.pack("<I", len(b)) + b
    for ff, es in frames.items():
        out += b"FRM2" + struct.pack("<HB", ff, min(len(es), 255))
        for _ff, bi, flags, pp, rp in es[:255]:
            out += bytes([bi]) + struct.pack("<H", flags)
            if flags & 1: out += struct.pack("<3f", *pp)
            if flags & 2: out += struct.pack("<3f", *rp)
    open(path, "wb").write(bytes(out))
    return True


# ---------------------------------------------------------------------------------- .mdl
def _seq_text(s, indent):
    pad = "\t" * indent
    parts = ['sequence "%s"' % s["name"]]
    if s.get("file"): parts.append('{file "%s"}' % s["file"])
    if s.get("speed") is not None: parts.append("{speed %s}" % _n(s["speed"]))
    if s.get("resume"): parts.append("{resume}")
    if s.get("autostart"): parts.append("{autostart}")
    if s.get("events"): parts.append("{events %s}" % "".join('{%s "%s"}' % (_n(f), n) for f, n in s["events"]))
    return pad + "{" + " ".join(parts) + "}"


def _shape_text(shp, indent):
    pad = "\t" * indent
    if shp["type"] == "obb2":
        L = [pad + "{obb2"]
        if shp.get("center"): L.append(pad + "\t{center %s}" % " ".join(_n(x) for x in shp["center"]))
        if shp.get("extent"): L.append(pad + "\t{extent %s}" % " ".join(_n(x) for x in shp["extent"]))
        if shp.get("axis"): L.append(pad + "\t{axis %s}" % " ".join(_n(x) for x in shp["axis"]))
        return L + [pad + "}"]
    if shp["type"] == "polygon2":
        return [pad + "{polygon2"] + [pad + "\t{vertex %s}" % " ".join(_n(x) for x in v) for v in shp["verts"]] + [pad + "}"]
    L = [pad + "{circle2"]
    if shp.get("center"): L.append(pad + "\t{center %s}" % " ".join(_n(x) for x in shp["center"]))
    if shp.get("radius") is not None: L.append(pad + "\t{radius %s}" % _n(shp["radius"]))
    return L + [pad + "}"]


def build_mdl_text(arm, view_names, new_actions, mdl_comment=""):
    """view_names: {original ply reference -> file name written}; extra views for bones: {bone orig name: [file,...]}"""
    L = []
    hdr = arm.get("goh_mdl_header", "")
    L.append(hdr if hdr else ";Exported by goh_tools (Blender)")
    if mdl_comment: L.append(mdl_comment)
    bones = [b for b in arm.data.bones if "goh_from_def" not in arm.pose.bones[b.name].keys()]
    W = {}
    for b in arm.data.bones:
        W[b.name] = b.matrix_local @ Matrix.Diagonal((1, 1, arm.pose.bones[b.name].get("goh_F", 1), 1))
    children = collections.defaultdict(list); roots = []
    names = {b.name for b in bones}
    for b in bones:
        (children[b.parent.name] if b.parent and b.parent.name in names else roots).append(b)
    seqs = json.loads(arm.get("goh_seqs_json", "[]"))
    known = {s["name"] for s in seqs}
    for pb in arm.pose.bones:
        for s in json.loads(pb.get("goh_sequences", "[]")): known.add(s["name"])
    for nm, speed, events in new_actions:
        if nm not in known: seqs.append({"name": nm, "file": None, "speed": speed, "resume": False, "autostart": False, "events": events})
    extras = json.loads(arm.get("goh_extras_json", "{}"))
    L.append("{skeleton")
    if extras.get("extension"): L.append('\t{extension "%s"}' % extras["extension"])
    if seqs:
        L.append("\t{animation")
        for s in seqs: L.append(_seq_text(s, 2))
        L.append("\t}")

    def emit(b, indent):
        pad = "\t" * indent
        pb = arm.pose.bones[b.name]
        oname = pb.get("goh_orig_name", b.name)
        joint = pb.get("goh_joint")
        head = "{bone" + ((" " + ("prizmatic" if joint == "prismatic" else joint)) if joint else "") + ' "%s"' % oname
        L.append(pad + head)
        # parameters
        raw = pb.get("goh_parameters_raw")
        cur = {k[len("goh_param_"):]: str(pb[k]) for k in pb.keys() if k.startswith("goh_param_")}
        if raw is not None or cur:
            parsed = {k: str(v) for k, v in _parse_params(raw or "").items()}
            txt = raw if (raw is not None and parsed == cur) else "".join("%s=%s;" % (k, v) for k, v in cur.items())
            if txt: L.append(pad + '\t{parameters "%s"}' % txt)
        lim = pb.get("goh_limits")
        if lim is not None: L.append(pad + "\t{limits %s %s}" % (_n(lim[0]), _n(lim[1])))
        elif pb.get("goh_cone_limit") is not None: L.append(pad + "\t{limits %s}" % _n(pb["goh_cone_limit"]))
        if pb.get("goh_speed") is not None: L.append(pad + "\t{speed %s}" % _n(pb["goh_speed"]))
        if pb.get("goh_speed2") is not None: L.append(pad + "\t{speed2 %s}" % _n(pb["goh_speed2"]))
        bseq = json.loads(pb.get("goh_sequences", "[]")); uvs = json.loads(pb.get("goh_uvanims", "[]"))
        if bseq or uvs:
            L.append(pad + "\t{animation")
            for s in bseq: L.append(_seq_text(s, indent + 2))
            for nm, d in uvs:
                inner = "".join("{%s %s}" % (kk.replace("shiftv", "shiftV").replace("shiftu", "shiftU"), " ".join(_n(x) if not isinstance(x, str) else x for x in vv[:3] + ([vv[3]] if len(vv) > 3 and vv[3] else []))) for kk, vv in d.items())
                L.append(pad + '\t\t{uv "%s" %s}' % (nm, inner))
            L.append(pad + "\t}")
        v12 = list(pb.get("goh_local", [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]))
        Wp = W[b.parent.name] if b.parent and b.parent.name in names else None
        Lc = (Wp.inverted() @ W[b.name]) if Wp is not None else W[b.name]
        cur12 = _matrix_to_l12(Lc)
        L.extend(_xform_lines(v12 if _close(v12, cur12) else cur12, Wp is None, indent + 1))
        views = json.loads(pb.get("goh_views", "[]"))
        groups = []
        for f, lod in views:
            if lod == 0 or not groups: groups.append([])
            groups[-1].append(view_names.get(f, f))
        for extra in view_names.get("__bone__" + oname, []):
            groups.append([extra])
        ex = json.loads(pb.get("goh_extra_json", "{}"))
        vextra = "".join(" {%s %s}" % (k[5:].capitalize(), v) for k, v in ex.items() if k.startswith("view_"))
        for gi, grp in enumerate(groups):
            if len(grp) == 1:
                L.append(pad + '\t{VolumeView "%s"%s}' % (grp[0], vextra if gi == 0 else ""))
            else:
                L.append(pad + "\t{LODView")
                for f in grp: L.append(pad + '\t\t{VolumeView "%s"}' % f)
                L.append(pad + "\t}")
        vis = pb.get("goh_visibility")
        if vis is not None: L.append(pad + "\t{visibility %s}" % _n(vis))
        if pb.get("goh_terminator"): L.append(pad + "\t{terminator}")
        for k2, v2 in ex.items():
            if k2.startswith(("view_", "def_")): continue
            L.append(pad + "\t{%s %s}" % (k2, v2))
        for c in children.get(b.name, []): emit(c, indent + 1)
        L.append(pad + "}")

    for r in roots: emit(r, 1)
    L.append("}")
    # volumes
    for v in json.loads(arm.get("goh_volumes_json", "[]")):
        L.append('{volume "%s"' % v["name"])
        if v["kind"] == "polyhedron":
            L.append('\t{polyhedron "%s"}' % view_names.get("vol:" + v["arg"][0], v["arg"][0]))
        elif v["kind"]:
            L.append("\t{%s %s}" % (v["kind"], " ".join(v["arg"])))
        L.append('\t{bone "%s"}' % v["bone"])
        L.extend(_xform_lines(v["local"], False, 1))
        L.append("}")
    for o in json.loads(arm.get("goh_obstacles_json", "[]")):
        L.append('{%s "%s"' % (o["kind"], o["name"]))
        for shp in o.get("shapes", []): L.extend(_shape_text(shp, 1))
        for _ in range(int(o.get("rotate", 0) or 0)): L.append("\t{rotate}")
        L.append("}")
    pvs = json.loads(arm.get("goh_previews_json", "[]"))
    if pvs:
        L.append('{extender "preview"')
        for pv in pvs:
            L.append('\t{"%s"' % pv["name"])
            if pv.get("camera"):
                L.append("\t\t{camera")
                for k2, v2 in pv["camera"].items(): L.append("\t\t\t{%s %s}" % (k2, " ".join(_n(x) for x in v2)))
                L.append("\t\t}")
            if pv.get("lights"):
                L.append("\t\t{lights")
                for lt in pv["lights"]:
                    L.append('\t\t\t{"%s"' % lt.get("name", "0"))
                    if lt.get("type"): L.append("\t\t\t\t{type %s}" % lt["type"])
                    for k2 in ("position", "direction", "color", "diffuse", "ambient"):
                        if lt.get(k2) is not None: L.append("\t\t\t\t{%s %s}" % (k2, " ".join(lt[k2])))
                    L.append("\t\t\t}")
                L.append("\t\t}")
            L.append("\t}")
        L.append("}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------- .def property edits
def _unflatten_volume(changed):
    """flattened goh_ keys (thickness, thickness_front, tags ...) -> SDL child lines"""
    L = []
    th = {k: v for k, v in changed.items() if k == "thickness" or k.startswith("thickness_")}
    if th:
        sides = "".join("{%s %s}" % (k[len("thickness_"):], v) for k, v in th.items() if k != "thickness")
        L.append("\t{thickness %s%s}" % (th.get("thickness", ""), (" " + sides) if sides else ""))
    for k, v in changed.items():
        if k in th: continue
        if k == "tags": L.append("\t{tags %s}" % " ".join('"%s"' % x for x in str(v).split()))
        elif k.startswith("able_"): L.append("\t{able {%s %s}}" % (k[5:], v))
        else: L.append("\t{%s %s}" % (k, v))
    return L


def def_text_with_edits(arm):
    """the .def text; volume properties changed in Blender are appended as override blocks"""
    tname = arm.get("goh_def_text")
    txt = bpy.data.texts[tname].as_string() if tname and tname in bpy.data.texts else None
    if txt is None: return None
    add = []
    for ob in bpy.data.objects:
        if ob.parent != arm or "goh_orig_json" not in ob.keys(): continue
        orig = json.loads(ob["goh_orig_json"])
        cur = {k[4:]: str(ob[k]) for k in ob.keys() if k.startswith("goh_") and k[4:] in orig}
        changed = {k: v for k, v in cur.items() if str(orig.get(k)) != v}
        if changed:
            keys = dict(cur) if any(k.startswith("thickness") for k in changed) else changed
            if any(k.startswith("thickness") for k in changed):
                keys = {k: v for k, v in cur.items() if k.startswith("thickness")}
                keys.update({k: v for k, v in changed.items() if not k.startswith("thickness")})
            add.append('\t{volume "%s"' % ob.get("goh_vol_name", ob.name))
            add.extend("\t" + x for x in _unflatten_volume(keys))
            add.append("\t}")
    if add:
        i = txt.rfind("}")
        txt = txt[:i] + "\n\t; --- edited in Blender (goh_tools)\n" + "\n".join(add) + "\n" + txt[i:]
    return txt


# ---------------------------------------------------------------------------------- driver
def export_mdl(arm, out_dir, mdl_name=None, meshes=True, anims=True, library_anims=False, materials=True, textures=True,
               volumes=True, def_files=True, copy_unexported=True, report=print):
    """Write the rig + everything it references into out_dir. Returns the list of written files."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    def w(p): written.append(p); return p
    if mdl_name is None:
        mdl_name = os.path.splitext(os.path.basename(arm.get("goh_model", ""))) [0] or arm.name.replace("_rig", "")
    kids = [o for o in arm.children_recursive if o.type == "MESH"] if hasattr(arm, "children_recursive") else [o for o in arm.children if o.type == "MESH"]
    visuals = [o for o in kids if "goh_volume" not in o.keys() and "goh_kind" not in o.keys()]
    vol_objs = {o["goh_vol_name"]: o for o in kids if "goh_vol_name" in o.keys()}
    view_names = {}; seen_files = {}
    # ---- meshes
    if meshes:
        for ob in visuals:
            ref = ob.get("goh_ply")
            fname = os.path.basename(ref) if ref else re.sub(r"[^\w\-.]", "_", ob.name) + ".ply"
            if not fname.lower().endswith(".ply"): fname += ".ply"
            if fname in seen_files:
                if ref: view_names[ref] = fname
                continue
            try:
                info = write_ply(w(os.path.join(out_dir, fname)), ob, arm, report)
                seen_files[fname] = info
                if ref: view_names[ref] = fname
                else:
                    bn = ob.parent_bone if ob.parent_type == "BONE" else None
                    if bn:
                        view_names.setdefault("__bone__" + (arm.pose.bones[bn].get("goh_orig_name", bn) if bn in arm.pose.bones else bn), []).append(fname)
            except Exception as e:
                written.pop(); report("[goh] WARNING mesh %s not exported: %s" % (ob.name, e))
        # views we did not export (lower LODs ...) keep their reference; copy the original file if we can find it
        if copy_unexported:
            src_dir = os.path.dirname(arm.get("goh_model", "")) or None
            for pb in arm.pose.bones:
                for f, _l in json.loads(pb.get("goh_views", "[]")):
                    if f in view_names: continue
                    base = os.path.basename(f)
                    if src_dir:
                        res = Resolver(src_dir, arm.get("goh_game_dir") or None)
                        p = res.locate(src_dir, f)
                        if p and os.path.isfile(p):
                            import shutil
                            shutil.copyfile(p, w(os.path.join(out_dir, base))); view_names[f] = base
    # ---- collision volumes
    if volumes:
        for v in json.loads(arm.get("goh_volumes_json", "[]")):
            if v["kind"] == "polyhedron" and v["arg"] and v["name"] in vol_objs:
                fn = os.path.basename(v["arg"][0])
                try:
                    write_vol(w(os.path.join(out_dir, fn)), vol_objs[v["name"]]); view_names["vol:" + v["arg"][0]] = fn
                except Exception as e:
                    written.pop(); report("[goh] WARNING volume %s not exported: %s" % (v["name"], e))
    # ---- materials
    if materials:
        mats = {}
        for ob in visuals:
            for m in ob.data.materials:
                if m is not None: mats[m.name] = m
        src_dir_t = os.path.dirname(arm.get("goh_model", "")) or None
        res_t = Resolver(src_dir_t, arm.get("goh_game_dir") or None) if src_dir_t else None
        for m in mats.values():
            if textures: written.extend(_save_new_texture(m, out_dir, report))
            if textures and res_t is not None:
                for key in ("diffuse", "bump", "specular"):
                    ref = m.get("goh_" + key)
                    if ref and not ref.startswith("$/"):
                        pth = res_t.find(ref, (".tga", ".dds", ".png", ".jpg", ""))
                        if pth and os.path.isfile(pth):
                            import shutil
                            dst = os.path.join(out_dir, os.path.basename(pth))
                            if not os.path.exists(dst): shutil.copyfile(pth, dst); written.append(dst)
            fn = m.get("goh_mtl") or (m.name + ".mtl")
            write_mtl(w(os.path.join(out_dir, os.path.basename(fn))), m)
    # ---- animations
    new_actions = []
    if anims:
        for act in bpy.data.actions:
            if act.get("goh_library") and not library_anims: continue
            if not any(re.match(r'pose\.bones\["', fc.data_path) for fc in _action_fcurves(act)): continue
            fn = act.get("goh_source") or (re.sub(r"[^\w\-.]", "_", act.name) + ".anm")
            try:
                if write_anm(w(os.path.join(out_dir, os.path.basename(fn))), act, arm, report):
                    speed = act.get("goh_speed")
                    k = float(speed) if (act.get("goh_speed_applied") and speed) else 1.0
                    ev = [(round((m.frame - 1) * k), m.name) for m in act.pose_markers]
                    new_actions.append((os.path.splitext(os.path.basename(fn))[0], speed, ev))
                else:
                    written.pop()
            except Exception as e:
                written.pop(); report("[goh] WARNING action %s not exported: %s" % (act.name, e))
    # ---- the .mdl
    text = build_mdl_text(arm, view_names, new_actions)
    open(w(os.path.join(out_dir, mdl_name + ".mdl")), "w", encoding="utf-8", newline="\n").write(text)
    # ---- .def / local .ext
    if def_files:
        dtxt = def_text_with_edits(arm)
        if dtxt:
            old_mdl = os.path.splitext(os.path.basename(arm.get("goh_model", "")))[0]
            if old_mdl and old_mdl != mdl_name:
                dtxt = re.sub(r'(\{\s*extension\s+")[^"]*(")', r"\g<1>%s.mdl\g<2>" % mdl_name, dtxt, flags=re.I)
            dname = os.path.basename(arm.get("goh_def_file", "")) or (mdl_name + ".def")
            if old_mdl and old_mdl != mdl_name: dname = mdl_name + ".def"      # a renamed model is a new entity: its .def is named after it
            open(w(os.path.join(out_dir, dname)), "w", encoding="utf-8", newline="\n").write(dtxt)
        for tname in json.loads(arm.get("goh_local_includes", "[]")):
            if tname in bpy.data.texts:
                open(w(os.path.join(out_dir, tname)), "w", encoding="utf-8", newline="\n").write(bpy.data.texts[tname].as_string())
    report("[goh] exported %d files to %s" % (len(written), out_dir))
    return written


# =====================================================================================================
#  Add-on registration
# =====================================================================================================
try:
    from bpy_extras.io_utils import ImportHelper
    from bpy.props import StringProperty, BoolProperty

    class IMPORT_OT_goh_mdl(bpy.types.Operator, ImportHelper):
        bl_idname = "import_scene.goh_mdl"
        bl_label = "Import Gates of Hell model"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".mdl"
        filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
        collision: BoolProperty(name="Collision volumes", default=True)
        anims: BoolProperty(name="Animations", default=True)
        numbers: BoolProperty(name="Number decals", default=True)
        lods: BoolProperty(name="Lower LODs (hidden)", default=False)
        speed: BoolProperty(name="Apply animation speed", default=True)
        limits: BoolProperty(name="Joint limits as constraints", default=True)
        previews: BoolProperty(name="Preview cameras / lights", default=True)
        obstacles: BoolProperty(name="Obstacle / area shapes", default=True)
        defs: BoolProperty(name=".def / .ext properties", default=True)
        animlib: StringProperty(name="Shared animations", description="'' = none, 'basic', 'all' or a regex of names (human skeletons: properties/animation/human)", default="")

        def execute(self, context):
            try:
                import_mdl(self.filepath, import_collision=self.collision, import_anims=self.anims,
                           import_enumerators=self.numbers, import_lods=self.lods, apply_speed=self.speed, joint_limits=self.limits, import_previews=self.previews, import_obstacles=self.obstacles, import_def=self.defs, anim_library=(self.animlib or None), report=lambda s: self.report({"INFO"}, s))
            except Exception as e:
                self.report({"ERROR"}, str(e)); return {"CANCELLED"}
            return {"FINISHED"}

    from bpy_extras.io_utils import ExportHelper

    class EXPORT_OT_goh_mdl(bpy.types.Operator, ExportHelper):
        bl_idname = "export_scene.goh_mdl"
        bl_label = "Export Gates of Hell model"
        bl_options = {"REGISTER"}
        filename_ext = ".mdl"
        filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
        meshes: BoolProperty(name="Meshes (.ply)", default=True)
        anims: BoolProperty(name="Animations (.anm)", default=True)
        library: BoolProperty(name="Shared library animations", default=False)
        materials: BoolProperty(name="Materials (.mtl)", default=True)
        textures: BoolProperty(name="Save new / edited textures (.dds)", default=True)
        volumes: BoolProperty(name="Collision volumes (.vol)", default=True)
        defs: BoolProperty(name=".def / .ext", default=True)

        def execute(self, context):
            arm = context.active_object
            if arm is None or arm.type != "ARMATURE":
                arms = [o for o in context.scene.objects if o.type == "ARMATURE" and "goh_model" in o.keys()]
                arm = arms[0] if arms else None
            if arm is None:
                self.report({"ERROR"}, "select the model's armature"); return {"CANCELLED"}
            try:
                export_mdl(arm, os.path.dirname(self.filepath), os.path.splitext(os.path.basename(self.filepath))[0], meshes=self.meshes,
                           anims=self.anims, library_anims=self.library, materials=self.materials, textures=self.textures,
                           volumes=self.volumes, def_files=self.defs, report=lambda s: self.report({"INFO"}, s))
            except Exception as e:
                import traceback; traceback.print_exc(); self.report({"ERROR"}, str(e)); return {"CANCELLED"}
            return {"FINISHED"}

    def _menu(self, context):
        self.layout.operator(IMPORT_OT_goh_mdl.bl_idname, text="Gates of Hell model (.mdl)")

    def _menu_export(self, context):
        self.layout.operator(EXPORT_OT_goh_mdl.bl_idname, text="Gates of Hell model (.mdl)")

    def register():
        for cls in (IMPORT_OT_goh_mdl, EXPORT_OT_goh_mdl):
            try: bpy.utils.register_class(cls)
            except ValueError: pass
        for menu, fn in ((bpy.types.TOPBAR_MT_file_import, _menu), (bpy.types.TOPBAR_MT_file_export, _menu_export)):
            try: menu.remove(fn)
            except Exception: pass
            menu.append(fn)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(_menu)
        bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
        bpy.utils.unregister_class(IMPORT_OT_goh_mdl)
        bpy.utils.unregister_class(EXPORT_OT_goh_mdl)
except Exception as _e:   # running without UI helpers
    def register(): pass
    def unregister(): pass

if "__file__" not in globals() or __name__ != "__main__":
    pass

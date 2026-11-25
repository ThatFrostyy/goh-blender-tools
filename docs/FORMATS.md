# Gates of Hell file formats (as reverse engineered for goh_tools)

All numbers little endian. Game units: 1 unit = 5 cm, Z up, +X forward. Everything below was verified against the files of game
build 1.0.6.9 (hundreds of models, all packs) and by import -> export -> import round trips.

## .ply  (mesh)
```
"EPLY" "BNDS" 6 x f32                    bounding box = min xyz, max xyz of the vertices (exact)
["SKIN" u32 n  n x (u8 len, name)]       skinned meshes: bone names. Bone index 0 = model root, name i is index i+1
"MESH" (one per material part)
    u32 a, u32 first_triangle, u32 triangle_count, u32 d
    [4 bytes colour]                     only if the material has {color "r g b a"}: r g b a bytes (a = 25 means 10%)
    u8 len + material file name (.mtl)
    [u8 n + n bytes bone palette]        skinned meshes only; every part of a file uses the same palette (max 22 entries seen)
"VERT" u32 count, u16 stride, u16 7(flags)   vertices
"INDX" u32 count, count x u16            triangle list, parts are consecutive ranges
```
`a` and `d` describe the vertex layout and shader:

| header bit | meaning |
|---|---|
| `a & 0x10`  | vertex has a normal |
| `a & 0x40`  | vertex has a packed colour (4 bytes) |
| `a & 0x200` | vertex has a second uv set |
| `d & 0x100` | vertex has a tangent (material has a bump map) |
| `d`         | `0x404` simple, `+0x100` bump, `+0x200` specular map (`0x704` = bump + specular) |

Rigid vertex order: `position(3f) [normal(3f)] [colour(i32)] uv(2f) [uv2(2f)] [tangent(4f)]` (a few meshes have 8 trailing padding bytes).
Typical: stride 32 (pos, normal, uv), 48 (+tangent), 36 / 52 / 60 (with colour), 40 / 56 (second uv).
uv is stored with v pointing down (Blender v = 1 - v). The tangent is (x, y, z, w) with w = +-1 handedness; the game's w is the opposite of Blender's
`bitangent_sign` for rigid meshes and the same sign for skinned meshes.

Skinned vertex (`a = 4376`): `position(3f) weight(f32) bone_idx(4 x u8) normal(3f) uv(2f) [tangent(4f)]` -> stride 40 (old soldiers) or 56.
Two bones per vertex: `weight` belongs to `bone_idx[0]`, `1 - weight` to `bone_idx[1]`; indices go through the palette; palette value 0 = root.

## .vol  (collision mesh)
```
"EVLM" "VERT" u32 n, n x (3f)   "INDX" u32 count, count x u16 (triangles)   "SIDE" u32 triangle_count, one byte per triangle (1..6)
```
The side byte is authored data (the armour side groups the `.ext` talks about); keep it, default 1.

## .anm  (animation)
```
"EANM" 01 00 06 00  "FRMS" u32 frame_count  "BMAP" u32 n, n x (u32 len, bone name)
"FRM2" (repeated)  u16 frame, u8 entry_count, entries: u8 bone_index, u16 flags, [3f position if flags&1] [3f rotation if flags&2]
```
Rotation = quaternion x, y, z (w >= 0 implied). Flags 4 and 8 carry no data. A position/rotation is the bone's *local* transform
(replacing the rest local transform), so pose = rest^-1 * animation. For mirrored nodes (determinant < 0) the stored quaternion belongs to
`local @ flip_z`. The game plays frames at `speed` (the sequence value in the `.mdl` / animation registry) times normal.
Shared soldier animations: `properties.pak: properties/animation/human/**.anm`, speeds + footstep events in `human_anm.ext`.

## .mdl  (skeleton + references, text)
```
{skeleton {animation {sequence "name" {file "x.anm"}{speed n}{resume}{autostart}{events {frame "name"}...}}}
  {bone [revolute|prizmatic|socket] "name" {parameters "k=v;"} {limits a b | cone} {speed n}{speed2 n} {visibility 0} {terminator}
     {matrix34 r1 r2 r3  t | Position x y z | orientation ...} {VolumeView "x.ply"} {LODView {VolumeView "lod0"}{VolumeView "lod1"}...}
     {animation {sequence ..} {uv "run" {shiftV min max step}}}  {bone ...children} } }
{volume "name" {polyhedron "x.vol" | box a b c | cylinder r h} {bone "b"} {matrix34 ... | Position ...}}
{obstacle|area "name" {obb2 {center}{extent}{axis}} | {polygon2 {vertex}...} | {circle2 {center}{radius}} {rotate}}
{extender "preview" {"name" {camera {origin}{lookat}{fov}{horizon}} {lights {"0" {type point|directional}{position}{direction}}}}}
```
`matrix34` is 3 rows R then translation t, row-vector convention (`p' = p R + t`); Blender needs `R^T`. Joint axes: revolute = local Z (degrees),
prizmatic = local Y (metres, 20 units = 1 m), socket = cone angle.
Soldiers: older style = one baked `skin.ply` with 3 duplicate skin bones (`skins`, `skind`, `skinw`); new style = one `skin` bone with a
`LODView` group per part (head, tunic, trousers, gear ...) taken from shared `[source]` folders.

## .mtl
`{material simple|bump|water|envmap {diffuse "name"}{bump "name"}{specular "name"}{color "r g b a"}{blend none|test|blend}}`.
Names starting with `$/` live in the game's texture packs (`resource/texture/**.pak`), others next to the model (`.tga` / `.dds`).

## .def / .ext / .inc  (SDL, with macros)
`{block head... {child}}`, `(call "name" key(value) ... args pos1 pos2)`, `(define "name" body)` with `%key` / `%0` parameters, `(include "file" key(val))`,
`(* a b)` arithmetic. `/x/y.ext` = from `properties.pak`; other paths are relative to the including file. Later definitions override earlier ones;
`props`, `tags` and `able` accumulate. `{volume "x" {thickness n {front a}{top b}...}}` is the armour of the collision volume `x`;
`{bone "x" {limits a b}{speed2 n}}` overrides the `.mdl`.

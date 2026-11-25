# goh_tools - Gates of Hell model import / export for Blender

One add-on file (`goh_tools.py`) that imports **and exports** Call to Arms: Gates of Hell models:
`.mdl` skeletons, `.ply` meshes, `.anm` animations, `.mtl` materials, `.vol` collision shapes and the `.def` / `.ext` unit data.

Tested with Blender 5.3 and game build 1.0.6.9.

## Install
Blender > Edit > Preferences > Add-ons > (arrow) Install from Disk... > `goh_tools.py`, then enable it.
Then **File > Import > Gates of Hell model (.mdl)** and **File > Export > Gates of Hell model (.mdl)**.

Scripting / headless:
```python
exec(open(r"D:/.../goh_tools.py").read())
arm = import_mdl(r"D:/.../your_model.mdl", anim_library="basic")        # soldiers: also load the shared animations
export_mdl(arm, r"D:/.../out_folder")                                   # writes mdl, ply, anm, mtl, vol, def ...
```
Models that are only inside the game's `.pak` files:
```python
d = extract_model_dir("entity/humanskin/[germans]/ger_heer_39", r"D:/.../Call to Arms - Gates of Hell")
arm = import_mdl(d + "/ger_heer_39_rfl_1.mdl", game_dir=r"D:/.../Call to Arms - Gates of Hell")
```

## Import
* **Rig**: every bone of the `.mdl` (also seats, fx points, sights), joint types with limits as Blender constraints (hinge, slider, ball),
  skinned parts with vertex groups, rigid parts following their bone. Mirrored bones handled.
* **Meshes**: all `.ply` layouts of the game (normals, tangents, vertex colours, second uv set, 1 or 2 bone weights), several materials, LODs.
* **Animations**: `.anm` as Actions with speeds, events as markers; the ~1600 shared soldier animations (`anim_library="basic" | "all" | regex`)
  with their registry speeds and footstep events.
* **Materials** (diffuse, bump, specular, alpha, colour tint), **collision volumes**, obstacle shapes, preview cameras / lights, texture scrolling.
* **.def / .ext**: the whole macro language is evaluated (includes, defines, `%params`, arithmetic, the game's shared `properties.pak`).
  Armour per volume (`goh_thickness_front` ...), component hit points, mass, mobility numbers, seats, weapon places, visors as custom properties;
  the original and the fully expanded text are kept as Blender text blocks.
* Files are found next to the model, in your mod and game `resource` folders, and inside the `.pak` archives (extracted on demand into a cache).
  If the model lives outside your mod folder (e.g. a scratch export), set the environment variable `GOH_EXTRA_RESOURCE` to your mod's `resource` folder
  (several folders separated by `;`) so its loose textures are found.

## Export
Select the model's armature, **File > Export > Gates of Hell model (.mdl)**, pick the target folder/name. It writes:
* `.mdl` rebuilt from the armature (bone transforms, joint limits and speeds, parameters, mesh references incl. LOD groups, volumes, obstacles, previews),
* `.ply` for every mesh object (exact vertex layout of the original, tangents recomputed, weights limited to 2 bones, max 22 bones per mesh is warned),
* `.anm` for every action (the shared library actions only if asked), `.mtl` for the materials, `.vol` for edited collision meshes,
* `.def` and local `.ext` from their text blocks; armour values you changed on the `vol_*` objects are appended as override blocks,
* new or edited textures (an image made in Blender, a changed image, or any image with the custom property `goh_export`) are written as `.dds` - DXT1 when opaque, DXT5 with alpha, full mipmap chain, like the game's own textures; the image name (or its `goh_name` property) becomes the texture name in the `.mtl`. Textures referenced by name next to the model are copied along.

An unchanged import round-trips: positions and UVs exact, normals within 0.001, tangents about 1 degree, bones, weights, animations and
properties identical (see `tests/`).

## Differences between model types
Soldiers use two pipelines: this mod's old style (one baked `skin.ply`, duplicate skin bones) and the game's new modular style (head, tunic,
trousers, gear as separate `skin`-bone parts from shared source folders). Vehicles are rigid parts plus skinned tracks / suspension, guns and planes
use the same rig structure with different joint types, buildings and landscape pieces are mostly meshes with collision volumes and vertex colours.
All of them go through the same readers and writers; `docs/FORMATS.md` describes the formats.

## Known limits
* Only Blender 5.3 was tested. The game itself was **not** run with exported files: exports are verified by re-importing them and comparing, not in game.
* Collision box / cylinder orientation, obstacle shapes, the side byte meaning in `.vol` files and the `speed2` meaning (turn speed) are best interpretations.
* Edited limits/speeds are written to the `.mdl`; a `.def` that overrides the same bone keeps overriding them.
* Models that need more than 22 bones per mesh must be split.
* The code is MIT licensed (see `LICENSE`). The game's files and anything extracted from them (meshes, textures) remain the property of their owners and are not part of this repository.

## Tests
`tests/README.md` - headless round-trip, import and animation tests (`GOH_GAME_DIR` must point at the game).

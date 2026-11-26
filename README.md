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
* **Export safety check**: only the animations of the model you export are written (clips of other imported models are left out); if two clips would get the same name the game refuses the model, so the export shows a warning and lets you stop or continue (`check_export(arm)` / `export_mdl(..., confirm=callback)` from Python)
  with their registry speeds and footstep events.
* **Materials** (diffuse, bump, specular, alpha, colour tint), **collision volumes**, obstacle shapes, preview cameras / lights, texture scrolling.
* **.def / .ext**: the whole macro language is evaluated (includes, defines, `%params`, arithmetic, the game's shared `properties.pak`).
  Armour per volume (`goh_thickness_front` ...), component hit points, mass, mobility numbers, seats, weapon places, visors as custom properties;
  the original and the fully expanded text are kept as Blender text blocks.
* Files are found next to the model, in your mod and game `resource` folders, and inside the `.pak` archives (extracted on demand into a cache).
  If the model lives outside your mod folder (e.g. a scratch export), set the environment variable `GOH_EXTRA_RESOURCE` to your mod's `resource` folder
  (several folders separated by `;`) so its loose textures are found.

## Export
Import and edit the `.mdl` that the entity's `.def` points at (`{Extension "x.mdl"}`). Some folders contain extra `.mdl` files that the game never loads; the importer warns when you open one of those.

Select the model's armature, **File > Export > Gates of Hell model (.mdl)**, pick the target folder/name. It writes:
* `.mdl` rebuilt from the armature (bone transforms, joint limits and speeds, parameters, mesh references incl. LOD groups, volumes, obstacles, previews),
* `.ply` for every mesh object (exact vertex layout of the original, tangents recomputed, weights limited to 2 bones, max 22 bones per mesh is warned),
* `.anm` for every action (the shared library actions only if asked), `.mtl` for the materials, `.vol` for edited collision meshes,
* `.def` and local `.ext` from their text blocks; armour values you changed on the `vol_*` objects are appended as override blocks,
* new or edited textures (an image made in Blender, a changed image, or any image with the custom property `goh_export`) are written as `.dds` - DXT1 when opaque, DXT5 with alpha, full mipmap chain, like the game's own textures; the image name (or its `goh_name` property) becomes the texture name in the `.mtl`. Textures referenced by name next to the model are copied along.

An unchanged import round-trips: positions and UVs exact, normals within 0.001, tangents about 1 degree, bones, weights, animations and
properties identical (see `tests/`).

## Models from scratch or from other software
Sidebar tab **GOH** in the 3D view (N panel) next to the import / export buttons:
* **New model rig**: an armature with the bone names the game expects. Template *Tank* has `basis`, `body`, `turret`, `gun_rot`, `gun`, wheels, seats,
  fuel / ammo points and fx points; *Blank* is just a root. Game units are 5 cm and the armature object carries that scale, so build your model **in metres** and it comes out right-sized.
* **Attach mesh to bone**: select the meshes (made in any program, any scale), make the armature active, pick the bone. Modifiers are applied and the vertices are moved into the bone's frame in game units
  (set "metres per Blender unit" if the mesh was modelled in centimetres or inches). The exporter writes raw vertices, so this step is what puts the part in the right place.
* **Collision volume from mesh**: convex hull (merged until at most 5000 triangles) or box of the selected meshes, written as `.vol` and attached to the bone you choose.
  A volume is one convex part: use one per hull / turret / barrel for hollow or L-shaped parts. Armour thickness goes into the starter `.def`.
* **Set up tracks and suspension**: per side a `wheels{l,r}` bone (`id=trackleft/trackright`), sliding suspension bones (limits +-0.1 m, speed 0.18) with a wheel bone each (spin about the side axis, `radius` in metres), `susp` and `track` bones,
  and a **skinned track belt** (outer and inner surface, UVs along the belt, every vertex follows its two nearest suspension bones) around the wheel circles. The *Tank* template already contains them; the operator adds them to any rig.
  Bone layout, parameters and the wheel orientation matrix are the ones of the game's own tanks. The belt uses the tracks material `$/_vehicle/tracks/m3_stuart_track`; change `goh_diffuse` / `goh_bump` / `goh_specular` on the material `goh_track` for another one.
* **Bake recoil / firing / impact clips**: actions named like the game's own, for the gun bone and the hull bone: `fire` (gun slides back 0.22 m in 4 frames and returns slowly, small hull kick), `hit` (hull shudder),
  `start` / `stop` (engine vibration) and `spring` (hull settles on its suspension). The recoil goes straight back along the gun's parent frame, like the game's own `fire.anm` (checked against a game tank).
  Existing clips of the same name are replaced. They are plain Actions: edit the curves, then export.
* **Check model**: lists what the game would refuse or silently drop (meshes not on a bone or moved out of their bone's frame, missing required bones, more than 65535 vertices, duplicate names)
  and warns about missing materials / textures / UVs / collision and unusual model size. The report is the text block `goh_check`; the export dialog can also write it as `goh_check.txt`
  with the size and SHA-1 of every written file.
* **Left / right**: all 488 vehicles of the game have a mirrored root bone (`basis` with Y flipped); models are authored the normal right-handed way (front +X, left +Y) and the engine mirrors the world.
  Rigs made by the *Tank* template get that root written on export. Imported game models show up mirrored left-to-right in Blender for the same reason (their `wheell` bones sit at -Y), so do not copy parts between
  an imported model and a from-scratch one without mirroring them.
* A rig made by the template exports a **starter `.def`** (tank entity with your volumes as armour entries; it expands with the game's own includes to the same properties as a real tank's `.def`, except weapon places and crew seats, which still have to be filled in by hand or copied from a similar unit).
  Not covered: animations (author them as Actions), tracks / skinned suspension, and the unit's `.set` registration.
From Python: `create_rig`, `adopt_mesh`, `generate_volume`, `validate_model`, `format_report` (see `tests/from_scratch.py`).

## Differences between model types
Soldiers come in two styles: an older one (a single baked `skin.ply` with duplicate skin bones) and the game's current modular one (head, tunic,
trousers and gear as separate parts on one `skin` bone, taken from shared source folders). Vehicles are rigid parts plus skinned tracks and suspension,
guns and planes use the same rig structure with different joint types, and buildings and landscape pieces are mostly meshes with collision volumes
and vertex colours. All of them go through the same readers and writers; `docs/FORMATS.md` describes the formats.

## License
MIT, see `LICENSE`. The game's files and anything extracted from them (meshes, textures) remain the property of their owners and are not part of this repository.

## Tests
`tests/README.md` - headless round-trip, import and animation tests (`GOH_GAME_DIR` must point at the game).

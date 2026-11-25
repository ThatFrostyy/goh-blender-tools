# Tests

These run headless inside Blender (the add-on needs `bpy`) and need the game files.
Set `GOH_GAME_DIR` to the game folder (the one that contains `resource` and `mods`), then:

```powershell
$env:GOH_GAME_DIR = "D:/Program Files/Steam/steamapps/common/Call to Arms - Gates of Hell"
$blender = "D:/Program Files/Steam/steamapps/common/Blender/blender.exe"

# import -> export -> import again, 16 model types (soldiers old and new style, vehicles, guns, planes, buildings, trees ...)
& $blender --background --factory-startup --python tests/roundtrip.py
& $blender --background --factory-startup --python tests/roundtrip.py -- game_soldier_new game_tank   # selected cases

# random import sample from one pack: pack count seed
& $blender --background --factory-startup --python tests/import_batch.py -- vehicle 40 1

# soldier animation library (timing + idle pose sanity)
& $blender --background --factory-startup --python tests/animations.py

# DDS texture writer (encodes real game textures, compares what Blender reads back)
& $blender --background --factory-startup --python tests/dds.py

# File > Import / Export operators registered as an add-on
& $blender --background --factory-startup --python tests/operators.py
```

* Files extracted from the game's `.pak` archives are cached in `tests/.cache` (git-ignored); exports go to `tests/out`.
* `GOH_MOD_ENTITY` (optional) points at a mod's `resource/entity/` folder for the two `mod_*` cases (an old-style soldier and a modded tank);
  they are skipped when the folder or models do not exist.
* Last run (Blender 5.3): the 16 round-trip cases give 14 perfect matches; the other two differ only
  in 2 to 6 vertices per mesh that no triangle used (the exporter drops them). 400 sampled game models import without failures.

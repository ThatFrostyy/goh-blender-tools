# Tests

These run headless inside Blender (the add-on needs `bpy`) and need the game files.
Set `GOH_GAME_DIR` to the game folder (the one that contains `resource` and `mods`), then:

```powershell
$env:GOH_GAME_DIR = "D:/Program Files/Steam/steamapps/common/Call to Arms - Gates of Hell"
$blender = "D:/Program Files/Steam/steamapps/common/Blender/blender.exe"

# import -> export -> import again, 14 game models (soldier, vehicles, gun, planes, buildings, trees, rocks, effects ...)
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
* Last run (Blender 5.3): 13 of the 14 round-trip cases are perfect matches; the plane differs only
  in 2 vertices per mesh that no triangle used (the exporter drops them). 400 sampled game models import without failures.

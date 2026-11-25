"""
Operator test: load goh_tools.py as an add-on module, register it and run File > Import / Export through bpy.ops.

  blender --background --factory-startup --python tests/operators.py
"""
import bpy, sys, os, importlib.util, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env

spec = importlib.util.spec_from_file_location("goh_tools", _env.TOOLS)
mod = importlib.util.module_from_spec(spec)
sys.modules["goh_tools"] = mod
spec.loader.exec_module(mod)
mod.register()
assert hasattr(bpy.ops.import_scene, "goh_mdl"), "import operator missing"
assert hasattr(bpy.ops.export_scene, "goh_mdl"), "export operator missing"
print("add-on registered, bl_info version", mod.bl_info["version"])

for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
folder = mod.extract_model_dir("entity/humanskin/[germans]/ger_heer_39", _env.GAME)
src = os.path.join(folder, "ger_heer_39_rfl_1.mdl")
r = bpy.ops.import_scene.goh_mdl(filepath=src, anims=False)
assert r == {"FINISHED"}, r
arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
bpy.context.view_layer.objects.active = arm
out = os.path.join(_env.OUT, "operator_export")
if os.path.isdir(out): shutil.rmtree(out)
os.makedirs(out)
r = bpy.ops.export_scene.goh_mdl(filepath=os.path.join(out, "my_soldier.mdl"))
assert r == {"FINISHED"}, r
files = sorted(os.listdir(out))
print("exported:", files)
assert "my_soldier.mdl" in files and any(f.endswith(".ply") for f in files) and any(f.endswith(".mtl") for f in files)
mod.unregister()
print("OK")

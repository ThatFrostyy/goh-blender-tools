"""
Export safety checks: clips from another imported model are left out, and duplicate animation names are reported
(and the export can be stopped).

  blender --background --factory-startup --python tests/duplicates.py
"""
import bpy, sys, os, shutil, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME, OUT = _env.GAME, _env.OUT

def model(pak):
    folder = extract_model_dir(pak, GAME)
    return os.path.join(folder, sorted(f for f in os.listdir(folder) if f.endswith(".mdl"))[0])

def mdl_sequences(path):
    return re.findall(r'\{sequence "([^"]+)"', open(path, encoding="utf-8").read().split("{bone", 1)[0])

a = import_mdl(model("entity/-vehicle/usa/tank_light/m3_stuart_late"), game_dir=GAME, report=lambda s: None)
b = import_mdl(model("entity/-vehicle/germany/cannon/75mm_pak40"), game_dir=GAME, report=lambda s: None)
assert a["goh_model"] != b["goh_model"]
print("clips in the file:", len(bpy.data.actions))

# 1. two different models in one file: only the exported model's clips are written, no duplicate names
msgs = []
out = os.path.join(OUT, "dup_a")
if os.path.isdir(out): shutil.rmtree(out)
assert check_export(a) == [], check_export(a)
export_mdl(a, out, mdl_name="stuart_only", report=msgs.append)
seqs = mdl_sequences(os.path.join(out, "stuart_only.mdl"))
assert len(seqs) == len(set(seqs)), seqs
assert any("left out" in m for m in msgs), msgs
print("1 ok: other model's clips left out;", len(seqs), "sequences, all unique")

# 2. the same clip name twice from the same model: reported, can be stopped or continued
act = next(x for x in bpy.data.actions if x.get("goh_rig") == a["goh_model"])
dup = act.copy(); dup["goh_source"] = act["goh_source"]; dup["goh_rig"] = act["goh_rig"]
probs = check_export(a)
assert len(probs) == 1 and "duplicate name" in probs[0], probs
print("2 ok: problem reported ->", probs[0][:70], "...")
try:
    export_mdl(a, os.path.join(OUT, "dup_b"), report=lambda s: None, confirm=lambda p: False)
    raise SystemExit("export should have been stopped")
except GohExportCancelled:
    print("3 ok: answering 'stop' cancels the export, nothing written:", not os.path.exists(os.path.join(OUT, "dup_b")))
msgs = []
files = export_mdl(a, os.path.join(OUT, "dup_c"), report=msgs.append, confirm=lambda p: True)
assert any("WARNING" in m and "duplicate" in m for m in msgs) and files
print("4 ok: answering 'continue' exports with the warning")
print("OK")

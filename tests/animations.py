"""
Soldier animation test: import a soldier with the shared animation library, check timing and that an idle pose starts near the rest pose.

  blender --background --factory-startup --python tests/animations.py -- [path/to/soldier.mdl]
"""
import bpy, sys, os, time, math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
if argv:
    mdl = argv[0]
else:
    mdl = os.path.join(extract_model_dir("entity/humanskin/[germans]/ger_heer_39", GAME), "ger_heer_39_rfl_1.mdl")
t0 = time.time()
arm = import_mdl(mdl, game_dir=GAME, anim_library="basic", report=lambda s: None)
dt = time.time() - t0
acts = {a.name: a for a in bpy.data.actions}
print("imported %d actions in %.1fs (curves are written directly, this must stay a few seconds)" % (len(acts), dt))
assert dt < 60, "animation import too slow"
sc = bpy.context.scene
idle = next((n for n in acts if "idle_empty_01" in n), None)
if idle:
    act = acts[idle]
    arm.animation_data_create(); arm.animation_data.action = act
    slots = list(act.slots) if hasattr(act, "slots") else []
    if slots: arm.animation_data.action_slot = slots[0]
    sc.frame_set(1); bpy.context.view_layer.update()
    far = []
    for pb in arm.pose.bones:
        q = pb.rotation_quaternion
        ang = math.degrees(q.angle) if q.angle < math.pi else math.degrees(2 * math.pi - q.angle)
        if ang > 45: far.append((pb.name, round(ang)))
    print("idle pose, bones more than 45 degrees from rest at frame 1:", far)
    assert len(far) < 6, "idle animation does not start near the rest pose"
walk = next((n for n in acts if "walk" in n), None)
print("walk animation:", walk, {k: acts[walk][k] for k in acts[walk].keys() if k.startswith("goh_")} if walk else None)
print("OK")

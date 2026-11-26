"""The model check on imported game models: no false errors (they all load in the game)."""
import bpy, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env
_env.load_tools(globals())
GAME = _env.GAME
CASES = (("entity/-vehicle/finland/tank_medium/t3485_44_fin", "t3485_44.mdl"), ("entity/-vehicle/ussr/cannon/76mm_zis3", "76mm_zis3.mdl"),
         ("entity/-vehicle/germany/btr/sdkfz222a", "sdkfz222a.mdl"), ("entity/humanskin/[germans]/ger_heer_39", "ger_heer_39_rfl_1.mdl"))
bad = 0
for vd, mdl in CASES:
    d = extract_model_dir(vd, GAME)
    arm = import_mdl(os.path.join(d, mdl), game_dir=GAME, report=lambda s: None)
    issues = validate_model(arm)
    errs = [t for l, t in issues if l == "error"]
    print(mdl, "errors:", errs, "| warnings:", [t for l, t in issues if l == "warning"][:3])
    bad += len(errs)
    for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
print("VALIDATE GAME OK" if not bad else "FALSE ERRORS: %d" % bad)

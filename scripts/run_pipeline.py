"""One review iteration, headless: build the model, check it, render it, save it.

    blender -b --factory-startup --python-exit-code 1 --python run_pipeline.py -- \
        --model model.py --out renders/iter_03 [--spec spec.json] [--mode clay|shaded|both] \
        [--res 512] [--final final.png] [--no-render]

Writes into --out:
    report.txt / report.json   geometry check results
    clay_sheet.png             8-view contact sheet (and/or shaded_sheet.png)
    model.blend                the built scene, for opening in Blender
Exit code is 1 if the model script raised, 2 if the geometry check found errors.
"""

import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import check_geometry  # noqa: E402
import modelkit  # noqa: E402
import render_views  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="model script (the source of truth)")
    ap.add_argument("--blend", help="open this .blend instead of running a model script")
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--mode", default="clay", choices=["clay", "shaded", "both", "none"])
    ap.add_argument("--res", type=int, default=512)
    ap.add_argument("--collection")
    ap.add_argument("--final", help="also render a presentation image (Cycles) to this path")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    if a.blend:
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    else:
        modelkit.reset_scene()
    if a.model:
        path = os.path.abspath(a.model)
        sys.path.insert(0, os.path.dirname(path))
        ns = {"__name__": "__main__", "__file__": path}
        try:
            with open(path, "r", encoding="utf-8-sig") as f:  # tolerate BOMs from PowerShell
                exec(compile(f.read(), path, "exec"), ns)
        except Exception:
            tb = traceback.format_exc()
            with open(os.path.join(out, "report.txt"), "w", encoding="utf-8") as f:
                f.write("MODEL SCRIPT FAILED\n" + tb)
            print("MODEL SCRIPT FAILED\n" + tb)
            sys.exit(1)

    spec = os.path.abspath(a.spec) if a.spec else None
    report = check_geometry.run(spec_path=spec, collection=a.collection,
                                out_path=os.path.join(out, "report.json"))
    with open(os.path.join(out, "report.txt"), "w", encoding="utf-8") as f:
        f.write(report["text"] + "\n")

    if not a.no_render and a.mode != "none":
        modes = ["clay", "shaded"] if a.mode == "both" else [a.mode]
        for m in modes:
            render_views.run(out, mode=m, res=a.res, collection=a.collection)

    if a.final:
        if bpy.context.scene.camera is None:
            modelkit.studio()
        modelkit.render_final(os.path.abspath(a.final))
        print(f"FINAL: {a.final}")

    if not a.no_save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "model.blend"))

    if report["summary"]["errors"]:
        sys.exit(2)


main()

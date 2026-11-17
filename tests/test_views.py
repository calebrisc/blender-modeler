"""render_views presets, the LEFT view and view validation (headless Blender).

    blender -b --factory-startup --python tests/test_views.py -- --out DIR
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import check, done, out_dir  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import modelkit as mk  # noqa: E402
import render_views as rv  # noqa: E402

OUT = out_dir()
mk.reset_scene()
# deliberately asymmetric in X and Y, so LEFT is a mirror of RIGHT and nothing else
mk.box("base", (0.4, 0.2, 0.05), (0, 0, 0.025))
mk.box("tower", (0.08, 0.08, 0.3), (0.12, 0.04, 0.2))
mk.cylinder("post", 0.02, 0.15, (-0.15, -0.06, 0.125), segments=16)


def size(path):
    img = bpy.data.images.load(path, check_existing=False)
    s = tuple(img.size)
    bpy.data.images.remove(img)
    return s


def mask(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[..., 3] > 0.5          # per-view PNGs keep alpha


def raises(fn):
    try:
        fn()
    except ValueError as ex:
        return str(ex)
    return None


res = 192
d0 = os.path.join(OUT, "views_default")
s0 = rv.run(d0, mode="clay", res=res)
check("default sheet unchanged: 8 views in 4 x 2", size(s0) == (4 * res + 5 * 4, 2 * res + 3 * 4)
      and not os.path.isfile(os.path.join(d0, "clay_left.png")), f"{size(s0)}")

d6 = os.path.join(OUT, "views_six")
s6 = rv.run(d6, mode="clay", res=res, views="six")
files = [os.path.join(d6, f"clay_{n}.png") for n in rv.PRESETS["six"]]
check("views='six': 6 renders, 3 x 2 sheet", all(map(os.path.isfile, files))
      and size(s6) == (3 * res + 4 * 4, 2 * res + 3 * 4), f"{size(s6)}")

L, R = mask(os.path.join(d6, "clay_left.png")), mask(os.path.join(d6, "clay_right.png"))
mirror = R[:, ::-1]
iou = (L & mirror).sum() / max((L | mirror).sum(), 1)
iou_plain = (L & R).sum() / max((L | R).sum(), 1)
check("LEFT is the mirror image of RIGHT (silhouette IoU)", iou > 0.95 and iou_plain < iou,
      f"IoU mirrored {iou:.3f}, unmirrored {iou_plain:.3f}")

check("custom view tuples still work", os.path.isfile(rv.run(os.path.join(OUT, "views_custom"), mode="clay", res=128,
                                                              views=[("close", 30, 40, False), "left"])))
check("unknown view name raises ValueError", raises(lambda: rv.run(OUT, mode="clay", views=["front", "lft"])) is not None)
check("unknown preset raises ValueError", raises(lambda: rv.resolve_views("seven")) is not None)
check("malformed custom view raises ValueError", raises(lambda: rv.resolve_views([("x", 10, 20)])) is not None)
check("empty view list raises ValueError", raises(lambda: rv.resolve_views([])) is not None)
n_obj = len(bpy.data.objects)
raises(lambda: rv.run(OUT, mode="clay", views=["nope"]))
check("a rejected call leaves the scene untouched", len(bpy.data.objects) == n_obj
      and bpy.context.scene.camera is None, f"{len(bpy.data.objects)} objects")
try:
    sh = rv.run(os.path.join(OUT, "views_six_shaded"), mode="shaded", res=res, views="six")
    print(f"INFO shaded six sheet: {sh}")
except Exception as ex:  # noqa: BLE001  (EEVEE needs a GPU context)
    print(f"INFO shaded render skipped: {ex!r}")
done()

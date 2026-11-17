"""UV-aware lathe / tube_mesh and closed loops (headless Blender).

    blender -b --factory-startup --python tests/test_uv_builders.py -- --out DIR

Also renders a before/after pair (same parts, checker material on their UVs):
uv_before_sheet.png (no uv_tile: no UVs) and uv_after_sheet.png (uv_tile set).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import check, done, out_dir  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import modelkit as mk  # noqa: E402
import render_views  # noqa: E402

OUT = out_dir()
mk.reset_scene()


def uvs(o):
    lay = o.data.uv_layers.get("UVMap")
    a = np.empty(len(o.data.loops) * 2, np.float32)
    lay.data.foreach_get("uv", a)
    return a.reshape(-1, 2)


def euler(o):
    me = o.data
    return len(me.vertices) - len(me.edges) + len(me.polygons)


def raises(fn):
    try:
        fn()
    except ValueError as ex:
        return str(ex)
    return None


VASE = [(0, 0), (0.05, 0), (0.06, 0.04), (0.04, 0.12), (0.045, 0.16), (0, 0.16)]
plen = sum(math.dist(a, b) for a, b in zip(VASE, VASE[1:]))

# ---- lathe: backward compatible without uv_tile -------------------------------
old = mk.lathe("vase old", VASE, segments=32)
check("lathe without uv_tile: no UV layer, still closed", not old.data.uv_layers and mk.is_closed(old),
      f"{len(old.data.polygons)} faces")

# ---- lathe with uv_tile ---------------------------------------------------------
tile = 0.1
v = mk.lathe("vase uv", VASE, segments=32, uv_tile=tile, location=(0.2, 0, 0))
uv = uvs(v)
U = max(1, round(math.tau * 0.06 / tile))
check("lathe uv_tile: same topology as without", len(v.data.polygons) == len(old.data.polygons)
      and mk.is_closed(v), f"{len(v.data.polygons)} faces")
check("lathe uv: u spans whole repeats (seamless seam)", abs(uv[:, 0].max() - U) < 1e-5 and uv[:, 0].min() >= -1e-6,
      f"u 0..{uv[:, 0].max():.4f}, expected {U}")
check("lathe uv: v = profile length / tile", abs(uv[:, 1].max() - plen / tile) < 1e-4,
      f"v max {uv[:, 1].max():.4f} vs {plen / tile:.4f}")
check("lathe uv: finite, one per corner", np.isfinite(uv).all() and len(uv) == len(v.data.loops))

# UVs survive the finishing stack (bevel + weighted normals) and baking
mk.finish(v, bevel=0.002)
mk.apply_modifiers(v)
uv2 = uvs(v)
check("lathe UVs survive finish + apply_modifiers", np.isfinite(uv2).all() and uv2[:, 1].max() > 0.9 * plen / tile,
      f"{len(v.data.polygons)} faces after bevel, v max {uv2[:, 1].max():.3f}")

# ---- lathe closed=True (ring / gasket) ------------------------------------------
ring_prof = [(0.03, 0.0), (0.04, 0.0), (0.04, 0.01), (0.03, 0.01), (0.03, 0.0)]   # repeated end is dropped
ring = mk.lathe("ring", ring_prof, segments=24, closed=True, uv_tile=0.05, location=(0.4, 0, 0))
check("lathe closed: watertight torus without a weld", mk.is_closed(ring) and mk.winding_consistent(ring)
      and euler(ring) == 0, f"V {len(ring.data.vertices)} (= 4 x 24), chi {euler(ring)}")
check("lathe closed: no duplicate seam vertices", len(ring.data.vertices) == 4 * 24)
check("lathe rejects a closed profile on the axis", raises(lambda: mk.lathe("x", VASE, closed=True)) is not None)
check("lathe rejects < 2 points", raises(lambda: mk.lathe("x", [(0.1, 0)])) is not None)
check("lathe rejects uv_tile <= 0", raises(lambda: mk.lathe("x", VASE, uv_tile=0)) is not None)

# ---- tube_mesh: backward compatible ---------------------------------------------
path = [(0, 0, 0), (0.1, 0, 0.02), (0.2, 0.05, 0.05), (0.3, 0.05, 0.1)]
t0 = mk.tube_mesh("tube old", path, 0.01, segments=12)
check("tube_mesh default: no UV layer, same counts as before", not t0.data.uv_layers
      and len(t0.data.vertices) == 4 * 12 + 2 and len(t0.data.polygons) == 3 * 12 + 2 * 12 and mk.is_closed(t0))

# ---- tube_mesh uv_tile --------------------------------------------------------------
tt = mk.tube_mesh("tube uv", path, 0.01, segments=12, uv_tile=0.05)
L = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
u = uvs(tt)
check("tube_mesh uv: closed, v = arc length / tile", mk.is_closed(tt) and abs(u[:, 1].max() - L / 0.05) < 1e-4,
      f"v max {u[:, 1].max():.4f} vs {L / 0.05:.4f}")
tmm = mk.tube_mesh("tube mm", [(x * 1000, y * 1000, z * 1000) for x, y, z in path], 10, segments=12, uv_tile=50,
                   units="mm")
check("tube_mesh uv_tile follows units", np.allclose(uvs(tmm), u, atol=1e-4))

# ---- tube_mesh closed=True ----------------------------------------------------------
n = 40
circle = [(0.05 * math.cos(2 * math.pi * k / n), 0.05 * math.sin(2 * math.pi * k / n), 0) for k in range(n + 1)]
loop = mk.tube_mesh("wire ring", circle, 0.006, segments=8, closed=True, uv_tile=0.05)
check("tube_mesh closed: watertight, no caps, repeated end dropped", mk.is_closed(loop) and mk.winding_consistent(loop)
      and len(loop.data.polygons) == n * 8 and euler(loop) == 0, f"{len(loop.data.polygons)} faces")
# non-planar loop: parallel transport leaves a twist that must be spread, not dumped at the seam
saddle = [(0.08 * math.cos(a), 0.08 * math.sin(a), 0.03 * math.sin(2 * a))
          for a in np.linspace(0, 2 * math.pi, 60, endpoint=False)]
sd = mk.tube_mesh("saddle ring", saddle, 0.01, segments=10, closed=True)
bm = bmesh.new()
bm.from_mesh(sd.data)
areas = np.array([f.calc_area() for f in bm.faces])
bm.free()
check("tube_mesh closed non-planar: seam faces like the rest (no twist kink)", mk.is_closed(sd)
      and areas.max() / np.median(areas) < 1.5, f"max/median face area {areas.max() / np.median(areas):.3f}")
check("tube_mesh closed rejects 2 points", raises(lambda: mk.tube_mesh("x", [(0, 0, 0), (1, 0, 0)], 0.01, closed=True))
      is not None)
check("tube_mesh closed rejects end_ext", raises(lambda: mk.tube_mesh("x", circle, 0.01, closed=True, end_ext=0.001))
      is not None)
check("tube_mesh rejects uv_tile <= 0", raises(lambda: mk.tube_mesh("x", path, 0.01, uv_tile=-1)) is not None)
check("mesh_from_data rejects mismatched uvs",
      raises(lambda: mk.mesh_from_data("x", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], uvs=[[(0, 0)]])) is not None)

# ---- multi-part object: UVs per island survive a join through bmesh ---------------
bmj = bmesh.new()
for o in (tt, loop):
    bmj.from_mesh(o.data)
mej = bpy.data.meshes.new("joined")
bmj.to_mesh(mej)
bmj.free()
joined = bpy.data.objects.new("joined", mej)
bpy.context.scene.collection.objects.link(joined)
check("multi-island mesh keeps one UVMap for all islands", joined.data.uv_layers.get("UVMap") is not None
      and len(uvs(joined)) == len(tt.data.loops) + len(loop.data.loops))
bpy.data.objects.remove(joined)


# ---- before / after renders: checker driven by UV ---------------------------------
def checker(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    ck = nt.nodes.new("ShaderNodeTexChecker")
    ck.inputs["Scale"].default_value = 8.0
    nt.links.new(tc.outputs["UV"], ck.inputs["Vector"])
    nt.links.new(ck.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return m


mat = checker("uv checker")
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
parts = {}
for tag, kw in (("before", {}), ("after", {"uv_tile": 0.05})):
    mk.reset_scene()
    mat = checker("uv checker")
    a = mk.lathe(f"vase {tag}", VASE, segments=48, **kw)
    b = mk.tube_mesh(f"hose {tag}", [(0.08, 0, 0.02), (0.14, 0.0, 0.08), (0.12, 0.06, 0.15)], 0.016, segments=16, **kw)
    c = mk.lathe(f"ring {tag}", [(0.07, 0.0), (0.085, 0.0), (0.085, 0.012), (0.07, 0.012)], segments=48,
                 closed=True, **kw)
    for o in (a, b, c):
        mk.assign(o, mat)
    try:
        sheet = render_views.run(OUT, mode="shaded", res=320, views=["three_quarter_left", "front"],
                                 sheet_name=f"uv_{tag}_sheet.png")
        print(f"INFO {tag} render: {sheet}")
    except Exception as ex:  # noqa: BLE001  (EEVEE may be missing on a GPU-less runner)
        print(f"INFO {tag} render skipped: {ex!r}")
done()

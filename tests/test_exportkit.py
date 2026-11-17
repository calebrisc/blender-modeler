"""exportkit: hidden-face removal, origins, lightmap UVs, glTF export + inspection.

    blender -b --factory-startup --python tests/test_exportkit.py -- --out DIR

Leaves DIR/export_test.glb for the "exportcli" test (bmod.py --script exportkit.py).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import check, done, out_dir  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

import exportkit as ek  # noqa: E402
import modelkit as mk  # noqa: E402

OUT = out_dir()
mk.reset_scene()


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc as ex:
        return str(ex) or exc.__name__
    return None


def faces(o):
    return len(o.data.polygons)


def healthy(o):
    """No loose geometry, no zero-area faces, and validate() finds nothing to fix."""
    me = o.data
    used = {v for p in me.polygons for v in p.vertices}
    edge_used = {k for p in me.polygons for k in p.edge_keys}
    zero = sum(1 for p in me.polygons if p.area < 1e-12)
    return (len(used) == len(me.vertices) and len(edge_used) == len(me.edges) and zero == 0
            and not me.validate(verbose=False))


# ---- scene: a body, a bolt half sunk into it, a buried pin, an open sheet ------------
body = mk.box("body", (0.4, 0.3, 0.2), (0, 0, 0.1))
bolt = mk.box("bolt", (0.04, 0.04, 0.06), (0.1, 0.05, 0.2))          # half inside the body's top
pin = mk.box("pin", (0.02, 0.02, 0.05), (-0.1, 0, 0.1))              # completely inside
sheet = mesh = bpy.data.meshes.new("sheet")
mesh.from_pydata([(-0.05, -0.05, 0.15), (0.05, -0.05, 0.15), (0.05, 0.05, 0.15), (-0.05, 0.05, 0.15)], [],
                 [(0, 1, 2, 3)])
sheet = bpy.data.objects.new("sheet", mesh)                         # open, buried in the body
bpy.context.scene.collection.objects.link(sheet)
# an open "tray" (box without its top) around a cube: open meshes must not hide anything
tray = mk.box("tray", (0.1, 0.1, 0.1), (0.5, 0, 0.05))
bm = bmesh.new()
bm.from_mesh(tray.data)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z > 0.5], context="FACES_ONLY")
bm.to_mesh(tray.data)
bm.free()
cube = mk.box("cube in tray", (0.04, 0.04, 0.04), (0.5, 0, 0.03))
# one object, two islands: a hex head on a washer (washer half sunk into the body)
p = bmesh.new()
bmesh.ops.create_cone(p, cap_ends=True, segments=12, radius1=0.012, radius2=0.012, depth=0.004,
                      matrix=Matrix.Translation((-0.1, 0.08, 0.2005)))
bmesh.ops.create_cone(p, cap_ends=True, segments=6, radius1=0.008, radius2=0.008, depth=0.006,
                      matrix=Matrix.Translation((-0.1, 0.08, 0.2052)))
hw = bpy.data.meshes.new("head+washer")
p.to_mesh(hw)
p.free()
fastener = bpy.data.objects.new("head+washer", hw)
bpy.context.scene.collection.objects.link(fastener)

# known UVs on the bolt, keyed by face centre, to prove the survivors keep theirs
lay = bolt.data.uv_layers.new(name="UVMap")
for poly in bolt.data.polygons:
    for li in poly.loop_indices:
        lay.data[li].uv = (poly.center.x * 10 + li * 0.01, poly.center.z * 10)
uv_before = {tuple(round(c, 5) for c in poly.center): [tuple(lay.data[li].uv) for li in poly.loop_indices]
             for poly in bolt.data.polygons}

parts = [body, bolt, pin, sheet, tray, cube, fastener]
before = {o.name: faces(o) for o in parts}
preview = ek.remove_hidden_faces(parts, dry_run=True)
check("dry_run reports but changes nothing", {o.name: faces(o) for o in parts} == before and preview["pin"] == 6,
      str(preview))
removed = ek.remove_hidden_faces(parts)
check("dry_run predicted exactly what was removed", preview == removed)
check("buried part loses every face", removed["pin"] == 6 and faces(pin) == 0, f"{removed['pin']} removed")
check("half-sunk part loses only its buried face", removed["bolt"] == 1 and faces(bolt) == 5
      and max((bolt.matrix_world @ p.center).z for p in bolt.data.polygons) > 0.22, f"{removed['bolt']} removed")
check("visible body faces stay (partly covered faces are kept)", removed["body"] == 0, f"{removed['body']}")
check("open sheet inside a body is removed", removed["sheet"] == 1)
check("open meshes hide nothing (cube in an open tray keeps its faces)", removed["cube in tray"] == 0
      and removed["tray"] == 0, f"cube {removed['cube in tray']}, tray {removed['tray']}")
check("islands of one object hide each other (head underside on its washer)", removed["head+washer"] >= 2,
      f"{removed['head+washer']} faces (washer bottom in the body, head bottom on the washer)")
check("mesh integrity after removal (no loose / zero-area / invalid)", all(healthy(o) for o in parts),
      ", ".join(f"{o.name} {before[o.name]}->{faces(o)}" for o in parts))
lay = bolt.data.uv_layers["UVMap"]
uv_after = {tuple(round(c, 5) for c in poly.center): [tuple(lay.data[li].uv) for li in poly.loop_indices]
            for poly in bolt.data.polygons}
same = all(np.allclose(sorted(uv_after[k]), sorted(uv_before[k])) for k in uv_after)
check("UVs of the surviving faces are unchanged", same and len(uv_after) == 5)
withmod = mk.finish(mk.box("bevelled", (0.1, 0.1, 0.1), (2, 0, 0)), bevel=0.005)
check("objects with modifiers are refused (ValueError)", raises(lambda: ek.remove_hidden_faces([withmod])))
inst = bpy.data.objects.new("instance", body.data)
bpy.context.scene.collection.objects.link(inst)
check("shared meshes are refused (would change every instance)", raises(lambda: ek.remove_hidden_faces([body])))
bpy.data.objects.remove(inst)
empty = bpy.data.objects.new("empty", None)
check("non-mesh objects are refused (TypeError)", raises(lambda: ek.remove_hidden_faces([empty]), TypeError))
for o in (withmod, tray, cube, sheet, pin):
    bpy.data.objects.remove(o)

# ---- set_origin ------------------------------------------------------------------
lever = mk.box("lever", (0.01, 0.06, 0.01), (0.1, -0.2, 0.3))
bpy.context.view_layer.update()
world_before = [lever.matrix_world @ v.co for v in lever.data.vertices]
ek.set_origin(lever, (0.1, -0.17, 0.3))
world_after = [lever.matrix_world @ v.co for v in lever.data.vertices]
check("set_origin moves the pivot, not the geometry",
      (lever.matrix_world.translation - Vector((0.1, -0.17, 0.3))).length < 1e-6
      and max((a - b).length for a, b in zip(world_before, world_after)) < 1e-6)
twin = bpy.data.objects.new("twin", lever.data)
check("set_origin refuses a shared mesh", raises(lambda: ek.set_origin(lever, (0, 0, 0))))
bpy.data.objects.remove(twin)

# ---- lightmap UV2 on a multi-island, tiling-UV body --------------------------------
vase = mk.lathe("vase", [(0, 0), (0.05, 0), (0.06, 0.05), (0.04, 0.15), (0, 0.15)], segments=32,
                location=(0, 0, 0.2))
uv0 = np.empty(len(bolt.data.loops) * 2, np.float32)
bolt.data.uv_layers["UVMap"].data.foreach_get("uv", uv0)
st = ek.lightmap_uv(bolt)
uv1 = np.empty(len(bolt.data.loops) * 2, np.float32)
bolt.data.uv_layers["UVMap"].data.foreach_get("uv", uv1)
check("lightmap_uv: UV2 with no overlap, inside 0..1", st["overlap_ratio"] == 0 and st["outside_0_1"] == 0
      and st["coverage"] > 0.1, str(st))
check("lightmap_uv keeps UVMap untouched and active for rendering", np.allclose(uv0, uv1)
      and bolt.data.uv_layers["UVMap"].active_render and bolt.data.uv_layers.active.name == "UVMap",
      f"max change {np.abs(uv0 - uv1).max() if len(uv0) else 0:.6f}, active {bolt.data.uv_layers.active.name}, "
      f"render {[u.name for u in bolt.data.uv_layers if u.active_render]}")
check("uv_overlap sees tiling UVs as overlapping", ek.uv_overlap(bolt, "UVMap")["outside_0_1"] > 0)
st2 = ek.lightmap_uv(fastener)
check("lightmap_uv on a mesh without UVs and with 2 islands", st2["overlap_ratio"] == 0
      and len(fastener.data.uv_layers) == 1, str(st2))

# ---- glTF: textured PBR material, normal map, hierarchy, extras ---------------------
img = bpy.data.images.new("checker", 64, 64)
px = np.indices((64, 64)).sum(0) % 2
img.pixels.foreach_set(np.repeat(px.astype(np.float32).ravel(), 4))
img.filepath_raw = os.path.join(OUT, "export_checker.png")
img.file_format = "PNG"
img.save()
mat = bpy.data.materials.new("textured")
mat.use_nodes = True
N, Ln = mat.node_tree.nodes, mat.node_tree.links
bsdf = N["Principled BSDF"]
tex = N.new("ShaderNodeTexImage")
tex.image = img
Ln.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nm = N.new("ShaderNodeNormalMap")
Ln.new(tex.outputs["Color"], nm.inputs["Color"])
Ln.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
sep = N.new("ShaderNodeSeparateColor")
Ln.new(tex.outputs["Color"], sep.inputs["Color"])
Ln.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
Ln.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
for o in (body, bolt, vase, lever):
    if not o.data.uv_layers:
        o.data.uv_layers.new(name="UVMap")
    mk.assign(o, mat)
ek.lightmap_uv(body)
ek.lightmap_uv(vase)
ek.lightmap_uv(lever)
root = bpy.data.objects.new("Asset", None)
bpy.context.scene.collection.objects.link(root)
for o in (body, bolt, vase, lever):
    o.parent = root
lever["interactive"] = "lever"
export_objs = [root, body, bolt, vase, lever]
for o in export_objs[1:]:
    o.data.calc_loop_triangles()
expect_tris = sum(len(o.data.loop_triangles) for o in export_objs[1:])
lo, hi = mk.bounds(export_objs[1:])
expect_size = tuple(hi - lo)
fastener.select_set(True)
bpy.context.view_layer.objects.active = fastener
glb = os.path.join(OUT, "export_test.glb")
res = ek.export_gltf(glb, export_objs)
check("export_gltf writes a GLB", os.path.isfile(glb) and res["settings"]["export_format"] == "GLB",
      f"{os.path.getsize(glb)} bytes")
check("temporary triangulate modifiers are gone, selection restored",
      not any(m.name == "_export_triangulate" for o in export_objs[1:] for m in o.modifiers)
      and fastener.select_get() and bpy.context.view_layer.objects.active == fastener)
n_obj, n_mat, n_img = len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.images)
rep = ek.inspect_gltf(glb)
check("inspect leaves the scene as it was", (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.images))
      == (n_obj, n_mat, n_img))
check("re-import: triangle count matches", rep["triangles"] == expect_tris, f"{rep['triangles']} vs {expect_tris}")
check("re-import: size matches (Z up, metres)", np.allclose(rep["bounds"]["size"], expect_size, atol=1e-4),
      f"{rep['bounds']['size']} vs {[round(v, 4) for v in expect_size]}")
prims = rep["primitive_list"]
check("every primitive has NORMAL, TANGENT and 2 UV sets", all(p["normal"] and p["tangent"] and p["uv_sets"] >= 2
                                                                for p in prims), f"{len(prims)} primitives")
m = rep["materials"].get("textured", {})
check("material keeps base colour, metallic-roughness and normal textures",
      m.get("base_color_tex") and m.get("metal_rough_tex") and m.get("normal_tex"), str(m))
check("custom properties arrive as node extras", rep["nodes"].get("lever", {}).get("extras", {}).get("interactive")
      == "lever")
check("hierarchy exported (root node with 4 children)", rep["nodes"].get("Asset", {}).get("children") == 4)
check("check_gltf passes on the right expectations",
      ek.check_gltf(rep, size=expect_size, tris=expect_tris, floor_z=lo.z, require_uv2=True) == [])
bad = ek.check_gltf(rep, size=(1, 1, 1), tris=expect_tris + 1, floor_z=0.5)
check("check_gltf reports wrong size, triangle count and floor", len(bad) == 3, "; ".join(bad))
gl2 = ek.export_gltf(os.path.join(OUT, "sub", "export_test.gltf"), export_objs)
check("export_gltf .gltf (separate files) into a new folder", os.path.isfile(gl2["path"])
      and ek.inspect_gltf(gl2["path"])["triangles"] == expect_tris)

# ---- error cases ---------------------------------------------------------------------
check("bad extension -> ValueError", raises(lambda: ek.export_gltf(os.path.join(OUT, "x.fbx"), export_objs)))
check("unknown exporter option -> ValueError", raises(lambda: ek.export_gltf(glb, export_objs, no_such_option=1)))
check("nothing to export -> ValueError", raises(lambda: ek.export_gltf(glb, [])))
check("missing file -> FileNotFoundError", raises(lambda: ek.inspect_gltf(os.path.join(OUT, "missing.glb")),
                                                  FileNotFoundError))
junk = os.path.join(OUT, "junk.glb")
with open(junk, "wb") as f:
    f.write(b"not a glb file at all")
check("not a GLB -> ValueError", raises(lambda: ek.read_gltf_json(junk)))
print(f"INFO blender {bpy.app.version_string}, glTF add-on "
      f"{'.'.join(map(str, __import__('io_scene_gltf2').bl_info['version']))}")
done()

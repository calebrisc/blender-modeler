"""Hand a finished model to a game engine or another tool: hidden-face removal, a
second (lightmap) UV set, origins for moving parts, glTF/GLB export and an
independent check of the exported file.

    import exportkit as ek
    ek.remove_hidden_faces(parts)              # buried caps, washer undersides, label backs
    ek.set_origin(lever, hinge_point)          # pivot for a part the game animates
    ek.lightmap_uv(body)                       # non-overlapping "UV2" (glTF TEXCOORD_1)
    ek.export_gltf("out/asset.glb", [root, body, lever])
    rep = ek.inspect_gltf("out/asset.glb")     # what an engine will actually get
    errors = ek.check_gltf(rep, size=(1.2, 0.65, 0.85), tris=23213)

From the command line (a separate Blender process, so the check is independent):

    python bmod.py --script exportkit.py -- inspect out/asset.glb [--size X Y Z] [--tol 0.002]
        [--tris N] [--uv2] ; exit 1 when the check finds errors

Runs inside Blender. lightmap_uv, export_gltf and inspect_gltf use bpy.ops (UV
unwrap/pack and the bundled glTF add-on have no data API): run them headless
(bmod.py --script), not from a live MCP session. Units: metres, Blender Z up
(the glTF exporter converts to Y up; inspect_gltf re-imports, so reports are Z up).
"""

import json
import math
import os
import struct
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import hostkit

DEFAULT_TOL = 0.00015          # faces this close to another part's skin count as covered (m)


# ---------------------------------------------------------------- hidden faces

def _require_mesh(obj, allow_modifiers=False):
    if not isinstance(obj, bpy.types.Object) or obj.type != "MESH":
        raise TypeError(f"{getattr(obj, 'name', obj)!r} is not a mesh object")
    if obj.modifiers and not allow_modifiers:
        raise ValueError(f"{obj.name}: apply its modifiers first (modelkit.apply_modifiers)")


def _face_islands(bm):
    """Island id per face (faces sharing an edge) and the island count."""
    bm.faces.ensure_lookup_table()
    fid = [-1] * len(bm.faces)
    n = 0
    for f in bm.faces:
        if fid[f.index] >= 0:
            continue
        stack, fid[f.index] = [f], n
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if fid[g.index] < 0:
                        fid[g.index] = n
                        stack.append(g)
        n += 1
    return fid, n


def _closed_islands(obj):
    """[(island id, BVH, lo, hi)] for every watertight island of obj, in world space.
    Open islands are skipped: an open sheet has no inside, so it cannot hide anything."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    fid, n = _face_islands(bm)
    out = []
    for k in range(n):
        faces = [f for f in bm.faces if fid[f.index] == k]
        if len(faces) < 4 or any(len(e.link_faces) != 2 for f in faces for e in f.edges):
            continue
        vs = sorted({v.index for f in faces for v in f.verts})
        bm.verts.ensure_lookup_table()
        remap = {v: i for i, v in enumerate(vs)}
        co = [bm.verts[v].co.copy() for v in vs]
        tree = BVHTree.FromPolygons(co, [[remap[v.index] for v in f.verts] for f in faces])
        a = np.array(co)
        out.append((k, tree, a.min(0), a.max(0), max(float(np.ptp(a, 0).max()) * 1e-6, 1e-9)))
    bm.free()
    return out


def _covered(tree, eps, p, tol, rays):
    """Inside the closed island (ray parity, majority vote) or within tol of its skin."""
    hit = tree.find_nearest(p)
    if hit[0] is not None and hit[3] <= tol:
        return True
    need, yes, no = rays // 2 + 1, 0, 0
    for d in hostkit.RAY_DIRS[:rays]:
        d = Vector(d).normalized()
        k, q = 0, p
        for _ in range(hostkit.MAX_HITS):
            h = tree.ray_cast(q, d)[0]
            if h is None:
                break
            k += 1
            q = h + d * eps
        yes, no = yes + (k % 2), no + (1 - k % 2)
        if yes >= need or no >= need:
            break
    return yes >= need


def remove_hidden_faces(objects, occluders=None, tol=DEFAULT_TOL, rays=3, dry_run=False):
    """Delete faces that nobody can see because they lie completely inside, or flush
    against (within tol, metres), another closed part: welded tube ends, the underside
    of washers and bolt heads, caps buried in a body, the backs of labels.

    A face goes only when all its corners, its centre and its edge midpoints are
    covered, by closed islands of `occluders` (default: `objects`) other than the
    face's own island. Islands of one object hide each other (a bolt head on its
    washer). Open islands never hide anything. Loose vertices/edges left behind are
    removed; UVs, custom normals and materials of the remaining faces are untouched.
    Meshes may end up open, which is fine for rendering and game engines but not for
    booleans or 3D printing, so do this last.

    Data loss: the base meshes are edited in place (there is no undo in a headless
    run), so work on a copy or a versioned file. A face lying on another part's skin
    (within tol) counts as covered, including a decal plane placed exactly on a
    surface; lift decals off the surface or lower tol. dry_run=True only counts the
    faces that would go and changes nothing.

    objects: single-user mesh objects without modifiers (TypeError / ValueError
    otherwise; a mesh shared by several objects would lose faces in all of them).
    Returns {object name: faces removed (or that would be, with dry_run)}. Cost is a
    few BVH queries per face; expect seconds for ~50k faces.
    """
    objects = list(objects)
    occluders = objects if occluders is None else list(occluders)
    for o in objects + occluders:
        _require_mesh(o)
    for o in objects:
        if o.data.users > 1:
            raise ValueError(f"{o.name}: mesh is shared by {o.data.users} objects; deleting faces would change "
                             "all of them (make it single-user first)")
    if rays not in (1, 3):
        raise ValueError("rays must be 1 or 3")
    bpy.context.view_layer.update()      # matrix_world of freshly placed objects is stale until then
    occ = [(o.name, k, t, lo - tol, hi + tol, eps) for o in occluders for k, t, lo, hi, eps in _closed_islands(o)]
    removed = {}
    for o in objects:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        fid, _ = _face_islands(bm)
        M = o.matrix_world
        kill = []
        for f in bm.faces:
            pts = [M @ v.co for v in f.verts]
            samples = pts + [sum(pts, Vector()) / len(pts)] + [(pts[i] + pts[i - 1]) / 2 for i in range(len(pts))]
            arr = np.array(samples)
            lo, hi = arr.min(0), arr.max(0)
            cands = [(t, eps) for name, k, t, a, b, eps in occ
                     if not (name == o.name and k == fid[f.index]) and np.all(lo >= a) and np.all(hi <= b)]
            if cands and all(any(_covered(t, eps, p, tol, rays) for t, eps in cands) for p in samples):
                kill.append(f)
        if kill and not dry_run:
            bmesh.ops.delete(bm, geom=kill, context="FACES_ONLY")
            loose_e = [e for e in bm.edges if not e.link_faces]
            bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
            loose_v = [v for v in bm.verts if not v.link_faces]
            bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
            bm.to_mesh(o.data)
            o.data.update()
        bm.free()
        removed[o.name] = len(kill)
    hostkit.clear_cache()
    return removed


# ---------------------------------------------------------------- origins and UVs

def set_origin(obj, point):
    """Move obj's origin to `point` (world space) without moving its geometry, e.g. to
    a hinge or a rotation axis the game animates around. Children keep their world
    placement. Raises ValueError if the mesh is shared by other objects."""
    _require_mesh(obj, allow_modifiers=True)
    if obj.data.users > 1:
        raise ValueError(f"{obj.name}: mesh is shared by {obj.data.users} objects; make it single-user first")
    bpy.context.view_layer.update()
    local = obj.matrix_world.inverted() @ Vector(point)
    kids = [(c, c.matrix_world.copy()) for c in obj.children]
    obj.data.transform(Matrix.Translation(-local))
    obj.matrix_world = obj.matrix_world @ Matrix.Translation(local)
    bpy.context.view_layer.update()
    for c, mw in kids:
        c.matrix_world = mw
    return obj


def uv_overlap(obj, layer=None, res=512):
    """How well a UV layer works as a lightmap/AO atlas: rasterise its triangles on a
    res x res grid. Returns covered texels, texels hit by more than one triangle,
    their ratio and the fraction of UV corners outside 0..1 (all should be 0 for a
    lightmap; tiling texture UVs overlap by design)."""
    _require_mesh(obj, allow_modifiers=True)
    me = obj.data
    lay = me.uv_layers.get(layer) if layer else me.uv_layers.active
    if lay is None:
        raise ValueError(f"{obj.name}: no UV layer {layer!r}")
    me.calc_loop_triangles()
    uv = np.empty(len(me.loops) * 2, np.float32)
    lay.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("loops", tri)
    cnt = np.zeros((res, res), np.uint16)
    for a, b, c in uv[tri.reshape(-1, 3)] * res:
        x0, y0 = np.maximum(np.floor(np.minimum(np.minimum(a, b), c)).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(np.maximum(np.maximum(a, b), c)).astype(int), res)
        d = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if x1 <= x0 or y1 <= y0 or abs(d) < 1e-12:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1] + 0.5
        w1 = ((xs - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (ys - a[1])) / d
        w2 = ((b[0] - a[0]) * (ys - a[1]) - (xs - a[0]) * (b[1] - a[1])) / d
        cnt[y0:y1, x0:x1] += ((w1 >= 0) & (w2 >= 0) & (w1 + w2 <= 1)).astype(np.uint16)
    covered, over = int((cnt > 0).sum()), int((cnt > 1).sum())
    return {"layer": lay.name, "covered_texels": covered, "overlap_texels": over,
            "overlap_ratio": round(over / max(covered, 1), 5), "coverage": round(covered / res / res, 4),
            "outside_0_1": round(float(((uv < 0) | (uv > 1)).any(1).mean()) if len(uv) else 0.0, 5)}


def lightmap_uv(obj, name="UV2", angle=66.0, margin=0.004):
    """Add (or redo) a second, non-overlapping UV set for lightmaps / baked AO: Smart
    UV Project, then pack. glTF exports it as TEXCOORD_1 (Godot UV2, Unity uv1,
    Unreal lightmap channel) when the mesh already has a UV layer; on a mesh without
    UVs it becomes the only layer (TEXCOORD_0). The first UV layer stays active for
    rendering and is not modified. Restores mode, selection and the active object. Returns
    uv_overlap() of the new layer. Uses bpy.ops: headless only."""
    _require_mesh(obj, allow_modifiers=True)
    me = obj.data
    if not me.polygons:
        raise ValueError(f"{obj.name}: no faces to unwrap")
    # keep names, not layer references: adding a layer reallocates them
    render = next((u.name for u in me.uv_layers if u.active_render), None)
    if me.uv_layers.get(name) is None and me.uv_layers.new(name=name) is None:
        raise RuntimeError(f"{obj.name}: cannot add another UV layer (Blender allows 8)")
    if render is None or render == name:
        render = next((u.name for u in me.uv_layers if u.name != name), name)
    lay = me.uv_layers[name]
    vl = bpy.context.view_layer
    prev_active, prev_sel = vl.objects.active, [o for o in vl.objects if o.select_get()]
    prev_mode = obj.mode
    for o in prev_sel:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj
    me.uv_layers.active = lay
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=margin, area_weight=0.0,
                                 correct_aspect=True, scale_to_bounds=False)
        props = bpy.ops.uv.pack_islands.get_rna_type().properties.keys()
        want = {"rotate": True, "margin": margin, "shape_method": "CONCAVE", "margin_method": "SCALED"}
        bpy.ops.uv.pack_islands(**{k: v for k, v in want.items() if k in props})
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=prev_mode)
        obj.select_set(False)
        for o in prev_sel:
            o.select_set(True)
        vl.objects.active = prev_active
    me.uv_layers.active = me.uv_layers[render]
    me.uv_layers[render].active_render = True
    return uv_overlap(obj, name)


# ---------------------------------------------------------------- glTF export

_DEFAULTS = {"export_apply": True, "export_yup": True, "export_texcoords": True, "export_normals": True,
             "export_tangents": True, "export_materials": "EXPORT", "export_image_format": "AUTO",
             "export_cameras": False, "export_lights": False, "export_extras": True, "export_animations": False}


def export_gltf(path, objects, triangulate=True, **settings):
    """Export exactly `objects` (selection-based) to .glb (binary) or .gltf
    (separate files) with game-friendly defaults: modifiers applied, Y up, UVs,
    normals, tangents, materials and custom properties (as extras).

    triangulate: export through temporary Triangulate modifiers (custom normals
    kept), because tangents can't be computed on n-gons; they are removed again,
    also when the export fails. settings: extra/overriding exporter options; an
    option this Blender's exporter doesn't have raises ValueError (defaults it lacks
    are skipped). Selection and active object are restored. The parent folder is
    created. Returns {"path", "settings"}.
    """
    objects = [o for o in objects if o is not None]
    if not objects:
        raise ValueError("export_gltf: nothing to export")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".glb", ".gltf"):
        raise ValueError(f"export_gltf: path must end in .glb or .gltf, got {path!r}")
    if not hasattr(bpy.ops.export_scene, "gltf"):
        raise RuntimeError("the glTF add-on (io_scene_gltf2) is not enabled")
    props = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
    unknown = sorted(set(settings) - props)
    if unknown:
        raise ValueError(f"export_gltf: this Blender's exporter has no option(s) {unknown}")
    kw = {k: v for k, v in _DEFAULTS.items() if k in props}
    kw.update(settings)
    kw.update(filepath=os.path.abspath(path), use_selection=True,
              export_format="GLB" if ext == ".glb" else "GLTF_SEPARATE")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    vl = bpy.context.view_layer
    prev_active, prev_sel = vl.objects.active, [o for o in vl.objects if o.select_get()]
    temp = []
    try:
        if triangulate:
            for o in objects:
                if o.type == "MESH" and not any(m.type == "TRIANGULATE" for m in o.modifiers):
                    t = o.modifiers.new("_export_triangulate", "TRIANGULATE")
                    t.quad_method, t.ngon_method = "BEAUTY", "BEAUTY"
                    if hasattr(t, "keep_custom_normals"):
                        t.keep_custom_normals = True
                    temp.append((o, t))
        for o in vl.objects:
            o.select_set(False)
        for o in objects:
            o.select_set(True)
        vl.objects.active = objects[0]
        bpy.ops.export_scene.gltf(**kw)
    finally:
        for o, t in temp:
            o.modifiers.remove(t)
        for o in vl.objects:
            o.select_set(False)
        for o in prev_sel:
            o.select_set(True)
        vl.objects.active = prev_active
    return {"path": kw["filepath"], "settings": {k: v for k, v in kw.items() if k != "filepath"}}


def read_gltf_json(path):
    """The JSON part of a .glb or .gltf file (stdlib only). Raises FileNotFoundError,
    or ValueError when the file is not valid glTF 2.0."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as f:
        data = f.read()
    if path.lower().endswith(".gltf"):
        return json.loads(data.decode("utf-8"))
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"{path}: not a GLB file (bad magic)")
    version, _ = struct.unpack_from("<II", data, 4)
    clen, ctype = struct.unpack_from("<II", data, 12)
    if version != 2 or ctype != 0x4E4F534A:
        raise ValueError(f"{path}: unsupported GLB version {version} or missing JSON chunk")
    return json.loads(data[20:20 + clen].decode("utf-8"))


def inspect_gltf(path):
    """What an engine gets from an exported file. File level (JSON): primitives and
    their attributes (TANGENT, TEXCOORD_n), material texture slots, node names and
    extras, image count. Geometry level: the file is imported, measured (triangles,
    world bounds, degenerate triangles, NaN) and everything it brought in is removed
    again, so the open scene is left as it was. Raises FileNotFoundError /
    ValueError for missing or invalid files."""
    gj = read_gltf_json(path)
    rep = {"path": os.path.abspath(path), "bytes": os.path.getsize(path), "nodes": {}, "materials": {},
           "primitives": 0, "images": len(gj.get("images", []))}
    for n in gj.get("nodes", []):
        rep["nodes"][n.get("name", "?")] = {"mesh": "mesh" in n, "extras": n.get("extras", {}),
                                            "children": len(n.get("children", []))}
    for m in gj.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        rep["materials"][m.get("name", "?")] = {"base_color_tex": "baseColorTexture" in pbr,
                                                "metal_rough_tex": "metallicRoughnessTexture" in pbr,
                                                "normal_tex": "normalTexture" in m}
    prims = []
    for mesh in gj.get("meshes", []):
        for p in mesh.get("primitives", []):
            a = p.get("attributes", {})
            prims.append({"mesh": mesh.get("name", "?"), "material": p.get("material"), "normal": "NORMAL" in a,
                          "tangent": "TANGENT" in a, "uv_sets": sum(1 for k in a if k.startswith("TEXCOORD_"))})
    rep["primitives"] = len(prims)
    rep["primitive_list"] = prims

    before = {c: set(getattr(bpy.data, c)) for c in ("objects", "meshes", "materials", "images", "collections")}
    try:
        bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))
        new = [o for o in bpy.data.objects if o not in before["objects"]]
        tris, degenerate, nan, pts = 0, 0, False, []
        for o in new:
            if o.type != "MESH":
                continue
            me = o.data
            me.calc_loop_triangles()
            co = np.empty(len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            M = np.array(o.matrix_world)
            w = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
            tv = np.empty(len(me.loop_triangles) * 3, np.int64)
            me.loop_triangles.foreach_get("vertices", tv)
            t = w[tv.reshape(-1, 3)]
            area = np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) / 2
            tris += len(t)
            degenerate += int((area < 1e-12).sum())
            nan = nan or bool(np.isnan(w).any())
            rep["nodes"].setdefault(o.name, {})["tris"] = len(t)
            if len(w):
                pts.append(w)
        rep["triangles"], rep["degenerate_triangles"], rep["nan"] = tris, degenerate, nan
        if pts:
            P = np.vstack(pts)
            rep["bounds"] = {"min": P.min(0).round(5).tolist(), "max": P.max(0).round(5).tolist(),
                             "size": np.ptp(P, 0).round(5).tolist()}
    finally:
        for o in [o for o in bpy.data.objects if o not in before["objects"]]:
            bpy.data.objects.remove(o, do_unlink=True)
        for coll in ("meshes", "materials", "images", "collections"):
            data = getattr(bpy.data, coll)
            for block in [b for b in data if b not in before[coll]]:
                data.remove(block)
    return rep


def check_gltf(rep, size=None, tol=0.002, tris=None, floor_z=0.0, require_uv2=False):
    """Errors (list of strings, empty = pass) for an inspect_gltf() report:
    overall size (metres, Blender X Y Z) within tol, triangle count, lowest point at
    floor_z (None skips), degenerate triangles / NaN, a normal map without
    tangents, and TEXCOORD_1 on every primitive when require_uv2."""
    err = []
    b = rep.get("bounds")
    if b is None:
        err.append("no mesh geometry in the file")
    else:
        if size is not None:
            off = [f"{'XYZ'[i]} {b['size'][i]:.4f} vs {s:.4f}" for i, s in enumerate(size)
                   if s is not None and abs(b["size"][i] - s) > tol]
            if off:
                err.append("size off: " + "; ".join(off) + " m")
        if floor_z is not None and abs(b["min"][2] - floor_z) > tol:
            err.append(f"lowest point z = {b['min'][2]:.4f} m, expected {floor_z}")
    if tris is not None and rep.get("triangles") != tris:
        err.append(f"triangles {rep.get('triangles')} vs expected {tris}")
    if rep.get("degenerate_triangles"):
        err.append(f"{rep['degenerate_triangles']} degenerate triangles")
    if rep.get("nan"):
        err.append("NaN vertex positions")
    mats = rep.get("materials", {})
    names = list(mats)
    for p in rep.get("primitive_list", []):
        m = mats.get(names[p["material"]]) if p["material"] is not None and p["material"] < len(names) else None
        if m and m["normal_tex"] and not p["tangent"]:
            err.append(f"mesh {p['mesh']}: normal map but no TANGENT attribute (engine must generate them)")
        if require_uv2 and p["uv_sets"] < 2:
            err.append(f"mesh {p['mesh']}: no second UV set (TEXCOORD_1)")
    return err


def _cli(argv):
    if not argv or argv[0] != "inspect" or len(argv) < 2:
        print("usage: exportkit.py inspect FILE.glb [--size X Y Z] [--tol M] [--tris N] [--uv2]")
        return 2
    path, args = argv[1], argv[2:]
    size = tris = None
    tol = 0.002
    if "--size" in args:
        i = args.index("--size")
        size = [float(x) for x in args[i + 1:i + 4]]
    if "--tol" in args:
        tol = float(args[args.index("--tol") + 1])
    if "--tris" in args:
        tris = int(args[args.index("--tris") + 1])
    rep = inspect_gltf(path)
    errors = check_gltf(rep, size=size, tol=tol, tris=tris, require_uv2="--uv2" in args)
    print(f"GLTF: {rep['triangles']} triangles, size {rep.get('bounds', {}).get('size')}, "
          f"{len(rep['materials'])} materials, {rep['images']} images, {rep['primitives']} primitives, "
          f"{rep['bytes'] / 1e6:.2f} MB")
    for e in errors:
        print(f"  [ERROR] {e}")
    print(f"GLTF CHECK: {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    code = _cli(sys.argv[1:])
    if code:
        sys.exit(code)

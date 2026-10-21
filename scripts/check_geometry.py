"""Numeric geometry checks for a Blender scene.

Vision models are bad at spotting floating parts, gaps and wrong proportions in
renders, so this measures them instead:

- floating parts: every connected mesh island is a "part"; parts are linked when
  they touch or intersect, and anything not linked to the grounded group is
  reported with its distance to the nearest neighbour
- mesh health: non-manifold edges, loose geometry, degenerate faces, inverted
  normals, unapplied/negative object scale, n-gons
- spec checks (optional spec.json): overall size, per-part size, required
  attachments, missing parts

Runs inside Blender. From an MCP code tool or the Python console:

    import sys; sys.path.insert(0, r"<skill>/scripts")
    import check_geometry
    report = check_geometry.run(spec_path="spec.json")

Objects whose name starts with "_" (cutters, helpers) and objects hidden from
render are ignored.
"""

import json
import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

GEOM_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}
MAX_ISLANDS_PER_OBJECT = 64   # above this an object counts as one part (arrays, text)
MAX_SAMPLES = 3000            # vertices sampled per part for distance queries


def target_objects(collection=None, names=None):
    if names:
        objs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    elif collection:
        objs = list(bpy.data.collections[collection].all_objects)
    else:
        objs = list(bpy.context.scene.objects)
    out = []
    for o in objs:
        if o.type not in GEOM_TYPES or o.name.startswith("_"):
            continue
        if o.hide_render or not o.visible_get():
            continue
        out.append(o)
    return out


def _world_bmesh(obj, depsgraph):
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    ev.to_mesh_clear()
    bm.transform(ev.matrix_world)
    return bm


def _islands(bm):
    bm.verts.index_update()
    island_of = [-1] * len(bm.verts)
    count = 0
    for v in bm.verts:
        if island_of[v.index] != -1:
            continue
        stack = [v]
        island_of[v.index] = count
        while stack:
            x = stack.pop()
            for e in x.link_edges:
                o = e.other_vert(x)
                if island_of[o.index] == -1:
                    island_of[o.index] = count
                    stack.append(o)
        count += 1
    return island_of, count


def _health(obj, bm):
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    multi = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    loose_v = sum(1 for v in bm.verts if not v.link_edges)
    loose_e = sum(1 for e in bm.edges if not e.link_faces)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-12)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    closed = boundary == 0 and multi == 0 and len(bm.faces) > 0
    volume = bm.calc_volume(signed=True) if closed else None
    return {
        "verts": len(bm.verts),
        "faces": len(bm.faces),
        "open_boundary_edges": boundary,
        "nonmanifold_edges": multi,
        "loose_verts": loose_v,
        "loose_edges": loose_e,
        "degenerate_faces": degenerate,
        "ngons": ngons,
        "closed": closed,
        "volume": round(volume, 6) if volume is not None else None,
        "scale": [round(s, 4) for s in obj.scale],
        "dimensions": [round(d, 4) for d in obj.dimensions],
    }


def _bbox_gap(a, b):
    d = 0.0
    for i in range(3):
        g = max(b["min"][i] - a["max"][i], a["min"][i] - b["max"][i], 0.0)
        d += g * g
    return math.sqrt(d)


def _min_distance(a, b, tol):
    """Smallest vertex-to-surface distance between two parts (both directions)."""
    if a["tree"].overlap(b["tree"]):
        return 0.0, True
    best = math.inf
    for src, dst in ((a, b), (b, a)):
        pts = src["coords"]
        step = max(1, len(pts) // MAX_SAMPLES)
        for p in pts[::step]:
            hit = dst["tree"].find_nearest(p)
            if hit[0] is not None and hit[3] < best:
                best = hit[3]
                if best <= tol:
                    return best, False
    return best, False


def _penetration(a, b):
    """Approximate how deep the two parts sink into each other (0 = just touching)."""
    depth = 0.0
    for src, dst in ((a, b), (b, a)):
        pts = src["coords"]
        step = max(1, len(pts) // MAX_SAMPLES)
        for p in pts[::step]:
            loc, normal, _, dist = dst["tree"].find_nearest(p)
            if loc is not None and (p - loc).dot(normal) < 0 and dist > depth:
                depth = dist
    return depth


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _fmt(v):
    return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"


def run(spec_path=None, spec=None, collection=None, names=None, tol=None,
        out_path=None, verbose=True):
    if spec is None and spec_path:
        with open(spec_path, "r", encoding="utf-8-sig") as f:
            spec = json.load(f)
    spec = spec or {}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objs = target_objects(collection or spec.get("collection"), names)

    errors, warnings, info = [], [], []
    report = {"objects": {}, "errors": errors, "warnings": warnings, "info": info}
    if not objs:
        errors.append("No visible geometry objects found.")
        return _finish(report, out_path, verbose)

    # Build parts (mesh islands) and per-object health.
    parts = []
    for obj in objs:
        bm = _world_bmesh(obj, depsgraph)
        h = _health(obj, bm)
        report["objects"][obj.name] = h
        if not bm.faces:
            errors.append(f"{obj.name}: no faces after modifiers (empty boolean result or bad curve?).")
            bm.free()
            continue
        island_of, n_islands = _islands(bm)
        h["islands"] = n_islands
        groups = {}
        if n_islands > MAX_ISLANDS_PER_OBJECT:
            info.append(f"{obj.name}: {n_islands} islands, treated as one part.")
            island_of = [0] * len(bm.verts)
        for f in bm.faces:
            groups.setdefault(island_of[f.verts[0].index], []).append(f)
        for k, faces in groups.items():
            vidx = {}
            coords, polys = [], []
            for f in faces:
                poly = []
                for v in f.verts:
                    if v.index not in vidx:
                        vidx[v.index] = len(coords)
                        coords.append(v.co.copy())
                    poly.append(vidx[v.index])
                polys.append(poly)
            mn = Vector((min(c[i] for c in coords) for i in range(3)))
            mx = Vector((max(c[i] for c in coords) for i in range(3)))
            label = obj.name if len(groups) == 1 else f"{obj.name}[island {k}]"
            parts.append({"label": label, "obj": obj.name, "coords": coords,
                          "tree": BVHTree.FromPolygons(coords, polys, epsilon=0.0),
                          "min": mn, "max": mx, "nverts": len(coords)})
        bm.free()

        # Per-object health findings.
        if h["nonmanifold_edges"]:
            warnings.append(f"{obj.name}: {h['nonmanifold_edges']} edges shared by >2 faces (non-manifold).")
        if h["loose_verts"] or h["loose_edges"]:
            warnings.append(f"{obj.name}: loose geometry ({h['loose_verts']} verts, {h['loose_edges']} edges).")
        if h["degenerate_faces"]:
            warnings.append(f"{obj.name}: {h['degenerate_faces']} zero-area faces.")
        if h["volume"] is not None and h["volume"] < 0:
            errors.append(f"{obj.name}: closed mesh with inverted normals (negative volume).")
        if any(s < 0 for s in obj.scale):
            warnings.append(f"{obj.name}: negative scale {h['scale']} (flips normals, breaks bevels).")
        elif max(obj.scale) - min(obj.scale) > 1e-4 and obj.modifiers:
            warnings.append(f"{obj.name}: non-uniform scale {h['scale']} with modifiers; bevel/solidify will be uneven. Apply scale.")
        if h["open_boundary_edges"] and obj.type == "MESH":
            info.append(f"{obj.name}: open mesh ({h['open_boundary_edges']} boundary edges); fine for planes/cloth, bad for solids and 3D printing.")
        if h["ngons"]:
            info.append(f"{obj.name}: {h['ngons']} n-gons (matters only before subdivision).")

    lo = Vector((min(p["min"][i] for p in parts) for i in range(3))) if parts else Vector()
    hi = Vector((max(p["max"][i] for p in parts) for i in range(3))) if parts else Vector()
    diag = (hi - lo).length
    if tol is None:
        tol = spec.get("contact_tolerance", max(1e-4, diag * 1e-3))
    report["overall"] = {"min": list(lo), "max": list(hi), "size": list(hi - lo),
                         "contact_tolerance": tol, "parts": len(parts)}

    # Contact graph between parts.
    n = len(parts)
    dsu = _DSU(n)
    contact = {}
    intersections = []
    for i in range(n):
        for j in range(i + 1, n):
            if _bbox_gap(parts[i], parts[j]) > tol:
                continue
            d, overlapping = _min_distance(parts[i], parts[j], tol)
            if d <= tol:
                dsu.union(i, j)
                contact[(i, j)] = True
                depth = _penetration(parts[i], parts[j]) if overlapping else 0.0
                intersections.append((parts[i]["label"], parts[j]["label"], depth))

    # Grounded group = the one containing the lowest part.
    lowest = min(range(n), key=lambda k: parts[k]["min"].z)
    root = dsu.find(lowest)
    want_ground = spec.get("ground", True)
    if want_ground and abs(lo.z) > tol:
        warnings.append(f"Asset does not sit on the ground: lowest point z = {lo.z:.4f} (expected 0).")

    floating = [k for k in range(n) if dsu.find(k) != root]
    if floating:
        root_parts = [k for k in range(n) if dsu.find(k) == root]
        groups = {}
        for k in floating:
            groups.setdefault(dsu.find(k), []).append(k)
        for members in groups.values():
            best, best_k, best_r = math.inf, None, None
            for k in members:
                # exact distance only for the closest candidates by bbox gap
                cands = sorted(root_parts, key=lambda r: _bbox_gap(parts[k], parts[r]))[:4]
                for r in cands:
                    d, _ = _min_distance(parts[k], parts[r], 0.0)
                    if d < best:
                        best, best_k, best_r = d, k, r
            names_ = ", ".join(parts[k]["label"] for k in members)
            c = (parts[best_k]["min"] + parts[best_k]["max"]) / 2
            errors.append(
                f"FLOATING: [{names_}] is not connected to the main body. "
                f"Closest gap {best:.4f} m between {parts[best_k]['label']} (center {_fmt(c)}) "
                f"and {parts[best_r]['label']}.")

    if intersections:
        desc = [f"{a}-{b} " + (f"embedded {d:.3f} m" if d > tol * 2 else "flush")
                for a, b, d in intersections]
        info.append("Contacts: " + "; ".join(desc))
    report["contacts"] = [{"a": a, "b": b, "depth": d} for a, b, d in intersections]

    # Object-level contact lookup for spec checks.
    def objects_touch(a_name, b_name):
        for (i, j) in contact:
            if {parts[i]["obj"], parts[j]["obj"]} == {a_name, b_name}:
                return True
        return False

    def objects_gap(a_name, b_name):
        pa = [p for p in parts if p["obj"] == a_name]
        pb = [p for p in parts if p["obj"] == b_name]
        return min((_min_distance(x, y, 0.0)[0] for x in pa for y in pb), default=math.inf)

    # Spec checks.
    if "overall" in spec:
        _check_dims("Overall size", list(hi - lo), spec["overall"], errors)
    for pname, pspec in spec.get("parts", {}).items():
        obj = bpy.data.objects.get(pname)
        if obj is None or obj.name not in report["objects"]:
            errors.append(f"Missing part from spec: '{pname}'.")
            continue
        if "dims" in pspec:
            _check_dims(f"{pname} dimensions", list(obj.dimensions), pspec, errors)
        for other in pspec.get("attached_to", []):
            if other not in report["objects"]:
                continue
            if not objects_touch(pname, other):
                errors.append(f"NOT ATTACHED: {pname} should touch {other}; gap {objects_gap(pname, other):.4f} m.")

    return _finish(report, out_path, verbose)


def _check_dims(label, actual, pspec, errors):
    want = pspec.get("dims") or pspec.get("size")
    if not want:
        return
    tol = pspec.get("tol")
    bad = []
    for axis, a, w in zip("XYZ", actual, want):
        if w is None:
            continue
        t = tol if tol is not None else max(0.002, abs(w) * 0.05)
        if abs(a - w) > t:
            bad.append(f"{axis} {a:.3f} vs {w:.3f}")
    if bad:
        errors.append(f"{label} off spec: " + "; ".join(bad) + " (m).")


def _finish(report, out_path, verbose):
    report["summary"] = {"errors": len(report["errors"]), "warnings": len(report["warnings"])}
    text = format_report(report)
    report["text"] = text
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in report.items() if k != "text"}, f, indent=2, default=list)
    if verbose:
        print(text)
    return report


def format_report(report):
    lines = []
    s = report.get("summary", {})
    ov = report.get("overall")
    lines.append(f"GEOMETRY CHECK: {s.get('errors', 0)} errors, {s.get('warnings', 0)} warnings")
    if ov:
        lines.append(f"  overall size (m): {_fmt(ov['size'])}  parts: {ov['parts']}  contact tol: {ov['contact_tolerance']:.4f}")
    for key in ("errors", "warnings", "info"):
        for msg in report[key]:
            lines.append(f"  [{key[:-1].upper() if key != 'info' else 'INFO'}] {msg}")
    lines.append("  objects:")
    for name, h in report["objects"].items():
        lines.append(f"    {name}: dims {_fmt(h['dimensions'])} verts {h['verts']} faces {h['faces']} islands {h.get('islands', '-')}")
    return "\n".join(lines)

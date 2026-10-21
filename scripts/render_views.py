"""Render a model from fixed review views and tile them into one contact sheet.

Sheet layout (4 x 2), always the same so critiques can name views:

    row 1 (orthographic):  FRONT   | RIGHT   | BACK    | TOP
    row 2 (perspective):   3/4 L   | 3/4 R   | 3/4 BACK| LOW HERO

Front looks from -Y toward +Y (Blender's numpad 1), Z is up.

Modes:
    clay    Workbench, studio light, cavity + outline, random colour per object.
            Fast; shows form, part boundaries and proportions.
    shaded  EEVEE with the scene's own materials (adds a temporary light rig and
            grey world if the scene has no lights).

Runs inside Blender:

    import render_views
    sheet = render_views.run(out_dir, mode="clay", res=512)

Everything it changes (camera, engine, resolution, visibility, world) is put
back afterwards, so it is safe to call on a live scene through MCP.
"""

import math
import os

import bpy
import numpy as np
from mathutils import Quaternion, Vector

import check_geometry

VIEWS = [
    # name, azimuth deg (0 = front, 90 = right), elevation deg, orthographic
    ("front", 0, 0, True),
    ("right", 90, 0, True),
    ("back", 180, 0, True),
    ("top", 0, 90, True),
    ("three_quarter_left", -35, 22, False),
    ("three_quarter_right", 35, 22, False),
    ("three_quarter_back", 150, 28, False),
    ("low_hero", 20, 6, False),
]
COLS = 4
BG = (0.32, 0.32, 0.34)


def _bounds(objs):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        ev = o.evaluated_get(depsgraph)
        pts += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    lo = Vector((min(p[i] for p in pts) for i in range(3)))
    hi = Vector((max(p[i] for p in pts) for i in range(3)))
    return lo, hi, pts


def _eevee_id():
    for ident in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            bpy.context.scene.render.engine = ident
            return ident
        except TypeError:
            continue
    raise RuntimeError("No EEVEE engine found")


def _place_camera(cam, center, radius, corners, az, el, ortho):
    a, e = math.radians(az), math.radians(el)
    direction = Vector((math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), math.sin(e)))
    top = el >= 89.9
    if top:
        direction = Vector((0, 0, 1))
    cd = cam.data
    cd.sensor_fit = "AUTO"
    if ortho:
        cd.type = "ORTHO"
        dist = radius * 3
    else:
        cd.type = "PERSP"
        cd.lens = 50
        half_fov = math.atan(cd.sensor_width / 2 / cd.lens)
        dist = radius / math.sin(half_fov) * 1.08
    cam.location = center + direction * dist
    # Top view: identity rotation looks down -Z with +Y up, like Blender's numpad 7.
    rot = Quaternion() if top else (-direction).to_track_quat("-Z", "Y")
    cam.rotation_euler = rot.to_euler()
    cd.clip_start = max(1e-4, dist - radius * 2) if ortho else max(1e-4, dist * 0.01)
    cd.clip_end = dist + radius * 4
    if ortho:
        # Frame the projected bounding box tightly in the view plane.
        right = rot @ Vector((1, 0, 0))
        up = rot @ Vector((0, 1, 0))
        xs = [(p - center).dot(right) for p in corners]
        ys = [(p - center).dot(up) for p in corners]
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        cam.location = cam.location + right * cx + up * cy
        cd.ortho_scale = max(max(xs) - min(xs), max(ys) - min(ys)) * 1.12


def _temp_lights(center, radius):
    made = []
    for name, az, el, energy, size in (("key", -45, 40, 250, 1.0), ("fill", 60, 15, 80, 1.5),
                                       ("rim", 170, 50, 160, 0.8)):
        ld = bpy.data.lights.new(f"_review_{name}", "AREA")
        ld.energy = energy * radius * radius
        ld.size = size * radius
        lo = bpy.data.objects.new(f"_review_{name}", ld)
        bpy.context.scene.collection.objects.link(lo)
        a, e = math.radians(az), math.radians(el)
        d = Vector((math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), math.sin(e)))
        lo.location = center + d * radius * 4
        lo.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        made.append(lo)
    return made


def _load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    px = px.reshape(h, w, 4)
    a = px[..., 3:4]
    return px[..., :3] * a + np.array(BG, dtype=np.float32) * (1 - a)


def _write_sheet(paths, res, out_path):
    rows = math.ceil(len(paths) / COLS)
    pad = 4
    W, H = COLS * res + (COLS + 1) * pad, rows * res + (rows + 1) * pad
    sheet = np.full((H, W, 3), 0.12, dtype=np.float32)
    for k, p in enumerate(paths):
        r, c = divmod(k, COLS)
        tile = _load_rgba(p)
        y0 = H - (r + 1) * (res + pad)  # pixel rows are stored bottom-up
        x0 = pad + c * (res + pad)
        sheet[y0:y0 + res, x0:x0 + res] = tile
    rgba = np.concatenate([sheet, np.ones((H, W, 1), dtype=np.float32)], axis=2)
    img = bpy.data.images.new("_review_sheet", W, H, alpha=False)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = out_path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def run(out_dir, mode="clay", res=512, collection=None, names=None, views=None,
        samples=32, sheet_name=None):
    scene = bpy.context.scene
    r = scene.render
    os.makedirs(out_dir, exist_ok=True)
    objs = check_geometry.target_objects(collection, names)
    if not objs:
        raise RuntimeError("Nothing to render: no visible geometry objects.")
    lo, hi, corners = _bounds(objs)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-3)
    targets = {o.name for o in objs}

    saved = {
        "camera": scene.camera, "engine": r.engine, "rx": r.resolution_x, "ry": r.resolution_y,
        "pct": r.resolution_percentage, "path": r.filepath, "transp": r.film_transparent,
        "fmt": r.image_settings.file_format, "cmode": r.image_settings.color_mode,
        "world": scene.world,
    }
    shading = scene.display.shading
    saved_shading = {k: getattr(shading, k) for k in (
        "light", "color_type", "show_cavity", "show_object_outline", "show_shadows")}
    hidden = []
    temp = []
    try:
        for o in scene.objects:
            if o.type in check_geometry.GEOM_TYPES and o.name not in targets and not o.hide_render:
                o.hide_render = True
                hidden.append(o)
        cam_data = bpy.data.cameras.new("_review_cam")
        cam = bpy.data.objects.new("_review_cam", cam_data)
        scene.collection.objects.link(cam)
        temp.append(cam)
        scene.camera = cam

        r.resolution_x = r.resolution_y = res
        r.resolution_percentage = 100
        r.film_transparent = True
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"

        if mode == "clay":
            r.engine = "BLENDER_WORKBENCH"
            shading.light = "STUDIO"
            shading.color_type = "RANDOM"
            shading.show_cavity = True
            shading.cavity_type = "BOTH"
            shading.show_object_outline = True
            shading.show_shadows = False
            scene.display.render_aa = "8"
        elif mode == "shaded":
            _eevee_id()
            try:
                scene.eevee.taa_render_samples = samples
            except AttributeError:
                pass
            has_lights = any(o.type == "LIGHT" and not o.hide_render for o in scene.objects)
            if not has_lights:
                temp += _temp_lights(center, radius)
            if scene.world is None or not has_lights:
                w = bpy.data.worlds.new("_review_world")
                w.use_nodes = True
                bgn = w.node_tree.nodes.get("Background")
                if bgn:
                    bgn.inputs["Color"].default_value = (0.4, 0.4, 0.42, 1)
                    bgn.inputs["Strength"].default_value = 0.6
                scene.world = w
        else:
            raise ValueError("mode must be 'clay' or 'shaded'")

        paths = []
        for name, az, el, ortho in VIEWS:
            if views and name not in views:
                continue
            _place_camera(cam, center, radius, corners, az, el, ortho)
            p = os.path.join(out_dir, f"{mode}_{name}.png")
            r.filepath = p
            bpy.ops.render.render(write_still=True)
            paths.append(p)

        sheet = os.path.join(out_dir, sheet_name or f"{mode}_sheet.png")
        _write_sheet(paths, res, sheet)
        print(f"RENDER: {len(paths)} views -> {sheet}")
        return sheet
    finally:
        scene.camera = saved["camera"]
        r.engine = saved["engine"]
        r.resolution_x, r.resolution_y = saved["rx"], saved["ry"]
        r.resolution_percentage = saved["pct"]
        r.filepath = saved["path"]
        r.film_transparent = saved["transp"]
        r.image_settings.file_format = saved["fmt"]
        r.image_settings.color_mode = saved["cmode"]
        for k, v in saved_shading.items():
            setattr(shading, k, v)
        tmp_world = scene.world if scene.world is not saved["world"] else None
        scene.world = saved["world"]
        if tmp_world:
            bpy.data.worlds.remove(tmp_world)
        for o in hidden:
            o.hide_render = False
        for o in temp:
            data = o.data
            bpy.data.objects.remove(o)
            if isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)

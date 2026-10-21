"""Building blocks for model scripts, with good defaults baked in.

The point is to make the high-quality path the easy path:
- primitives are built at their true size (no object scale), so bevels and
  solidify stay even
- finish() adds the bevel + weighted-normal + smooth-shading stack that stops
  hard-surface parts looking like raw CG primitives
- material() adds subtle procedural colour/roughness variation and bump,
  because perfectly uniform surfaces read as fake

Units are metres, Z up, the model faces -Y (front view), origin at floor centre.

    import modelkit as mk
    mk.reset_scene()
    top = mk.box("Top", (1.2, 0.7, 0.04), (0, 0, 0.73))
    mk.finish(top, bevel=0.004)
    mk.assign(top, mk.material("Oak", (0.45, 0.28, 0.15), roughness=0.55, variation=0.25))
"""

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector


# ---------------------------------------------------------------- scene

def reset_scene():
    """Delete all objects and orphaned data, leaving an empty scene."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.images, bpy.data.node_groups):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    bpy.context.scene.unit_settings.system = "METRIC"


def collection(name):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


def _link(obj, coll):
    (coll or bpy.context.scene.collection).objects.link(obj)
    return obj


def _object_from_bmesh(name, bm, location, rotation, coll):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    obj.rotation_euler = Euler([math.radians(a) for a in rotation])
    return _link(obj, coll)


# ---------------------------------------------------------------- primitives
# rotation is in degrees (XYZ euler). All sizes are full sizes, not half sizes.

def box(name, size, location=(0, 0, 0), rotation=(0, 0, 0), coll=None):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    return _object_from_bmesh(name, bm, location, rotation, coll)


def cylinder(name, radius, depth, location=(0, 0, 0), rotation=(0, 0, 0), segments=48,
             radius_top=None, coll=None):
    """Cylinder along local Z, centred on location. radius_top makes a cone/taper."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=radius, radius2=radius if radius_top is None else radius_top,
                          depth=depth)
    return _object_from_bmesh(name, bm, location, rotation, coll)


def sphere(name, radius, location=(0, 0, 0), rotation=(0, 0, 0), segments=48, rings=24, coll=None):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    return _object_from_bmesh(name, bm, location, rotation, coll)


def lathe(name, profile, location=(0, 0, 0), rotation=(0, 0, 0), segments=64, coll=None):
    """Spin a profile [(radius, z), ...] around local Z. Bottom to top.

    Use for anything turned: vases, bottles, lamp bases, table legs, knobs.
    Start/end the profile at radius 0 to get closed caps.
    """
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in profile]
    edges = [bm.edges.new((verts[i], verts[i + 1])) for i in range(len(verts) - 1)]
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1),
                   angle=math.tau, steps=segments, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _object_from_bmesh(name, bm, location, rotation, coll)


def extrude_profile(name, points, depth, location=(0, 0, 0), rotation=(0, 0, 0), coll=None):
    """Extrude a closed 2D outline [(x, y), ...] (counter-clockwise) along local Z by depth.

    Use for brackets, panels, side profiles of furniture, logos, cut-out shapes.
    """
    bm = bmesh.new()
    verts = [bm.verts.new((x, y, 0.0)) for x, y in points]
    face = bm.faces.new(verts)
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [v for v in res["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, depth), verts=top)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _object_from_bmesh(name, bm, location, rotation, coll)


def tube(name, points, radius, location=(0, 0, 0), smooth=True, resolution=12,
         bevel_resolution=6, coll=None, to_mesh=True):
    """Sweep a round section along a path of 3D points (world offsets from location).

    Use for handles, pipes, cables, wire frames, bent rods. Make the first and last
    points sit inside the parts they connect to, or check_geometry will flag a gap.
    """
    cd = bpy.data.curves.new(name, "CURVE")
    cd.dimensions = "3D"
    cd.bevel_depth = radius
    cd.bevel_resolution = bevel_resolution
    cd.use_fill_caps = True
    cd.resolution_u = resolution
    if smooth:
        sp = cd.splines.new("BEZIER")
        sp.bezier_points.add(len(points) - 1)
        for bp, p in zip(sp.bezier_points, points):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    else:
        sp = cd.splines.new("POLY")
        sp.points.add(len(points) - 1)
        for pt, p in zip(sp.points, points):
            pt.co = (*p, 1.0)
    obj = _link(bpy.data.objects.new(name, cd), coll)
    obj.location = location
    if to_mesh:
        obj = convert_to_mesh(obj)
        weld(obj)  # curve caps come out as separate islands
    return obj


def weld(obj, dist=1e-5):
    """Merge coincident vertices so separately generated pieces form one closed mesh."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def convert_to_mesh(obj):
    """Bake a curve/text object (or modifiers) into a plain mesh object of the same name."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    me = bpy.data.meshes.new_from_object(ev)
    name, loc, rot, colls = obj.name, obj.location.copy(), obj.rotation_euler.copy(), list(obj.users_collection)
    mats = [s.material for s in obj.material_slots]
    bpy.data.objects.remove(obj, do_unlink=True)
    new = bpy.data.objects.new(name, me)
    new.location, new.rotation_euler = loc, rot
    for c in colls:
        c.objects.link(new)
    for m in mats:
        if m:
            new.data.materials.append(m)
    return new


# ---------------------------------------------------------------- finishing

def finish(obj, bevel=0.003, segments=3, angle=30, weighted_normals=True):
    """Hard-surface finish: angle-limited bevel, weighted normals, smooth shading.

    bevel is the edge radius in metres. Real objects almost never have perfectly
    sharp edges; 1-5 mm for furniture/products, 0.5 mm for small electronics.
    """
    if bevel and bevel > 0:
        m = obj.modifiers.new("Bevel", "BEVEL")
        m.width = bevel
        m.segments = segments
        m.limit_method = "ANGLE"
        m.angle_limit = math.radians(angle)
        m.use_clamp_overlap = True
        m.harden_normals = True
    if weighted_normals:
        w = obj.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        w.keep_sharp = True
    shade_smooth(obj)
    return obj


def shade_smooth(obj):
    if obj.type == "MESH":
        obj.data.shade_smooth()
    return obj


def subsurf(obj, levels=2, render_levels=None):
    """Subdivision surface. Only on clean quad cages; add support loops to keep edges crisp."""
    m = obj.modifiers.new("Subdivision", "SUBSURF")
    m.levels = levels
    m.render_levels = levels if render_levels is None else render_levels
    shade_smooth(obj)
    return obj


def solidify(obj, thickness, offset=-1.0):
    m = obj.modifiers.new("Solidify", "SOLIDIFY")
    m.thickness = thickness
    m.offset = offset
    m.use_even_offset = True
    return obj


def array(obj, count, offset, relative=False):
    """Repeat obj count times. offset is a vector in metres (or relative factors)."""
    m = obj.modifiers.new("Array", "ARRAY")
    m.count = count
    m.use_relative_offset = relative
    m.use_constant_offset = not relative
    if relative:
        m.relative_offset_displace = offset
    else:
        m.constant_offset_displace = offset
    return obj


def mirror(obj, x=True, y=False, z=False, mirror_object=None):
    m = obj.modifiers.new("Mirror", "MIRROR")
    m.use_axis = (x, y, z)
    m.use_clip = True
    if mirror_object:
        m.mirror_object = mirror_object
    return obj


def boolean(obj, cutter, operation="DIFFERENCE"):
    """Boolean with a hidden helper cutter. Cutter is renamed with a '_' prefix so checks ignore it."""
    if not cutter.name.startswith("_"):
        cutter.name = "_" + cutter.name
    cutter.display_type = "WIRE"
    cutter.hide_render = True
    m = obj.modifiers.new("Boolean", "BOOLEAN")
    m.object = cutter
    m.operation = operation
    m.solver = "EXACT"
    return obj


def apply_modifiers(obj):
    """Bake the modifier stack into the mesh (use before exporting to games/printing)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    old = obj.data
    obj.modifiers.clear()
    obj.data = me
    bpy.data.meshes.remove(old)
    return obj


# ---------------------------------------------------------------- materials

def material(name, color, roughness=0.5, metallic=0.0, variation=0.15, bump=0.15,
             scale=8.0, specular=0.5, transmission=0.0, coat=0.0, emission=None):
    """Principled material with subtle procedural break-up.

    variation: 0-1, how much colour value and roughness wander across the surface.
    bump: bump strength (0 disables). scale: noise frequency (higher = finer).
    Colours are linear RGB in 0-1.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    _set(bsdf, ("Specular IOR Level", "Specular"), specular)
    _set(bsdf, ("Transmission Weight", "Transmission"), transmission)
    _set(bsdf, ("Coat Weight", "Clearcoat"), coat)
    if emission:
        _set(bsdf, ("Emission Color", "Emission"), (*emission[:3], 1.0))
        _set(bsdf, ("Emission Strength",), emission[3] if len(emission) > 3 else 1.0)

    if variation <= 0 and bump <= 0:
        return mat
    coord = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 6.0
    links.new(coord.outputs["Object"], noise.inputs["Vector"])

    if variation > 0:
        hsv = nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Color"].default_value = (*color, 1.0)
        val = nodes.new("ShaderNodeMapRange")
        val.inputs["To Min"].default_value = 1.0 - variation * 0.5
        val.inputs["To Max"].default_value = 1.0 + variation * 0.5
        links.new(noise.outputs["Fac"], val.inputs["Value"])
        links.new(val.outputs["Result"], hsv.inputs["Value"])
        links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
        rough = nodes.new("ShaderNodeMapRange")
        rough.inputs["To Min"].default_value = max(0.0, roughness * (1 - variation))
        rough.inputs["To Max"].default_value = min(1.0, roughness * (1 + variation))
        links.new(noise.outputs["Fac"], rough.inputs["Value"])
        links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    if bump > 0:
        fine = nodes.new("ShaderNodeTexNoise")
        fine.inputs["Scale"].default_value = scale * 12
        fine.inputs["Detail"].default_value = 4.0
        links.new(coord.outputs["Object"], fine.inputs["Vector"])
        bn = nodes.new("ShaderNodeBump")
        bn.inputs["Strength"].default_value = bump
        bn.inputs["Distance"].default_value = 0.002
        links.new(fine.outputs["Fac"], bn.inputs["Height"])
        links.new(bn.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def wood(name, light=(0.30, 0.15, 0.06), dark=(0.11, 0.05, 0.018), roughness=0.5,
         grain_density=40.0, grain_axis="X", bump=0.15):
    """Procedural wood: noise stretched into fine streaks along grain_axis.

    grain_density: streaks per metre across the grain (higher = finer). Colours
    are linear RGB; the defaults are a mid oak. Walnut: light (0.12, 0.06, 0.03),
    dark (0.035, 0.017, 0.008). Pale birch: light (0.55, 0.38, 0.22), dark (0.32, 0.2, 0.1).

    grain_axis must follow each part's long axis in object space (legs: "Z",
    a board lying along X: "X"), so make one material per grain direction.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    coord = nodes.new("ShaderNodeTexCoord")

    # Squash space across the grain so noise turns into long thin streaks.
    across = grain_density
    stretch = {"X": (1, across, across), "Y": (across, 1, across), "Z": (across, across, 1)}[grain_axis]
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = stretch
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    # Wobble the streaks a little with a large, unstretched noise.
    warp = nodes.new("ShaderNodeTexNoise")
    warp.inputs["Scale"].default_value = 3.0
    warp_amt = nodes.new("ShaderNodeVectorMath")
    warp_amt.operation = "SCALE"
    warp_amt.inputs["Scale"].default_value = 0.6
    links.new(coord.outputs["Object"], warp.inputs["Vector"])
    links.new(warp.outputs["Color"], warp_amt.inputs[0])
    warped = nodes.new("ShaderNodeVectorMath")
    warped.operation = "ADD"
    links.new(mapping.outputs["Vector"], warped.inputs[0])
    links.new(warp_amt.outputs["Vector"], warped.inputs[1])

    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 1.5
    grain.inputs["Detail"].default_value = 10.0
    grain.inputs["Roughness"].default_value = 0.65
    links.new(warped.outputs["Vector"], grain.inputs["Vector"])

    ramp = nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.35, (*dark, 1)
    els[1].position, els[1].color = 0.65, (*light, 1)
    links.new(grain.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    rough = nodes.new("ShaderNodeMapRange")
    rough.inputs["To Min"].default_value = roughness + 0.1
    rough.inputs["To Max"].default_value = roughness - 0.1
    links.new(grain.outputs["Fac"], rough.inputs["Value"])
    links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    if bump > 0:
        bn = nodes.new("ShaderNodeBump")
        bn.inputs["Strength"].default_value = bump
        bn.inputs["Distance"].default_value = 0.0005
        bn.invert = True  # dark latewood sits slightly lower
        links.new(grain.outputs["Fac"], bn.inputs["Height"])
        links.new(bn.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def assign_wood(obj, name="Wood", **kw):
    """Assign wood with the grain along the object's longest local axis.

    Reuses one material per (name, axis), e.g. "Oak_X", "Oak_Z". Getting this
    wrong shows blotchy end-grain on big faces, so prefer this over wood().
    """
    dims = obj.dimensions
    axis = "XYZ"[max(range(3), key=lambda i: dims[i])]
    key = f"{name}_{axis}"
    mat = bpy.data.materials.get(key) or wood(key, grain_axis=axis, **kw)
    return assign(obj, mat)


def _set(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return


def assign(obj, mat):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return obj


# ---------------------------------------------------------------- presentation

def bounds(objs=None):
    objs = objs or [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.name.startswith("_")]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        ev = o.evaluated_get(depsgraph)
        pts += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    lo = Vector((min(p[i] for p in pts) for i in range(3)))
    hi = Vector((max(p[i] for p in pts) for i in range(3)))
    return lo, hi


def studio(objs=None, azimuth=-35, elevation=18, lens=65, floor=True, floor_color=(0.5, 0.5, 0.5),
           world_strength=0.25):
    """Hero camera + soft three-light rig + floor for a presentation render.

    Sets scene.camera. Objects created here are named with a leading '_' so the
    geometry check ignores them.
    """
    scene = bpy.context.scene
    lo, hi = bounds(objs)
    center = (lo + hi) / 2
    radius = (hi - lo).length / 2
    coll = collection("_Studio")

    cam = bpy.data.objects.new("_Camera", bpy.data.cameras.new("_Camera"))
    coll.objects.link(cam)
    cam.data.lens = lens
    a, e = math.radians(azimuth), math.radians(elevation)
    d = Vector((math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), math.sin(e)))
    half = math.atan(cam.data.sensor_width / 2 / lens)
    cam.location = center + d * (radius / math.sin(half) * 1.15)
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Power scales with radius^2 because the lights sit at 4x radius.
    for name, az, el, power, size in (("Key", -50, 45, 250, 1.2), ("Fill", 65, 20, 70, 2.0),
                                      ("Rim", 165, 55, 180, 0.8)):
        ld = bpy.data.lights.new(f"_{name}", "AREA")
        ld.energy = power * radius * radius
        ld.size = size * radius
        lo_ = bpy.data.objects.new(f"_{name}", ld)
        coll.objects.link(lo_)
        a, e = math.radians(az), math.radians(el)
        dd = Vector((math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), math.sin(e)))
        lo_.location = center + dd * radius * 4
        lo_.rotation_euler = (-dd).to_track_quat("-Z", "Y").to_euler()

    if floor:
        f = box("_Floor", (radius * 40, radius * 40, 0.001), (center.x, center.y, lo.z - 0.0005), coll=coll)
        assign(f, material("_FloorMat", floor_color, roughness=0.8, variation=0, bump=0))

    w = bpy.data.worlds.new("_World")
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.6, 0.62, 0.65, 1)
    bg.inputs["Strength"].default_value = world_strength
    scene.world = w
    return cam


def render_final(path, engine="CYCLES", samples=128, res=(1600, 1200), denoise=True):
    scene = bpy.context.scene
    r = scene.render
    if engine == "CYCLES":
        r.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = denoise
        try:
            scene.cycles.device = "GPU"
        except Exception:
            pass
    else:
        for ident in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
            try:
                r.engine = ident
                break
            except TypeError:
                continue
    r.resolution_x, r.resolution_y = res
    r.resolution_percentage = 100
    r.film_transparent = False
    r.image_settings.file_format = "PNG"
    r.filepath = path
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        pass
    bpy.ops.render.render(write_still=True)
    return path

# Modelling techniques and Blender 5.x gotchas

## What makes code-built models look cheap, and the fix

| Looks fake because | Fix |
|---|---|
| Perfectly sharp edges catch no light | `mk.finish(obj, bevel=…)`: 1–5 mm on furniture/products, 0.3–1 mm on small electronics, 1–3 cm on buildings |
| Everything is a raw cube or cylinder | use the right builder: `lathe` for turned parts, `extrude_profile` for cut outlines, `tube` for bent rods/handles, tapered `cylinder(radius_top=…)` for legs |
| Parts too thick and chunky | check real dimensions: table tops 18–40 mm, shelves 18 mm, chair legs 25–45 mm, sheet metal 1–3 mm, glass 4–6 mm, mug wall 3–4 mm |
| Parts float or barely touch | `check_geometry` errors; extend parts 2–10 mm *into* what they join (joinery, welds, screws all overlap in reality) |
| Uniform flat colour | `mk.material(variation≥0.1, bump>0)`; wood via `mk.assign_wood`; no pure black/white (keep base colour in 0.02–0.9) |
| No secondary detail | add 2–3 of: panel gaps, insets, trims, feet/glides, screws, seams, chamfered undersides, lips and rims, labels |
| Perfect regularity | slight variation for organic-ish materials; real objects are symmetric but not identical across materials |
| Harsh or flat lighting | `mk.studio()`: soft area-light key/fill/rim, floor for contact shadows, AgX view transform |

## Builders: when to use which

- **box + finish:** boards, panels, slabs, housings. Size is the true size,
  with no object scale, so bevels stay even.
- **cylinder(radius_top=…):** tapered legs and posts, cones. `segments` 32–64,
  fewer for tiny parts.
- **lathe(profile):** anything round with a varying profile (vases, bottles,
  bowls, lamp bases, knobs, turned legs, wheels, cups). The profile is
  `[(radius, z), …]` from bottom to top. Start and end at radius 0 for a solid.
  For a hollow vessel, go up the outside, across the rim, and down the inside
  (rim thickness = wall).
- **extrude_profile(points, depth):** shapes defined by an outline (brackets,
  chair side frames, signs, gears, letters, cut-outs). Points go
  counter-clockwise. Rotate the result to stand it up (`rotation=(90, 0, 0)`
  puts the outline in XZ, extruded along -Y… check in the TOP view).
- **tube(points, radius):** handles, pipes, cables, wire frames, bent tubing.
  Ends must sit inside the parts they connect to.
- **subsurf:** smooth organic-ish hard surface (mouse, controller, soap bar).
  Subdivision *shrinks* the cage, so a subsurfed part that sat on something
  will float. Lower it into its support by a few mm and re-check.
  Start with a low-poly box cage made with bmesh and keep it all quads. Add
  support loops near edges that should stay crisp; without them everything
  turns into a blob.
- **boolean:** holes, slots and engraved panel lines. Use sparingly. The
  cutter gets a `_` prefix and is hidden. Prefer modelling the shape directly
  (extrude_profile) when you can. Use `solver = "EXACT"`.
- **array / mirror:** slats, spokes, rungs, bolts, symmetric halves. Model one
  half and mirror it.

## Detail recipes
- **Panel gap:** two boxes 1–2 mm apart, each finished with bevel. This reads
  much better than one box with a texture.
- **Inset/frame:** an outer frame box plus an inner panel recessed 3–5 mm.
- **Feet/glides:** small cylinders 5–10 mm tall under legs and cabinets.
- **Lip/rim:** a lathe profile that thickens at the top edge.
- **Screws:** `cylinder` head (r 3–4 mm, depth 1 mm) slightly embedded, with a
  boolean slot or a thin box cross.
- **Rounded corners on slabs:** a larger bevel (5–20 mm) with 4–6 segments, but
  keep the angle limit so flat faces stay flat.

## Materials quick reference (linear RGB)
- Oak `mk.assign_wood(o, "Oak")` (defaults); Walnut light (0.12, 0.06, 0.03)
  dark (0.035, 0.017, 0.008); Birch light (0.55, 0.38, 0.22) dark (0.32, 0.2, 0.1)
- Brushed steel `material((0.6, 0.6, 0.62), roughness=0.35, metallic=1, variation=0.1)`
- Black powder-coat `material((0.03, 0.03, 0.03), roughness=0.55, variation=0.1, bump=0.05)`
- Brass `material((0.8, 0.55, 0.25), roughness=0.3, metallic=1)`
- Matte plastic `material(color, roughness=0.5, variation=0.05, bump=0.02)`
- Ceramic glaze `material(color, roughness=0.15, coat=0.5, variation=0.05, bump=0)`
- Fabric `material(color, roughness=0.9, variation=0.2, bump=0.3, scale=60)`
- Glass `material((0.9, 0.95, 0.95), roughness=0.02, transmission=1, variation=0, bump=0)`
  needs Cycles for good results.
- Concrete/stone `material((0.4, 0.4, 0.38), roughness=0.85, variation=0.3, bump=0.4, scale=4)`

## Blender 5.x API gotchas
- Builders create meshes at true size. If you scale an object, apply the scale
  before adding bevel or solidify, or they come out uneven (the check warns).
- `mesh.shade_smooth()` exists. The old `use_auto_smooth` was removed in 4.1;
  rely on bevel `harden_normals` + the WeightedNormal modifier (`mk.finish`).
- The EEVEE engine id is `BLENDER_EEVEE` (it was `BLENDER_EEVEE_NEXT` in
  4.2–4.x). The helpers try both.
- Principled BSDF socket names: `"Specular IOR Level"`, `"Transmission Weight"`,
  `"Coat Weight"`, `"Emission Color"`, `"Emission Strength"`.
- `ShaderNodeMix` has duplicate socket names per data type. Prefer
  `HueSaturation`/`MapRange`, or index sockets by type.
- Headless (`-b`) runs: `bpy.ops` that need a 3D viewport (view3d.*, most
  edit-mode ops) fail. Use bmesh and data API instead, which is what modelkit
  does.
- Use `obj.evaluated_get(depsgraph)` to read geometry *with* modifiers.
- Angles in `rotation_euler` are radians. modelkit builders take degrees.
- When unsure about an API, check the docs instead of guessing. With MCP
  connected, use `search_api_docs` / `get_python_api_docs`; otherwise
  docs.blender.org/api/current.

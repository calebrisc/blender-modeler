# Changelog

## 0.1.0 (2026-10-21)
- First public version.
- Spec → blockout → detail → materials → presentation workflow with a
  measured-check + 8-view critique loop.
- `check_geometry`: floating parts (island-level), gaps, flush/embedded
  contacts, mesh health, spec dimensions and required attachments.
- `render_views`: clay (Workbench, random colour per part) and shaded (EEVEE)
  contact sheets.
- `modelkit`: true-size builders (box, cylinder, sphere, lathe, extrude_profile,
  tube), bevel/weighted-normal finishing, procedural materials and wood with
  automatic grain direction, studio lighting, Cycles final render.
- `bmod.py`: cross-platform launcher that finds Blender and summarises results.
- Tested on Blender 5.2.1 (Windows).

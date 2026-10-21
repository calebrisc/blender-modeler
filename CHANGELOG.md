# Changelog

## 0.2.0 (2026-10-21)
- New `scripts/setup_blender_mcp.py`: installs the official MCP add-on (from
  zip or source folder), saves *Allow Online Access* (with a prefs backup), and
  `--check` diagnoses live-mode problems with a fix for each.
- Documented the real cause of "MCP installed but not connecting": the add-on
  starts its server only at Blender launch and only if Online Access is
  already on, so turning it on later (or not saving preferences) leaves the
  server stopped. Use *Start MCP Bridge Server* or restart; no reinstall needed.
  `--online-mode` is the no-settings-change alternative.

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

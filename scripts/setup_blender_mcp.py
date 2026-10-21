"""Set up and diagnose the official Blender Lab MCP add-on (live mode).

    python setup_blender_mcp.py --check
    python setup_blender_mcp.py --install-addon PATH   # mcp-x.y.z.zip, or the add-on source folder
    python setup_blender_mcp.py --enable-online-access

Why this exists: the add-on only tries to start its server once, when Blender
starts, and only if Online Access is already on. Turning Online Access on in a
running Blender does not start it, and the setting is lost unless preferences
are saved. Both lead to "the MCP tools time out" with no visible error.

Three ways to get the server running:
  1. Online Access saved on (--enable-online-access, or Preferences > System >
     Allow Online Access + Save Preferences), then start Blender.
  2. Launch Blender with --online-mode: on for that launch only, nothing saved.
  3. Already open with the server stopped: turn Online Access on, then
     Preferences > Add-ons > MCP > Start MCP Bridge Server. No reinstall needed.

--install-addon and --enable-online-access change Blender's saved preferences,
so Blender must be closed (it would overwrite them on quit). A backup of
userpref.blend is made first. Runs with any Python 3.8+.
"""

import argparse
import datetime
import os
import shutil
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bmod import blender_version, find_blender  # noqa: E402

ADDON_ID = "mcp"
PORT = 9876

PROBE = r"""
import bpy, json, os, addon_utils
p = bpy.context.preferences
mods = [a.module for a in p.addons]
mod = next((m for m in mods if m == "mcp" or m.endswith(".mcp")), None)
installed = [m.__name__ for m in addon_utils.modules() if m.__name__.split(".")[-1] == "mcp"]
a = p.addons.get(mod) if mod else None
print("PROBE" + json.dumps({
    "installed": installed,
    "enabled_module": mod,
    "online_access_saved": p.system.use_online_access,
    "autostart": getattr(a.preferences, "use_autostart", None) if a else None,
    "port": getattr(a.preferences, "port", None) if a else None,
    "userpref": bpy.utils.user_resource("CONFIG", path="userpref.blend"),
}))
"""

ENABLE_ONLINE = r"""
import bpy
bpy.context.preferences.system.use_online_access = True
bpy.ops.wm.save_userpref()
print("ONLINE_SAVED", bpy.context.preferences.system.use_online_access)
"""


def run_blender(exe, args, timeout=180):
    r = subprocess.run([exe] + args, capture_output=True, text=True, errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def probe(exe):
    # No --factory-startup: we want the user's real preferences.
    _, out = run_blender(exe, ["-b", "--python-expr", PROBE])
    import json
    for line in out.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[5:])
    raise RuntimeError("Could not read Blender preferences:\n" + out[-2000:])


def blender_cmdlines():
    """Command lines of running Blender processes ([] if none or unknown)."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"Name='blender.exe'\").CommandLine"],
                capture_output=True, text=True, timeout=30).stdout
        else:
            out = subprocess.run(["pgrep", "-ifa", "blender"], capture_output=True, text=True).stdout
        return [l.strip() for l in out.splitlines() if l.strip() and "setup_blender_mcp" not in l]
    except Exception:  # noqa: BLE001
        return []


def blender_running():
    return bool(blender_cmdlines())


def port_listening(port=PORT):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def backup_prefs(path):
    if path and os.path.isfile(path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = f"{path}.bak-{stamp}"
        shutil.copy2(path, dst)
        print(f"Backed up preferences to {dst}")


def require_closed():
    if blender_running():
        print("Blender is running. Close it first (it would overwrite the saved preferences on quit).")
        sys.exit(1)


def check(exe):
    v = blender_version(exe)
    print(f"Blender:        {exe} ({v})")
    info = probe(exe)
    ok = True

    def line(label, good, text, fix=None):
        nonlocal ok
        ok &= good
        print(f"{'OK ' if good else 'FIX'} {label:<15} {text}")
        if fix and not good:
            print(f"    -> {fix}")

    major_minor = tuple(int(x) for x in v.split(".")[:2]) if v[0].isdigit() else (0, 0)
    line("Blender 5.1+", major_minor >= (5, 1), v, "the official MCP add-on needs Blender 5.1 or newer")
    line("Add-on", bool(info["installed"]), ", ".join(info["installed"]) or "not installed",
         "python setup_blender_mcp.py --install-addon <mcp-x.y.z.zip or addon folder>")
    line("Add-on enabled", bool(info["enabled_module"]), info["enabled_module"] or "disabled",
         "Edit > Preferences > Add-ons > MCP: tick the checkbox, or re-run --install-addon")
    cmdlines = blender_cmdlines()
    running = bool(cmdlines)
    listening = port_listening(info["port"] or PORT)
    online_flag = any("--online-mode" in c for c in cmdlines)
    if not info["online_access_saved"] and online_flag:
        print("--  Online Access   saved OFF, but the open Blender was launched with --online-mode "
              "(on for that launch only)")
    elif running and listening and not info["online_access_saved"]:
        line("Online Access", False, "on in the open Blender but NOT saved (off again next launch)",
             "in Blender: Edit > Preferences > (menu at bottom left) > Save Preferences")
    else:
        line("Online Access", bool(info["online_access_saved"]),
             "saved ON" if info["online_access_saved"] else "saved OFF (server will not start)",
             "python setup_blender_mcp.py --enable-online-access (with Blender closed), "
             "or turn it on in Edit > Preferences > System and Save Preferences")
    line("Auto Start", info["autostart"] is not False, str(info["autostart"]),
         "Edit > Preferences > Add-ons > MCP: tick Auto Start, then Save Preferences")
    if running:
        line("Server", listening, f"listening on {info['port'] or PORT}" if listening else "Blender open but server not running",
             "turn on Online Access, then Edit > Preferences > Add-ons > MCP > Start MCP Bridge Server "
             "(no reinstall needed), or restart Blender")
    else:
        print(f"--  Server          Blender is closed; the server starts with Blender"
              f"{' (port is busy: another MCP add-on?)' if listening else ''}")
    print("\nAll good." if ok else "\nFix the items marked FIX, then start Blender.")
    return 0 if ok else 1


def install_addon(exe, path):
    require_closed()
    path = os.path.abspath(path)
    backup_prefs(probe(exe).get("userpref"))
    if os.path.isdir(path):
        out_dir = tempfile.mkdtemp(prefix="blmcp_")
        code, out = run_blender(exe, ["--factory-startup", "-c", "extension", "build",
                                      "--source-dir", path, "--output-dir", out_dir])
        zips = [f for f in os.listdir(out_dir) if f.endswith(".zip")]
        if code or not zips:
            print(out)
            return 1
        path = os.path.join(out_dir, zips[0])
    code, out = run_blender(exe, ["-c", "extension", "install-file", "-r", "user_default", "-e", path])
    print(out.strip())
    return code


def enable_online(exe):
    require_closed()
    backup_prefs(probe(exe).get("userpref"))
    code, out = run_blender(exe, ["-b", "--python-expr", ENABLE_ONLINE])
    if "ONLINE_SAVED True" not in out:
        print(out[-2000:])
        return 1
    print("Online Access is now on and saved.")
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--install-addon", metavar="PATH")
    ap.add_argument("--enable-online-access", action="store_true")
    ap.add_argument("--blender", metavar="PATH")
    a = ap.parse_args()
    exe = find_blender(a.blender)
    if not exe:
        print("Blender not found; set BLENDER_EXE or pass --blender PATH.")
        return 3
    code = 0
    if a.install_addon:
        code |= install_addon(exe, a.install_addon)
    if a.enable_online_access:
        code |= enable_online(exe)
    if a.check or not (a.install_addon or a.enable_online_access):
        code |= check(exe)
    return code


if __name__ == "__main__":
    sys.exit(main())

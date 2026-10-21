"""Launcher: find Blender, run one review iteration headless, print a short summary.

Runs with any Python 3.8+ (stdlib only); Blender itself does the work.

    python bmod.py --model model.py --spec spec.json --out renders/iter_01 --mode clay
    python bmod.py --where          # show which Blender will be used

All arguments except --where/--verbose/--blender are passed to run_pipeline.py
(--model, --blend, --out, --spec, --mode, --res, --collection, --final,
--no-render, --no-save).

Blender is found from, in order: --blender PATH, $BLENDER_EXE, $BLENDER_PATH,
`blender` on PATH, then the standard install locations (newest version wins).

Exit codes: 0 ok, 1 model script failed / Blender error, 2 geometry check
found errors, 3 Blender not found.
"""

import glob
import os
import platform
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "run_pipeline.py")
KEEP = re.compile(r"Traceback|Error|FAILED|GEOMETRY|RENDER:|FINAL:|^\s+File |^\s+\[")


def _version_key(path):
    m = re.search(r"(\d+)\.(\d+)", path)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def candidates():
    for env in ("BLENDER_EXE", "BLENDER_PATH"):
        if os.environ.get(env):
            yield os.environ[env]
    on_path = shutil.which("blender")
    if on_path:
        yield on_path
    system = platform.system()
    found = []
    if system == "Windows":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.expandvars(r"%LOCALAPPDATA%\Programs")):
            found += glob.glob(os.path.join(root, "Blender Foundation", "Blender*", "blender.exe"))
        found += glob.glob(os.path.expandvars(
            r"%ProgramFiles(x86)%\Steam\steamapps\common\Blender\blender.exe"))
    elif system == "Darwin":
        found += glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender")
        found += glob.glob(os.path.expanduser("~/Applications/Blender*.app/Contents/MacOS/Blender"))
    else:
        found += ["/snap/bin/blender", "/usr/bin/blender", "/usr/local/bin/blender"]
        found += glob.glob(os.path.expanduser("~/blender*/blender"))
        found += glob.glob("/opt/blender*/blender")
    yield from sorted(found, key=_version_key, reverse=True)


def find_blender(explicit=None):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for c in candidates():
        if c and os.path.isfile(c):
            return c
    return None


def blender_version(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Blender (\d+\.\d+(\.\d+)?)", out)
        return m.group(1) if m else "unknown"
    except Exception as ex:  # noqa: BLE001
        return f"unknown ({ex})"


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args
    where = "--where" in args
    explicit = None
    if "--blender" in args:
        i = args.index("--blender")
        explicit = args[i + 1]
        del args[i:i + 2]
    args = [a for a in args if a not in ("--verbose", "--where")]

    exe = find_blender(explicit)
    if not exe:
        print("Blender not found. Install Blender 4.2+ (5.1+ recommended) or set BLENDER_EXE "
              "to the blender executable.")
        return 3
    if where:
        print(f"{exe}\nBlender {blender_version(exe)}")
        return 0

    out_dir = None
    if "--out" in args:
        out_dir = os.path.abspath(args[args.index("--out") + 1])
    cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "1",
           "--python", PIPELINE, "--"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    log = proc.stdout + proc.stderr
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "blender.log"), "w", encoding="utf-8") as f:
            f.write(log)

    report = os.path.join(out_dir, "report.txt") if out_dir else None
    if verbose or not report or not os.path.isfile(report):
        print(log if verbose else "\n".join(l for l in log.splitlines() if KEEP.search(l)))
    if report and os.path.isfile(report):
        with open(report, encoding="utf-8") as f:
            text = f.read()
        print(text)
        if text.startswith("MODEL SCRIPT FAILED"):
            return 1
        m = re.search(r"GEOMETRY CHECK: (\d+) errors", text)
        code = 2 if m and int(m.group(1)) > 0 else 0
        sheets = sorted(glob.glob(os.path.join(out_dir, "*_sheet.png")))
        for s in sheets:
            print(f"SHEET: {s}")
        if "--final" in args:
            print(f"FINAL: {os.path.abspath(args[args.index('--final') + 1])}")
        if out_dir and os.path.isfile(os.path.join(out_dir, "model.blend")):
            print(f"BLEND: {os.path.join(out_dir, 'model.blend')}")
        return code
    print(f"Blender exited with code {proc.returncode}; see {out_dir}/blender.log" if out_dir
          else f"Blender exited with code {proc.returncode}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

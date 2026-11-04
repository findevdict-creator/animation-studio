"""Static check and sandboxed run for Asset Builder scripts.

A script is plain Python that defines ``build(params: dict) -> None`` and may import only
bpy, bmesh, mathutils, math, random and colorsys. It builds one prop at the origin, Z up,
in metres. It never saves, loads, renders or touches files: the runner does the saving,
after applying scale and stamping the scene properties the scanner reads.

The run happens in a child Python with the bpy wheel (``sys.executable`` here), with a wall
clock limit, in an empty working directory, so a crash or a hang costs one subprocess and
the only file it can produce is the one the runner writes under ``library/props``.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_MODULES = {"bpy", "bmesh", "mathutils", "math", "random", "colorsys"}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "breakpoint", "input", "exit", "quit", "memoryview",
}
# Attribute names that write, load or leave the scene. Blocked wherever they appear.
BANNED_ATTRS = {
    "save", "save_as_mainfile", "save_mainfile", "save_render", "write", "load", "open_mainfile", "read_homefile",
    "read_factory_settings", "filepath", "filepath_raw", "render", "libraries", "import_scene", "export_scene",
    "wm", "app", "utils", "system", "subprocess", "environ", "run_script", "text",
}
REPO_ROOT = Path(__file__).resolve().parents[2]


def static_check(script: str) -> list[str]:
    """Why the script may not run; empty means it passes."""
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return [f"syntax error line {exc.lineno}: {exc.msg}"]
    errors: list[str] = []
    has_build = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build" and isinstance(getattr(node, "parent", None), ast.Module):
            has_build = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    errors.append(f"line {node.lineno}: import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_MODULES or node.level:
                errors.append(f"line {node.lineno}: import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            errors.append(f"line {node.lineno}: '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                errors.append(f"line {node.lineno}: attribute '{node.attr}' is not allowed")
            elif node.attr.startswith("__"):
                errors.append(f"line {node.lineno}: dunder attribute '{node.attr}' is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(f"line {node.lineno}: {type(node).__name__.lower()} is not allowed")
    # top-level build(params)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build":
            has_build = True
            if len(node.args.args) != 1:
                errors.append("build must take exactly one argument, params")
    if not has_build:
        errors.append("the script must define a top-level function build(params)")
    return sorted(set(errors), key=lambda e: (int(e.split()[1].rstrip(":")) if e.startswith("line") else 0, e))


# Runs inside the child process. Receives one JSON argument.
CHILD = r'''
import json, sys, traceback
spec = json.loads(sys.argv[1])
import bpy
from mathutils import Matrix
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0
scene.render.fps = 24
ns = {"__name__": "asset_script", "__builtins__": __builtins__}
try:
    exec(compile(spec["script"], "asset_script", "exec"), ns)
    ns["build"](dict(spec["params"]))
except Exception:
    print("ASSET_SCRIPT_ERROR " + json.dumps(traceback.format_exc(limit=4)))
    sys.exit(3)
meshes = [o for o in scene.objects if o.type == "MESH"]
if not meshes:
    print("ASSET_SCRIPT_ERROR " + json.dumps("build() created no mesh objects"))
    sys.exit(3)
for obj in scene.objects:
    if obj.type in ("MESH", "ARMATURE") and any(abs(s - 1.0) > 1e-6 for s in obj.scale):
        if obj.type == "MESH" and obj.data.users == 1:
            obj.data.transform(Matrix.Diagonal(obj.scale).to_4x4())
        obj.scale = (1.0, 1.0, 1.0)
lo = [1e9] * 3; hi = [-1e9] * 3
polys = 0
for obj in meshes:
    polys += len(obj.data.polygons)
    for corner in obj.bound_box:
        v = obj.matrix_world @ obj.data.vertices[0].co.__class__(corner)
        for i in range(3):
            lo[i] = min(lo[i], v[i]); hi[i] = max(hi[i], v[i])
bounds = [round(hi[i] - lo[i], 4) for i in range(3)]
for k, v in spec["scene_props"].items():
    scene[k] = v
bpy.ops.wm.save_as_mainfile(filepath=spec["out"], compress=True)
print("ASSET_SCRIPT_RESULT " + json.dumps({"bounds_m": bounds, "polys": polys, "objects": len(meshes), "min_z": round(lo[2], 4)}))
'''


@dataclass
class RunResult:
    ok: bool
    error: str = ""
    seconds: float = 0.0
    bounds_m: tuple[float, float, float] | None = None
    polys: int = 0
    objects: int = 0
    min_z: float = 0.0
    stdout_tail: str = ""

    def metrics(self) -> dict[str, Any]:
        return {"bounds_m": self.bounds_m, "polys": self.polys, "objects": self.objects, "min_z": self.min_z, "seconds": round(self.seconds, 2)}


def run_script(
    library: Path,
    script: str,
    params: dict[str, Any],
    out_file: Path,
    scene_props: dict[str, Any],
    time_limit_s: float = 300.0,
    python: str | None = None,
) -> RunResult:
    """Run a checked script in a child process and save the result to ``out_file``, which
    must lie under ``library/props``. Static-check failures are returned, not raised."""
    library = Path(library).resolve()
    out_file = Path(out_file).resolve()
    if library / "props" not in out_file.parents:
        return RunResult(False, f"output {out_file} is not under {library / 'props'}")
    errors = static_check(script)
    if errors:
        return RunResult(False, "static check: " + "; ".join(errors))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    spec = {"script": script, "params": params, "out": str(out_file), "scene_props": scene_props}
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="asset_builder_") as cwd:
        try:
            proc = subprocess.run(
                [python or sys.executable, "-u", "-c", CHILD, json.dumps(spec)],
                cwd=cwd, env=env, capture_output=True, text=True, timeout=time_limit_s,
            )
        except subprocess.TimeoutExpired:
            return RunResult(False, f"time limit of {time_limit_s:.0f} s exceeded", time.monotonic() - start)
    seconds = time.monotonic() - start
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-12:])
    for line in proc.stdout.splitlines():
        if line.startswith("ASSET_SCRIPT_ERROR "):
            return RunResult(False, "script failed: " + json.loads(line.split(" ", 1)[1]).strip()[-800:], seconds, stdout_tail=tail)
        if line.startswith("ASSET_SCRIPT_RESULT "):
            data = json.loads(line.split(" ", 1)[1])
            if not out_file.exists():
                return RunResult(False, "the runner saved nothing", seconds, stdout_tail=tail)
            return RunResult(True, "", seconds, tuple(data["bounds_m"]), data["polys"], data["objects"], data["min_z"], tail)
    return RunResult(False, f"child exited with {proc.returncode} and no result", seconds, stdout_tail=tail)

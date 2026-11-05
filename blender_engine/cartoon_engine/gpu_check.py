"""Is the GPU usable from this worker? Run inside the container:

    docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml exec worker-render \\
        python -m cartoon_engine.gpu_check

It prints which Cycles device the studio would choose, renders a small test scene with Cycles
on that device and on the CPU (timing both), and renders it with Eevee to show whether EGL is
hardware (NVIDIA) or software (Mesa llvmpipe). Exit code 0 when everything rendered; 1 when
``--require-gpu`` is given and Cycles found no GPU, or when a render failed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path


def _scene(width: int, height: int):
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0))
    bpy.ops.object.shade_smooth()
    bpy.ops.object.modifier_add(type="SUBSURF")
    bpy.context.object.modifiers[-1].levels = 2
    bpy.context.object.modifiers[-1].render_levels = 2
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    return scene


def _render(scene, path: Path) -> float:
    import bpy

    scene.render.filepath = str(path)
    t = time.monotonic()
    bpy.ops.render.render(write_still=True)
    if not path.is_file():
        raise RuntimeError(f"{path.name} was not written")
    return time.monotonic() - t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--require-gpu", action="store_true", help="fail when Cycles finds no GPU")
    ap.add_argument("--skip-cpu", action="store_true", help="do not time the CPU render")
    a = ap.parse_args(argv)

    import bpy

    from cartoon_engine.core import device as D

    print(f"Blender {bpy.app.version_string}")
    dev = D.choose()
    for backend, result in dev.tried.items():
        print(f"  {backend:6} {result}")
    print(f"Cycles device: {dev.describe()}  (STUDIO_CYCLES_DEVICE={dev.policy})")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        times = {}
        runs = [("chosen", dev)] + ([] if (a.skip_cpu or not dev.gpu) else [("cpu", D.Device("CPU"))])
        for label, d in runs:
            scene = _scene(a.width, a.height)
            D._chosen = d
            D.use_cycles_device(scene, samples=a.samples)
            try:
                times[d.backend] = _render(scene, out / f"cycles_{label}.png")
                print(f"Cycles {d.backend:5} {a.width}x{a.height} {a.samples} samples: {times[d.backend]:.2f} s")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"Cycles {d.backend:5} FAILED: {exc}")
        D._chosen = dev
        if dev.gpu and "CPU" in times and dev.backend in times:
            print(f"GPU speed-up on this scene: {times['CPU'] / max(times[dev.backend], 1e-6):.1f}x "
                  "(the first GPU render includes loading kernels; episodes keep them loaded)")

        from cartoon_engine.jobs.render_scene import eevee_possible

        if not eevee_possible():
            print("Eevee: unavailable (libEGL.so.1 cannot be loaded)")
        else:
            scene = _scene(a.width, a.height)
            scene.render.engine = "BLENDER_EEVEE"
            try:
                t = _render(scene, out / "eevee.png")
                r = D.eevee_renderer() or "unknown"
                kind = "software, no GPU" if "llvmpipe" in r.lower() or "softpipe" in r.lower() else "hardware"
                print(f"Eevee: {t:.2f} s on {r} ({kind})")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"Eevee FAILED: {exc}")
    if a.require_gpu and not dev.gpu:
        print("No GPU: Cycles renders on the CPU. See docs/RUNNING.md, 'Rendering on an NVIDIA GPU'.")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

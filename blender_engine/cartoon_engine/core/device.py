"""Which device Cycles renders on, chosen once per Blender process from what Blender sees.

``STUDIO_CYCLES_DEVICE`` picks the policy (default ``auto``):

* ``auto``: OptiX, then CUDA, then the CPU: the first backend with at least one GPU wins.
* ``optix`` / ``cuda``: only that backend, else the CPU (a missing GPU never fails a job).
* ``cpu``: never look for a GPU.

Every render path calls :func:`use_cycles_device`, which sets the scene to the chosen device,
prints one ``[studio] cycles device: …`` line to the job log and returns what is in force so
reports can record it. A GPU gets more samples for final renders (``STUDIO_GPU_MIN_SAMPLES``,
default 64, 0 turns it off) and denoises on the GPU; the CPU keeps the requested samples.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field

ORDER = ("OPTIX", "CUDA")
POLICIES = {"auto": ORDER, "optix": ("OPTIX",), "cuda": ("CUDA",), "cpu": ()}
DEFAULT_GPU_MIN_SAMPLES = 64


@dataclass(frozen=True)
class Device:
    backend: str  # "OPTIX", "CUDA" or "CPU"
    names: tuple[str, ...] = ()
    policy: str = "auto"
    tried: dict[str, str] = field(default_factory=dict)

    @property
    def gpu(self) -> bool:
        return self.backend != "CPU"

    def describe(self) -> str:
        return f"{self.backend} ({', '.join(self.names)})" if self.names else self.backend

    def as_dict(self) -> dict:
        d = asdict(self)
        d["names"], d["gpu"] = list(self.names), self.gpu
        return d


_chosen: Device | None = None


def policy() -> str:
    p = os.environ.get("STUDIO_CYCLES_DEVICE", "auto").strip().lower() or "auto"
    if p not in POLICIES:
        print(f"[studio] STUDIO_CYCLES_DEVICE={p!r} is not one of {sorted(POLICIES)}; using auto", flush=True)
        p = "auto"
    return p


def gpu_min_samples() -> int:
    try:
        return max(0, int(os.environ.get("STUDIO_GPU_MIN_SAMPLES", DEFAULT_GPU_MIN_SAMPLES)))
    except ValueError:
        return DEFAULT_GPU_MIN_SAMPLES


def _gpus(prefs, backend: str) -> list:
    """The GPUs of ``backend`` Blender can see (enabled), or [] (and why, via the exception)."""
    prefs.compute_device_type = backend  # TypeError when this build has no such backend
    if hasattr(prefs, "refresh_devices"):
        prefs.refresh_devices()
    else:  # older builds
        prefs.get_devices()
    found = [d for d in prefs.devices if d.type == backend]
    for d in prefs.devices:
        d.use = d.type == backend  # GPU only: mixing in the CPU slows small GPU tiles down
    return found


def choose(refresh: bool = False) -> Device:
    """Pick the device for this process (cached). Never raises: anything odd means the CPU."""
    global _chosen
    if _chosen is not None and not refresh:
        return _chosen
    p = policy()
    tried: dict[str, str] = {}
    dev = None
    try:
        import bpy

        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in POLICIES[p]:
            try:
                found = _gpus(prefs, backend)
            except Exception as exc:  # noqa: BLE001 - a backend this build or driver lacks
                tried[backend] = f"unavailable: {exc}"[:200]
                continue
            if found:
                tried[backend] = "ok"
                dev = Device(backend, tuple(d.name for d in found), p, tried)
                break
            tried[backend] = "no device"
        if dev is None:
            try:
                prefs.compute_device_type = "NONE"
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 - no Cycles add-on: still report the CPU
        tried["cycles"] = f"preferences unavailable: {exc}"[:200]
    _chosen = dev or Device("CPU", (), p, tried)
    print(f"[studio] cycles device: {_chosen.describe()} (policy {p}; tried {tried or 'nothing'})", file=sys.stdout,
          flush=True)
    return _chosen


def use_cycles_device(scene, final: bool = False, samples: int | None = None) -> dict:
    """Render ``scene`` with Cycles on the chosen device.

    ``final`` marks an episode frame: on a GPU it gets at least ``STUDIO_GPU_MIN_SAMPLES``
    samples and persistent data (the scene stays loaded between frames). Previews and the CPU
    keep the samples they asked for. Denoising stays OpenImageDenoise everywhere (the look does
    not change with the device); on a GPU it runs there. Returns what is in force.
    """
    dev = choose()
    scene.render.engine = "CYCLES"
    requested = samples if samples is not None else scene.cycles.samples
    in_force = requested
    if dev.gpu:
        scene.cycles.device = "GPU"
        if final and gpu_min_samples():
            in_force = max(requested, gpu_min_samples())
        try:
            scene.render.use_persistent_data = bool(final)
        except AttributeError:
            pass
    else:
        scene.cycles.device = "CPU"
    scene.cycles.samples = in_force
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except (TypeError, AttributeError):
        scene.cycles.use_denoising = False
    if hasattr(scene.cycles, "denoising_use_gpu"):
        scene.cycles.denoising_use_gpu = dev.gpu
    return {"device": dev.backend, "device_names": list(dev.names), "samples": in_force,
            "samples_requested": requested, "denoise": bool(scene.cycles.use_denoising)}


def eevee_renderer() -> str | None:
    """What the GPU module draws with (e.g. ``NVIDIA … / OPENGL`` or ``Mesa llvmpipe`` for
    software EGL), or None when that cannot be asked yet."""
    try:
        import gpu

        return f"{gpu.platform.vendor_get()} {gpu.platform.renderer_get()} / {gpu.platform.backend_type_get()}"
    except Exception:  # noqa: BLE001
        return None

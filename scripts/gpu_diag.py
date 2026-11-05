"""What does this container see of the NVIDIA driver? Standard library only, no Blender.

Pipe it into a worker (no rebuild needed):

    Get-Content scripts/gpu_diag.py | docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml exec -T worker-render python -
    cat scripts/gpu_diag.py | docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml exec -T worker-render python -

It prints the user and capabilities, the GPU device nodes, where the driver libraries are,
and what cuInit returns when libcuda is loaded straight (the call Cycles makes).
"""

import ctypes
import glob
import os
import shutil
import subprocess

CUDA_ERRORS = {0: "SUCCESS", 3: "NOT_INITIALIZED", 34: "STUB_LIBRARY", 100: "NO_DEVICE", 101: "INVALID_DEVICE",
               304: "OPERATING_SYSTEM", 500: "NOT_FOUND", 801: "NOT_SUPPORTED", 803: "SYSTEM_DRIVER_MISMATCH",
               804: "COMPAT_NOT_SUPPORTED_ON_DEVICE", 999: "UNKNOWN"}


def line(k, v):
    print(f"{k:22} {v}")


def main():
    line("uid/gid", f"{os.getuid()}/{os.getgid()}")
    with open("/proc/self/status") as f:
        caps = {k: v.strip() for k, v in (ln.split(":", 1) for ln in f if ln.startswith(("CapEff", "NoNewPrivs")))}
    line("CapEff / NoNewPrivs", f"{caps.get('CapEff')} / {caps.get('NoNewPrivs')}")
    for k in ("NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES"):
        line(k, os.environ.get(k, "-"))
    for pat in ("/dev/dxg", "/dev/nvidia*"):
        for p in glob.glob(pat) or [f"{pat} (none)"]:
            if os.path.exists(p):
                st = os.stat(p)
                line("device", f"{p} mode {oct(st.st_mode & 0o777)} uid {st.st_uid} gid {st.st_gid} "
                               f"rw={os.access(p, os.R_OK | os.W_OK)}")
            else:
                line("device", p)
    libs = sorted({p for pat in ("/usr/lib/wsl/lib/libcuda*", "/usr/lib/wsl/lib/libnvoptix*", "/usr/lib/wsl/lib/libdxcore*",
                                 "/usr/lib/wsl/lib/libd3d12*", "/usr/lib/x86_64-linux-gnu/libcuda*",
                                 "/usr/lib/x86_64-linux-gnu/libnvoptix*", "/usr/lib/x86_64-linux-gnu/libEGL_nvidia*",
                                 "/usr/lib64/libcuda*") for p in glob.glob(pat)})
    line("driver libraries", ", ".join(libs) or "none")
    drivers = glob.glob("/usr/lib/wsl/drivers/*")
    line("/usr/lib/wsl/drivers", f"{len(drivers)} entries" + (f" e.g. {os.path.basename(drivers[0])}" if drivers else ""))
    line("egl vendors", ", ".join(sorted(os.path.basename(p) for p in glob.glob("/usr/share/glvnd/egl_vendor.d/*"))) or "none")
    smi = shutil.which("nvidia-smi") or ("/usr/lib/wsl/lib/nvidia-smi" if os.path.exists("/usr/lib/wsl/lib/nvidia-smi") else None)
    if smi:
        r = subprocess.run([smi, "-L"], capture_output=True, text=True)
        line("nvidia-smi -L", (r.stdout or r.stderr).strip() or f"exit {r.returncode}")
    else:
        line("nvidia-smi", "not in the container")
    try:
        cuda = ctypes.CDLL("libcuda.so.1")
    except OSError as exc:
        line("libcuda.so.1", f"cannot load: {exc}")
        return
    with open("/proc/self/maps") as f:
        where = sorted({ln.split()[-1] for ln in f if "libcuda" in ln})
    line("libcuda loaded from", ", ".join(where))
    rc = cuda.cuInit(0)
    line("cuInit(0)", f"{rc} {CUDA_ERRORS.get(rc, '')}")
    if rc == 0:
        n, v = ctypes.c_int(), ctypes.c_int()
        cuda.cuDeviceGetCount(ctypes.byref(n))
        cuda.cuDriverGetVersion(ctypes.byref(v))
        line("devices / driver API", f"{n.value} / {v.value // 1000}.{v.value % 1000 // 10}")
    for name in ("libnvoptix.so.1", "libEGL_nvidia.so.0"):
        try:
            ctypes.CDLL(name)
            line(name, "loads")
        except OSError as exc:
            line(name, f"cannot load: {str(exc)[:120]}")


if __name__ == "__main__":
    main()

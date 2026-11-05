"""Cycles device choice (``cartoon_engine.core.device``): OptiX, then CUDA, then the CPU, and a
machine without a GPU (like CI) always lands on the CPU with the requested samples."""

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from cartoon_engine.core import device as D


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    D._chosen = None
    yield
    D._chosen = None


def test_cpu_policy_never_looks_for_a_gpu(monkeypatch):
    monkeypatch.setenv("STUDIO_CYCLES_DEVICE", "cpu")
    dev = D.choose()
    assert dev.backend == "CPU" and dev.tried == {} and not dev.gpu


def test_auto_tries_optix_then_cuda_and_falls_back(monkeypatch):
    monkeypatch.setenv("STUDIO_CYCLES_DEVICE", "auto")
    dev = D.choose()
    assert list(dev.tried)[: 1] == ["OPTIX"]
    if not dev.gpu:  # no NVIDIA GPU here: both were tried, none found
        assert list(dev.tried) == ["OPTIX", "CUDA"] and all(v != "ok" for v in dev.tried.values())
    else:
        assert dev.tried[dev.backend] == "ok" and dev.names


def test_unknown_policy_means_auto(monkeypatch):
    monkeypatch.setenv("STUDIO_CYCLES_DEVICE", "quantum")
    assert D.choose().policy == "auto"


def test_samples_on_the_cpu_and_on_a_gpu(monkeypatch):
    scene = bpy.context.scene
    monkeypatch.setenv("STUDIO_GPU_MIN_SAMPLES", "64")
    D._chosen = D.Device("CPU")
    got = D.use_cycles_device(scene, final=True, samples=4)
    assert got["device"] == "CPU" and got["samples"] == 4 and scene.cycles.device == "CPU" and scene.cycles.samples == 4
    assert got["denoise"]

    D._chosen = D.Device("OPTIX", ("Fake RTX",))  # what a GPU worker does with the scene
    got = D.use_cycles_device(scene, final=True, samples=4)
    assert got["samples"] == 64 and got["samples_requested"] == 4 and scene.cycles.device == "GPU"
    assert scene.render.use_persistent_data
    assert D.use_cycles_device(scene, final=True, samples=256)["samples"] == 256  # never fewer than asked
    assert D.use_cycles_device(scene, samples=4)["samples"] == 4  # previews stay fast
    monkeypatch.setenv("STUDIO_GPU_MIN_SAMPLES", "0")
    assert D.use_cycles_device(scene, final=True, samples=4)["samples"] == 4

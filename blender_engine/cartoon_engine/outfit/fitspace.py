"""Skeleton space: moving garment geometry from the reference body it was generated on to
another character of the same rig profile (architecture 9.2, "fit adjustment").

Body meshes of two characters do not share topology (the skin modifier's hulls change with
the proportions), so a garment cannot be bound to body vertices. It is bound to the limb
segments instead: every point is expressed in the frames of its nearest segments (position
along the segment, offsets across it in units of the local limb radius) and rebuilt on the
target's segments. Points are blended over up to three segments with the same weighting
the body skin weights use, so the mapping is smooth across joints. The head uses one affine
map from head size, because the head is not a skin-modifier limb.

Pure numpy: no bpy, so tests run anywhere and Blender and tests see the same numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from cartoon_engine.character.skeleton import Skeleton, segments

HEAD_SEGMENT = "DEF-spine.006"
NEIGHBOURS = 3


def region_key(seg: tuple[str, str, str, str]) -> str:
    """``upper_arm.L`` for a sided limb segment, ``torso``/``neck``/``head`` otherwise."""
    bone, region = seg[0], seg[1]
    if region in ("torso", "neck", "head"):
        return region
    return f"{region}.{bone.rsplit('.', 1)[-1]}"


@dataclass
class SegArrays:
    segs: list[tuple[str, str, str, str]]
    a: np.ndarray
    b: np.ndarray
    ra: np.ndarray  # (n, 2) radii at a
    rb: np.ndarray
    u: np.ndarray
    v: np.ndarray
    w: np.ndarray
    length: np.ndarray

    @property
    def keys(self) -> list[str]:
        return [region_key(s) for s in self.segs]


def _radius(sk: Skeleton, joint: str) -> tuple[float, float]:
    r = sk.radii.get(joint)
    return (float(r[0]), float(r[1])) if r else (0.02 * sk.height, 0.02 * sk.height)


def seg_arrays(sk: Skeleton) -> SegArrays:
    segs = segments(sk)
    a = np.array([sk.j(s[2]) for s in segs], dtype=float)
    b = np.array([sk.j(s[3]) for s in segs], dtype=float)
    ra = np.array([_radius(sk, s[2]) for s in segs])
    rb = np.array([_radius(sk, s[3]) for s in segs])
    d = b - a
    length = np.maximum(np.linalg.norm(d, axis=1), 1e-6)
    u = d / length[:, None]
    # across-frame: world X projected off the axis (world Y for segments along X); the same
    # rule on every skeleton, so frames correspond between characters
    ref = np.where((np.abs(u[:, 0]) > 0.9)[:, None], np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    v = ref - (ref * u).sum(axis=1)[:, None] * u
    v /= np.linalg.norm(v, axis=1)[:, None]
    w = np.cross(u, v)
    return SegArrays(segs, a, b, ra, rb, u, v, w, length)


def nearest(sa: SegArrays, pts: np.ndarray):
    """Per point: normalised distance and parameter t to every segment, shape (n, segs)."""
    ab = sa.b - sa.a
    denom = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    rel = pts[:, None, :] - sa.a[None]
    t = np.clip((rel * ab[None]).sum(axis=2) / denom[None], 0.0, 1.0)
    closest = sa.a[None] + t[..., None] * ab[None]
    d = np.linalg.norm(pts[:, None, :] - closest, axis=2)
    r = sa.ra.max(axis=1)[None] * (1 - t) + sa.rb.max(axis=1)[None] * t
    return d / np.maximum(r, 1e-4), t


def classify(sk: Skeleton, pts: np.ndarray, sa: SegArrays | None = None) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(region key, segment index, t) of the nearest segment for each point."""
    sa = sa or seg_arrays(sk)
    nd, t = nearest(sa, pts)
    idx = nd.argmin(axis=1)
    keys = sa.keys
    return [keys[i] for i in idx], idx, t[np.arange(len(pts)), idx]


@dataclass
class Binding:
    idx: np.ndarray  # (n, k) segment indices
    weight: np.ndarray  # (n, k), rows sum to 1
    along: np.ndarray  # (n, k) position along the segment in segment lengths (may leave 0..1)
    tc: np.ndarray  # (n, k) clamped parameter for the radius lookup
    dv: np.ndarray  # (n, k) offset along v, in local radius units
    dw: np.ndarray
    head: np.ndarray  # (n,) bool: mapped with the head affine instead


def _radii_at(sa: SegArrays, idx: np.ndarray, t: np.ndarray) -> np.ndarray:
    return sa.ra[idx] * (1 - t)[..., None] + sa.rb[idx] * t[..., None]


def bind(sk: Skeleton, pts: np.ndarray) -> Binding:
    sa = seg_arrays(sk)
    nd, _ = nearest(sa, pts)
    order = np.argsort(nd, axis=1)[:, :NEIGHBOURS]
    rows = np.arange(len(pts))[:, None]
    ndk = nd[rows, order]
    infl = 1.0 / np.maximum(ndk, 1e-3) ** 6
    infl[ndk > ndk[:, :1] * 1.6 + 0.3] = 0.0  # blend only with segments nearly as close
    weight = infl / infl.sum(axis=1, keepdims=True)
    rel = pts[:, None, :] - sa.a[order]
    along = (rel * sa.u[order]).sum(axis=2) / sa.length[order]
    tc = np.clip(along, 0.0, 1.0)
    rad = _radii_at(sa, order, tc)
    dv = (rel * sa.v[order]).sum(axis=2) / np.maximum(rad[..., 0], 1e-5)
    dw = (rel * sa.w[order]).sum(axis=2) / np.maximum(rad[..., 1], 1e-5)
    head_i = next(i for i, s in enumerate(sa.segs) if s[0] == HEAD_SEGMENT)
    return Binding(order, weight, along, tc, dv, dw, head=order[:, 0] == head_i)


def head_affine(src: Skeleton, dst: Skeleton):
    """Map points around the head by head size: centre to centre, per-axis scale."""
    c0, c1 = np.array(src.head_center), np.array(dst.head_center)
    scale = np.array([dst.head_w / src.head_w, dst.head_d / src.head_d, dst.head_h / src.head_h])
    return lambda p: c1 + (np.asarray(p) - c0) * scale


def apply(binding: Binding, src: Skeleton, dst: Skeleton, pts: np.ndarray) -> np.ndarray:
    """``pts`` (bound on ``src`` with ``binding``) rebuilt on ``dst``."""
    s0, s1 = seg_arrays(src), seg_arrays(dst)
    del s0
    idx = binding.idx
    # offsets keep their size in radius units of the target; radii are per axis
    r1 = _radii_at(s1, idx, binding.tc)
    mapped = (s1.a[idx] + (binding.along * s1.length[idx])[..., None] * s1.u[idx]
              + (binding.dv * r1[..., 0])[..., None] * s1.v[idx] + (binding.dw * r1[..., 1])[..., None] * s1.w[idx])
    out = (mapped * binding.weight[..., None]).sum(axis=1)
    if binding.head.any():
        out[binding.head] = head_affine(src, dst)(pts[binding.head])
    return out


def retarget(src: Skeleton, dst: Skeleton, pts: np.ndarray) -> np.ndarray:
    return apply(bind(src, pts), src, dst, pts)


def rigid_map(src: Skeleton, dst: Skeleton, pts: np.ndarray, anchor: np.ndarray, use_head: bool):
    """A rigid piece keeps its shape: one translation and a uniform scale. Head pieces use
    the head affine at the centre and the mean head scale; others map their anchor through
    skeleton space and scale by the local limb radius."""
    pts = np.asarray(pts, dtype=float)
    if use_head:
        f = head_affine(src, dst)
        c0 = np.array(src.head_center)
        s = float(np.mean([dst.head_w / src.head_w, dst.head_d / src.head_d, dst.head_h / src.head_h]))
        return f(c0) + (pts - c0) * s
    a0 = np.asarray(anchor, dtype=float)[None]
    b = bind(src, a0)
    a1 = apply(b, src, dst, a0)[0]
    s0, s1 = seg_arrays(src), seg_arrays(dst)
    i, t = b.idx[0, 0], b.tc[0, 0]
    r0 = (s0.ra[i] * (1 - t) + s0.rb[i] * t).mean()
    r1 = (s1.ra[i] * (1 - t) + s1.rb[i] * t).mean()
    return a1 + (pts - a0[0]) * (r1 / r0)


# ------------------------------------------------------------------ storage
def skeleton_json(sk: Skeleton) -> str:
    return json.dumps({"height": sk.height, "head_h": sk.head_h, "head_w": sk.head_w, "head_d": sk.head_d,
                       "head_center": list(sk.head_center), "jaw_narrow": sk.jaw_narrow,
                       "joints": {k: list(v) for k, v in sk.joints.items()},
                       "radii": {k: list(v) for k, v in sk.radii.items()}}, sort_keys=True)


def skeleton_from_json(text: str) -> Skeleton:
    d = json.loads(text)
    sk = Skeleton(d["height"], d["head_h"], d["head_w"], d["head_d"], tuple(d["head_center"]), d["jaw_narrow"])
    sk.joints = {k: tuple(v) for k, v in d["joints"].items()}
    sk.radii = {k: tuple(v) for k, v in d["radii"].items()}
    return sk

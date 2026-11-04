"""Rig profile ``cartoon_humanoid@1.0`` (architecture 8): the contract that characters,
outfits and animations are checked against. ``cartoon_engine.rigging`` builds rigs that
satisfy it and ``cartoon_engine.validate`` checks them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RigProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str
    generator: str
    required_deform_bones: tuple[str, ...]
    required_controls: tuple[str, ...]
    extra_bones: tuple[str, ...]
    expressions: tuple[str, ...]
    expression_regions: tuple[str, ...]
    visemes: tuple[str, ...]
    eye_keys: tuple[str, ...]
    mask_regions: tuple[str, ...]
    attachment_points: dict[str, str]  # name -> bone
    face_control_bone: str
    head_deform_bone: str
    rotation_mode: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def expression_keys(self) -> list[str]:
        return [f"expr_{e}_{r}" for e in self.expressions for r in self.expression_regions]

    def viseme_keys(self) -> list[str]:
        return [f"vis_{v}" for v in self.visemes]

    def shape_keys(self) -> list[str]:
        return [*self.expression_keys(), *self.viseme_keys(), *self.eye_keys]

    def face_properties(self) -> list[str]:
        """Custom properties on the face control bone that drive the shape keys (0..1)."""
        return [*(f"expr_{e}" for e in self.expressions), *(f"vis_{v}" for v in self.visemes),
                *self.eye_keys]


CARTOON_HUMANOID_V1 = RigProfile(
    id="cartoon_humanoid",
    version="1.0",
    generator="rigify human metarig without the face rig",
    required_deform_bones=(
        "DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005",
        "DEF-spine.006",
        *(f"DEF-{b}.{s}" for s in "LR" for b in (
            "shoulder", "upper_arm", "forearm", "hand", "thigh", "shin", "foot", "toe",
            "thumb.01", "thumb.02", "thumb.03", "f_index.01", "f_index.02", "f_index.03",
            "f_middle.01", "f_middle.02", "f_middle.03", "f_ring.01", "f_ring.02", "f_ring.03",
            "f_pinky.01", "f_pinky.02", "f_pinky.03")),
    ),
    required_controls=("root", "torso", "chest", "hips", "neck", "head",
                       *(f"{b}.{s}" for s in "LR" for b in ("hand_ik", "upper_arm_fk", "forearm_fk", "hand_fk",
                                                             "foot_ik", "thigh_fk", "shin_fk", "foot_fk",
                                                             "thumb.01_master", "f_index.01_master"))),
    extra_bones=("face_ctrl", "eye_target", "DEF-eye.L", "DEF-eye.R"),
    expressions=("happy", "sad", "angry", "surprised", "fearful", "confused", "excited", "laughing", "crying"),
    expression_regions=("brow", "eyes", "mouth"),
    visemes=("AI", "E", "O", "U", "FV", "L", "MBP", "WQ", "TH", "rest"),
    eye_keys=("eye_blink_L", "eye_blink_R", "eye_wide_L", "eye_wide_R", "eye_squint_L", "eye_squint_R"),
    mask_regions=("head", "neck", "torso", "upper_arm", "forearm", "hand", "thigh", "shin", "foot"),
    attachment_points={"head_top": "DEF-spine.006", "face_eyes": "DEF-spine.006", "back_center": "DEF-spine.003",
                       "hand_R_grip": "DEF-hand.R", "hand_L_grip": "DEF-hand.L", "wrist_L": "DEF-forearm.L.001",
                       "wrist_R": "DEF-forearm.R.001"},
    face_control_bone="face_ctrl",
    head_deform_bone="DEF-spine.006",
    rotation_mode="QUATERNION",
)

BUILTIN_RIG_PROFILES = {CARTOON_HUMANOID_V1.ref: CARTOON_HUMANOID_V1}

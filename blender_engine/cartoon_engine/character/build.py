"""Assemble a character from a ``ResolvedCharacter``: geometry now, rig and controls in
``rig.build_rig``. Returns the objects by role so the job and the validators find them
without guessing names."""

from __future__ import annotations

from dataclasses import dataclass, field

from studio_contracts.character import ResolvedCharacter

from cartoon_engine.character import body, hair, head, materials
from cartoon_engine.character.face import shape_key_states
from cartoon_engine.character.skeleton import Skeleton, build_skeleton

TEMPLATE_ID = "TPL-HUMANOID-STANDIN"
TEMPLATE_VERSION = "0.1"


@dataclass
class Character:
    resolved: ResolvedCharacter
    skeleton: Skeleton
    collection: object
    body: object
    head: object
    eyes: dict[str, object]
    hair: object | None
    head_parts: head.HeadParts
    materials: dict
    shape_keys: list[str] = field(default_factory=list)
    rig: object | None = None

    def meshes(self) -> list:
        return [o for o in (self.body, self.head, self.eyes["L"], self.eyes["R"], self.hair) if o is not None]


def build_geometry(rc: ResolvedCharacter, collection_name: str | None = None) -> Character:
    import bpy

    sk = build_skeleton(rc)
    mats = materials.build_materials(rc)
    b = body.build_body(sk, mats)
    hp = head.build_head(sk, rc, mats)
    keys = head.add_shape_keys(hp, shape_key_states())
    hr = hair.build_hair(sk, rc, hp.layout, mats)

    coll = bpy.data.collections.new(collection_name or f"CHAR_{rc.name}")
    bpy.context.scene.collection.children.link(coll)
    for obj in (b, hp.head, hp.eyes["L"], hp.eyes["R"], hr):
        if obj is None:
            continue
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
    return Character(resolved=rc, skeleton=sk, collection=coll, body=b, head=hp.head, eyes=hp.eyes, hair=hr,
                     head_parts=hp, materials=mats, shape_keys=keys)

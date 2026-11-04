from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import Principal, current_principal

router = APIRouter(tags=["identity"])


class MeOut(BaseModel):
    user_id: uuid.UUID
    external_id: str
    email: str | None
    name: str
    roles: list[str]
    permissions: list[str]


@router.get("/me", response_model=MeOut, summary="Who the token belongs to and what they may do")
def me(who: Principal = Depends(current_principal)) -> MeOut:
    return MeOut(user_id=who.user_id, external_id=who.external_id, email=who.email, name=who.name,
                 roles=sorted(who.roles), permissions=sorted(who.permissions))

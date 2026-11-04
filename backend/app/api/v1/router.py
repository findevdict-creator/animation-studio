from fastapi import APIRouter

from app.api.v1 import assets, characters, health, jobs, me

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(jobs.router)
api_router.include_router(assets.router)
api_router.include_router(characters.router)

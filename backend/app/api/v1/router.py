from fastapi import APIRouter

from app.api.v1 import (
    accessories,
    assets,
    characters,
    health,
    jobs,
    library,
    me,
    outfit_spec,
    outfits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(jobs.router)
api_router.include_router(assets.router)
api_router.include_router(characters.router)
api_router.include_router(library.router)
api_router.include_router(outfit_spec.router)
api_router.include_router(outfits.router)
api_router.include_router(outfits.wardrobe_router)
api_router.include_router(accessories.catalog_router)
api_router.include_router(accessories.router)

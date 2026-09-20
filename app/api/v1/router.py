"""Version 1 of the API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, health, packages, platform, quota, tenants, workspaces

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(workspaces.router)
api_router.include_router(packages.router)
api_router.include_router(packages.topics_router)
api_router.include_router(quota.router)
api_router.include_router(platform.router)

__all__ = ["api_router", "health"]

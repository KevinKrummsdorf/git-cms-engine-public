"""Aggregates all CMS engine API routers into a single APIRouter.

Consumers (such as hosting FastAPI applications) mount this single router,
per the pattern documented in the project README:

    from api.router import cms_router
    app.include_router(cms_router)
"""

from __future__ import annotations

from fastapi import APIRouter

from api.articles import router as articles_router
from api.sources import router as sources_router
from api.sync import router as sync_router

# Initialize the aggregate router for all CMS endpoints
cms_router = APIRouter()
cms_router.include_router(sync_router)
cms_router.include_router(articles_router)
cms_router.include_router(sources_router)

__all__ = ["cms_router"]
"""Shared FastAPI dependencies for the CMS engine API routers."""

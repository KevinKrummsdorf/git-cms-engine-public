"""Shared FastAPI dependencies for the CMS engine API routers."""

from __future__ import annotations

from repository.database import get_db

__all__ = ["get_db"]

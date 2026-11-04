"""Standalone ASGI entry point for the Headless CMS engine."""

from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import select
import frontmatter

from api.router import cms_router
from repository.database import engine, SessionLocal
from models.article import ArticleModel, Base
from config import settings


def sync_local_markdown_files(db: Session, content_path: str) -> None:
    """Scans the local content directory for Markdown files and performs transactional upserts.

    Ignoriert strikt 'private', '_templates' und Dateien ausserhalb von 'content'.
    """
    from services.processor import upsert_single_article, validate_frontmatter, InvalidFrontmatterError

    if not os.path.exists(content_path):
        return

    UNPUBLISHED_STATUSES = {"draft", "bilder", "review", "archived", "private"}

    active_files = []
    active_file_paths = set()

    for root, dirs, files in os.walk(content_path):
        # STRIKTER SCHUTZ: private, _templates und versteckte Ordner niemals scannen
        dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d != "private"]

        for file in files:
            if not file.endswith(".md") or file.startswith((".", "_")):
                continue

            file_path = os.path.join(root, file)
            rel_file_path = os.path.relpath(file_path, content_path).replace("\\", "/")
            rel_parts = Path(rel_file_path).parts

            # Nur Dateien im Ordner content/ zulassen
            if not rel_parts or rel_parts[0] != "content":
                continue

            try:
                post = frontmatter.load(file_path)
                metadata = post.metadata or {}
            except Exception as e:
                print(f"Error loading metadata from {file_path}: {e}")
                continue

            status_val = metadata.get("status")
            published_val = metadata.get("published")

            if status_val is not None:
                status_str = str(status_val).strip().lower()
                is_published = (status_str not in UNPUBLISHED_STATUSES)
            elif published_val is not None:
                is_published = bool(published_val)
            else:
                is_published = True

            if not is_published:
                print(f"[LOCAL SYNC SKIP] Skipping unpublished article {rel_file_path} (status: '{status_val}')")
                continue

            try:
                validate_frontmatter(metadata, rel_file_path)
            except InvalidFrontmatterError as e:
                print(f"[LOCAL SYNC INVALID] Skipping {rel_file_path}: {e}")
                continue

            active_files.append((rel_file_path, metadata))
            active_file_paths.add(rel_file_path)

    for rel_file_path, metadata in active_files:
        try:
            upsert_single_article(db, rel_file_path, metadata, active_file_paths)
        except Exception as e:
            print(f"Error during local sync/upsert of {rel_file_path}: {e}")

    try:
        all_db_articles = db.execute(select(ArticleModel)).scalars().all()
        deleted_count = 0
        for art in all_db_articles:
            if art.file_path not in active_file_paths:
                db.delete(art)
                deleted_count += 1
        if deleted_count > 0:
            db.flush()
            print(f"[LOCAL CLEANUP OK] Successfully deleted {deleted_count} stale articles.")
    except Exception as e:
        print(f"Error during local stale cleanup: {e}")

    try:
        from services.processor import rebuild_all_relations_and_sources
        rebuild_all_relations_and_sources(db)
    except Exception as e:
        print(f"Error rebuilding local relations and sources: {e}")

    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema und Tabellen anlegen
    Base.metadata.create_all(bind=engine)

    # Lokalen Sync durchführen, falls CMS_USE_LOCAL_CONTENT aktiviert ist
    if settings.cms_use_local_content:
        db = SessionLocal()
        try:
            content_dir = str(settings.cms_raw_data_path)
            sync_local_markdown_files(db, content_dir)
        finally:
            db.close()

    yield


app = FastAPI(
    title="git-cms-engine",
    description="Standalone Git-backed Headless CMS Engine.",
    version="0.4.0",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrierung der CMS-Routen
app.include_router(cms_router)

# Bereitstellung von /data (ausschließlich für bereits aufbereitete Markdown- und Media-Dateien)
settings.cms_processed_data_path.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=str(settings.cms_processed_data_path)), name="data")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

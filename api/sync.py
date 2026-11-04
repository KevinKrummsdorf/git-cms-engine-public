"""FastAPI Router endpoints coordinates content repository cloning and database synchronization.

Authenticates incoming webhooks, processes files recursively under traversal protection,
maps article metadata to PostgreSQL, and prunes stale articles.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from config import settings
from repository.database import get_db
from services.exceptions import (
    GitSyncError,
    WebhookAuthenticationError,
)
from services.git_sync import sync_repository, verify_github_signature, verify_sync_secret
from services.parser import is_safe_path
from services.processor import process_all_markdown_files

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cms", tags=["sync"])


def _authenticate_request(
    raw_body: bytes,
    sync_secret_header: str | None,
    github_signature_header: str | None,
) -> None:
    """Authenticates the synchronization request against configured secrets.

    Accepts either a timing-safe header shared secret or a valid GitHub HMAC signature.

    Args:
        raw_body (bytes): The raw request body bytes.
        sync_secret_header (str | None): Custom shared-secret HTTP header.
        github_signature_header (str | None): GitHub signature HTTP header.

    Raises:
        WebhookAuthenticationError: If both credentials are missing or invalid.
    """
    if github_signature_header:
        verify_github_signature(
            raw_body, github_signature_header, settings.cms_sync_secret.get_secret_value()
        )
        return

    verify_sync_secret(sync_secret_header, settings.cms_sync_secret.get_secret_value())


@router.post("/sync")
async def sync_content(
    request: Request,
    db: Session = Depends(get_db),
    x_cms_sync_secret: str | None = Header(default=None, alias="X-CMS-Sync-Secret"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> JSONResponse:
    """Pulls the content repository and synchronizes PostgreSQL relational metadata.

    Performs sequential pipeline operations:
    1. Authenticates webhook trigger requests in timing-safe constant time.
    2. Executes safety checks to prevent critical parent directory deletions.
    3. Clears and re-clones the Git repository raw data target folder (or bypasses if local mode).
    4. Recursively parses and updates article entries within transactional boundaries.
    5. Outputs processed documents and cleans up database/disk records.

    Args:
        request (Request): Starlette/FastAPI request object.
        db (Session): Database session context dependency.
        x_cms_sync_secret (str | None): Optional webhook shared-secret authorization token.
        x_hub_signature_256 (str | None): Optional GitHub webhook signature.

    Raises:
        HTTPException (401): If request authentication fails.
        HTTPException (500): If cloning or metadata parsing fails.

    Returns:
        JSONResponse: Standard JSON structure summarizing processing performance.
    """
    raw_body = await request.body()

    try:
        _authenticate_request(raw_body, x_cms_sync_secret, x_hub_signature_256)
    except WebhookAuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail={"status": "error", "error_code": "UNAUTHORIZED", "message": str(exc)},
        )

    try:
        if settings.cms_use_local_content:
            sync_repository(
                repo_url=settings.cms_git_url,
                local_path=settings.cms_raw_data_path,
                branch=settings.cms_git_branch,
                git_pat=(
                    settings.cms_git_pat.get_secret_value() if settings.cms_git_pat else None
                ),
                use_local_content=True,
            )
        else:
            raw_data_path = settings.cms_raw_data_path

            # 2. Strict Safety Validations prior to directory cleaning
            if not raw_data_path:
                logger.error("Safety Check failed: CMS_RAW_DATA_PATH is not defined!")
                raise GitSyncError("Safety Check failed: CMS_RAW_DATA_PATH is not defined!")

            resolved_raw_path = raw_data_path.resolve()
            resolved_configured_path = settings.cms_raw_data_path.resolve()

            # Confirm that the targeted path matches configured settings
            if resolved_raw_path != resolved_configured_path:
                logger.error(
                    f"Safety Check failed: Path {resolved_raw_path} does not match configured {resolved_configured_path}"
                )
                raise GitSyncError("Safety Check failed: raw_data path does not match configured path.")

            # Validate safety to protect against path-traversal escapes
            if not is_safe_path(settings.cms_raw_data_path, raw_data_path):
                logger.error("Safety Check failed: is_safe_path check failed for raw_data_path")
                raise GitSyncError("Safety Check failed: is_safe_path check failed.")

            # Enforce protections to ensure critical system directories are never deleted
            project_root = Path.cwd().resolve()
            critical_paths = {
                Path("/").resolve(),
                project_root,
                Path("/app").resolve(),
            }
            if resolved_raw_path in critical_paths:
                logger.error(f"Safety Check failed: Attempted to delete critical path: {resolved_raw_path}")
                raise GitSyncError(f"Safety Check failed: Attempted to delete critical path: {resolved_raw_path}")

            if resolved_raw_path in project_root.parents:
                logger.error(f"Safety Check failed: Attempted to delete project parent path: {resolved_raw_path}")
                raise GitSyncError(f"Safety Check failed: Attempted to delete parent path: {resolved_raw_path}")

            # 3. Robust Folder Contents Pruning (Safely purge contents without deleting the Docker mount point)
            logger.info(f"Cleaning contents of raw_data directory before sync: {resolved_raw_path}")

            def handle_remove_readonly(func, path_to_remove, excinfo):
                """Helper to override write protection when removing read-only file nodes."""
                try:
                    os.chmod(path_to_remove, stat.S_IWRITE)
                    func(path_to_remove)
                except Exception as force_err:
                    logger.warning(f"Failed to force delete read-only file/directory {path_to_remove}: {force_err}")

            if raw_data_path.exists():
                for item in raw_data_path.iterdir():
                    try:
                        if item.is_file() or item.is_symlink():
                            try:
                                item.unlink()
                            except PermissionError:
                                os.chmod(item, stat.S_IWRITE)
                                item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, onerror=handle_remove_readonly)
                    except Exception as item_err:
                        logger.warning(f"Failed to delete item '{item}': {item_err}")
            else:
                os.makedirs(raw_data_path, exist_ok=True)

            # 4. Git Pull and Copy Procedures
            sync_repository(
                repo_url=settings.cms_git_url,
                local_path=settings.cms_repo_local_path,
                branch=settings.cms_git_branch,
                git_pat=(
                    settings.cms_git_pat.get_secret_value() if settings.cms_git_pat else None
                ),
                use_local_content=False,
            )

            repo_path = settings.cms_repo_local_path

            for folder_name in ["content", "resources"]:
                src_folder = repo_path / folder_name
                dst_folder = raw_data_path / folder_name

                if dst_folder.exists():
                    if dst_folder.is_dir():
                        shutil.rmtree(dst_folder, onerror=handle_remove_readonly)
                    else:
                        dst_folder.unlink()

                if src_folder.is_dir():
                    shutil.copytree(src_folder, dst_folder)
                    logger.info(f"Copied folder {folder_name} from {repo_path} to {raw_data_path}")

        processed_files = process_all_markdown_files(db)
        db.commit()
    except GitSyncError as exc:
        db.rollback()
        logger.exception("CMS sync failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error_code": "SYNC_FAILED", "message": str(exc)},
        )

    processed_files_payload = processed_files

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": "Content synchronization and database sync completed successfully",
            "processed_files": processed_files_payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

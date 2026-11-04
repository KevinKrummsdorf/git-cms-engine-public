"""Centralized configuration for the git-cms-engine module.

All runtime configuration parameters are loaded from environment variables
via a single Pydantic ``BaseSettings`` instance. Importing ``settings`` guarantees
consistent, validated configuration values across all application modules.

Sensitive settings (such as ``CMS_GIT_PAT`` and ``CMS_SYNC_SECRET``) are stored
using Pydantic ``SecretStr`` structures to prevent exposure in application logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the CMS engine, sourced from the environment.

    Every field can be overridden via an environment variable of the same
    name (case-insensitive) or via a local ``.env`` file, which is
    convenient for local development but never required in production.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Git repository -----------------------------------------------
    cms_use_local_content: bool = Field(
        default=False,
        description="If True, skips all Git network operations and reads directly from local path.",
    )
    cms_git_url: str = Field(
        default="https://github.com/KevinKrummsdorf/my-docs",
        description="Clone URL for the content repository (HTTPS or SSH).",
    )
    cms_git_pat: Optional[SecretStr] = Field(
        default=None,
        description="Personal Access Token for private repositories.",
    )
    cms_git_branch: str = Field(
        default="main",
        description="Branch to check out / pull during sync.",
    )
    cms_repo_local_path: Path = Field(
        default=Path("./.cms_repo_cache"),
        description="Local filesystem path used as the Git working copy.",
    )

    # --- Dual-Volume Data Paths ----------------------------------------
    cms_raw_data_path: Path = Field(
        default=Path("/app/raw_data"),
        description="Local filesystem path where raw Git repository data resides.",
    )
    cms_processed_data_path: Path = Field(
        default=Path("/app/data"),
        description="Local filesystem path for processed and published markdown files.",
    )
    cms_allowed_folders: list[str] = Field(
        default_factory=list,
        description="Optional list of relative folder/directory paths under content/ to restrict content scanning/processing. If empty, all content is allowed.",
    )

    # --- Webhook / sync authentication ---------------------------------
    cms_sync_secret: SecretStr = Field(
        default=SecretStr("changeme"),
        description="Shared secret required to trigger POST /api/v1/cms/sync.",
    )

    # --- Database --------------------------------------------------------
    cms_database_url: str = Field(
        default="postgresql+psycopg2://postgres:password@db:5432/cms_test_db",
        description="SQLAlchemy connection string for the PostgreSQL database.",
    )
    cms_db_schema: str = Field(
        default="cms",
        description="Target PostgreSQL schema for storing CMS metadata.",
    )

    @field_validator(
        "cms_repo_local_path",
        "cms_raw_data_path",
        "cms_processed_data_path",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: object) -> object:
        """Coerces string-based filesystem path arguments into standard Path objects.

        Args:
            value (object): Input filesystem path value (string or Path).

        Returns:
            object: Coerced Path instance.
        """
        if isinstance(value, str):
            return Path(value)
        return value


settings = Settings()

__all__ = ["Settings", "settings"]

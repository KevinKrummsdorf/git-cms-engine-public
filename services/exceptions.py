"""Custom exceptions for the git-cms-engine parser service.

Centralizing exceptions in a dedicated module keeps error handling
predictable across the sync engine, the FastAPI routers, and the tests.
All exceptions inherit from ``ArticleParsingError`` (except the security
critical ``PathTraversalError``, which is intentionally kept as a
standalone, high-severity exception so it can never be silently caught
by a broad ``except ArticleParsingError`` handler).
"""

from __future__ import annotations

from pathlib import Path


class PathTraversalError(Exception):
    """Raised when a resolved path escapes the allowed repository root.

    This is a security-critical exception. It must be raised whenever a
    file path -- derived from a slug, a repository scan, or an internal
    link -- resolves outside of the configured ``base_dir``. Callers
    should never attempt to recover from this by falling back to a
    "best effort" path; the operation must be aborted entirely.
    """

    def __init__(self, base_dir: Path, target_path: Path) -> None:
        self.base_dir = base_dir
        self.target_path = target_path
        super().__init__(
            f"Path traversal detected: '{target_path}' escapes the "
            f"allowed base directory '{base_dir}'."
        )


class ArticleParsingError(Exception):
    """Generic error raised when a Markdown article cannot be parsed.

    Used as a wrapper for unexpected I/O errors, malformed YAML, or any
    other failure that occurs while reading/parsing a specific file.
    """

    def __init__(self, file_path: Path, message: str) -> None:
        self.file_path = file_path
        self.message = message
        super().__init__(f"Failed to parse article '{file_path}': {message}")


class InvalidFrontmatterError(ArticleParsingError):
    """Raised when a file's frontmatter fails Pydantic schema validation.

    Kept as a subclass of ``ArticleParsingError`` so that generic
    "something went wrong while parsing this file" handlers still catch
    it, while more specific handlers can target frontmatter validation
    issues explicitly (e.g. to report actionable CMS editor feedback).
    """

    def __init__(self, file_path: Path, message: str) -> None:
        super().__init__(file_path, f"Invalid frontmatter - {message}")


class GitSyncError(Exception):
    """Raised when cloning or pulling the content repository fails.

    Wraps any non-zero exit / OS-level failure from the underlying ``git``
    subprocess invocation (clone, fetch, checkout, pull) so callers (the
    ``/sync`` API route) can map it to a single, predictable
    ``500 SYNC_FAILED`` response instead of leaking raw subprocess output.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class WebhookAuthenticationError(Exception):
    """Raised when a ``/sync`` request fails signature/secret verification.

    Deliberately does not include the received or expected secret in the
    exception message to avoid leaking sensitive material into logs.
    """


class ArticleRepositoryError(Exception):
    """Raised when persisting parsed articles to PostgreSQL fails.

    Wraps SQLAlchemy-level errors (constraint violations, connection
    failures, transaction rollbacks) behind a domain-specific exception so
    the API layer never needs to import SQLAlchemy exception types.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

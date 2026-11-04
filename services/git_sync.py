"""Git synchronization service for the git-cms-engine module.

Responsible for turning the configured remote content repository
(``settings.cms_git_url``) into an up-to-date local working copy
(``settings.cms_repo_local_path``) that :func:`services.parser.scan_and_parse_repository`
can then read from.

Supports a local fallback mode (``use_local_content=True``) to bypass
Git operations entirely during local development or offline testing.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from services.exceptions import GitSyncError, WebhookAuthenticationError

__all__ = [
    "build_authenticated_url",
    "sync_repository",
    "verify_sync_secret",
    "verify_github_signature",
]

_GIT_TIMEOUT_SECONDS = 120


def build_authenticated_url(git_url: str, git_pat: str | None) -> str:
    """Injects a Personal Access Token into an HTTPS Git clone URL.

    Args:
        git_url (str): The raw remote Git repository URL (e.g. HTTPS or SSH).
        git_pat (str | None): The Personal Access Token used for authentication.

    Returns:
        str: The authenticated Git URL containing the credential, or the original
        URL if the PAT is not provided or the scheme is not HTTP/HTTPS.
    """
    if not git_pat:
        return git_url

    parts = urlsplit(git_url)
    if parts.scheme not in ("http", "https"):
        return git_url

    netloc = f"{git_pat}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_git_command(args: list[str], cwd: Path | None = None) -> str:
    """Runs a git subprocess command and returns its stdout.

    Args:
        args (list[str]): The arguments to pass to the git executable.
        cwd (Path | None, optional): The directory in which to execute the command.

    Raises:
        GitSyncError: If the git command fails, times out, or the executable is not found.

    Returns:
        str: The standard output from the executed git command.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise GitSyncError("Git executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitSyncError(f"Git command timed out after {_GIT_TIMEOUT_SECONDS}s.") from exc

    if result.returncode != 0:
        raise GitSyncError(f"Git command failed: {result.stderr.strip() or result.stdout.strip()}")

    return result.stdout


def sync_repository(
    repo_url: str,
    local_path: Path,
    branch: str = "main",
    git_pat: str | None = None,
    use_local_content: bool = False,
) -> bool:
    """Ensures the local path contains the latest repository files.

    If use_local_content is True, all network git operations are bypassed, and
    the function returns False. Otherwise, the local path directory contents are
    completely purged and the specified repository branch is cloned.

    Args:
        repo_url (str): The remote Git URL of the repository.
        local_path (Path): The local target filesystem directory to clone into.
        branch (str, optional): The target branch to clone. Defaults to "main".
        git_pat (str | None, optional): Git Personal Access Token for authentication.
        use_local_content (bool, optional): Bypasses all Git network processes if True.

    Raises:
        GitSyncError: If cloning fails, or if local path is missing in offline mode.

    Returns:
        bool: True if a repository clone was performed, False if bypassed.
    """
    # 0. Local mode: bypass git operations completely for offline testing.
    if use_local_content:
        if not local_path.exists():
            raise GitSyncError(
                f"Local content path '{local_path}' does not exist while CMS_USE_LOCAL_CONTENT is True."
            )
        return False

    if not repo_url:
        raise GitSyncError("CMS_GIT_URL is empty, but local mode is disabled (CMS_USE_LOCAL_CONTENT=false).")

    authenticated_url = build_authenticated_url(repo_url, git_pat)

    # 1. Safely purge contents of raw_data without deleting the root mount point directory.
    if local_path.exists():
        for item in local_path.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as exc:
                print(f"[GIT SYNC WARNING] Failed to remove '{item}': {exc}")
    else:
        local_path.mkdir(parents=True, exist_ok=True)

    # 2. Clone the repository cleanly into the emptied target directory.
    _run_git_command(
        ["clone", "--branch", branch, "--single-branch", authenticated_url, "."],
        cwd=local_path,
    )
    return True


def verify_sync_secret(received_secret: str | None, expected_secret: str) -> None:
    """Verifies a shared-secret header value in constant time.

    Args:
        received_secret (str | None): The secret token received in the HTTP header.
        expected_secret (str): The expected secret token configured in the system settings.

    Raises:
        WebhookAuthenticationError: If the received secret does not match the expected secret.
    """
    if not received_secret or not hmac.compare_digest(received_secret, expected_secret):
        raise WebhookAuthenticationError("Missing or invalid X-CMS-Sync-Secret header.")


def verify_github_signature(payload_body: bytes, signature_header: str | None, secret: str) -> None:
    """Verifies a GitHub webhook HMAC signature (X-Hub-Signature-256) in constant time.

    Args:
        payload_body (bytes): The raw request payload body bytes.
        signature_header (str | None): The HMAC signature header value received from GitHub.
        secret (str): The configured webhook secret.

    Raises:
        WebhookAuthenticationError: If the signature header is missing, malformed, or invalid.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        raise WebhookAuthenticationError("Missing or malformed X-Hub-Signature-256 header.")

    expected_digest = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    received_digest = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected_digest, received_digest):
        raise WebhookAuthenticationError("GitHub webhook signature verification failed.")

"""Markdown and YAML-Frontmatter parser for the git-cms-engine module.

This service is the single entry point for turning a local Git checkout
of Markdown knowledge-base articles into structured, validated Python
objects (``ParsedArticle``) that can be persisted to PostgreSQL or
serialized directly to the Next.js frontend.

Security Note:
    Because file paths in this module are ultimately derived from data that
    originates in a Git repository (i.e. content that could theoretically be
    influenced by a malicious commit, a crafted slug, or a symlink), *every*
    path operation is guarded against path traversal via :func:`is_safe_path`.
    Callers must never bypass this guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from schemas.article import FrontmatterSchema, ParsedArticle
from services.exceptions import (
    ArticleParsingError,
    InvalidFrontmatterError,
    PathTraversalError,
)

__all__ = [
    "is_safe_path",
    "parse_markdown_file",
    "scan_and_parse_repository",
]

# Matches standard Markdown inline links: [Link Title](target)
# Internal links are identified by a relative target ending in `.md`
# (optionally followed by an anchor, e.g. `./craft.md#privilege-escalation`)
# and explicitly NOT starting with a URI scheme (http://, https://, mailto:, etc.).
_MARKDOWN_LINK_PATTERN = re.compile(
    r"\[(?P<title>[^\]]*)\]\((?P<target>[^)\s]+\.md(?:#[^)\s]*)?)\)"
)
_EXTERNAL_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """Verifies that a target filesystem path resolves within a trusted base directory.

    Both paths are resolved to their absolute, canonical forms (resolving symlinks and
    removing relative segments like `..` or `.`) before comparison. This serves as the
    authoritative safety guard to prevent path-traversal vulnerabilities.

    Args:
        base_dir (Path): The trusted root directory (e.g. repository root). Must exist.
        target_path (Path): The path to evaluate, either relative to base_dir or absolute.

    Returns:
        bool: True if the resolved target path lies within or matches the base directory,
        False otherwise.
    """
    resolved_base = base_dir.resolve()
    try:
        resolved_target = target_path.resolve()
    except (OSError, RuntimeError):
        # Fail closed on symlink loops or filesystem-level failures.
        return False

    return resolved_target == resolved_base or resolved_base in resolved_target.parents


def _resolve_and_guard(base_dir: Path, relative_or_absolute_path: Path) -> Path:
    """Resolves a target path against a base directory and enforces safety boundaries.

    Args:
        base_dir (Path): The trusted base directory.
        relative_or_absolute_path (Path): The relative or absolute path to resolve.

    Raises:
        PathTraversalError: If the resolved path lies outside of the base directory.

    Returns:
        Path: The absolute, verified target path.
    """
    resolved_base = base_dir.resolve()

    candidate = relative_or_absolute_path
    if not candidate.is_absolute():
        candidate = resolved_base / candidate

    if not is_safe_path(resolved_base, candidate):
        raise PathTraversalError(resolved_base, candidate)

    return candidate.resolve()


def _is_ignored(name: str) -> bool:
    """Determines if a directory or file should be excluded from content scanning.

    Files and directories whose names begin with `_` (commonly used for drafts or templates)
    or `.` (hidden configurations like `.git` or `.DS_Store`) are excluded from processing.

    Args:
        name (str): The filename or directory name to inspect.

    Returns:
        bool: True if the file or directory should be ignored, False otherwise.
    """
    return name.startswith("_") or name.startswith(".")


def _extract_internal_links(markdown_body: str) -> list[str]:
    """Extracts internal Markdown relative links pointing to local Markdown files.

    Excludes links prefixed with external schemes (e.g., http, https, mailto) even
    if they terminate with a `.md` extension.

    Args:
        markdown_body (str): The raw Markdown text content to parse.

    Returns:
        list[str]: A list of relative targets extracted from internal links,
        preserving order of appearance and eliminating duplicates.
    """
    seen: set[str] = set()
    ordered_links: list[str] = []

    for match in _MARKDOWN_LINK_PATTERN.finditer(markdown_body):
        target = match.group("target").strip()

        if _EXTERNAL_SCHEME_PATTERN.match(target):
            continue

        if target not in seen:
            seen.add(target)
            ordered_links.append(target)

    return ordered_links


def parse_markdown_file(file_path: Path, base_dir: Path) -> ParsedArticle:
    """Parses a target Markdown file and validates its frontmatter schema.

    Executes path safety validation, verifies file extensions, reads raw content,
    extracts the YAML frontmatter, resolves the structure against the validation schema,
    and indexes inline markdown links.

    Args:
        file_path (Path): The relative or absolute path of the target Markdown file.
        base_dir (Path): The trusted base directory used to resolve target paths.

    Raises:
        PathTraversalError: If the target file escapes the base directory.
        ArticleParsingError: If the file is inaccessible, contains invalid encoding, or is malformed.
        InvalidFrontmatterError: If YAML metadata fails Pydantic schema validation.

    Returns:
        ParsedArticle: A structured Pydantic object representing the parsed article data.
    """
    resolved_base = base_dir.resolve()
    resolved_file = _resolve_and_guard(resolved_base, file_path)

    if resolved_file.suffix.lower() != ".md":
        raise ArticleParsingError(
            resolved_file, "Only '.md' files are supported by the parser."
        )

    if not resolved_file.is_file():
        raise ArticleParsingError(resolved_file, "File does not exist or is not a regular file.")

    try:
        # Open using explicit UTF-8 context to guarantee deterministic file closing on Windows platforms.
        with open(resolved_file, mode="r", encoding="utf-8") as file_handle:
            post = frontmatter.load(file_handle)
    except UnicodeDecodeError as exc:
        raise ArticleParsingError(
            resolved_file, f"File is not valid UTF-8 encoded text: {exc}"
        ) from exc
    except OSError as exc:
        raise ArticleParsingError(resolved_file, f"Could not read file: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ArticleParsingError(resolved_file, f"Could not parse file: {exc}") from exc

    try:
        metadata = FrontmatterSchema.model_validate(post.metadata)
    except ValidationError as exc:
        raise InvalidFrontmatterError(resolved_file, str(exc)) from exc

    raw_content: str = post.content
    internal_links = _extract_internal_links(raw_content)

    try:
        relative_path = resolved_file.relative_to(resolved_base)
    except ValueError as exc:
        raise PathTraversalError(resolved_base, resolved_file) from exc

    return ParsedArticle(
        metadata=metadata,
        raw_content=raw_content,
        extracted_internal_links=internal_links,
        file_path=relative_path,
    )


def scan_and_parse_repository(repo_path: Path) -> list[ParsedArticle]:
    """Recursively scans a target directory and parses all discovered Markdown files.

    Performs depth-first traversal of the filesystem, ignoring hidden files, drafts,
    and applying strict path validation to every encountered node.

    Args:
        repo_path (Path): The absolute root directory path containing the Git repository files.

    Raises:
        ArticleParsingError: If the path does not exist, is not a directory, or processing fails.
        PathTraversalError: If traversal discovers nodes outside the base repository boundaries.

    Returns:
        list[ParsedArticle]: A sorted list of successfully parsed article schema structures.
    """
    resolved_repo = repo_path.resolve()

    if not resolved_repo.is_dir():
        raise ArticleParsingError(resolved_repo, "Repository path does not exist or is not a directory.")

    markdown_files = _discover_markdown_files(resolved_repo, resolved_repo)

    return [
        parse_markdown_file(file_path, resolved_repo)
        for file_path in sorted(markdown_files)
    ]


def _discover_markdown_files(directory: Path, base_dir: Path) -> list[Path]:
    """Recursively collects eligible Markdown files within a target directory tree.

    Args:
        directory (Path): The directory being scanned.
        base_dir (Path): The trusted root path to validate traversal boundaries.

    Raises:
        PathTraversalError: If traversal detects a node escaping the base directory.

    Returns:
        list[Path]: A list of absolute paths to eligible Markdown files.
    """
    if not is_safe_path(base_dir, directory):
        raise PathTraversalError(base_dir, directory)

    discovered: list[Path] = []

    for entry in directory.iterdir():
        if _is_ignored(entry.name):
            continue

        if not is_safe_path(base_dir, entry):
            raise PathTraversalError(base_dir, entry)

        if entry.is_dir():
            discovered.extend(_discover_markdown_files(entry, base_dir))
        elif entry.is_file() and entry.suffix.lower() == ".md":
            discovered.append(entry)

    return discovered

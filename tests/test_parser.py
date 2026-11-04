"""Unit tests for services.parser.

Covers:
- Path Traversal protection (is_safe_path / PathTraversalError).
- File filtering rules (_drafts, hidden files, non-.md files ignored).
- Successful parsing & frontmatter validation of a sample article.
- Internal link extraction.
- Invalid frontmatter raising InvalidFrontmatterError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schemas.article import FrontmatterSchema, ParsedArticle
from services.exceptions import (
    ArticleParsingError,
    InvalidFrontmatterError,
    PathTraversalError,
)
from services.parser import (
    is_safe_path,
    parse_markdown_file,
    scan_and_parse_repository,
)


def _base_frontmatter_kwargs() -> dict:
    """Minimal set of required FrontmatterSchema fields for isolated tests."""
    return {
        "id": 12345,
        "title": "Sample",
        "slug": "sample",
        "route": "/writeups/sample",
        "category": "writeups",
        "subcategory": "htb",
        "subsubcategory": "linux",
        "description": "Sample description.",
        "date": "2024-01-01",
        "updated": "2024-01-02",
        "status": "published",
    }

VALID_FRONTMATTER = """---
id: 12345
title: "Craft - HTB Writeup"
slug: craft
route: /writeups/htb/craft
category: writeups
subcategory: htb
subsubcategory: linux
description: "Exploiting an insecure Flask API and a
vulnerable Docker registry."
tags: ["htb", "docker", "flask"]
date: 2024-01-15
updated: 2024-02-01
relevance: high
status: published
published: true
math: false
charts: false
parent: []
related: ["shocker"]
related_sources: ["Kappes2013"]
---

# Craft

See the [Shocker writeup](../linux/shocker.md) for a related box.

Also check [external resource](https://example.com/craft.md)
which is not internal.

Relative link with anchor: [Privilege Escalation](./craft.md#privesc).
"""

INVALID_FRONTMATTER_MISSING_FIELDS = """---
title: "Broken Article"
slug: broken
---

Body content without required fields.
"""

NOT_A_MARKDOWN_FILE = "Just some text, not a markdown article."


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a minimal fake Git-repo-like directory structure."""
    root = tmp_path / "knowledge-base"
    root.mkdir()

    writeups_dir = root / "writeups" / "htb"
    writeups_dir.mkdir(parents=True)
    (writeups_dir / "craft.md").write_text(VALID_FRONTMATTER, encoding="utf-8")

    # Ignored: starts with underscore
    drafts_dir = root / "_drafts"
    drafts_dir.mkdir()
    (drafts_dir / "secret-draft.md").write_text(VALID_FRONTMATTER, encoding="utf-8")

    # Ignored: hidden directory (.git-like)
    hidden_dir = root / ".git"
    hidden_dir.mkdir()
    (hidden_dir / "config.md").write_text(VALID_FRONTMATTER, encoding="utf-8")

    # Ignored: hidden file
    (root / ".DS_Store").write_text("binary-ish", encoding="utf-8")

    # Ignored: non-.md file
    (root / "notes.txt").write_text(NOT_A_MARKDOWN_FILE, encoding="utf-8")

    return root


class TestFrontmatterListCoercion:
    """Tolerant parsing for list-like fields (tags, parent, related, related_sources).

    Frontmatter is frequently hand-written, and it's a common mistake to
    write a single-value list field as a bare YAML scalar instead of a
    proper list, e.g. ``related_sources: citeKey2024`` instead of
    ``related_sources: ["citeKey2024"]``. These fields must degrade
    gracefully instead of raising a validation error.
    """

    def test_single_string_related_sources_is_coerced_to_list(self) -> None:
        metadata = FrontmatterSchema(
            **_base_frontmatter_kwargs(), related_sources="citeKey2024"
        )

        assert metadata.related_sources == ["citeKey2024"]

    def test_comma_separated_string_is_split_into_multiple_entries(self) -> None:
        metadata = FrontmatterSchema(
            **_base_frontmatter_kwargs(),
            related_sources="citeKey2024, citeKey2025 ,citeKey2026",
        )

        assert metadata.related_sources == ["citeKey2024", "citeKey2025", "citeKey2026"]

    def test_none_is_coerced_to_empty_list(self) -> None:
        metadata = FrontmatterSchema(**_base_frontmatter_kwargs(), tags=None)

        assert metadata.tags == []

    def test_empty_string_is_coerced_to_empty_list(self) -> None:
        metadata = FrontmatterSchema(**_base_frontmatter_kwargs(), parent="")

        assert metadata.parent == []

    def test_existing_list_passes_through_unchanged(self) -> None:
        metadata = FrontmatterSchema(
            **_base_frontmatter_kwargs(), related=["shocker", "lame"]
        )

        assert metadata.related == ["shocker", "lame"]

    def test_single_string_tags_is_coerced_to_list(self) -> None:
        metadata = FrontmatterSchema(**_base_frontmatter_kwargs(), tags="htb")

        assert metadata.tags == ["htb"]

    def test_relational_fields_parent_children_child_related_parsing(self) -> None:
        metadata = FrontmatterSchema(
            **_base_frontmatter_kwargs(),
            parent="die-cia-triade",
            child="auth-vs-authz-access-control, oauth2-vs-oidc-protokolle",
            related="die-cia-triade, least-privilege-prinzip",
        )

        assert metadata.parent == ["die-cia-triade"]
        assert metadata.children == [
            "auth-vs-authz-access-control",
            "oauth2-vs-oidc-protokolle",
        ]
        assert metadata.related == ["die-cia-triade", "least-privilege-prinzip"]

    def test_empty_and_missing_relational_fields_default_to_empty_lists(self) -> None:
        metadata = FrontmatterSchema(
            **_base_frontmatter_kwargs(),
            parent=[],
            children=None,
            related="",
        )

        assert metadata.parent == []
        assert metadata.children == []
        assert metadata.related == []

    def test_non_string_scalar_still_raises_validation_error(self) -> None:
        with pytest.raises(Exception):
            FrontmatterSchema(**_base_frontmatter_kwargs(), related_sources=42)


class TestIsSafePath:
    def test_path_inside_base_dir_is_safe(self, tmp_path: Path) -> None:
        base_dir = tmp_path
        target = tmp_path / "articles" / "craft.md"
        target.parent.mkdir(parents=True)
        target.touch()

        assert is_safe_path(base_dir, target) is True

    def test_base_dir_itself_is_safe(self, tmp_path: Path) -> None:
        assert is_safe_path(tmp_path, tmp_path) is True

    def test_path_traversal_via_dotdot_is_unsafe(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "repo"
        base_dir.mkdir()
        outside_target = base_dir / ".." / "outside.md"

        assert is_safe_path(base_dir, outside_target) is False

    def test_sibling_directory_is_unsafe(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "repo"
        base_dir.mkdir()
        sibling = tmp_path / "other-repo" / "secret.md"
        sibling.parent.mkdir()
        sibling.touch()

        assert is_safe_path(base_dir, sibling) is False


class TestParseMarkdownFile:
    def test_parses_valid_article_successfully(self, repo_root: Path) -> None:
        file_path = repo_root / "writeups" / "htb" / "craft.md"

        result = parse_markdown_file(file_path, repo_root)

        assert isinstance(result, ParsedArticle)
        assert result.metadata.title == "Craft - HTB Writeup"
        assert result.metadata.slug == "craft"
        assert result.metadata.route == "/writeups/htb/craft"
        assert result.metadata.published is True
        assert result.metadata.related_sources == ["Kappes2013"]
        assert result.file_path == Path("writeups") / "htb" / "craft.md"
        assert "# Craft" in result.raw_content

    def test_extracts_only_internal_md_links(self, repo_root: Path) -> None:
        file_path = repo_root / "writeups" / "htb" / "craft.md"

        result = parse_markdown_file(file_path, repo_root)

        assert "../linux/shocker.md" in result.extracted_internal_links
        assert "./craft.md#privesc" in result.extracted_internal_links
        assert not any(
            "example.com" in link for link in result.extracted_internal_links
        )
        assert len(result.extracted_internal_links) == 2

    def test_raises_on_path_traversal_attempt(self, repo_root: Path) -> None:
        malicious_path = repo_root / ".." / "outside.md"

        with pytest.raises(PathTraversalError):
            parse_markdown_file(malicious_path, repo_root)

    def test_raises_on_slug_style_traversal_attempt(self, repo_root: Path) -> None:
        # Simulates an attacker-controlled slug like "../../etc/passwd"
        malicious_relative = Path("../../../etc/passwd.md")

        with pytest.raises(PathTraversalError):
            parse_markdown_file(malicious_relative, repo_root)

    def test_raises_on_invalid_frontmatter(self, repo_root: Path) -> None:
        broken_file = repo_root / "broken.md"
        broken_file.write_text(INVALID_FRONTMATTER_MISSING_FIELDS, encoding="utf-8")

        with pytest.raises(InvalidFrontmatterError):
            parse_markdown_file(broken_file, repo_root)

    def test_raises_on_non_markdown_extension(self, repo_root: Path) -> None:
        txt_file = repo_root / "notes.txt"

        with pytest.raises(ArticleParsingError):
            parse_markdown_file(txt_file, repo_root)

    def test_raises_on_missing_file(self, repo_root: Path) -> None:
        missing_file = repo_root / "writeups" / "htb" / "does-not-exist.md"

        with pytest.raises(ArticleParsingError):
            parse_markdown_file(missing_file, repo_root)

    def test_raises_on_invalid_utf8_content(self, repo_root: Path) -> None:
        # Write raw bytes that are not valid UTF-8 (e.g. a stray Latin-1
        # encoded byte sequence), to make sure explicit UTF-8 decoding
        # fails predictably instead of silently mis-decoding content.
        invalid_utf8_file = repo_root / "writeups" / "htb" / "invalid-encoding.md"
        invalid_utf8_file.write_bytes(b"---\ntitle: \xff\xfe Broken\n---\nBody")

        with pytest.raises(ArticleParsingError):
            parse_markdown_file(invalid_utf8_file, repo_root)

    def test_file_handle_is_closed_after_parsing_on_windows(self, repo_root: Path) -> None:
        """Regression test: parsing must not leave the file handle open.

        On Windows, a lingering open handle would block subsequent
        rename/delete operations (e.g. during a `git pull` / sync cycle).
        We simulate this by renaming the just-parsed file immediately
        after `parse_markdown_file` returns; this fails with a
        `PermissionError` on Windows if the handle were still held open.
        """
        file_path = repo_root / "writeups" / "htb" / "craft.md"

        parse_markdown_file(file_path, repo_root)

        renamed_path = file_path.with_name("craft-renamed.md")
        file_path.rename(renamed_path)

        assert renamed_path.exists()
        assert not file_path.exists()


class TestScanAndParseRepository:
    def test_scans_repository_and_ignores_filtered_paths(self, repo_root: Path) -> None:
        articles = scan_and_parse_repository(repo_root)

        assert len(articles) == 1
        assert articles[0].metadata.slug == "craft"
        assert articles[0].file_path == Path("writeups") / "htb" / "craft.md"

    def test_ignores_underscore_and_dot_prefixed_entries(self, repo_root: Path) -> None:
        articles = scan_and_parse_repository(repo_root)
        parsed_paths = {str(article.file_path) for article in articles}

        assert not any("_drafts" in path for path in parsed_paths)
        assert not any(".git" in path for path in parsed_paths)

    def test_raises_for_nonexistent_repo_path(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does-not-exist"

        with pytest.raises(ArticleParsingError):
            scan_and_parse_repository(nonexistent)


class TestFrontmatterRequiredFields:
    """Nur id, title, slug, route, category, subcategory, subsubcategory,
    date, updated und status sind Pflicht – alles andere ist optional."""

    REQUIRED = (
        "id", "title", "slug", "route", "category",
        "subcategory", "subsubcategory", "date", "updated", "status",
    )

    @pytest.mark.parametrize("field", REQUIRED)
    def test_missing_required_field_raises(self, field: str) -> None:
        kwargs = _base_frontmatter_kwargs()
        kwargs.pop(field)
        with pytest.raises(Exception):
            FrontmatterSchema(**kwargs)

    @pytest.mark.parametrize("field", REQUIRED)
    def test_empty_required_field_raises(self, field: str) -> None:
        kwargs = _base_frontmatter_kwargs()
        kwargs[field] = "   "
        with pytest.raises(Exception):
            FrontmatterSchema(**kwargs)

    def test_only_required_fields_is_valid(self) -> None:
        kwargs = _base_frontmatter_kwargs()
        kwargs.pop("description")
        metadata = FrontmatterSchema(**kwargs)

        assert metadata.description is None
        assert metadata.series is None
        assert metadata.tags == []
        assert metadata.relevance is None

    def test_summary_is_accepted_as_description(self) -> None:
        kwargs = _base_frontmatter_kwargs()
        kwargs.pop("description")
        metadata = FrontmatterSchema(**kwargs, summary="Kurzfassung")

        assert metadata.description == "Kurzfassung"

    def test_series_without_order_defaults_to_zero(self) -> None:
        metadata = FrontmatterSchema(**_base_frontmatter_kwargs(), series={"id": "s1", "order": None})

        assert metadata.series is not None
        assert metadata.series.id == "s1"
        assert metadata.series.order == 0

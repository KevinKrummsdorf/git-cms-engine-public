"""Unit tests for the rebuild_all_relations_and_sources function."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from models.article import ArticleModel, ArticleRelationModel, ArticleSourceModel, ZoteroSourceModel
from services.processor import rebuild_all_relations_and_sources


def test_rebuild_all_relations_and_sources() -> None:
    """Verifies that rebuild_all_relations_and_sources correctly processes metadata

    and markdown links to populate relations and bibliography sources.
    """
    db = MagicMock()

    # Mock ArticleModel objects
    art1 = ArticleModel(
        id=111,
        title="Article One",
        slug="article-one",
        route="security/article-one",
        file_path="content/security/article-one.md",
    )
    art2 = ArticleModel(
        id=222,
        title="Article Two",
        slug="article-two",
        route="security/article-two",
        file_path="content/security/article-two.md",
    )

    articles = [art1, art2]

    # Mock database queries:
    # side_effect returns articles for the first fetch, and Zotero source cite-keys for the second.
    db.execute.return_value.scalars.return_value.all.side_effect = [
        articles,
        ["RFC 7748"],
    ]

    # Mock frontmatter load for files
    mock_fm1 = MagicMock()
    mock_fm1.metadata = {
        "parent": ["security/article-two"],
        "related_sources": ["RFC 7748"],
    }
    mock_fm1.content = "Some markdown content with [Article Two](../security/article-two.md) link."

    mock_fm2 = MagicMock()
    mock_fm2.metadata = {
        "related": ["security/article-one"],
    }
    mock_fm2.content = "No links."

    # Patch Path.exists, Path.is_file, open, and frontmatter.load
    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("frontmatter.load", side_effect=[mock_fm1, mock_fm2]):

        rebuild_all_relations_and_sources(db)

    # Check that db.add was called to insert new mappings
    assert db.add.call_count > 0

    # Extract all added entities
    added_objects = [call[0][0] for call in db.add.call_args_list]

    relations = [obj for obj in added_objects if isinstance(obj, ArticleRelationModel)]
    sources = [obj for obj in added_objects if isinstance(obj, ArticleSourceModel)]

    # Parent relation: art2 (222) is parent of art1 (111) -> article_id=111, target_article_id=222, relation_type='parent'
    assert any(r.article_id == 111 and r.target_article_id == 222 and r.relation_type == 'parent' for r in relations)
    # Internal link relation: art1 (111) linked to art2 (222) -> article_id=111, target_article_id=222, relation_type='internal'
    assert any(r.article_id == 111 and r.target_article_id == 222 and r.relation_type == 'internal' for r in relations)
    # Related relation: art2 (222) related to art1 (111) -> article_id=222, target_article_id=111, relation_type='related'
    assert any(r.article_id == 222 and r.target_article_id == 111 and r.relation_type == 'related' for r in relations)
    assert any(s.article_id == 111 and s.source_key == "RFC 7748" for s in sources)

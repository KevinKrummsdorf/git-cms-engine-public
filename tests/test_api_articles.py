"""Unit tests for GET /api/v1/cms/articles/by-route dynamic loader and /articles list endpoints.

Monkeypatches both DB and on-demand file operations so that tests are 100% fast,
reproducible, and run without a real PostgreSQL database or repository checkout.
"""

from __future__ import annotations

import datetime
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from main import app
from repository.database import get_db
from models.article import ArticleModel, ArticleRelationModel, ArticleSourceModel
from schemas.article import ArticleDetailResponse

class _FakeDbSession:
    def __init__(self, article=None, relations=None, zotero_sources=None):
        self.article = article
        self.relations = relations or []
        self.zotero_sources = zotero_sources or []

    def execute(self, statement, *args, **kwargs):
        # We simulate executing selects for ArticleModel and ZoteroSourceModel
        # compile statement into a string representation
        stmt_str = str(statement).lower()
        if "from zotero.sources" in stmt_str:
            return _FakeResult(self.zotero_sources)
        if "from cms.articles" in stmt_str:
            if "join cms.article_relations" in stmt_str:
                # Relations join
                return _FakeResult(self.relations)
            # Regular query for ArticleModel
            return _FakeResult([self.article] if self.article else [])
        return _FakeResult([])


class _FakeResult:
    def __init__(self, items):
        self.items = items

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self.items[0] if self.items else None

    def scalars(self):
        return self

    def all(self):
        return self.items

    def first(self):
        return self.items[0] if self.items else None


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_article_by_route_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set up DB to return no article
    def _get_empty_db():
        yield _FakeDbSession(article=None)

    app.dependency_overrides[get_db] = _get_empty_db
    try:
        response = client.get("/api/v1/cms/articles/by-route?route=/non-existent")
        assert response.status_code == 404
        assert response.json()["error_code"] == "ARTICLE_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_article_by_route_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 1. Prepare fake Markdown content with frontmatter on filesystem
    article_id = 987654321
    slug = "test-article"
    route = "/writeups/test-article"
    file_path = "writeups/test-article.md"

    md_content = """---
id: 987654321
title: "Dynamic Loader Test"
slug: test-article
route: /writeups/test-article
category: writeups
description: "Unit testing dynamic markdown loading."
date: 2024-01-01
---

# Real Dynamic Markdown Body!

This content is loaded directly from filesystem on-demand.
"""
    # Write the mock markdown file under a tmp directory
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    target_file = mock_repo / "writeups" / "test-article.md"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(md_content, encoding="utf-8")

    # Override settings repo path to our temp directory
    from config import settings
    monkeypatch.setattr(settings, "cms_processed_data_path", mock_repo)

    # 2. Mock database query result
    db_article = ArticleModel(
        id=article_id,
        title="Dynamic Loader Test",
        slug=slug,
        route=route,
        category="writeups",
        subcategory=None,
        description="Unit testing dynamic markdown loading.",
        tags=[],
        date=datetime.date(2024, 1, 1),
        published=True,
        math=False,
        charts=False,
        file_path=file_path,
        sources=[],
    )

    def _get_mock_db():
        yield _FakeDbSession(article=db_article)

    app.dependency_overrides[get_db] = _get_mock_db
    try:
        # 3. Call the API endpoint
        response = client.get(f"/api/v1/cms/articles/by-route?route={route}")
        assert response.status_code == 200

        payload = response.json()
        assert payload["metadata"]["id"] == article_id
        assert payload["metadata"]["title"] == "Dynamic Loader Test"
        assert "Real Dynamic Markdown Body!" in payload["content"]["raw_markdown"]
        assert payload["content"]["reading_time_minutes"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_article_by_route_file_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Set up settings local path but don't write the file
    from config import settings
    monkeypatch.setattr(settings, "cms_processed_data_path", tmp_path)

    db_article = ArticleModel(
        id=123,
        title="Missing File",
        slug="missing-file",
        route="/missing",
        category="writeups",
        file_path="writeups/missing.md",
        date=datetime.date(2024, 1, 1),
        published=True,
        math=False,
        charts=False,
        sources=[],
    )

    def _get_mock_db():
        yield _FakeDbSession(article=db_article)

    app.dependency_overrides[get_db] = _get_mock_db
    try:
        response = client.get("/api/v1/cms/articles/by-route?route=/missing")
        assert response.status_code == 404
        assert response.json()["error_code"] == "ARTICLE_FILE_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_article_by_route_path_traversal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config import settings
    monkeypatch.setattr(settings, "cms_processed_data_path", tmp_path)

    db_article = ArticleModel(
        id=999,
        title="Traversal Attack",
        slug="traversal",
        route="/traversal",
        category="security",
        file_path="../../etc/passwd",
        date=datetime.date(2024, 1, 1),
        published=True,
        math=False,
        charts=False,
        sources=[],
    )

    def _get_mock_db():
        yield _FakeDbSession(article=db_article)

    app.dependency_overrides[get_db] = _get_mock_db
    try:
        response = client.get("/api/v1/cms/articles/by-route?route=/traversal")
        assert response.status_code == 400
        assert response.json()["error_code"] == "PATH_TRAVERSAL_DETECTED"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_article_by_route_with_related_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config import settings
    # 1. Prepare fake Markdown content with frontmatter containing related_sources on filesystem
    article_id = 111
    slug = "crypto-article"
    route = "/writeups/crypto-article"
    file_path = "writeups/crypto-article.md"

    md_content = """---
id: 111
title: "Crypto Article"
slug: crypto-article
route: /writeups/crypto-article
category: writeups
description: "Testing sources extraction."
date: 2024-01-01
related_sources:
  - NIST800-56Ar3
  - RFC8446
---

# Real Dynamic Markdown Body!
"""
    # Write the mock markdown file under a tmp directory
    mock_repo = tmp_path / "mock_repo_sources"
    mock_repo.mkdir()
    target_file = mock_repo / "writeups" / "crypto-article.md"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(md_content, encoding="utf-8")

    # Override settings repo path to our temp directory
    monkeypatch.setattr(settings, "cms_processed_data_path", mock_repo)

    # 2. Mock database query result
    db_article = ArticleModel(
        id=article_id,
        title="Crypto Article",
        slug=slug,
        route=route,
        category="writeups",
        subcategory=None,
        description="Testing sources extraction.",
        tags=[],
        date=datetime.date(2024, 1, 1),
        published=True,
        math=False,
        charts=False,
        file_path=file_path,
        sources=[],
    )

    from models.article import ZoteroSourceModel
    fake_source1 = ZoteroSourceModel(
        cite_key="NIST800-56Ar3",
        title="Recommendation for pair-wise key-establishment schemes",
        authors="Elaine Barker; Lily Chen",
        year=2018,
        url="https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-56Ar3.pdf"
    )
    fake_source2 = ZoteroSourceModel(
        cite_key="RFC8446",
        title="The Transport Layer Security (TLS) Protocol Version 1.3",
        authors="E. Rescorla",
        year=2018,
        url="https://datatracker.ietf.org/doc/rfc8446"
    )

    def _get_mock_db():
        yield _FakeDbSession(
            article=db_article,
            zotero_sources=[fake_source1, fake_source2]
        )

    app.dependency_overrides[get_db] = _get_mock_db
    try:
        # 3. Call the API endpoint
        response = client.get(f"/api/v1/cms/articles/by-route?route={route}")
        assert response.status_code == 200

        payload = response.json()
        assert len(payload["sources"]) == 2
        assert payload["sources"][0]["cite_key"] == "NIST800-56Ar3"
        assert payload["sources"][0]["title"] == "Recommendation for pair-wise key-establishment schemes"
        assert payload["sources"][0]["authors"] == ["Elaine Barker", "Lily Chen"]
        assert payload["sources"][0]["publisher"] is None
        assert payload["sources"][0]["year"] == 2018
        assert payload["sources"][0]["url"] == "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-56Ar3.pdf"

        assert payload["sources"][1]["cite_key"] == "RFC8446"
        assert payload["sources"][1]["title"] == "The Transport Layer Security (TLS) Protocol Version 1.3"
        assert payload["sources"][1]["authors"] == ["E. Rescorla"]
        assert payload["sources"][1]["publisher"] is None
        assert payload["sources"][1]["year"] == 2018
        assert payload["sources"][1]["url"] == "https://datatracker.ietf.org/doc/rfc8446"
    finally:
        app.dependency_overrides.pop(get_db, None)

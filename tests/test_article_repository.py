"""Integration tests for repository.article_repository.upsert_articles.

These tests exercise the real upsert/delete/relation-resolution logic
against a PostgreSQL database, since the ORM models use PostgreSQL-only
types (``ARRAY``) and schema-qualified tables (``cms.articles``) that
SQLite cannot compile.

Requires a reachable PostgreSQL instance configured via the
``TEST_DATABASE_URL`` environment variable, e.g.::

    postgresql+psycopg2://postgres:postgres@localhost:5432/postgres

If it is not set (or unreachable), the whole module is skipped rather
than failing the suite -- this keeps `pytest` runnable in environments
without Docker/PostgreSQL (e.g. minimal CI containers), while still
providing full coverage wherever a database is available.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from models.article import ArticleModel, ArticleRelationModel, ArticleSourceModel, Base
from repository.article_repository import upsert_articles
from schemas.article import FrontmatterSchema, ParsedArticle

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping PostgreSQL-backed repository tests.",
)


def _make_parsed_article(
    *,
    slug: str,
    route: str,
    file_path: str,
    parent: list[str] | None = None,
    related: list[str] | None = None,
    related_sources: list[str] | None = None,
    internal_links: list[str] | None = None,
) -> ParsedArticle:
    metadata = FrontmatterSchema(
        title=f"Title for {slug}",
        slug=slug,
        route=route,
        category="writeups",
        description="Some description.",
        date="2024-01-01",
        published=True,
        parent=parent or [],
        related=related or [],
        related_sources=related_sources or [],
    )
    return ParsedArticle(
        metadata=metadata,
        raw_content=f"# {slug}\n\nBody text.",
        extracted_internal_links=internal_links or [],
        file_path=Path(file_path),
    )


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS cms"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine) -> Session:
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    yield session
    session.rollback()
    # Clean slate between tests.
    session.execute(text("TRUNCATE TABLE cms.article_relations RESTART IDENTITY CASCADE"))
    session.execute(text("TRUNCATE TABLE cms.article_sources RESTART IDENTITY CASCADE"))
    session.execute(text("TRUNCATE TABLE cms.articles RESTART IDENTITY CASCADE"))
    session.commit()
    session.close()


class TestUpsertArticles:
    def test_creates_new_articles(self, session: Session) -> None:
        parsed = [_make_parsed_article(slug="craft", route="/writeups/craft", file_path="craft.md")]

        stats = upsert_articles(session, parsed)
        session.commit()

        assert stats.created == 1
        assert stats.updated == 0
        assert stats.deleted == 0

        stored = session.query(ArticleModel).filter_by(slug="craft").one()
        assert stored.route == "/writeups/craft"

    def test_updates_existing_article_matched_by_file_path(self, session: Session) -> None:
        first_pass = [
            _make_parsed_article(slug="craft-v1", route="/writeups/craft", file_path="craft.md")
        ]
        upsert_articles(session, first_pass)
        session.commit()

        second_pass = [
            _make_parsed_article(slug="craft-v2", route="/writeups/craft-v2", file_path="craft.md")
        ]
        stats = upsert_articles(session, second_pass)
        session.commit()

        assert stats.created == 0
        assert stats.updated == 1

        rows = session.query(ArticleModel).all()
        assert len(rows) == 1
        assert rows[0].slug == "craft-v2"

    def test_deletes_articles_missing_from_new_scan(self, session: Session) -> None:
        first_pass = [
            _make_parsed_article(slug="craft", route="/writeups/craft", file_path="craft.md"),
            _make_parsed_article(slug="shocker", route="/writeups/shocker", file_path="shocker.md"),
        ]
        upsert_articles(session, first_pass)
        session.commit()

        second_pass = [
            _make_parsed_article(slug="craft", route="/writeups/craft", file_path="craft.md"),
        ]
        stats = upsert_articles(session, second_pass)
        session.commit()

        assert stats.deleted == 1
        remaining = session.query(ArticleModel).all()
        assert len(remaining) == 1
        assert remaining[0].slug == "craft"

    def test_persists_sources_bridge_table(self, session: Session) -> None:
        parsed = [
            _make_parsed_article(
                slug="craft",
                route="/writeups/craft",
                file_path="craft.md",
                related_sources=["Kappes2013", "ISO27701"],
            )
        ]

        upsert_articles(session, parsed)
        session.commit()

        article = session.query(ArticleModel).filter_by(slug="craft").one()
        cite_keys = {source.cite_key for source in article.sources}
        assert cite_keys == {"Kappes2013", "ISO27701"}

    def test_resolves_related_article_relation_to_existing_target(self, session: Session) -> None:
        parsed = [
            _make_parsed_article(slug="shocker", route="/writeups/shocker", file_path="shocker.md"),
            _make_parsed_article(
                slug="craft",
                route="/writeups/craft",
                file_path="craft.md",
                related=["/writeups/shocker"],
            ),
        ]

        upsert_articles(session, parsed)
        session.commit()

        craft = session.query(ArticleModel).filter_by(slug="craft").one()
        shocker = session.query(ArticleModel).filter_by(slug="shocker").one()

        relation = next(
            r for r in craft.outgoing_links if r.relation_type == "related"
        )
        assert relation.target_route == "/writeups/shocker"
        assert relation.target_article_id == shocker.id

    def test_keeps_broken_link_relation_with_null_target_id(self, session: Session) -> None:
        parsed = [
            _make_parsed_article(
                slug="craft",
                route="/writeups/craft",
                file_path="craft.md",
                related=["/writeups/does-not-exist"],
            ),
        ]

        upsert_articles(session, parsed)
        session.commit()

        craft = session.query(ArticleModel).filter_by(slug="craft").one()
        relation = craft.outgoing_links[0]
        assert relation.target_route == "/writeups/does-not-exist"
        assert relation.target_article_id is None

    def test_removing_a_related_link_removes_the_relation_row(self, session: Session) -> None:
        first_pass = [
            _make_parsed_article(slug="shocker", route="/writeups/shocker", file_path="shocker.md"),
            _make_parsed_article(
                slug="craft",
                route="/writeups/craft",
                file_path="craft.md",
                related=["/writeups/shocker"],
            ),
        ]
        upsert_articles(session, first_pass)
        session.commit()

        second_pass = [
            _make_parsed_article(slug="shocker", route="/writeups/shocker", file_path="shocker.md"),
            _make_parsed_article(slug="craft", route="/writeups/craft", file_path="craft.md", related=[]),
        ]
        upsert_articles(session, second_pass)
        session.commit()

        craft = session.query(ArticleModel).filter_by(slug="craft").one()
        assert len(craft.outgoing_links) == 0

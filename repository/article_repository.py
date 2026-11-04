"""Persistence layer mapping ParsedArticle objects to PostgreSQL relational tables.

Manages all write and read database operations for articles, Zotero citations,
and self-referential article relationships.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models.article import ArticleModel, ArticleRelationModel, ArticleSourceModel
from schemas.article import ParsedArticle
from services.exceptions import ArticleRepositoryError

logger = logging.getLogger(__name__)

__all__ = ["SyncStats", "upsert_articles"]


@dataclass
class SyncStats:
    """Dataclass capturing database metrics for a single synchronization execution.

    Attributes:
        total (int): The total number of processed articles.
        created (int): The number of newly inserted article database records.
        updated (int): The number of modified article database records.
        deleted (int): The number of removed stale article database records.
        ignored (int): The number of skipped article records.
        errors (list[str]): The list of encountered error strings.
    """

    total: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        """Converts stats instance values into a standard dictionary.

        Returns:
            dict[str, int]: A dictionary mapping metric names to their integer counts.
        """
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "ignored": self.ignored,
        }


def _apply_metadata(article_row: ArticleModel, parsed: ParsedArticle, file_path_str: str) -> None:
    """Applies parsed frontmatter metadata attributes to a target ArticleModel ORM instance.

    Args:
        article_row (ArticleModel): The database model instance.
        parsed (ParsedArticle): The parsed article schema containing validated parameters.
        file_path_str (str): The POSIX-normalized repository file path.
    """
    metadata = parsed.metadata
    article_row.id = metadata.id
    article_row.title = metadata.title
    article_row.slug = metadata.slug
    article_row.route = metadata.route
    article_row.category = metadata.category
    article_row.subcategory = metadata.subcategory
    article_row.description = metadata.description
    article_row.tags = metadata.tags
    article_row.date = metadata.date
    article_row.updated = metadata.updated
    article_row.relevance = metadata.relevance
    article_row.status = metadata.status
    article_row.published = metadata.published
    article_row.math = metadata.math
    article_row.charts = metadata.charts
    article_row.parent = metadata.parent or []
    article_row.children = metadata.children or []
    article_row.related = metadata.related or []
    article_row.file_path = file_path_str


def _sync_sources(session: Session, article_row: ArticleModel, cite_keys: list[str]) -> None:
    """Synchronizes many-to-many Zotero citations for a specific article.

    Compares existing mapping table relations with the provided set of keys, deleting
    removed entries and instantiating missing relational associations.

    Args:
        session (Session): The active database session.
        article_row (ArticleModel): The article record to associate with.
        cite_keys (list[str]): The list of active citation keys.
    """
    desired = set(cite_keys)
    existing = {source.source_key: source for source in article_row.sources}

    for source_key, source_row in list(existing.items()):
        if source_key not in desired:
            session.delete(source_row)

    for source_key in desired:
        if source_key not in existing:
            session.add(ArticleSourceModel(article_id=article_row.id, source_key=source_key))


def _normalize_relative_path(current_file_path: str, relative_target: str) -> str:
    """Resolves a relative Markdown link path against the directory of the current file.

    Args:
        current_file_path (str): The path of the source file.
        relative_target (str): The relative link path to resolve.

    Returns:
        str: The normalized relative path string, or an empty string if resolution fails.
    """
    try:
        target_path_str = relative_target.split("#")[0].split("?")[0]
        if not target_path_str.endswith(".md"):
            return ""
        current_dir = Path(current_file_path).parent
        norm = os.path.normpath(os.path.join(current_dir, target_path_str))
        return norm.replace("\\", "/")
    except Exception:
        return ""


def upsert_articles(session: Session, parsed_articles: list[ParsedArticle]) -> SyncStats:
    """Performs bulk upsert and cleanup synchronization for a set of parsed articles.

    Matches existing records by repository-relative file path, inserting new records,
    updating modified entries, deleting records whose source files no longer exist,
    and rebuilding self-referential parent/related article links.

    Args:
        session (Session): The active SQLAlchemy database session.
        parsed_articles (list[ParsedArticle]): Sorted parsed article schemas to persist.

    Raises:
        ArticleRepositoryError: If database transactions or operations fail.

    Returns:
        SyncStats: Compilation performance summary mapping counts of applied operations.
    """
    stats = SyncStats(total=len(parsed_articles))

    try:
        existing_by_path = {
            row.file_path: row for row in session.execute(select(ArticleModel)).scalars()
        }
        seen_paths: set[str] = set()

        for parsed in parsed_articles:
            file_path_str = str(Path(parsed.file_path).as_posix())
            seen_paths.add(file_path_str)

            article_row = existing_by_path.get(file_path_str)
            if article_row is None:
                article_row = ArticleModel()
                session.add(article_row)
                _apply_metadata(article_row, parsed, file_path_str)
                session.flush()  # Assign unique primary key before setting relationship elements
                stats.created += 1
            else:
                _apply_metadata(article_row, parsed, file_path_str)
                stats.updated += 1

            _sync_sources(session, article_row, parsed.metadata.related_sources)

        # Stale database record deletion pass
        for file_path_str, article_row in existing_by_path.items():
            if file_path_str not in seen_paths:
                session.delete(article_row)
                stats.deleted += 1

        session.flush()

        # Re-resolve and link internal article relationships
        all_articles = session.execute(select(ArticleModel)).scalars().all()
        route_to_id = {art.route: art.id for art in all_articles}
        path_to_id = {art.file_path: art.id for art in all_articles}

        # Clear existing associations to prevent duplicate primary keys or conflicts
        session.execute(delete(ArticleRelationModel))

        for parsed in parsed_articles:
            file_path_str = str(Path(parsed.file_path).as_posix())
            parent_id = parsed.metadata.id

            target_ids: set[int] = set()

            for route in parsed.metadata.parent:
                tid = route_to_id.get(route)
                if tid is not None:
                    target_ids.add(tid)

            for route in parsed.metadata.children:
                tid = route_to_id.get(route)
                if tid is not None:
                    target_ids.add(tid)

            for route in parsed.metadata.related:
                tid = route_to_id.get(route)
                if tid is not None:
                    target_ids.add(tid)

            for link in parsed.extracted_internal_links:
                norm_path = _normalize_relative_path(file_path_str, link)
                tid = path_to_id.get(norm_path)
                if tid is not None:
                    target_ids.add(tid)

            for tid in target_ids:
                if tid != parent_id:
                    session.add(ArticleRelationModel(parent_id=parent_id, related_id=tid))

        session.flush()
        return stats
    except SQLAlchemyError as exc:
        raise ArticleRepositoryError(f"Failed to persist parsed articles: {exc}") from exc

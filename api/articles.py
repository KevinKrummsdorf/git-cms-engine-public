"""FastAPI Router endpoints exposing article indices and detailed query routes.

Serves compiled articles, indexes structural metadata, resolves child-parent relation graphs,
and manages citation links for consumption by frontends.
"""

from __future__ import annotations

from pathlib import Path
import frontmatter

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from config import settings
from models.article import ArticleModel, ArticleSourceModel, ArticleRelationModel, ZoteroSourceModel
from repository.database import get_db
from schemas.article import (
    ArticleContentResponse,
    ArticleDetailResponse,
    ArticleListItem,
    ArticleListResponse,
    InternalLinksSchema,
    ArticleRelationItem,
    SourceResponse,
    SeriesNavigationItem,
    SeriesNavigationResponse,
)
from services.parser import is_safe_path

router = APIRouter(prefix="/api/v1/cms", tags=["articles"])

_WORDS_PER_MINUTE = 200


@router.get("/articles", response_model=ArticleListResponse)
def list_articles(
    db: Session = Depends(get_db),
    published: bool = Query(default=True),
    category: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> ArticleListResponse:
    """Returns a list of published or unpublished articles for indexing and overview pages."""
    stmt = select(ArticleModel).where(ArticleModel.published == published)

    if category:
        stmt = stmt.where(ArticleModel.category == category)
    if tag:
        stmt = stmt.where(ArticleModel.tags.any(tag))

    stmt = stmt.order_by(ArticleModel.date.desc(), ArticleModel.id.desc())

    if limit is not None:
        stmt = stmt.limit(limit)

    articles = db.execute(stmt).scalars().all()
    items = [ArticleListItem.model_validate(article) for article in articles]

    return ArticleListResponse(count=len(items), articles=items)


@router.get("/articles/by-route", response_model=ArticleDetailResponse)
def get_article_by_route(
    route: str = Query(..., description="The exact URL-encoded routing path."),
    db: Session = Depends(get_db),
) -> ArticleDetailResponse:
    """Retrieves full article metadata and compiled Markdown payload matching a target route."""
    clean_route = route.strip("/")
    if clean_route.endswith(".md"):
        clean_route = clean_route[:-3]

    # Load article record along with its associated bibliographic citations
    article = db.execute(
        select(ArticleModel)
        .options(joinedload(ArticleModel.sources).joinedload(ArticleSourceModel.zotero_source))
        .where(
            (ArticleModel.route == clean_route) | 
            (ArticleModel.route == f"/{clean_route}") |
            (ArticleModel.slug == clean_route.split('/')[-1])
        )
    ).unique().scalar_one_or_none()

    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "error_code": "ARTICLE_NOT_FOUND", "message": f"No article exists for route '{route}'."},
        )

    # Strictly resolve target path against the compiled files directory on disk
    target_path = (settings.cms_processed_data_path / article.file_path).resolve()

    if not is_safe_path(settings.cms_processed_data_path, target_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "error_code": "PATH_TRAVERSAL_DETECTED", "message": "Path traversal detected."}
        )

    if not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "status": "error",
                "error_code": "ARTICLE_FILE_NOT_FOUND",
                "message": f"The processed source file for route '{route}' is missing or inaccessible at path '{target_path}'."
            },
        )

    try:
        with open(target_path, mode="r", encoding="utf-8") as file_handle:
            post = frontmatter.load(file_handle)
            raw_markdown = post.content
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "error_code": "INTERNAL_SERVER_ERROR", "message": f"Failed to read source Markdown file: {exc}"},
        )

    word_count = len(raw_markdown.split()) if raw_markdown else 0
    reading_time_minutes = max(1, round(word_count / _WORDS_PER_MINUTE))

    sources: list[SourceResponse] = []
    related_sources = post.metadata.get("related_sources", [])
    if isinstance(related_sources, str):
        related_sources = [r.strip() for r in related_sources.split(",") if r.strip()]
    elif not isinstance(related_sources, list):
        related_sources = []

    if related_sources:
        z_stmt = select(ZoteroSourceModel).where(ZoteroSourceModel.cite_key.in_(related_sources))
        z_sources = db.execute(z_stmt).scalars().all()
        z_sources_map = {z.cite_key: z for z in z_sources}

        for key in related_sources:
            if key in z_sources_map:
                z_source = z_sources_map[key]
                authors_list = [
                    author.strip()
                    for author in z_source.authors.split(";")
                    if z_source.authors and author.strip()
                ] if z_source.authors else []
                sources.append(
                    SourceResponse(
                        cite_key=z_source.cite_key,
                        title=z_source.title,
                        authors=authors_list,
                        publisher=None,
                        year=z_source.year,
                        url=z_source.url,
                    )
                )
            else:
                sources.append(SourceResponse(cite_key=key))
    else:
        if article.sources:
            for article_source in article.sources:
                if article_source.zotero_source:
                    z_source = article_source.zotero_source
                    authors_list = [
                        author.strip()
                        for author in z_source.authors.split(";")
                        if z_source.authors and author.strip()
                    ] if z_source.authors else []
                    sources.append(
                        SourceResponse(
                            cite_key=z_source.cite_key,
                            title=z_source.title,
                            authors=authors_list,
                            publisher=None,
                            year=z_source.year,
                            url=z_source.url,
                        )
                    )
                else:
                    sources.append(SourceResponse(cite_key=article_source.source_key))

    outgoing_relations = db.execute(
        select(ArticleRelationModel)
        .options(joinedload(ArticleRelationModel.target_article))
        .where(ArticleRelationModel.article_id == article.id)
    ).scalars().all()

    incoming_parent_relations = db.execute(
        select(ArticleRelationModel)
        .options(joinedload(ArticleRelationModel.parent_article))
        .where(
            (ArticleRelationModel.target_article_id == article.id) &
            (ArticleRelationModel.relation_type == 'parent')
        )
    ).scalars().all()

    def make_relation_item(rel: ArticleRelationModel, use_parent: bool = False) -> ArticleRelationItem:
        target = rel.parent_article if use_parent else rel.target_article

        if target:
            target_route = target.route.strip("/")
            if target_route.endswith(".md"):
                target_route = target_route[:-3]
            return ArticleRelationItem(
                id=target.id,
                title=target.title,
                route=target_route
            )

        fallback_route = rel.target_route.strip("/")
        if fallback_route.endswith(".md"):
            fallback_route = fallback_route[:-3]

        return ArticleRelationItem(
            id=None,
            title=fallback_route.split("/")[-1].replace("-", " ").title(),
            route=fallback_route
        )

    parent_summary: ArticleRelationItem | None = None
    children_summaries: list[ArticleRelationItem] = []
    related_summaries: list[ArticleRelationItem] = []

    for rel in outgoing_relations:
        if rel.relation_type == 'parent' and not parent_summary:
            parent_summary = make_relation_item(rel)
        elif rel.relation_type in ('children', 'child'):
            children_summaries.append(make_relation_item(rel))
        elif rel.relation_type == 'related':
            related_summaries.append(make_relation_item(rel))

    for rel in incoming_parent_relations:
        child_item = make_relation_item(rel, use_parent=True)
        if not any(c.route == child_item.route for c in children_summaries):
            children_summaries.append(child_item)

    if not related_summaries:
        related_articles = db.execute(
            select(ArticleModel)
            .where(
                (ArticleModel.id != article.id) &
                (ArticleModel.published == True) &
                (ArticleModel.category == article.category)
            )
            .limit(5)
        ).scalars().all()
        for rel in related_articles:
            clean_rel_route = rel.route.strip("/")
            if clean_rel_route.endswith(".md"):
                clean_rel_route = clean_rel_route[:-3]
            related_summaries.append(ArticleRelationItem(
                id=rel.id,
                title=rel.title,
                route=clean_rel_route
            ))

    series_data: SeriesNavigationResponse | None = None
    if article.series_id:
        series_stmt = select(ArticleModel).where(ArticleModel.series_id == article.series_id)

        if article.published:
            series_stmt = series_stmt.where(ArticleModel.published.is_(True))

        series_stmt = series_stmt.order_by(
            ArticleModel.series_order.asc(),
            ArticleModel.id.asc()
        )
        series_items = db.execute(series_stmt).scalars().all()

        current_idx = next((i for i, item in enumerate(series_items) if item.id == article.id), -1)

        if current_idx != -1:
            prev_item = series_items[current_idx - 1] if current_idx > 0 else None
            next_item = series_items[current_idx + 1] if current_idx < len(series_items) - 1 else None

            series_data = SeriesNavigationResponse(
                series_id=article.series_id,
                current_order=article.series_order or 0,
                total_parts=len(series_items),
                prev=SeriesNavigationItem(
                    title=prev_item.title,
                    route=prev_item.route.strip("/").removesuffix(".md")
                ) if prev_item else None,
                next=SeriesNavigationItem(
                    title=next_item.title,
                    route=next_item.route.strip("/").removesuffix(".md")
                ) if next_item else None,
            )

    return ArticleDetailResponse(
        metadata=ArticleListItem.model_validate(article),
        content=ArticleContentResponse(
            raw_markdown=raw_markdown,
            reading_time_minutes=reading_time_minutes,
        ),
        sources=sources,
        internal_links=InternalLinksSchema(
            parent=parent_summary,
            children=children_summaries,
            related_articles=related_summaries,
        ),
        series=series_data,
    )

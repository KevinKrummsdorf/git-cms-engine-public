"""FastAPI Router exposing citation sources.

Serves academic research sources and synchronized citations from the Zotero database table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from models.article import ArticleSourceModel, ZoteroSourceModel
from repository.database import get_db
from schemas.article import SourceResponse, SourcesListResponse

router = APIRouter(prefix="/api/v1/cms", tags=["sources"])


@router.get("/sources", response_model=SourcesListResponse)
def list_sources(
    db: Session = Depends(get_db),
    cite_key: str | None = Query(default=None),
    article_id: int | None = Query(default=None),
) -> SourcesListResponse:
    """Returns a list of distinct citation sources, optionally filtered by cite key or article.

    Args:
        db (Session): The active database session dependency.
        cite_key (str | None): Optional citation key filter (e.g. 'Kappes2013').
        article_id (int | None): Optional article ID filter.

    Returns:
        SourcesListResponse: Container containing the total count and list of citation sources.
    """
    stmt = select(ArticleSourceModel).options(joinedload(ArticleSourceModel.zotero_source))

    if cite_key:
        stmt = stmt.where(ArticleSourceModel.source_key == cite_key)
    if article_id:
        stmt = stmt.where(ArticleSourceModel.article_id == article_id)

    article_sources = db.execute(stmt).scalars().all()

    sources_dict = {}
    for article_source in article_sources:
        if article_source.source_key not in sources_dict:
            if article_source.zotero_source:
                z_source = article_source.zotero_source
                authors_list = [
                    author.strip()
                    for author in z_source.authors.split(";")
                    if z_source.authors and author.strip()
                ] if z_source.authors else []
                sources_dict[article_source.source_key] = SourceResponse(
                    cite_key=z_source.cite_key,
                    title=z_source.title,
                    authors=authors_list,
                    publisher=None,
                    year=z_source.year,
                    url=z_source.url,
                )
            else:
                sources_dict[article_source.source_key] = SourceResponse(cite_key=article_source.source_key)

    sources = list(sources_dict.values())

    return SourcesListResponse(total=len(sources), sources=sources)

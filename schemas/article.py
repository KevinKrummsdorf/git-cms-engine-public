"""Pydantic schemas for the git-cms-engine article parsing pipeline.

This module defines the data contracts used across the parser service:

- ``FrontmatterSchema``: strict validation of the YAML frontmatter block
  found at the top of each Markdown article.
- ``ParsedArticle``: the fully parsed representation of a single Markdown
  file, combining validated metadata, raw content, and extracted internal
  links, as returned by ``services.parser``.
- ``ArticleResponse``: API-facing representation used by the FastAPI
  routers (kept for backwards compatibility with existing endpoints).
"""

import datetime
import re
from datetime import date
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, AliasChoices


class SeriesFrontmatter(BaseModel):
    id: Optional[str] = None
    order: int = 0

    model_config = ConfigDict(extra="ignore")

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            value = str(value)
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("order", mode="before")
    @classmethod
    def _coerce_order(cls, value: Any) -> int:
        """Fehlende oder ungültige Werte fallen auf 0 zurück (optionales Feld)."""
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0


class FrontmatterSchema(BaseModel):
    """Validated representation of a Markdown article's YAML frontmatter.

    All fields map 1:1 to the keys expected in the frontmatter block of
    every ``.md`` file managed by the CMS engine.

    Pflichtfelder (fehlend oder leer -> ValidationError):
    ``id``, ``title``, ``slug``, ``route``, ``category``, ``subcategory``,
    ``subsubcategory``, ``date``, ``updated``, ``status``.
    Alle anderen Felder sind optional und werden tolerant normalisiert.
    """

    # --- Pflichtfelder ---
    id: int  # Unique ID from frontmatter (e.g. 3745094207)
    title: str
    slug: str
    route: str
    category: str
    subcategory: str
    subsubcategory: str
    date: date
    updated: date
    status: str

    # --- Optionale Felder ---
    series: Optional[SeriesFrontmatter] = None
    description: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("description", "summary"),
    )
    tags: List[str] = Field(default_factory=list)
    relevance: Optional[str] = None

    @field_validator(
        "title", "slug", "route", "category", "subcategory", "subsubcategory", "status",
        mode="before",
    )
    @classmethod
    def _require_non_empty_string(cls, value: Any) -> Any:
        """Pflicht-Strings: Zahlen werden zu Strings, leere Werte sind unzulässig."""
        if value is None or isinstance(value, bool):
            return value  # Pydantic meldet den Typfehler
        if isinstance(value, (int, float)):
            value = str(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("must not be empty")
        return value

    @field_validator("status", mode="after")
    @classmethod
    def _normalize_status(cls, value: str) -> str:
        return value.lower()

    @field_validator("description", "relevance", mode="before")
    @classmethod
    def _coerce_optional_text(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            value = ", ".join(str(v).strip() for v in value if v is not None and str(v).strip())
        elif not isinstance(value, str):
            value = str(value)
        return value.strip() or None

    @field_validator("series", mode="before")
    @classmethod
    def _coerce_series(cls, value: Any) -> Any:
        return value if isinstance(value, dict) else None

    @field_validator("published", "math", "charts", mode="before")
    @classmethod
    def _none_to_false(cls, value: Any) -> Any:
        return False if value is None else value

    @field_validator("date", "updated", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> Any:
        """Parses dates from ISO format (YYYY-MM-DD) or German format (DD.MM.YYYY or DD.MM.YY)."""
        if value is None or value == "":
            return None
        if isinstance(value, date) and not isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
            try:
                return date.fromisoformat(val_str)
            except ValueError:
                pass
            for fmt in ("%d.%m.%Y", "%d.%m.%y"):
                try:
                    return datetime.datetime.strptime(val_str, fmt).date()
                except ValueError:
                    pass
        return value

    published: bool = False
    math: bool = False
    charts: bool = False
    parent: List[str] = Field(default_factory=list)
    children: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("children", "child"),
    )
    related: List[str] = Field(default_factory=list)
    related_sources: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_published_from_status(cls, data: Any) -> Any:
        """Leitet published deterministisch aus status ab, falls published nicht explizit gesetzt ist."""
        if isinstance(data, dict):
            status_val = str(data.get("status") or "draft").strip().lower()
            if data.get("published") is None:
                data = {**data, "published": (status_val == "published")}
        return data

    @field_validator("tags", "parent", "children", "related", "related_sources", mode="before")
    @classmethod
    def _coerce_to_list(cls, value: Any) -> List[str]:
        if value is None:
            return []

        if isinstance(value, str):
            if "," in value:
                return [item.strip() for item in value.split(",") if item.strip()]
            stripped = value.strip()
            return [stripped] if stripped else []

        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if item is not None and str(item).strip()]

        return value


class ParsedArticle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    metadata: FrontmatterSchema
    raw_content: str
    extracted_internal_links: List[str] = Field(default_factory=list)
    file_path: Path


class ArticleResponse(FrontmatterSchema):
    id: int
    content: Optional[str] = None


class SeriesNavigationItem(BaseModel):
    title: str
    route: str

    model_config = ConfigDict(from_attributes=True)


class SeriesNavigationResponse(BaseModel):
    series_id: str
    current_order: int
    total_parts: int
    prev: Optional[SeriesNavigationItem] = None
    next: Optional[SeriesNavigationItem] = None

    model_config = ConfigDict(from_attributes=True)


class ArticleListItem(BaseModel):
    """A single entry in the ``GET /api/v1/cms/articles`` listing response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    route: str
    category: str
    subcategory: Optional[str] = None
    subsubcategory: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    date: date
    updated: Optional[date] = None
    published: bool
    status: str = "draft"
    has_math: bool = Field(default=False, validation_alias="math")
    has_charts: bool = Field(default=False, validation_alias="charts")
    series_id: Optional[str] = None
    series_order: Optional[int] = 0

    @field_validator("status", mode="before")
    @classmethod
    def _default_status(cls, value: Any) -> Any:
        """Altdaten mit status=NULL dürfen die API nicht mit einem 500er abbrechen."""
        return "draft" if value is None else value


class ArticleListResponse(BaseModel):
    count: int
    articles: List[ArticleListItem]


class SourceResponse(BaseModel):
    cite_key: str
    title: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    publisher: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None


class SourcesListResponse(BaseModel):
    total: int
    sources: List[SourceResponse]


class ArticleRelationItem(BaseModel):
    id: Optional[int] = None
    title: str
    route: str

    model_config = ConfigDict(from_attributes=True)


class InternalLinksSchema(BaseModel):
    parent: Optional[ArticleRelationItem] = None
    children: List[ArticleRelationItem] = []
    related_articles: List[ArticleRelationItem] = []


class ArticleContentResponse(BaseModel):
    raw_markdown: str
    reading_time_minutes: int


class ArticleDetailResponse(BaseModel):
    metadata: ArticleListItem
    content: ArticleContentResponse
    sources: List[SourceResponse] = Field(default_factory=list)
    internal_links: InternalLinksSchema
    series: Optional[SeriesNavigationResponse] = None

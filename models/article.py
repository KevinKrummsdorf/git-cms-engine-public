"""Database Models representing CMS schemas and relational mappings.

Defines the SQLAlchemy declarative mapping classes for articles, relationships,
and integrated bibliographic sources (Zotero).
"""

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Boolean,
    Date,
    Text,
    DateTime,
    ForeignKey,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship

CMS_SCHEMA = "cms"
ZOTERO_SCHEMA = "zotero"


class Base(DeclarativeBase):
    """The base class for all Declarative database models."""
    pass


class ZoteroSourceModel(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": ZOTERO_SCHEMA}

    cite_key = Column(String(100), primary_key=True)
    title = Column(Text, nullable=False)
    authors = Column(Text, nullable=True)
    year = Column(Integer, nullable=True)
    url = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ArticleModel(Base):
    __tablename__ = "articles"
    __table_args__ = {"schema": CMS_SCHEMA}

    id = Column(BigInteger, primary_key=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    route = Column(String(255), unique=True, nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    subcategory = Column(String(50), nullable=True)
    subsubcategory = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(
        ARRAY(String(50)),
        nullable=False,
        server_default=text("'{}'::varchar[]"),
    )
    date = Column(Date, nullable=False)
    updated = Column(Date, nullable=True)
    relevance = Column(Text, nullable=True)
    status = Column(String(50), default="draft")
    published = Column(Boolean, nullable=False, default=False)
    math = Column(Boolean, nullable=False, default=False)
    charts = Column(Boolean, nullable=False, default=False)

    series_id = Column(String(100), nullable=True, index=True)
    series_order = Column(Integer, nullable=True, default=0)

    parent = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    children = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    related = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    file_path = Column(String(1000), unique=True, nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    sources = relationship(
        "ArticleSourceModel", back_populates="article", cascade="all, delete-orphan"
    )
    outgoing_relations = relationship(
        "ArticleRelationModel",
        foreign_keys="ArticleRelationModel.article_id",
        back_populates="parent_article",
        cascade="all, delete-orphan",
    )


class ArticleSourceModel(Base):
    __tablename__ = "article_sources"
    __table_args__ = {"schema": CMS_SCHEMA}

    article_id = Column(
        BigInteger,
        ForeignKey(f"{CMS_SCHEMA}.articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_key = Column(
        String(100),
        ForeignKey(f"{ZOTERO_SCHEMA}.sources.cite_key", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )

    article = relationship("ArticleModel", back_populates="sources")
    zotero_source = relationship("ZoteroSourceModel")


class ArticleRelationModel(Base):
    __tablename__ = "article_relations"
    __table_args__ = {"schema": CMS_SCHEMA}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    article_id = Column(
        BigInteger,
        ForeignKey(f"{CMS_SCHEMA}.articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_article_id = Column(
        BigInteger,
        ForeignKey(f"{CMS_SCHEMA}.articles.id", ondelete="CASCADE"),
        nullable=True,
    )
    target_route = Column(String(255), nullable=False)
    relation_type = Column(String(50), nullable=False)

    parent_article = relationship(
        "ArticleModel",
        foreign_keys=[article_id],
        back_populates="outgoing_relations",
    )
    target_article = relationship(
        "ArticleModel",
        foreign_keys=[target_article_id],
    )

import datetime
from unittest.mock import MagicMock
import pytest
from models.article import ArticleModel
from services.processor import (
    compare_and_update_article,
    handle_route_and_slug_conflicts,
    upsert_single_article,
    normalize_date,
)


def test_normalize_date():
    today = datetime.date.today()
    assert normalize_date("2024-05-15") == datetime.date(2024, 5, 15)
    assert normalize_date("15.06.2024") == datetime.date(2024, 6, 15)
    assert normalize_date("3.6.2023") == datetime.date(2023, 6, 3)
    assert normalize_date(datetime.date(2023, 10, 1)) == datetime.date(2023, 10, 1)
    assert normalize_date("invalid-date") == today
    assert normalize_date(None) == today
    assert normalize_date("15.06.2024", default_to_today=False) == datetime.date(2024, 6, 15)
    assert normalize_date(None, default_to_today=False) is None


def test_compare_and_update_article_no_changes():
    article = ArticleModel(
        title="Test Title",
        slug="test-slug",
        route="test/route",
        category="Tech",
        subcategory="Python",
        description="A great article",
        tags=["a", "b"],
        date=datetime.date(2024, 1, 1),
        status="published",
        published=True,
        math=False,
        charts=False,
        relevance="high",
        file_path="tech/test.md",
        updated=None,
    )

    # Compare with identical metadata
    changed = compare_and_update_article(
        existing_article=article,
        meta_title="Test Title",
        meta_slug="test-slug",
        meta_route="test/route",
        meta_category="Tech",
        meta_subcategory="Python", meta_subsubcategory=None,
        meta_description="A great article",
        meta_tags=["a", "b"],
        meta_date="2024-01-01",
        meta_updated=None,
        meta_status="published",
        meta_published=True,
        meta_math=False,
        meta_charts=False,
        meta_relevance="high",
        file_path="tech/test.md",
        is_new=False,
    )

    assert not changed
    assert article.updated is None


def test_compare_and_update_article_with_changes():
    article = ArticleModel(
        title="Test Title",
        slug="test-slug",
        route="test/route",
        category="Tech",
        subcategory="Python",
        description="A great article",
        tags=["a", "b"],
        date=datetime.date(2024, 1, 1),
        status="published",
        published=True,
        math=False,
        charts=False,
        relevance="high",
        file_path="tech/test.md",
        updated=None,
    )

    # Compare with modified category
    changed = compare_and_update_article(
        existing_article=article,
        meta_title="Test Title",
        meta_slug="test-slug",
        meta_route="test/route",
        meta_category="Science",  # Changed!
        meta_subcategory="Python", meta_subsubcategory=None,
        meta_description="A great article",
        meta_tags=["a", "b"],
        meta_date="2024-01-01",
        meta_updated=None,
        meta_status="published",
        meta_published=True,
        meta_math=False,
        meta_charts=False,
        meta_relevance="high",
        file_path="tech/test.md",
        is_new=False,
    )

    assert changed
    assert article.category == "Science"
    assert article.updated == datetime.date.today()


def test_compare_and_update_article_is_new_no_updated_field():
    article = ArticleModel(
        title="Test Title",
        slug="test-slug",
        route="test/route",
        category="Tech",
        file_path="tech/test.md",
        updated=None,
    )

    changed = compare_and_update_article(
        existing_article=article,
        meta_title="Test Title 2",
        meta_slug="test-slug",
        meta_route="test/route",
        meta_category="Tech",
        meta_subcategory=None, meta_subsubcategory=None,
        meta_description=None,
        meta_tags=[],
        meta_date="2024-01-01",
        meta_updated=None,
        meta_status="published",
        meta_published=True,
        meta_math=False,
        meta_charts=False,
        meta_relevance=None,
        file_path="tech/test.md",
        is_new=True,  # Is a new article row!
    )

    assert changed
    assert article.title == "Test Title 2"
    assert article.updated is None  # Should remain None for newly inserted records


def test_compare_and_update_article_with_german_updated_date():
    article = ArticleModel(
        title="ECDH",
        slug="ecdh",
        route="security/ecdh",
        category="Security",
        file_path="security/ecdh.md",
        date=datetime.date(2023, 6, 3),
        updated=None,
    )

    changed = compare_and_update_article(
        existing_article=article,
        meta_title="ECDH",
        meta_slug="ecdh",
        meta_route="security/ecdh",
        meta_category="Security",
        meta_subcategory=None, meta_subsubcategory=None,
        meta_description=None,
        meta_tags=[],
        meta_date="2023-06-03",
        meta_updated="15.06.2024",
        meta_status="published",
        meta_published=True,
        meta_math=False,
        meta_charts=False,
        meta_relevance=None,
        file_path="security/ecdh.md",
        is_new=False,
    )

    assert changed
    assert article.updated == datetime.date(2024, 6, 15)


def test_handle_route_and_slug_conflicts_stale():
    db = MagicMock()

    # Mocking colliding article (route conflict)
    stale_article = ArticleModel(
        file_path="stale/old.md",
        route="colliding/route",
        slug="old-slug",
    )

    db.execute.return_value.scalars.return_value.first.side_effect = [
        stale_article,  # First query: route collision check
        None,           # Second query: slug collision check
    ]

    active_file_paths = {"active/new.md"}

    # Should delete the stale article immediately to free the route
    handle_route_and_slug_conflicts(
        db=db,
        current_file_path="active/new.md",
        meta_route="colliding/route",
        meta_slug="new-slug",
        active_file_paths=active_file_paths,
    )

    db.delete.assert_called_once_with(stale_article)
    db.flush.assert_called_once()


def test_handle_route_and_slug_conflicts_active_raises_value_error():
    db = MagicMock()

    active_article = ArticleModel(
        file_path="active/other.md",
        route="colliding/route",
        slug="other-slug",
    )

    db.execute.return_value.scalars.return_value.first.return_value = active_article

    active_file_paths = {"active/new.md", "active/other.md"}

    # Should raise a ValueError since the colliding route is active in the current sync run
    with pytest.raises(ValueError) as excinfo:
        handle_route_and_slug_conflicts(
            db=db,
            current_file_path="active/new.md",
            meta_route="colliding/route",
            meta_slug="new-slug",
            active_file_paths=active_file_paths,
        )

    assert "Routen-Konflikt" in str(excinfo.value)
    db.delete.assert_not_called()


def test_upsert_single_article_identifies_by_route():
    db = MagicMock()

    # Mock existing article with route
    existing = ArticleModel(
        file_path="tech/old-path.md",
        route="tech/my-article",
        slug="my-article",
    )

    # db.execute search mock sequence
    db.execute.return_value.scalars.return_value.first.side_effect = [
        None,      # 1. Route collision check (no other article)
        None,      # 2. Slug collision check (no other article)
        None,      # 3. File path check (not found on 'tech/new-path.md')
        existing,  # 4. Route check (found 'tech/my-article')
    ]

    active_file_paths = {"tech/new-path.md"}
    meta = {
        "id": 4242,
        "title": "My Updated Title",
        "slug": "my-article",
        "route": "tech/my-article",
        "category": "Tech",
        "subcategory": "Backend",
        "subsubcategory": "Python",
        "date": "2024-02-02",
        "updated": "2024-02-03",
        "status": "published",
    }

    result = upsert_single_article(
        db=db,
        file_path="tech/new-path.md",
        meta=meta,
        active_file_paths=active_file_paths,
    )

    # Must be the exact same article row, updated with the new file_path
    assert result is existing
    assert result.file_path == "tech/new-path.md"
    assert result.title == "My Updated Title"


def test_compare_and_update_article_relational_fields():
    article = ArticleModel(
        title="Test Title",
        slug="test-slug",
        route="test/route",
        category="Tech",
        file_path="tech/test.md",
        parent=[],
        children=[],
        related=[],
    )

    changed = compare_and_update_article(
        existing_article=article,
        meta_title="Test Title",
        meta_slug="test-slug",
        meta_route="test/route",
        meta_category="Tech",
        meta_subcategory=None, meta_subsubcategory=None,
        meta_description=None,
        meta_tags=[],
        meta_date="2024-01-01",
        meta_updated=None,
        meta_status="published",
        meta_published=True,
        meta_math=False,
        meta_charts=False,
        meta_relevance=None,
        file_path="tech/test.md",
        is_new=False,
        meta_parent=["die-cia-triade"],
        meta_children=["auth-vs-authz-access-control"],
        meta_related=["least-privilege-prinzip"],
    )

    assert changed
    assert article.parent == ["die-cia-triade"]
    assert article.children == ["auth-vs-authz-access-control"]
    assert article.related == ["least-privilege-prinzip"]


def test_upsert_single_article_rejects_missing_required_fields():
    from services.processor import InvalidFrontmatterError

    db = MagicMock()
    meta = {"title": "No required fields", "slug": "x", "route": "x", "category": "Tech"}

    with pytest.raises(InvalidFrontmatterError) as excinfo:
        upsert_single_article(db=db, file_path="tech/x.md", meta=meta, active_file_paths={"tech/x.md"})

    message = str(excinfo.value)
    for field in ("id", "subcategory", "subsubcategory", "date", "updated", "status"):
        assert field in message
    db.add.assert_not_called()

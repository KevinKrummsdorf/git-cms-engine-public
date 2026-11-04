from __future__ import annotations

import datetime
from pathlib import Path
import pytest
from sqlalchemy.orm import Session
from models.article import ArticleModel
from services.processor import process_all_markdown_files
from config import settings


def test_process_markdown_files_with_allowed_folders_and_disk_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Mock settings
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"

    raw_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "cms_raw_data_path", raw_dir)
    monkeypatch.setattr(settings, "cms_processed_data_path", processed_dir)
    monkeypatch.setattr(settings, "cms_allowed_folders", ["security"])

    # Create content directory under raw
    content_dir = raw_dir / "content"
    content_dir.mkdir(parents=True)

    # 2. Create some files
    # Allowed folder file
    sec_dir = content_dir / "security"
    sec_dir.mkdir()
    sec_file = sec_dir / "allowed.md"
    sec_file.write_text(
        "---\n"
        "id: 111\n"
        "title: Allowed Article\n"
        "slug: allowed\n"
        "route: security/allowed\n"
        "category: security\n"
        "description: Allowed description\n"
        "subcategory: general\n"
        "subsubcategory: misc\n"
        "date: 2024-01-01\n"
        "updated: 2024-01-02\n"
        "status: published\n"
        "published: true\n"
        "---\n"
        "Body content"
    )

    # Ignored folder file
    tech_dir = content_dir / "tech"
    tech_dir.mkdir()
    tech_file = tech_dir / "ignored.md"
    tech_file.write_text(
        "---\n"
        "id: 222\n"
        "title: Ignored Article\n"
        "slug: ignored\n"
        "route: tech/ignored\n"
        "category: tech\n"
        "description: Ignored description\n"
        "subcategory: general\n"
        "subsubcategory: misc\n"
        "date: 2024-01-01\n"
        "updated: 2024-01-02\n"
        "status: published\n"
        "published: true\n"
        "---\n"
        "Body content"
    )

    # 3. Mock database session
    class FakeDb:
        def __init__(self):
            self.deleted_articles = []
            self.added_articles = []

        def execute(self, stmt):
            class ScalarResult:
                def all(self):
                    # Simulate existing article in database that is now stale
                    stale_art = ArticleModel(
                        id=999,
                        title="Stale Article",
                        slug="stale",
                        route="security/stale",
                        category="security",
                        file_path="content/security/stale.md",
                        date=datetime.date(2024, 1, 1),
                        published=True,
                    )
                    return [stale_art]

                def first(self):
                    return None

            class Result:
                def scalars(self):
                    return ScalarResult()
            return Result()

        def add(self, obj):
            self.added_articles.append(obj)

        def delete(self, obj):
            self.deleted_articles.append(obj)

        def flush(self):
            pass

        def begin_nested(self):
            import contextlib
            return contextlib.nullcontext()

        def rollback(self):
            pass

        def commit(self):
            pass

    db = FakeDb()

    # Pre-create a stale processed file on disk under processed_dir
    stale_processed_file = processed_dir / "content/security/stale.md"
    stale_processed_file.parent.mkdir(parents=True, exist_ok=True)
    stale_processed_file.write_text("old processed data")

    # 4. Run processor
    stats = process_all_markdown_files(db)

    # Verify counts
    # Total files encountered is 2 (allowed.md and ignored.md)
    assert stats["total"] == 2
    # Ignored count should be 1 (ignored.md)
    assert stats["ignored"] == 1
    # Created should be 1 (allowed.md)
    assert stats["created"] == 1
    # Deleted should be 1 (stale.md)
    assert stats["deleted"] == 1

    # Verify that the stale file on disk was unlinked
    assert not stale_processed_file.exists()

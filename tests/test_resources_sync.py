from __future__ import annotations

import os
import shutil
from pathlib import Path
import pytest
from services.processor import sync_resources_directory, process_all_markdown_files
from config import settings


def test_sync_resources_directory(tmp_path: Path) -> None:
    # 1. Create fake raw_data/resources folder with subdirs and images
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"

    resources_src = raw_dir / "resources"
    resources_src.mkdir(parents=True)

    # Add resource in root of resources
    file1 = resources_src / "image1.png"
    file1.write_text("fake png content")

    # Add resource in nested folder
    nested_dir = resources_src / "subfolder"
    nested_dir.mkdir()
    file2 = nested_dir / "screenshot.jpg"
    file2.write_text("fake jpg content")

    # Run sync
    sync_resources_directory(raw_dir, processed_dir)

    # Verify that files were correctly copied to processed_dir / resources
    expected_file1 = processed_dir / "resources" / "image1.png"
    expected_file2 = processed_dir / "resources" / "subfolder" / "screenshot.jpg"

    assert expected_file1.exists()
    assert expected_file1.read_text() == "fake png content"

    assert expected_file2.exists()
    assert expected_file2.read_text() == "fake jpg content"


def test_sync_resources_no_source_dir(tmp_path: Path) -> None:
    # Verify it doesn't fail when resources doesn't exist
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"

    raw_dir.mkdir()
    sync_resources_directory(raw_dir, processed_dir)
    assert not (processed_dir / "resources").exists()

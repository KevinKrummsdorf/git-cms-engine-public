"""Unit tests for the POST /api/v1/cms/sync endpoint's auth & error handling.

Database and Git operations are monkeypatched so these tests run without
a real PostgreSQL instance or network access -- they focus purely on the
API contract (status codes, auth branching, error-code payloads), which
is exactly what docs/API.md promises callers.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import api.sync as sync_module
from main import app
from repository.article_repository import SyncStats
from repository.database import get_db
from services.exceptions import GitSyncError

SYNC_SECRET = "test-sync-secret"


class _FakeSession:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _fake_get_db():
    yield _FakeSession()


@pytest.fixture(autouse=True)
def _override_db_dependency():
    app.dependency_overrides[get_db] = _fake_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _configure_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sync_module.settings, "cms_sync_secret", _SecretStrLike(SYNC_SECRET)
    )
    yield


class _SecretStrLike:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSyncAuthentication:
    def test_rejects_request_without_any_auth_header(self, client: TestClient) -> None:
        response = client.post("/api/v1/cms/sync", json={})

        assert response.status_code == 401
        assert response.json()["error_code"] == "UNAUTHORIZED"

    def test_rejects_wrong_shared_secret(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/cms/sync", json={}, headers={"X-CMS-Sync-Secret": "wrong"}
        )

        assert response.status_code == 401

    def test_accepts_correct_shared_secret(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sync_module, "sync_repository", lambda **kwargs: True)
        monkeypatch.setattr(sync_module, "process_all_markdown_files", lambda db: 0)

        response = client.post(
            "/api/v1/cms/sync", json={}, headers={"X-CMS-Sync-Secret": SYNC_SECRET}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["processed_files"] == 0

    def test_accepts_valid_github_hmac_signature(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sync_module, "sync_repository", lambda **kwargs: True)
        monkeypatch.setattr(sync_module, "process_all_markdown_files", lambda db: 0)

        body_bytes = b'{"ref": "refs/heads/main"}'
        digest = hmac.new(SYNC_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()

        response = client.post(
            "/api/v1/cms/sync",
            content=body_bytes,
            headers={
                "X-Hub-Signature-256": f"sha256={digest}",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200

    def test_rejects_invalid_github_hmac_signature(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/cms/sync",
            json={"ref": "refs/heads/main"},
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )

        assert response.status_code == 401


class TestSyncErrorHandling:
    def test_git_sync_failure_returns_500_sync_failed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_git_error(**kwargs):
            raise GitSyncError("simulated clone failure")

        monkeypatch.setattr(sync_module, "sync_repository", _raise_git_error)

        response = client.post(
            "/api/v1/cms/sync", json={}, headers={"X-CMS-Sync-Secret": SYNC_SECRET}
        )

        assert response.status_code == 500
        assert response.json()["error_code"] == "SYNC_FAILED"


def test_git_sync_copies_only_specific_folders(
    client: TestClient, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set up temp paths for local repo cache and raw data path
    repo_cache = tmp_path / "repo_cache"
    raw_data = tmp_path / "raw_data"

    repo_cache.mkdir()
    raw_data.mkdir()

    # Create directories in repo_cache
    content_dir = repo_cache / "content"
    conent_dir = repo_cache / "conent"
    resources_dir = repo_cache / "resources"
    other_dir = repo_cache / "other_unrelated_folder"

    content_dir.mkdir()
    conent_dir.mkdir()
    resources_dir.mkdir()
    other_dir.mkdir()

    # Put some dummy files
    (content_dir / "a.md").write_text("content a")
    (conent_dir / "b.md").write_text("conent b")
    (resources_dir / "img.png").write_text("png")
    (other_dir / "ignore.txt").write_text("ignore")

    # Monkeypatch settings
    monkeypatch.setattr(sync_module.settings, "cms_use_local_content", False)
    monkeypatch.setattr(sync_module.settings, "cms_repo_local_path", repo_cache)
    monkeypatch.setattr(sync_module.settings, "cms_raw_data_path", raw_data)

    # Monkeypatch sync_repository to do nothing (since we populated the directories manually)
    monkeypatch.setattr(sync_module, "sync_repository", lambda **kwargs: True)
    # Monkeypatch process_all_markdown_files to do nothing and return 0
    monkeypatch.setattr(sync_module, "process_all_markdown_files", lambda db: 0)

    response = client.post(
        "/api/v1/cms/sync", json={}, headers={"X-CMS-Sync-Secret": SYNC_SECRET}
    )

    assert response.status_code == 200

    # Verify copied folders in raw_data
    assert (raw_data / "content" / "a.md").exists()
    assert (raw_data / "resources" / "img.png").exists()

    # Verify unrelated folders are NOT copied
    assert not (raw_data / "conent").exists()
    assert not (raw_data / "other_unrelated_folder").exists()

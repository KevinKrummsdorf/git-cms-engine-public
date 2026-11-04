"""Unit tests for services.git_sync.

Covers:
- PAT injection into HTTPS clone URLs (build_authenticated_url).
- Shared-secret webhook verification (verify_sync_secret).
- GitHub HMAC signature verification (verify_github_signature).
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from services.exceptions import WebhookAuthenticationError
from services.git_sync import (
    build_authenticated_url,
    verify_github_signature,
    verify_sync_secret,
)


class TestBuildAuthenticatedUrl:
    def test_injects_token_into_https_url(self) -> None:
        url = build_authenticated_url("https://github.com/org/repo.git", "ghp_abc123")

        assert url == "https://ghp_abc123@github.com/org/repo.git"

    def test_returns_unchanged_when_no_token_provided(self) -> None:
        url = build_authenticated_url("https://github.com/org/repo.git", None)

        assert url == "https://github.com/org/repo.git"

    def test_ignores_token_for_ssh_urls(self) -> None:
        ssh_url = "git@github.com:org/repo.git"

        url = build_authenticated_url(ssh_url, "ghp_abc123")

        assert url == ssh_url

    def test_preserves_port_when_present(self) -> None:
        url = build_authenticated_url("https://git.example.com:8443/org/repo.git", "tok")

        assert url == "https://tok@git.example.com:8443/org/repo.git"


class TestVerifySyncSecret:
    def test_accepts_matching_secret(self) -> None:
        verify_sync_secret("correct-secret", "correct-secret")

    def test_rejects_missing_secret(self) -> None:
        with pytest.raises(WebhookAuthenticationError):
            verify_sync_secret(None, "correct-secret")

    def test_rejects_incorrect_secret(self) -> None:
        with pytest.raises(WebhookAuthenticationError):
            verify_sync_secret("wrong-secret", "correct-secret")


class TestVerifyGithubSignature:
    def _make_signature(self, secret: str, body: bytes) -> str:
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_accepts_valid_signature(self) -> None:
        secret = "webhook-secret"
        body = b'{"ref": "refs/heads/main"}'
        signature = self._make_signature(secret, body)

        verify_github_signature(body, signature, secret)

    def test_rejects_missing_header(self) -> None:
        with pytest.raises(WebhookAuthenticationError):
            verify_github_signature(b"{}", None, "secret")

    def test_rejects_malformed_header(self) -> None:
        with pytest.raises(WebhookAuthenticationError):
            verify_github_signature(b"{}", "not-a-valid-signature", "secret")

    def test_rejects_signature_with_wrong_secret(self) -> None:
        body = b'{"ref": "refs/heads/main"}'
        signature = self._make_signature("correct-secret", body)

        with pytest.raises(WebhookAuthenticationError):
            verify_github_signature(body, signature, "wrong-secret")

    def test_rejects_signature_for_tampered_body(self) -> None:
        secret = "webhook-secret"
        original_body = b'{"ref": "refs/heads/main"}'
        signature = self._make_signature(secret, original_body)
        tampered_body = b'{"ref": "refs/heads/malicious"}'

        with pytest.raises(WebhookAuthenticationError):
            verify_github_signature(tampered_body, signature, secret)

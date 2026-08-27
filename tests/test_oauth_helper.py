"""Tests for Blogger OAuth credential loading."""

from unittest.mock import MagicMock, patch

from src.publishers.oauth_helper import get_blogger_credentials


def test_refreshes_tokenless_github_actions_credentials(monkeypatch, tmp_path):
    """CI credentials must exchange their refresh token before publishing."""
    monkeypatch.setenv("BLOGGER_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("BLOGGER_CLIENT_ID", "client-id")
    monkeypatch.setenv("BLOGGER_CLIENT_SECRET", "client-secret")

    credentials = MagicMock(valid=False, refresh_token="refresh-token")
    credentials.to_json.return_value = "{}"

    with patch("src.publishers.oauth_helper.Credentials", return_value=credentials) as factory:
        result = get_blogger_credentials(
            client_secret_path=tmp_path / "missing-client-secret.json",
            token_path=tmp_path / "token.json",
        )

    assert result is credentials
    factory.assert_called_once()
    credentials.refresh.assert_called_once()
    assert (tmp_path / "token.json").read_text(encoding="utf-8") == "{}"

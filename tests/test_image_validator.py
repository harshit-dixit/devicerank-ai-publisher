"""Tests for image URL validation, mock domain rejection, and live HTTP checks."""

from unittest.mock import MagicMock, patch
from src.utils.image_validator import is_safe_image_domain, validate_image_url


def test_is_safe_image_domain():
    # Safe domains
    assert is_safe_image_domain("https://images.unsplash.com/photo-123.jpg")
    assert is_safe_image_domain("https://cdn.vox-cdn.com/uploads/hub/image.png")
    assert is_safe_image_domain("https://fdn.gsmarena.com/imgroot/news/pic.webp")

    # Mock & reserved domains should be rejected
    assert not is_safe_image_domain("https://example.com/image.jpg")
    assert not is_safe_image_domain("https://sub.example.net/pic.png")
    assert not is_safe_image_domain("https://example.org/test.webp")
    assert not is_safe_image_domain("https://localhost:8080/image.jpg")
    assert not is_safe_image_domain("https://via.placeholder.com/600x400")
    assert not is_safe_image_domain("https://test.com/photo.jpg")


@patch("requests.head")
def test_validate_image_url_success(mock_head):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {
        "Content-Type": "image/jpeg",
        "Content-Length": "45000",
    }
    mock_head.return_value = mock_resp

    url = "https://images.unsplash.com/photo-valid.jpg"
    result = validate_image_url(url, verify_live_http=True)
    assert result == url


@patch("requests.head")
def test_validate_image_url_rejects_html_error(mock_head):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Hotlink protection returning HTML error page under 200
    mock_resp.headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": "2000",
    }
    mock_head.return_value = mock_resp

    url = "https://cdn.example-media.com/hotlink-blocked.jpg"
    result = validate_image_url(url, verify_live_http=True)
    assert result is None


@patch("requests.head")
def test_validate_image_url_rejects_404_or_error(mock_head):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_head.return_value = mock_resp

    url = "https://images.unsplash.com/photo-missing.jpg"
    result = validate_image_url(url, verify_live_http=True)
    assert result is None


def test_validate_image_url_rejects_mock_domain_without_network():
    url = "https://example.com/ai-launch.jpg"
    result = validate_image_url(url, verify_live_http=False)
    assert result is None

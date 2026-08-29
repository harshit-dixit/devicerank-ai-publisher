"""Tests for licensed evergreen image discovery and attribution metadata."""

from unittest.mock import MagicMock, patch

from src.image_sources import UnsplashImageFetcher


def _photo(index: int) -> dict:
    return {
        "id": f"photo-{index}",
        "width": 1600,
        "height": 900,
        "alt_description": f"Keyword research workspace {index}",
        "urls": {
            "regular": f"https://images.unsplash.com/photo-{index}?w=1080&fit=max",
        },
        "links": {
            "html": f"https://unsplash.com/photos/photo-{index}",
            "download_location": f"https://api.unsplash.com/photos/photo-{index}/download",
        },
        "user": {
            "name": f"Photographer {index}",
            "links": {"html": f"https://unsplash.com/@photographer{index}"},
        },
    }


@patch("src.image_sources.requests.get")
def test_unsplash_search_returns_tracked_attributed_images(mock_get):
    search_response = MagicMock()
    search_response.json.return_value = {"results": [_photo(1), _photo(2), _photo(3)]}
    tracked_response = MagicMock()
    mock_get.side_effect = [
        search_response,
        tracked_response,
        tracked_response,
        tracked_response,
    ]

    images = UnsplashImageFetcher("test-access-key").search(
        "low competition keywords",
        count=3,
        fallback_query="SEO keyword research analytics",
    )

    assert len(images) == 3
    assert images[0].url.startswith("https://images.unsplash.com/photo-1")
    assert images[0].alt_text == "Keyword research workspace 1"
    assert images[0].photographer_name == "Photographer 1"
    assert "utm_source=devicerank" in images[0].photographer_url
    assert "utm_medium=referral" in images[0].source_url
    assert mock_get.call_count == 4
    search_call = mock_get.call_args_list[0]
    assert search_call.kwargs["params"]["orientation"] == "landscape"
    assert search_call.kwargs["params"]["content_filter"] == "high"
    assert search_call.kwargs["headers"]["Authorization"] == "Client-ID test-access-key"


@patch("src.image_sources.requests.get")
def test_unsplash_rejects_untracked_or_unattributed_results(mock_get):
    incomplete = _photo(1)
    incomplete["user"] = {"name": "", "links": {}}
    search_response = MagicMock()
    search_response.json.return_value = {"results": [incomplete]}
    mock_get.return_value = search_response

    images = UnsplashImageFetcher("test-access-key").search("keyword research", count=1)

    assert images == []
    assert mock_get.call_count == 1

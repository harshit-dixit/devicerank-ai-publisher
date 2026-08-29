"""Licensed image discovery for evergreen DeviceRank tutorials."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from src.utils.image_validator import validate_image_url
from src.utils.logger import logger
from src.utils.sanitizer import sanitize_url, strip_html


UNSPLASH_API_URL = "https://api.unsplash.com/search/photos"
UNSPLASH_HOME_URL = "https://unsplash.com/"
UNSPLASH_TRACKING_PARAMETERS = {
    "utm_source": "devicerank",
    "utm_medium": "referral",
}

CATEGORY_IMAGE_QUERIES = {
    "seo_tips": "SEO keyword research analytics",
    "adsense_tips": "website advertising analytics",
    "digital_marketing_tips": "digital marketing workspace",
    "blogging_tips": "blogging content writing laptop",
    "wordpress_tips": "website design laptop",
    "shopify_tips": "ecommerce online store",
    "gsc_tips": "search analytics dashboard",
    "ga4_tips": "web analytics dashboard",
}


@dataclass(frozen=True)
class ArticleImage:
    """One publishable image and the attribution required by its provider."""

    url: str
    alt_text: str
    photographer_name: str
    photographer_url: str
    source_url: str
    width: int
    height: int


def _add_tracking_parameters(url: str) -> str:
    """Add DeviceRank referral parameters without discarding provider parameters."""
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(UNSPLASH_TRACKING_PARAMETERS)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _is_unsplash_url(url: str, required_host: Optional[str] = None) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if required_host:
        return hostname == required_host
    return hostname == "unsplash.com" or hostname.endswith(".unsplash.com")


def _positive_dimension(value: Any) -> Optional[int]:
    try:
        dimension = int(value)
    except (TypeError, ValueError):
        return None
    return dimension if dimension > 0 else None


class UnsplashImageFetcher:
    """Find and register Unsplash photos for use in a published article."""

    def __init__(self, access_key: str, timeout_seconds: float = 10.0):
        self.access_key = access_key.strip()
        self.timeout_seconds = timeout_seconds
        if not self.access_key:
            raise ValueError("An Unsplash access key is required")

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Client-ID {self.access_key}",
            "Accept-Version": "v1",
            "User-Agent": "DeviceRankPublisher/1.0",
        }

    def search(
        self,
        query: str,
        count: int = 3,
        fallback_query: Optional[str] = None,
    ) -> List[ArticleImage]:
        """Return up to ``count`` usable photos, falling back to a broader query."""
        if count < 1:
            return []

        images: List[ArticleImage] = []
        seen_ids: Set[str] = set()
        queries = [query.strip()]
        if fallback_query and fallback_query.strip().lower() != query.strip().lower():
            queries.append(fallback_query.strip())

        for search_query in queries:
            if not search_query:
                continue
            for photo in self._search_photos(search_query, per_page=max(count, 6)):
                photo_id = str(photo.get("id") or "").strip()
                if not photo_id or photo_id in seen_ids:
                    continue
                seen_ids.add(photo_id)
                image = self._prepare_image(photo, fallback_alt=query)
                if image:
                    images.append(image)
                if len(images) >= count:
                    return images

        return images

    def _search_photos(self, query: str, per_page: int) -> List[Dict[str, Any]]:
        try:
            response = requests.get(
                UNSPLASH_API_URL,
                headers=self._headers,
                params={
                    "query": query,
                    "per_page": min(per_page, 30),
                    "orientation": "landscape",
                    "content_filter": "high",
                    "order_by": "relevant",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", []) if isinstance(payload, dict) else []
            return [item for item in results if isinstance(item, dict)]
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Unsplash image search failed for '%s': %s", query, exc)
            return []

    def _prepare_image(
        self,
        photo: Dict[str, Any],
        fallback_alt: str,
    ) -> Optional[ArticleImage]:
        urls = photo.get("urls") if isinstance(photo.get("urls"), dict) else {}
        links = photo.get("links") if isinstance(photo.get("links"), dict) else {}
        user = photo.get("user") if isinstance(photo.get("user"), dict) else {}
        user_links = user.get("links") if isinstance(user.get("links"), dict) else {}

        image_url = validate_image_url(str(urls.get("regular") or ""), verify_live_http=False)
        photographer_url = sanitize_url(str(user_links.get("html") or ""), enforce_https=True)
        source_url = sanitize_url(str(links.get("html") or ""), enforce_https=True)
        download_location = sanitize_url(
            str(links.get("download_location") or ""), enforce_https=True
        )
        photographer_name = strip_html(str(user.get("name") or "")).strip()

        if (
            not image_url
            or not _is_unsplash_url(image_url, required_host="images.unsplash.com")
            or not photographer_url
            or not _is_unsplash_url(photographer_url)
            or not source_url
            or not _is_unsplash_url(source_url)
            or not download_location
            or not _is_unsplash_url(download_location, required_host="api.unsplash.com")
            or not photographer_name
        ):
            return None

        if not self._track_download(download_location):
            return None

        raw_alt = photo.get("alt_description") or photo.get("description") or fallback_alt
        alt_text = " ".join(strip_html(str(raw_alt)).split())[:180] or fallback_alt
        width = _positive_dimension(photo.get("width"))
        height = _positive_dimension(photo.get("height"))
        if not width or not height:
            return None
        return ArticleImage(
            url=image_url,
            alt_text=alt_text,
            photographer_name=photographer_name,
            photographer_url=_add_tracking_parameters(photographer_url),
            source_url=_add_tracking_parameters(source_url),
            width=width,
            height=height,
        )

    def _track_download(self, download_location: str) -> bool:
        """Register selection of a photo as required by the Unsplash API."""
        try:
            response = requests.get(
                download_location,
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.warning("Could not register Unsplash photo selection: %s", exc)
            return False

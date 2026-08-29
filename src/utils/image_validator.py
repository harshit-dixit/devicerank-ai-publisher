"""Image URL validation, domain filtering, and HTTP content-type verification for DeviceRank."""

from typing import Optional, Set
from urllib.parse import urlparse
import requests
from src.utils.logger import logger
from src.utils.sanitizer import sanitize_url

# Domains that are mock fixtures or placeholders and never valid live article images
RESERVED_AND_MOCK_DOMAINS: Set[str] = {
    "example.com",
    "example.net",
    "example.org",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "test.com",
    "invalid",
    "placeholder.com",
    "via.placeholder.com",
    "dummyimage.com",
    "mock.com",
}

VALID_IMAGE_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/")


def is_safe_image_domain(url: str) -> bool:
    """Checks if the URL's domain is allowed and not a mock or reserved placeholder domain."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip()
        if not hostname:
            return False

        # Check exact and subdomain matches for reserved/mock domains
        for mock_domain in RESERVED_AND_MOCK_DOMAINS:
            if hostname == mock_domain or hostname.endswith("." + mock_domain):
                return False

        return True
    except Exception:
        return False


def validate_image_url(
    url: Optional[str],
    timeout_seconds: float = 3.0,
    verify_live_http: bool = True,
) -> Optional[str]:
    """Validates an image URL:
    
    1. Enforces HTTPS and syntax validity.
    2. Rejects mock/reserved/placeholder domains.
    3. Performs a fast live HTTP request (HEAD or range GET) to verify:
       - HTTP status 200 or 304.
       - Content-Type header begins with 'image/'.
       - Not a tiny tracking pixel (< 500 bytes).
       - Not an HTML error page returned under 200 (e.g. hotlink blocks).
    
    Returns the sanitized URL if valid, or None if invalid or unreachable.
    """
    if not url:
        return None

    sanitized = sanitize_url(url, enforce_https=True)
    if not sanitized:
        return None

    if not is_safe_image_domain(sanitized):
        logger.debug(f"Rejecting image with unsafe/mock domain: {sanitized}")
        return None

    if not verify_live_http:
        return sanitized

    # Live HTTP verification with fast timeout
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 DeviceRankPublisher/1.0"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    try:
        # First attempt fast HEAD request
        resp = requests.head(sanitized, headers=headers, timeout=timeout_seconds, allow_redirects=True)

        # If HEAD is disallowed (405) or unsupported, fallback to GET with stream
        if resp.status_code == 405 or resp.status_code == 403:
            resp = requests.get(sanitized, headers=headers, timeout=timeout_seconds, stream=True, allow_redirects=True)
            # Close connection immediately after headers
            resp.close()

        if resp.status_code not in (200, 304):
            logger.debug(f"Image validation failed for {sanitized}: HTTP {resp.status_code}")
            return None

        content_type = (resp.headers.get("Content-Type") or "").lower().strip()
        if not any(content_type.startswith(prefix) for prefix in VALID_IMAGE_MIME_PREFIXES):
            logger.debug(f"Image validation failed for {sanitized}: non-image Content-Type '{content_type}'")
            return None

        # Check content length if provided (reject <= 500 bytes tracking pixels)
        content_length = resp.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) < 500:
                    logger.debug(f"Image validation failed for {sanitized}: tiny image ({content_length} bytes)")
                    return None
            except ValueError:
                pass

        return sanitized

    except Exception as e:
        logger.debug(f"Live image verification error for {sanitized}: {e}")
        return None

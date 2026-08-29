"""Image URL validation, domain filtering, caching, and byte-level verification for DeviceRank."""

from typing import Dict, Optional, Set
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
    "dummy.com",
}

VALID_IMAGE_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/")

# Image magic bytes signatures for byte-level inspection
IMAGE_MAGIC_BYTES = (
    b"\xff\xd8\xff",       # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",             # GIF87a
    b"GIF89a",             # GIF89a
    b"RIFF",               # WebP (RIFF....WEBP)
    b"ftypavif",           # AVIF (in first 16 bytes)
    b"ftypavis",           # AVIS (in first 16 bytes)
)

# Process-level cache to avoid repeated HTTP calls for the same image URL during a run
_VALIDATED_IMAGES_CACHE: Dict[str, Optional[str]] = {}


def is_safe_image_domain(url: str) -> bool:
    """Checks if the URL's domain is allowed and not a mock or reserved placeholder domain."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip()
        if not hostname:
            return False

        for mock_domain in RESERVED_AND_MOCK_DOMAINS:
            if hostname == mock_domain or hostname.endswith("." + mock_domain):
                return False

        return True
    except Exception:
        return False


def _inspect_image_bytes(chunk: bytes) -> bool:
    """Checks if the initial byte chunk matches known image magic numbers."""
    if not chunk or len(chunk) < 12:
        return False
    # Check direct prefix magic numbers
    if any(chunk.startswith(sig) for sig in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return True
    # Check WebP (RIFF????WEBP)
    if chunk.startswith(b"RIFF") and b"WEBP" in chunk[:16]:
        return True
    # Check AVIF
    if b"ftypavif" in chunk[:24] or b"ftypavis" in chunk[:24]:
        return True
    return False


def validate_image_url(
    url: Optional[str],
    timeout_seconds: float = 3.0,
    verify_live_http: bool = True,
) -> Optional[str]:
    """Validates an image URL:
    
    1. Enforces HTTPS and syntax validity.
    2. Rejects mock/reserved/placeholder domains immediately without network.
    3. Checks process-level validation cache.
    4. Performs live HTTP request (HEAD or streamed GET) to verify:
       - HTTP status 200 or 304.
       - Content-Type header begins with 'image/'.
       - Inspects magic bytes and verifies size >= 500 bytes when Content-Length is absent.
       - Rejects HTML error pages (e.g. 403 hotlink blocks returning 200 HTML).
    
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

    # Check in-memory cache
    if sanitized in _VALIDATED_IMAGES_CACHE:
        return _VALIDATED_IMAGES_CACHE[sanitized]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 DeviceRankPublisher/1.0"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    try:
        # First attempt HEAD request
        resp = requests.head(sanitized, headers=headers, timeout=timeout_seconds, allow_redirects=True)

        content_length_val: Optional[int] = None
        content_type = (resp.headers.get("Content-Type") or "").lower().strip()

        # If HEAD fails or returns 405/403, fallback to streamed GET
        if resp.status_code in (405, 403) or not resp.headers.get("Content-Type"):
            resp = requests.get(sanitized, headers=headers, timeout=timeout_seconds, stream=True, allow_redirects=True)
            content_type = (resp.headers.get("Content-Type") or "").lower().strip()

        if resp.status_code not in (200, 304):
            logger.debug(f"Image validation failed for {sanitized}: HTTP {resp.status_code}")
            _VALIDATED_IMAGES_CACHE[sanitized] = None
            return None

        # If Content-Type is text/html or not an image, reject
        if content_type and not any(content_type.startswith(prefix) for prefix in VALID_IMAGE_MIME_PREFIXES):
            logger.debug(f"Image validation failed for {sanitized}: non-image Content-Type '{content_type}'")
            _VALIDATED_IMAGES_CACHE[sanitized] = None
            return None

        # Inspect Content-Length if present
        content_length = resp.headers.get("Content-Length")
        if content_length:
            try:
                content_length_val = int(content_length)
                if content_length_val < 500:
                    logger.debug(f"Image validation failed for {sanitized}: tiny image ({content_length_val} bytes)")
                    _VALIDATED_IMAGES_CACHE[sanitized] = None
                    return None
            except ValueError:
                pass

        # If Content-Length is missing or chunked, stream the first 4KB to verify magic bytes
        if content_length_val is None:
            get_resp = requests.get(sanitized, headers=headers, timeout=timeout_seconds, stream=True, allow_redirects=True)
            try:
                first_chunk = next(get_resp.iter_content(chunk_size=4096), b"")
                if len(first_chunk) < 500:
                    logger.debug(f"Image validation failed for {sanitized}: streamed payload < 500 bytes")
                    _VALIDATED_IMAGES_CACHE[sanitized] = None
                    return None

                if not _inspect_image_bytes(first_chunk):
                    # Also check if it's text/html error page
                    if b"<html" in first_chunk.lower() or b"<!doctype" in first_chunk.lower():
                        logger.debug(f"Image validation failed for {sanitized}: HTML page disguised as image")
                        _VALIDATED_IMAGES_CACHE[sanitized] = None
                        return None
            finally:
                get_resp.close()

        _VALIDATED_IMAGES_CACHE[sanitized] = sanitized
        return sanitized

    except Exception as e:
        logger.debug(f"Live image verification error for {sanitized}: {e}")
        _VALIDATED_IMAGES_CACHE[sanitized] = None
        return None

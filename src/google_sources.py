"""Official Google source loading, evidence extraction, and URL validation."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from config.settings import GOOGLE_SOURCES_CONFIG_PATH, settings
from src.fetchers.content_extractor import ContentExtractor
from src.utils.logger import logger


_OFFICIAL_GOOGLE_HOSTS = {
    "blog.google",
    "developers.google.com",
    "search.google.com",
    "support.google.com",
}


def is_official_google_url(url: str) -> bool:
    """Accept HTTPS URLs only on the official Google hosts used by this project."""
    try:
        parsed = urlparse(str(url).strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme.lower() == "https" and hostname in _OFFICIAL_GOOGLE_HOSTS
    except Exception:
        return False


class GoogleSource(BaseModel):
    """One editorially approved first-party Google documentation page."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    title: str = Field(min_length=8, max_length=140)
    url: str

    @field_validator("url")
    @classmethod
    def validate_official_url(cls, value: str) -> str:
        if not is_official_google_url(value):
            raise ValueError("Google sources must use an approved official HTTPS host")
        return value


class GoogleSourceCatalog(BaseModel):
    categories: Dict[str, List[GoogleSource]]


class GoogleEvidence(BaseModel):
    """Fetched evidence that can be supplied to the article writer."""

    title: str
    url: str
    excerpt: str

    @field_validator("url")
    @classmethod
    def validate_official_url(cls, value: str) -> str:
        if not is_official_google_url(value):
            raise ValueError("Evidence URLs must use an approved official Google HTTPS host")
        return value


def load_google_source_catalog(
    config_path: Path = GOOGLE_SOURCES_CONFIG_PATH,
) -> GoogleSourceCatalog:
    if not config_path.exists():
        raise FileNotFoundError(f"Google sources config not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as file_handle:
        return GoogleSourceCatalog.model_validate(json.load(file_handle))


def get_category_google_sources(
    category_key: str,
    limit: int = 3,
    config_path: Path = GOOGLE_SOURCES_CONFIG_PATH,
) -> List[GoogleSource]:
    catalog = load_google_source_catalog(config_path)
    return catalog.categories.get(category_key, [])[:limit]


def fetch_google_evidence(sources: List[GoogleSource]) -> List[GoogleEvidence]:
    """Fetch approved pages concurrently; omit pages without usable evidence."""
    if not sources:
        return []

    def fetch(source: GoogleSource) -> GoogleEvidence | None:
        if not is_official_google_url(source.url):
            return None
        extracted = ContentExtractor.extract(
            source.url,
            timeout=settings.http_timeout_seconds,
        )
        final_url = str(extracted.get("final_url") or source.url)
        if not is_official_google_url(final_url):
            logger.warning(f"Rejected Google source that redirected off the allowlist: {source.url}")
            return None
        excerpt = str(extracted.get("text") or extracted.get("meta_description") or "").strip()
        if not excerpt:
            logger.warning(f"No usable evidence extracted from approved Google source: {source.url}")
            return None
        return GoogleEvidence(title=source.title, url=source.url, excerpt=excerpt[:4500])

    workers = min(3, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(fetch, sources))
    return [result for result in results if result is not None]

"""Configuration loader and settings validation for DeviceRank AI Publisher."""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Base project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
FEEDS_CONFIG_PATH = CONFIG_DIR / "feeds.json"
EVERGREEN_TOPICS_CONFIG_PATH = CONFIG_DIR / "evergreen_topics.json"
GOOGLE_SOURCES_CONFIG_PATH = CONFIG_DIR / "google_sources.json"

# Load .env file from project root
load_dotenv(PROJECT_ROOT / ".env")


class FeedItem(BaseModel):
    name: str
    url: str
    enabled: bool = True


class CategoryConfig(BaseModel):
    name: str
    blogger_label: str
    description: str
    feeds: List[FeedItem]


class FeedsConfig(BaseModel):
    categories: Dict[str, CategoryConfig]


class Settings(BaseModel):
    # Gemini AI
    gemini_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    gemini_model: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-flash"))

    # Blogger
    blogger_blog_id: Optional[str] = Field(default_factory=lambda: os.getenv("BLOGGER_BLOG_ID"))
    blogger_client_id: Optional[str] = Field(
        default_factory=lambda: os.getenv("BLOGGER_CLIENT_ID")
    )
    blogger_client_secret: Optional[str] = Field(
        default_factory=lambda: os.getenv("BLOGGER_CLIENT_SECRET")
    )
    blogger_refresh_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("BLOGGER_REFRESH_TOKEN")
    )
    blogger_client_secret_file: str = Field(
        default_factory=lambda: os.getenv("BLOGGER_CLIENT_SECRET_FILE", "client_secret.json")
    )
    blogger_token_file: str = Field(
        default_factory=lambda: os.getenv("BLOGGER_TOKEN_FILE", "token.json")
    )

    # Publishing Preferences
    default_publish_status: str = Field(
        default_factory=lambda: os.getenv("DEFAULT_PUBLISH_STATUS", "DRAFT").upper()
    )
    max_posts_per_run: int = Field(
        default_factory=lambda: int(os.getenv("MAX_POSTS_PER_RUN", "1"))
    )
    target_word_count: int = Field(
        default_factory=lambda: int(os.getenv("TARGET_WORD_COUNT", "1000"))
    )
    evergreen_min_word_count: int = Field(
        default_factory=lambda: int(os.getenv("EVERGREEN_MIN_WORD_COUNT", "1200")),
        ge=900,
    )
    evergreen_quality_attempts: int = Field(
        default_factory=lambda: int(os.getenv("EVERGREEN_QUALITY_ATTEMPTS", "3")),
        ge=1,
        le=4,
    )
    evergreen_image_count: int = Field(
        default_factory=lambda: int(os.getenv("EVERGREEN_IMAGE_COUNT", "3")),
        ge=1,
        le=5,
    )
    allow_legacy_news_publishing: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_LEGACY_NEWS_PUBLISHING", "false").lower()
        in {"1", "true", "yes"}
    )
    digest_story_count: int = Field(
        default_factory=lambda: int(os.getenv("DIGEST_STORY_COUNT", "8")),
        ge=6,
        le=8,
    )
    digest_target_word_count: int = Field(
        default_factory=lambda: int(os.getenv("DIGEST_TARGET_WORD_COUNT", "1400")),
        ge=800,
    )

    # HTTP & Concurrency Settings
    http_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    )
    http_max_retries: int = Field(
        default_factory=lambda: int(os.getenv("HTTP_MAX_RETRIES", "3"))
    )
    max_concurrent_fetches: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_FETCHES", "5"))
    )

    # Optional Unsplash
    unsplash_access_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("UNSPLASH_ACCESS_KEY")
    )

    # Paths
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "history.db"

    def get_client_secret_path(self) -> Path:
        p = Path(self.blogger_client_secret_file)
        if not p.is_absolute():
            return self.project_root / p
        return p

    def get_token_path(self) -> Path:
        p = Path(self.blogger_token_file)
        if not p.is_absolute():
            return self.project_root / p
        return p


def load_feeds_config(config_path: Path = FEEDS_CONFIG_PATH) -> FeedsConfig:
    """Load feeds configuration from json."""
    if not config_path.exists():
        raise FileNotFoundError(f"Feeds configuration file not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return FeedsConfig(**data)


# Global singleton settings
settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)

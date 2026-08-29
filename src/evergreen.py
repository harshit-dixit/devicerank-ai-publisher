"""Curated evergreen topic loading, rotation, and internal-link selection."""

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from config.settings import EVERGREEN_TOPICS_CONFIG_PATH


EVERGREEN_SOURCE_PREFIX = "urn:devicerank:evergreen:"
_TEACHING_TITLE_WORDS = re.compile(
    r"\b(how to|checklist|guide|tips|fix|improve|build|create|set up|track|measure|read|choose|write|find|remove)\b",
    re.IGNORECASE,
)
_NEWS_TITLE_WORDS = re.compile(
    r"\b(breaking news|announces?|announced|launches?|launched|latest news|roundup|daily brief|weekly brief)\b",
    re.IGNORECASE,
)
_TOKEN_WORDS = re.compile(r"[a-z0-9]{3,}")
_STOP_WORDS = {
    "and", "are", "for", "from", "how", "into", "that", "the", "this", "tips",
    "using", "with", "without", "your",
}


class EvergreenTopic(BaseModel):
    """One editorially approved tutorial topic."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    title: str = Field(min_length=35, max_length=70)
    primary_keyword: str = Field(min_length=3, max_length=100)
    search_intent: str = Field(min_length=20)
    reader_problem: str = Field(min_length=20)
    outcome: str = Field(min_length=20)
    sections: List[str] = Field(min_length=4, max_length=8)

    @model_validator(mode="after")
    def validate_evergreen_title(self):
        if not _TEACHING_TITLE_WORDS.search(self.title):
            raise ValueError(f"Topic title must teach or solve a task: {self.title}")
        if _NEWS_TITLE_WORDS.search(self.title):
            raise ValueError(f"News-style title is not allowed: {self.title}")
        if re.search(r"\b20\d{2}\b", self.title):
            raise ValueError(f"Dated titles are not evergreen: {self.title}")
        return self

    @property
    def source_id(self) -> str:
        """Stable synthetic source used by the existing publication ledger."""
        return f"{EVERGREEN_SOURCE_PREFIX}{self.id}"


class EvergreenCategory(BaseModel):
    name: str
    blogger_label: str
    description: str
    topics: List[EvergreenTopic] = Field(min_length=1)


class EvergreenCatalog(BaseModel):
    categories: Dict[str, EvergreenCategory]

    @model_validator(mode="after")
    def validate_catalog(self):
        topic_ids = [topic.id for category in self.categories.values() for topic in category.topics]
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("Evergreen topic IDs must be unique across the catalog")
        return self


class SelectedEvergreenTopic(BaseModel):
    category_key: str
    category_name: str
    blogger_label: str
    category_description: str
    topic: EvergreenTopic


def load_evergreen_catalog(
    config_path: Path = EVERGREEN_TOPICS_CONFIG_PATH,
) -> EvergreenCatalog:
    """Load and validate the curated evergreen topic catalog."""
    if not config_path.exists():
        raise FileNotFoundError(f"Evergreen topics config not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as file_handle:
        return EvergreenCatalog.model_validate(json.load(file_handle))


def is_devicerank_url(url: str) -> bool:
    """Return true only for HTTPS links on the configured DeviceRank Blogger host."""
    try:
        parsed = urlparse(str(url).strip())
        return parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == "devicerank.blogspot.com"
    except Exception:
        return False


def iter_selected_topics(catalog: EvergreenCatalog) -> Iterable[SelectedEvergreenTopic]:
    for category_key, category in catalog.categories.items():
        for topic in category.topics:
            yield SelectedEvergreenTopic(
                category_key=category_key,
                category_name=category.name,
                blogger_label=category.blogger_label,
                category_description=category.description,
                topic=topic,
            )


def get_topic_by_id(
    catalog: EvergreenCatalog,
    topic_id: str,
) -> Optional[SelectedEvergreenTopic]:
    normalized = topic_id.strip().lower()
    return next(
        (item for item in iter_selected_topics(catalog) if item.topic.id == normalized),
        None,
    )


def select_next_topic(
    catalog: EvergreenCatalog,
    published_source_ids: Set[str],
    category_key: Optional[str] = None,
) -> Optional[SelectedEvergreenTopic]:
    """Choose the next unused topic while balancing output across categories."""
    if category_key:
        if category_key not in catalog.categories:
            valid = ", ".join(catalog.categories)
            raise ValueError(f"Unknown evergreen category '{category_key}'. Choose one of: {valid}")
        eligible_categories = [category_key]
    else:
        eligible_categories = list(catalog.categories)

    candidates: Dict[str, List[SelectedEvergreenTopic]] = {}
    used_counts: Dict[str, int] = {}
    for key in eligible_categories:
        category = catalog.categories[key]
        selected_items = [
            SelectedEvergreenTopic(
                category_key=key,
                category_name=category.name,
                blogger_label=category.blogger_label,
                category_description=category.description,
                topic=topic,
            )
            for topic in category.topics
        ]
        candidates[key] = [
            item for item in selected_items if item.topic.source_id not in published_source_ids
        ]
        used_counts[key] = len(selected_items) - len(candidates[key])

    available_keys = [key for key in eligible_categories if candidates[key]]
    if not available_keys:
        return None

    chosen_key = min(available_keys, key=lambda key: (used_counts[key], eligible_categories.index(key)))
    return candidates[chosen_key][0]


def select_relevant_internal_links(
    selected: SelectedEvergreenTopic,
    published_posts: List[Dict[str, str]],
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Rank trusted published posts by category and title overlap for internal linking."""
    topic_text = " ".join(
        [selected.topic.title, selected.topic.primary_keyword, *selected.topic.sections]
    ).lower()
    topic_tokens = {
        token for token in _TOKEN_WORDS.findall(topic_text) if token not in _STOP_WORDS
    }

    scored = []
    seen_urls = set()
    for index, post in enumerate(published_posts):
        title = str(post.get("title") or "").strip()
        url = str(post.get("blogger_url") or "").strip()
        category = str(post.get("category") or "").strip()
        if not title or not url or url in seen_urls:
            continue
        if not is_devicerank_url(url):
            continue

        title_tokens = {
            token for token in _TOKEN_WORDS.findall(title.lower()) if token not in _STOP_WORDS
        }
        overlap = len(topic_tokens & title_tokens)
        category_bonus = 4 if category == selected.category_key else 0
        score = category_bonus + overlap
        if score <= 0:
            continue
        seen_urls.add(url)
        scored.append((score, -index, {"title": title, "blogger_url": url, "category": category}))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit]]

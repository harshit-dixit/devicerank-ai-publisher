"""Story ranking, scoring, and candidate selection engine for DeviceRank AI Publisher."""

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


def _parse_datetime(date_val: Any) -> Optional[datetime]:
    """Attempts to parse various datetime formats into a UTC-aware datetime."""
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            return date_val.replace(tzinfo=timezone.utc)
        return date_val.astimezone(timezone.utc)

    s = str(date_val).strip()
    # Try ISO format
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Try email/RFC 2822 format
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    return None


def _tokenize_title(title: str) -> Set[str]:
    """Tokenizes a title into lower-cased significant words."""
    words = re.findall(r"\b[a-z0-9]{3,}\b", title.lower())
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "how", "what",
        "why", "when", "where", "new", "top", "best", "will", "your", "into"
    }
    return {w for w in words if w not in stop_words}


def _title_similarity(title1: str, title2: str) -> float:
    """Calculates Jaccard word similarity between two titles."""
    t1 = _tokenize_title(title1)
    t2 = _tokenize_title(title2)
    if not t1 or not t2:
        return 0.0
    intersection = t1.intersection(t2)
    union = t1.union(t2)
    return len(intersection) / len(union)


class StoryRanker:
    """Multi-factor scoring and ranking system for candidate stories."""

    @staticmethod
    def calculate_freshness_score(published_date: Optional[datetime]) -> float:
        """Calculates freshness score (0.0 to 1.0) with exponential time decay."""
        if not published_date:
            return 0.35  # Moderate fallback for undated feeds

        now = datetime.now(timezone.utc)
        age_hours = (now - published_date).total_seconds() / 3600.0

        if age_hours < 0:
            # Future date or clock skew
            return 1.0
        elif age_hours <= 3.0:
            return 1.0
        elif age_hours <= 6.0:
            return 0.90
        elif age_hours <= 12.0:
            return 0.75
        elif age_hours <= 24.0:
            return 0.55
        elif age_hours <= 48.0:
            return 0.30
        elif age_hours <= 96.0:
            return 0.15
        else:
            return 0.05

    @staticmethod
    def calculate_richness_score(
        has_image: bool,
        has_full_text: bool,
        summary_length: int,
    ) -> float:
        """Scores content completeness and suitability for rich SEO generation."""
        score = 0.30  # Baseline
        if has_image:
            score += 0.30
        if has_full_text:
            score += 0.25
        if summary_length >= 250:
            score += 0.15
        elif summary_length >= 100:
            score += 0.08
        return min(1.0, score)

    @classmethod
    def score_candidate(
        cls,
        candidate: Any,
        selected_sources: Dict[str, int],
        seen_titles: List[str],
    ) -> float:
        """Computes comprehensive score for a candidate story."""
        # Normalize attributes (dict or object)
        def get_val(item, key, default=None):
            if isinstance(item, dict):
                return item.get(key, default)
            return getattr(item, key, default)

        title = str(get_val(candidate, "title", ""))
        source_name = str(get_val(candidate, "source_name", "Unknown"))
        pub_val = get_val(candidate, "published_date")
        image_url = get_val(candidate, "image_url")
        full_text = get_val(candidate, "full_text")
        summary = str(get_val(candidate, "summary", ""))

        dt = _parse_datetime(pub_val)
        freshness = cls.calculate_freshness_score(dt)
        richness = cls.calculate_richness_score(
            has_image=bool(image_url),
            has_full_text=bool(full_text and len(str(full_text)) > 300),
            summary_length=len(summary),
        )

        base_score = (0.50 * freshness) + (0.50 * richness)

        # Source diversity penalty (discourages multiple articles from same feed in one batch)
        source_count = selected_sources.get(source_name, 0)
        diversity_penalty = source_count * 0.40

        # Duplication penalty against previously selected titles
        duplication_penalty = 0.0
        for seen in seen_titles:
            sim = _title_similarity(title, seen)
            if sim >= 0.60:
                duplication_penalty = max(duplication_penalty, 0.85)
            elif sim >= 0.40:
                duplication_penalty = max(duplication_penalty, 0.40)

        final_score = max(0.0, base_score - diversity_penalty - duplication_penalty)
        return round(final_score, 4)

    @classmethod
    def rank_and_select(
        cls,
        candidates: List[Any],
        limit: int = 1,
        max_per_source: int = 2,
    ) -> List[Tuple[Any, float]]:
        """Ranks candidates dynamically, ensuring source diversity and freshness.

        Returns list of (candidate, score) tuples for top selections.
        """
        if not candidates or limit <= 0:
            return []

        remaining = list(candidates)
        selected: List[Tuple[Any, float]] = []
        selected_sources: Dict[str, int] = {}
        seen_titles: List[str] = []

        while remaining and len(selected) < limit:
            best_item = None
            best_score = -1.0
            best_idx = -1

            for idx, item in enumerate(remaining):
                source = (
                    item.get("source_name", "")
                    if isinstance(item, dict)
                    else getattr(item, "source_name", "")
                )
                if selected_sources.get(source, 0) >= max_per_source:
                    continue

                score = cls.score_candidate(item, selected_sources, seen_titles)
                if score > best_score:
                    best_score = score
                    best_item = item
                    best_idx = idx

            if best_item is None:
                # If all remaining hit max_per_source cap, relax cap if needed
                if remaining:
                    best_idx = 0
                    best_item = remaining[0]
                    best_score = cls.score_candidate(best_item, selected_sources, seen_titles)
                else:
                    break

            # Select best item
            remaining.pop(best_idx)
            selected.append((best_item, best_score))

            title = (
                best_item.get("title", "")
                if isinstance(best_item, dict)
                else getattr(best_item, "title", "")
            )
            source = (
                best_item.get("source_name", "")
                if isinstance(best_item, dict)
                else getattr(best_item, "source_name", "")
            )
            seen_titles.append(title)
            selected_sources[source] = selected_sources.get(source, 0) + 1

        return selected

    @classmethod
    def select_latest(
        cls,
        candidates: List[Any],
        limit: int = 8,
        max_per_source: int = 2,
    ) -> List[Tuple[Any, float]]:
        """Selects the newest stories first while preserving reasonable source diversity."""
        if not candidates or limit <= 0:
            return []

        indexed = list(enumerate(candidates))

        def newest_first_key(pair):
            index, item = pair
            published = item.get("published_date") if isinstance(item, dict) else getattr(item, "published_date", None)
            parsed = _parse_datetime(published)
            return (
                parsed is not None,
                parsed.timestamp() if parsed else float("-inf"),
                -index,
            )

        ordered = sorted(indexed, key=newest_first_key, reverse=True)
        selected: List[Tuple[Any, float]] = []
        selected_indices: Set[int] = set()
        selected_sources: Dict[str, int] = {}
        seen_titles: List[str] = []

        def add_candidate(index: int, item: Any):
            source = item.get("source_name", "") if isinstance(item, dict) else getattr(item, "source_name", "")
            score = cls.score_candidate(item, selected_sources, seen_titles)
            selected.append((item, score))
            selected_indices.add(index)
            selected_sources[source] = selected_sources.get(source, 0) + 1
            title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
            seen_titles.append(title)

        for index, item in ordered:
            source = item.get("source_name", "") if isinstance(item, dict) else getattr(item, "source_name", "")
            if selected_sources.get(source, 0) >= max_per_source:
                continue
            add_candidate(index, item)
            if len(selected) >= limit:
                break

        # If diversity limits leave the digest short, fill it with the newest remaining stories.
        if len(selected) < limit:
            for index, item in ordered:
                if index in selected_indices:
                    continue
                add_candidate(index, item)
                if len(selected) >= limit:
                    break

        # The diversity pass can select an older item before a fallback item. Present the
        # final batch in true newest-first order for the digest prompt and rendered post.
        selected.sort(
            key=lambda pair: (
                _parse_datetime(
                    pair[0].get("published_date")
                    if isinstance(pair[0], dict)
                    else getattr(pair[0], "published_date", None)
                )
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )

        return selected

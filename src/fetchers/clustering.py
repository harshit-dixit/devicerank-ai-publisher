"""Semantic topic clustering, anti-chaining grouping, and cross-run topic fingerprinting for DeviceRank."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from src.fetchers.rss_fetcher import RawArticle


# Comprehensive English stop words (pronouns, prepositions, conjunctions, auxiliaries)
ENGLISH_STOP_WORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "also", "just", "like", "make", "many", "much",
    "well", "back", "even", "still", "way", "take", "come", "get", "see", "know",
    "look", "first", "new", "top", "good", "great", "last", "long", "little",
}

# Generic tech and common news words that must NEVER independently trigger a cluster match
GENERIC_TECH_STOP_WORDS: Set[str] = {
    "ai", "google", "apple", "microsoft", "amazon", "meta", "samsung", "nvidia",
    "intel", "amd", "openai", "agent", "agents", "model", "models", "software",
    "hardware", "app", "apps", "marketing", "search", "report", "reports",
    "launch", "launches", "update", "updates", "feature", "features", "tool",
    "tools", "data", "platform", "platforms", "system", "systems", "service",
    "services", "news", "review", "reviews", "dispute", "disputes", "mode",
    "redesign", "docs", "documentation", "ecommerce", "statistics", "ad",
    "ads", "advertising", "wix", "org", "tech", "technology", "today", "yesterday",
    "week", "month", "year", "users", "user", "device", "devices", "digital",
    "strategy", "guide", "tips", "details", "spotted", "revealed", "reveals",
    "expected", "coming", "comes", "posts", "post", "says", "said", "announced",
    "announces", "unveiled", "unveils", "brings", "bring", "pack", "packs", "shows",
    "show", "best", "ways", "how", "what", "why", "splits", "versus", "vs",
    "phone", "phones", "smartphone", "smartphones", "laptop", "laptops",
}

ALL_STOP_WORDS = ENGLISH_STOP_WORDS.union(GENERIC_TECH_STOP_WORDS)


def extract_specific_entity_tokens(title: str, summary: str = "") -> Set[str]:
    """Extracts specific model numbers, product names, acronyms, and distinct entities.
    
    Filters out broad generic words (e.g. 'google', 'ai', 'search') and common English
    stop words so that two articles sharing only generic words are NEVER clustered together.
    """
    text = f"{title} {summary}".lower()
    raw_tokens = re.findall(r"\b[a-z0-9][a-z0-9\.\-]{1,}[a-z0-9]\b|\b[a-z0-9]{2,}\b", text)
    specific_tokens: Set[str] = set()

    for tok in raw_tokens:
        cleaned = tok.strip(".-")
        if not cleaned:
            continue
        if cleaned in ALL_STOP_WORDS:
            continue
        if len(cleaned) < 2:
            continue
        # 2-letter tokens are only allowed if they contain a digit or are known product codes
        if len(cleaned) == 2 and not any(c.isdigit() for c in cleaned) and cleaned not in {"fe", "se", "xr", "xs", "gt", "fx", "rx", "tx", "os", "ip", "vr", "ar", "mr"}:
            continue
        specific_tokens.add(cleaned)

    return specific_tokens


def extract_title_entities(title: str) -> Set[str]:
    """Extracts high-priority entity tokens specifically from the article headline."""
    title_lower = title.lower()
    tokens = re.findall(r"\b[a-z0-9][a-z0-9\.\-]{1,}[a-z0-9]\b|\b[a-z0-9]{2,}\b", title_lower)
    result = set()
    for t in tokens:
        cleaned = t.strip(".-")
        if cleaned and cleaned not in ALL_STOP_WORDS and len(cleaned) >= 2:
            if len(cleaned) == 2 and not any(c.isdigit() for c in cleaned) and cleaned not in {"fe", "se", "xr", "xs", "gt", "fx", "rx", "tx", "os", "ip", "vr", "ar", "mr"}:
                continue
            result.add(cleaned)
    return result


def generate_topic_fingerprint(title: str, summary: str = "") -> str:
    """Generates a stable normalized topic fingerprint for 48-72h deduplication."""
    title_entities = extract_title_entities(title)
    if not title_entities:
        all_entities = extract_specific_entity_tokens(title, summary)
        title_entities = all_entities

    if not title_entities:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower().strip()).strip("-")
        return slug[:40]

    sorted_tokens = sorted(title_entities)[:4]
    return "-".join(sorted_tokens)


def calculate_strict_similarity(
    art1_tokens: Set[str],
    art2_tokens: Set[str],
    art1_title_entities: Set[str],
    art2_title_entities: Set[str],
) -> float:
    """Calculates strict similarity between two articles without token-union chaining.
    
    Requires substantial overlap in specific entity tokens (not generic tech words).
    """
    if not art1_tokens or not art2_tokens:
        return 0.0

    # 1. Headline entity overlap (highest confidence signal)
    if art1_title_entities and art2_title_entities:
        title_inter = art1_title_entities.intersection(art2_title_entities)
        title_min = min(len(art1_title_entities), len(art2_title_entities))
        if title_min > 0:
            title_ratio = len(title_inter) / title_min
            # If at least 2 distinct specific entities match in title, or 60%+ title entities match:
            if len(title_inter) >= 2 or (len(title_inter) >= 1 and title_ratio >= 0.60):
                return 0.90

    # 2. Body + Title specific entity overlap
    inter = art1_tokens.intersection(art2_tokens)
    union = art1_tokens.union(art2_tokens)
    jaccard = len(inter) / len(union) if union else 0.0
    overlap = len(inter) / min(len(art1_tokens), len(art2_tokens))

    # Strict requirement: must share at least 2 specific non-generic entity tokens
    if len(inter) < 2:
        return 0.0

    # Weighted similarity
    sim = (0.40 * jaccard) + (0.60 * overlap)
    return sim


@dataclass
class StoryCluster:
    """A cluster of one or more source articles covering the same specific product or event."""

    canonical_article: RawArticle
    articles: List[RawArticle] = field(default_factory=list)
    canonical_tokens: Set[str] = field(default_factory=set)
    canonical_title_entities: Set[str] = field(default_factory=set)
    fingerprint: str = ""

    @property
    def source_names(self) -> List[str]:
        """Returns unique list of source outlet names in this cluster."""
        names = []
        for art in self.articles:
            if art.source_name and art.source_name not in names:
                names.append(art.source_name)
        return names

    @property
    def source_urls(self) -> List[str]:
        """Returns unique list of source URLs in this cluster."""
        urls = []
        for art in self.articles:
            if art.link and art.link not in urls:
                urls.append(art.link)
        return urls

    @property
    def combined_summary(self) -> str:
        """Synthesizes summaries from all articles in the cluster."""
        summaries = [art.summary.strip() for art in self.articles if art.summary and art.summary.strip()]
        return " | ".join(dict.fromkeys(summaries))

    @property
    def combined_full_text(self) -> Optional[str]:
        """Synthesizes full text from all articles in the cluster for rich context."""
        texts = [art.full_text.strip() for art in self.articles if art.full_text and art.full_text.strip()]
        if not texts:
            return None
        return "\n\n---\n\n".join(texts[:3])

    def add_article(self, article: RawArticle):
        """Adds an article to this cluster and preserves canonical representation."""
        if article.link in self.source_urls:
            return
        self.articles.append(article)

        # Check if the incoming article is a better canonical article (has verified image or longer text)
        canon_richness = (
            (1.0 if self.canonical_article.image_url else 0.0)
            + (1.0 if self.canonical_article.full_text else 0.0)
            + (len(self.canonical_article.summary) / 500.0)
        )
        new_richness = (
            (1.0 if article.image_url else 0.0)
            + (1.0 if article.full_text else 0.0)
            + (len(article.summary) / 500.0)
        )
        if new_richness > canon_richness:
            self.canonical_article = article
            self.canonical_tokens = extract_specific_entity_tokens(article.title, article.summary)
            self.canonical_title_entities = extract_title_entities(article.title)
            self.fingerprint = generate_topic_fingerprint(article.title, article.summary)


class TopicClusterer:
    """Groups candidate articles into distinct semantic story clusters with anti-chaining protection."""

    @staticmethod
    def cluster_articles(
        articles: List[RawArticle],
        similarity_threshold: float = 0.55,
    ) -> List[StoryCluster]:
        """Clusters raw articles into distinct topic clusters.
        
        Articles covering the exact same event or product across different feeds
        are merged into a single StoryCluster. Unrelated stories sharing only
        broad generic words (e.g. 'google', 'ai', 'marketing') are NEVER merged.
        """
        clusters: List[StoryCluster] = []

        for article in articles:
            tokens = extract_specific_entity_tokens(article.title, article.summary)
            title_entities = extract_title_entities(article.title)
            fingerprint = generate_topic_fingerprint(article.title, article.summary)

            if not tokens and not title_entities:
                cluster = StoryCluster(
                    canonical_article=article,
                    articles=[article],
                    canonical_tokens=tokens,
                    canonical_title_entities=title_entities,
                    fingerprint=fingerprint,
                )
                clusters.append(cluster)
                continue

            best_cluster: Optional[StoryCluster] = None
            best_sim = -1.0

            for cluster in clusters:
                # Anti-chaining: compare strictly against the canonical article of the cluster,
                # NOT a growing union of all tokens from previous merged members.
                sim = calculate_strict_similarity(
                    tokens,
                    cluster.canonical_tokens,
                    title_entities,
                    cluster.canonical_title_entities,
                )

                if sim > best_sim:
                    best_sim = sim
                    best_cluster = cluster

            if best_cluster is not None and best_sim >= similarity_threshold:
                # Merge into existing cluster
                best_cluster.add_article(article)
            else:
                # Create new cluster
                new_cluster = StoryCluster(
                    canonical_article=article,
                    articles=[article],
                    canonical_tokens=tokens,
                    canonical_title_entities=title_entities,
                    fingerprint=fingerprint,
                )
                clusters.append(new_cluster)

        return clusters

    @staticmethod
    def filter_by_recent_fingerprints(
        clusters: List[StoryCluster],
        recent_fingerprints: Set[str],
    ) -> List[StoryCluster]:
        """Filters out candidate clusters whose entity fingerprints were published in the last 48-72 hours."""
        if not recent_fingerprints:
            return clusters

        filtered: List[StoryCluster] = []
        for cluster in clusters:
            fp = cluster.fingerprint.lower()
            if fp in recent_fingerprints:
                continue

            tokens = cluster.canonical_title_entities
            if tokens and any(fp_item in recent_fingerprints for fp_item in [fp, "-".join(sorted(tokens))]):
                continue

            filtered.append(cluster)

        return filtered

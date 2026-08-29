"""Semantic topic clustering and multi-source story aggregation for DeviceRank AI Publisher."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from src.fetchers.rss_fetcher import RawArticle


# Common English and generic news stop words to ignore during clustering
STOP_WORDS: Set[str] = {
    "the", "and", "for", "with", "this", "that", "from", "how", "what",
    "why", "when", "where", "new", "top", "best", "will", "your", "into",
    "about", "over", "after", "says", "said", "report", "reports", "could",
    "more", "first", "here", "just", "like", "make", "than", "them", "then",
    "they", "their", "some", "other", "many", "most", "also", "have", "has",
    "been", "were", "tech", "news", "review", "launch", "announces", "announced",
}


def extract_topic_tokens(title: str, summary: str = "") -> Set[str]:
    """Extracts normalized alphanumeric entities and significant tokens from title and summary."""
    text = f"{title} {summary}".lower()
    # Match words and alphanumeric entity tokens (e.g. s26, m5, 18b, rtx5090)
    tokens = re.findall(r"\b[a-z0-9][a-z0-9\.\-]{1,}[a-z0-9]\b|\b[a-z0-9]{2,}\b", text)
    cleaned = set()
    for tok in tokens:
        t = tok.strip(".-")
        if len(t) >= 2 and t not in STOP_WORDS and not t.isdigit():
            cleaned.add(t)
        elif t.isdigit() and len(t) >= 2:
            # Keep numbers like model numbers / dollar figures (e.g. 18, 5090)
            cleaned.add(t)
    return cleaned


def calculate_cluster_similarity(
    tokens1: Set[str],
    tokens2: Set[str],
    title_tokens1: Optional[Set[str]] = None,
    title_tokens2: Optional[Set[str]] = None,
) -> float:
    """Calculates Jaccard similarity and token overlap between two token sets with title priority."""
    if not tokens1 or not tokens2:
        return 0.0

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    jaccard = len(intersection) / len(union) if union else 0.0

    min_len = min(len(tokens1), len(tokens2))
    overlap_ratio = len(intersection) / max(1, min_len)

    # Base weighted token similarity
    sim = (0.35 * jaccard) + (0.65 * overlap_ratio)

    # If title tokens strongly overlap (e.g. 'galaxy', 's26', 'fe'), boost similarity
    if title_tokens1 and title_tokens2:
        title_inter = title_tokens1.intersection(title_tokens2)
        title_min = min(len(title_tokens1), len(title_tokens2))
        if title_min > 0:
            title_ratio = len(title_inter) / title_min
            if title_ratio >= 0.40:
                sim = max(sim, 0.35 + (0.50 * title_ratio))

    return sim


@dataclass
class StoryCluster:
    """A cluster of one or more source articles covering the same product or event."""

    canonical_article: RawArticle
    articles: List[RawArticle] = field(default_factory=list)
    tokens: Set[str] = field(default_factory=set)
    title_tokens: Set[str] = field(default_factory=set)

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
        """Adds an article to this cluster and updates canonical representation if richer."""
        if article.link in self.source_urls:
            return
        self.articles.append(article)
        art_tokens = extract_topic_tokens(article.title, article.summary)
        art_title_tokens = extract_topic_tokens(article.title)
        self.tokens.update(art_tokens)
        self.title_tokens.update(art_title_tokens)

        # Check if the incoming article is a better canonical article (has image or longer text)
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


class TopicClusterer:
    """Groups candidate articles into distinct semantic story clusters."""

    @staticmethod
    def cluster_articles(
        articles: List[RawArticle],
        similarity_threshold: float = 0.32,
    ) -> List[StoryCluster]:
        """Clusters a list of raw articles into distinct topic clusters.
        
        Articles covering the exact same event or product across different feeds
        are merged into a single StoryCluster.
        """
        clusters: List[StoryCluster] = []

        for article in articles:
            tokens = extract_topic_tokens(article.title, article.summary)
            title_tokens = extract_topic_tokens(article.title)
            if not tokens:
                cluster = StoryCluster(
                    canonical_article=article,
                    articles=[article],
                    tokens=tokens,
                    title_tokens=title_tokens,
                )
                clusters.append(cluster)
                continue

            best_cluster: Optional[StoryCluster] = None
            best_sim = -1.0

            for cluster in clusters:
                # Compare against cluster tokens as well as each member article's tokens
                sim_cluster = calculate_cluster_similarity(
                    tokens, cluster.tokens, title_tokens, cluster.title_tokens
                )
                member_sims = [
                    calculate_cluster_similarity(
                        tokens,
                        extract_topic_tokens(a.title, a.summary),
                        title_tokens,
                        extract_topic_tokens(a.title),
                    )
                    for a in cluster.articles
                ]
                sim = max(sim_cluster, max(member_sims, default=0.0))

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
                    tokens=tokens,
                    title_tokens=title_tokens,
                )
                clusters.append(new_cluster)

        return clusters

"""DeviceRank AI Publisher CLI Application.

Unified entry point for RSS fetching, candidate ranking, Gemini SEO generation,
Blogger publishing, and automated orchestration.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import typer
from rich.panel import Panel
from rich.table import Table
from config.settings import load_feeds_config, settings
from src.agents.seo_writer import SEOWriter
from src.db.history import StoryStatus, history_db
from src.evergreen import (
    EVERGREEN_SOURCE_PREFIX,
    get_topic_by_id,
    iter_selected_topics,
    load_evergreen_catalog,
    select_next_topic,
    select_relevant_internal_links,
)
from src.fetchers.clustering import TopicClusterer
from src.fetchers.ranking import StoryRanker
from src.fetchers.rss_fetcher import RSSFetcher, RawArticle
from src.google_sources import fetch_google_evidence, get_category_google_sources
from src.publishers.blogger_client import BloggerClient
from src.publishers.oauth_helper import authenticate_blogger_oauth, export_github_secrets_info
from src.utils.logger import console, display_articles_table, logger, print_banner
from src.utils.slots import SlotInfo, SlotType, get_current_slot

app = typer.Typer(
    name="devicerank",
    help="DeviceRank Evergreen Publisher - Helpful tutorial publishing for Blogger",
    add_completion=False,
)

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def _resolve_evergreen_slot(slot: str, now_utc: Optional[datetime] = None) -> tuple[str, str]:
    """Return the IST calendar date and one of the two daily publishing slots."""
    normalized = slot.strip().lower()
    if normalized not in {"auto", "morning", "evening"}:
        raise typer.BadParameter("--slot must be auto, morning, or evening")
    local_now = (now_utc or datetime.now(timezone.utc)).astimezone(IST)
    resolved_slot = normalized
    if normalized == "auto":
        resolved_slot = "morning" if local_now.hour < 14 else "evening"
    return local_now.date().isoformat(), resolved_slot


@app.command()
def categories():
    """List the approved evergreen publishing categories."""
    print_banner()
    config = load_evergreen_catalog()

    table = Table(title="Approved Evergreen Categories", header_style="bold magenta")
    table.add_column("Key", style="cyan", width=24)
    table.add_column("Display Name", style="white", width=24)
    table.add_column("Blogger Label", style="green", width=28)
    table.add_column("Topics", style="yellow", width=8)
    table.add_column("Scope", style="white", min_width=40)

    for key, cat in config.categories.items():
        table.add_row(key, cat.name, cat.blogger_label, str(len(cat.topics)), cat.description)

    console.print(table)


@app.command(hidden=True)
def fetch(
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="Category key (tech_news, seo_tips, gadgets, monetization)"
    ),
    limit: int = typer.Option(5, "--limit", "-l", help="Number of ranked candidates to display"),
    include_processed: bool = typer.Option(
        False, "--all", "-a", help="Include already processed articles"
    ),
):
    """Fetch feeds into the queue and display top-ranked candidate stories."""
    print_banner()
    fetcher = RSSFetcher()
    dedup = not include_processed

    if category:
        articles = fetcher.fetch_category(category, max_items=10, deduplicate=dedup)
        ranked = StoryRanker.rank_and_select(articles, limit=limit)
        _display_ranked_table(ranked, title=f"Top Ranked Candidates: {category}")
    else:
        results = fetcher.fetch_all(max_per_category=5, deduplicate=dedup)
        for cat_key, arts in results.items():
            ranked = StoryRanker.rank_and_select(arts, limit=limit)
            _display_ranked_table(ranked, title=f"Top Ranked Candidates: {cat_key}")


def _display_ranked_table(ranked_items, title: str):
    """Displays ranked articles with calculated scores."""
    if not ranked_items:
        console.print(f"[dim]No candidate articles found for {title}.[/dim]")
        return

    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", width=3)
    table.add_column("Score", style="bold green", width=7)
    table.add_column("Source", style="yellow", width=16)
    table.add_column("Title", style="white", min_width=30)
    table.add_column("Published", style="dim", width=18)

    for idx, (item, score) in enumerate(ranked_items, 1):
        title_val = item.get("title") if isinstance(item, dict) else getattr(item, "title", "")
        source_val = item.get("source_name") if isinstance(item, dict) else getattr(item, "source_name", "")
        pub_val = item.get("published_date") if isinstance(item, dict) else getattr(item, "published_date", "")
        pub_str = str(pub_val)[:16] if pub_val else "N/A"
        table.add_row(str(idx), f"{score:.2f}", source_val, title_val[:60], pub_str)

    console.print(table)


def _raw_article_from_queue(item) -> RawArticle:
    """Converts a persisted queue record back into the fetcher's article model."""
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        try:
            raw_tags = json.loads(raw_tags)
        except json.JSONDecodeError:
            raw_tags = []

    return RawArticle(
        title=item["title"],
        link=item["source_url"],
        source_name=item["source_name"],
        category=item["category"],
        blogger_label=item.get("blogger_label") or item["category"],
        published_date=item.get("published_date"),
        raw_published_date=item.get("raw_published_date"),
        summary=item.get("summary") or "",
        full_text=item.get("full_text"),
        image_url=item.get("image_url"),
        tags=raw_tags if isinstance(raw_tags, list) else [],
    )


def _require_legacy_news_publishing_enabled() -> None:
    """Prevent accidental return to RSS/news publishing after the evergreen migration."""
    if not settings.allow_legacy_news_publishing:
        raise typer.BadParameter(
            "Legacy RSS/news publishing is disabled. Use 'run-evergreen'. "
            "Set ALLOW_LEGACY_NEWS_PUBLISHING=true only for an intentional one-off migration."
        )


@app.command(name="evergreen-topics")
def evergreen_topics(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        "-c",
        help="Optional evergreen category key",
    ),
):
    """List the approved evergreen tutorial topics and their publication state."""
    print_banner()
    catalog = load_evergreen_catalog()
    if category and category not in catalog.categories:
        valid = ", ".join(catalog.categories)
        raise typer.BadParameter(f"Unknown evergreen category '{category}'. Choose one of: {valid}")

    published_ids = history_db.get_published_source_ids(EVERGREEN_SOURCE_PREFIX)
    table = Table(title="Approved Evergreen Topics", header_style="bold magenta")
    table.add_column("Status", width=10)
    table.add_column("Category", width=24)
    table.add_column("Topic ID", width=32)
    table.add_column("Teaching Title", min_width=44)

    for selected in iter_selected_topics(catalog):
        if category and selected.category_key != category:
            continue
        used = selected.topic.source_id in published_ids
        table.add_row(
            "[green]Used[/green]" if used else "[cyan]Ready[/cyan]",
            selected.category_name,
            selected.topic.id,
            selected.topic.title,
        )
    console.print(table)


@app.command(name="run-evergreen")
def run_evergreen(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        "-c",
        help="Optional category key; omit for balanced category rotation",
    ),
    topic_id: Optional[str] = typer.Option(
        None,
        "--topic-id",
        help="Generate one exact approved topic instead of selecting the next unused topic",
    ),
    publish: bool = typer.Option(
        True,
        "--publish/--no-publish",
        help="Publish to Blogger or only create a local preview",
    ),
    draft: bool = typer.Option(
        True,
        "--draft/--live",
        help="Publish as Draft (default) or Live",
    ),
    save_html: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save the generated Blogger HTML to output/",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Idempotency ID; defaults to the IST date plus morning/evening slot",
    ),
    slot: str = typer.Option(
        "auto",
        "--slot",
        help="Publishing slot: auto, morning, or evening",
    ),
):
    """Generate and optionally publish one approved evergreen tutorial."""
    print_banner()
    catalog = load_evergreen_catalog()
    if category and category not in catalog.categories:
        valid = ", ".join(catalog.categories)
        raise typer.BadParameter(f"Unknown evergreen category '{category}'. Choose one of: {valid}")

    ist_date, resolved_slot = _resolve_evergreen_slot(slot)
    resolved_run_id = run_id.strip() if run_id and run_id.strip() else (
        f"{ist_date}-{resolved_slot}-evergreen" if publish else None
    )
    blogger: Optional[BloggerClient] = None
    if publish:
        blogger = BloggerClient()
        synced = blogger.sync_remote_ledger(max_posts=150)
        if synced:
            console.print(f"[dim]Synchronized {synced} Blogger posts for deduplication and internal links.[/dim]")

        if resolved_run_id and history_db.is_slot_published(resolved_run_id, live_only=True):
            console.print(f"[yellow]Run '{resolved_run_id}' is already live. No duplicate was created.[/yellow]")
            return
        if resolved_run_id and history_db.is_slot_draft(resolved_run_id):
            existing = history_db.get_slot_post(resolved_run_id)
            existing_id = existing.get("blogger_post_id") if existing else None
            if not draft and existing_id:
                promoted = blogger.publish_draft_post(
                    existing_id,
                    minimum_image_count=settings.evergreen_image_count,
                )
                history_db.sync_remote_post(
                    blogger_post_id=existing_id,
                    title=existing.get("title", ""),
                    category=existing.get("category", "evergreen"),
                    slot_id=resolved_run_id,
                    blogger_url=promoted.get("url"),
                    status="LIVE",
                )
                console.print(f"[bold green]Published existing draft:[/bold green] {promoted.get('url')}")
                return
            console.print(f"[yellow]Draft for run '{resolved_run_id}' already exists. No duplicate was created.[/yellow]")
            return

    published_ids = history_db.get_published_source_ids(EVERGREEN_SOURCE_PREFIX)
    if topic_id:
        selected = get_topic_by_id(catalog, topic_id)
        if not selected:
            raise typer.BadParameter(f"Unknown evergreen topic ID '{topic_id}'.")
        if category and selected.category_key != category:
            raise typer.BadParameter(
                f"Topic '{topic_id}' belongs to '{selected.category_key}', not '{category}'."
            )
        if publish and selected.topic.source_id in published_ids:
            console.print(f"[yellow]Topic '{topic_id}' is already recorded as published. Skipping.[/yellow]")
            return
    else:
        try:
            selected = select_next_topic(catalog, published_ids, category_key=category)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not selected:
            console.print(
                "[yellow]No unused approved topics remain in this scope. Add new topics to "
                "config/evergreen_topics.json before publishing again.[/yellow]"
            )
            return

    console.print(
        Panel(
            f"[bold white]{selected.topic.title}[/bold white]\n"
            f"[dim]Category: {selected.category_name} | Keyword: {selected.topic.primary_keyword}[/dim]\n"
            f"[dim]Outcome: {selected.topic.outcome}[/dim]",
            title="Selected Evergreen Tutorial",
            border_style="green",
        )
    )

    link_candidates = history_db.get_published_articles_for_linking(limit=100)
    internal_links = select_relevant_internal_links(selected, link_candidates, limit=3)
    approved_google_sources = get_category_google_sources(selected.category_key, limit=3)
    google_evidence = fetch_google_evidence(approved_google_sources)
    if google_evidence:
        console.print(
            f"[dim]Grounded with {len(google_evidence)} fetched official Google source(s).[/dim]"
        )
    else:
        console.print(
            "[yellow]No official Google source evidence could be fetched; outbound citations "
            "are disabled for this article.[/yellow]"
        )
    writer = SEOWriter()
    generated = writer.write_evergreen(
        selected,
        internal_links=internal_links,
        google_sources=google_evidence,
        required_image_count=settings.evergreen_image_count if publish else 0,
    )

    preview_path = None
    if save_html:
        output_dir = settings.project_root / "output"
        output_dir.mkdir(exist_ok=True)
        preview_path = output_dir / f"evergreen_{selected.topic.id}.html"
        with open(preview_path, "w", encoding="utf-8") as preview_file:
            preview_file.write(f"<!-- Title: {generated.title} -->\n")
            preview_file.write(f"<!-- Meta Description: {generated.meta_description} -->\n")
            preview_file.write(f"<!-- Focus Keyword: {generated.focus_keyword} -->\n")
            preview_file.write(f"<!-- Labels: {', '.join(generated.labels)} -->\n\n")
            preview_file.write(generated.html_content)
        console.print(f"[cyan]Preview saved:[/cyan] {preview_path}")

    result = None
    if publish:
        result = blogger.publish_post(
            generated,
            is_draft=draft,
            slot_id=resolved_run_id,
            topic_fingerprints=[selected.topic.id],
        )

    status_text = "PREVIEW" if not publish else ("DRAFT" if draft else "LIVE")
    result_url = result.get("url") if result else None
    console.print(
        f"\n[bold green]{status_text} evergreen tutorial ready.[/bold green]\n"
        f"[bold]Title:[/bold] {generated.title}\n"
        f"[bold]Meta Description:[/bold] {generated.meta_description}\n"
        f"[bold]Words:[/bold] {generated.word_count}\n"
        f"[bold]Images:[/bold] {generated.image_count}\n"
        f"[bold]Internal Links:[/bold] {len(internal_links)}\n"
        f"[bold]Official Google Sources:[/bold] {len(google_evidence)}\n"
        f"[bold]Publishing Slot:[/bold] {resolved_slot} ({ist_date} IST)"
        + (f"\n[bold]URL:[/bold] {result_url}" if result_url else "")
    )

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write("## DeviceRank Evergreen Publisher\n\n")
            summary_file.write(f"- **Title:** {generated.title}\n")
            summary_file.write(f"- **Category:** {selected.category_name}\n")
            summary_file.write(f"- **Status:** {status_text}\n")
            summary_file.write(f"- **Words:** {generated.word_count}\n")
            summary_file.write(f"- **Images:** {generated.image_count}\n")
            summary_file.write(f"- **Internal links:** {len(internal_links)}\n")
            summary_file.write(f"- **Official Google sources:** {len(google_evidence)}\n")
            summary_file.write(f"- **Slot:** {resolved_slot} ({ist_date} IST)\n")
            if result_url:
                summary_file.write(f"- **Link:** [View post]({result_url})\n")


@app.command(hidden=True)
def generate(
    category: str = typer.Option(
        "tech_news", "--category", "-c", help="Category key (tech_news, seo_tips, gadgets, monetization)"
    ),
    publish: bool = typer.Option(
        False, "--publish", "-p", help="Publish directly to Blogger after generation"
    ),
    draft: bool = typer.Option(
        True, "--draft/--live", help="Publish as Draft (default) or Live"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Number of posts to generate (defaults to MAX_POSTS_PER_RUN)"
    ),
    save_html: bool = typer.Option(
        True, "--save/--no-save", help="Save generated HTML file locally for preview"
    ),
):
    """Generate SEO-optimized articles using Gemini AI and optionally publish to Blogger."""
    print_banner()
    if publish:
        _require_legacy_news_publishing_enabled()
    actual_limit = limit if limit is not None else settings.max_posts_per_run

    fetcher = RSSFetcher()
    writer = SEOWriter()

    console.print(f"[bold cyan]Fetching fresh stories for category:[/bold cyan] [green]{category}[/green]")
    articles = fetcher.fetch_category(category, max_items=actual_limit * 4, deduplicate=True)

    # Rank and select top candidates
    ranked = StoryRanker.rank_and_select(articles, limit=actual_limit)

    if not ranked:
        console.print("[yellow]No new unprocessed articles found for this category.[/yellow]")
        return

    processed_count = 0
    output_dir = settings.project_root / "output"
    output_dir.mkdir(exist_ok=True)

    for article, score in ranked:
        url_hash = history_db.hash_url(article.link)
        history_db.mark_story_selected(url_hash, score)

        console.print(
            Panel(
                f"[bold white]{article.title}[/bold white]\n"
                f"[dim]Source: {article.source_name} | Score: {score:.2f} | Link: {article.link}[/dim]",
                title="Selected Story Candidate",
                border_style="blue",
            )
        )

        try:
            generated = writer.write_article(article)
            history_db.update_story_status(url_hash, StoryStatus.GENERATED)

            console.print("\n[bold green]Generated SEO Article:[/bold green]")
            console.print(f"[bold]Title:[/bold] {generated.title}")
            console.print(f"[bold]Focus Keyword:[/bold] {generated.focus_keyword}")
            console.print(f"[bold]Meta Description:[/bold] {generated.meta_description}")
            console.print(f"[bold]Word Count:[/bold] ~{generated.word_count} words")
            console.print(f"[bold]Labels:[/bold] {', '.join(generated.labels)}")

            # Save HTML preview
            if save_html:
                safe_title = "".join(c for c in generated.title if c.isalnum() or c in " -_").strip()[:50]
                preview_file = output_dir / f"{category}_{safe_title}.html"
                with open(preview_file, "w", encoding="utf-8") as f:
                    f.write(f"<!-- Title: {generated.title} -->\n")
                    f.write(f"<!-- Meta Description: {generated.meta_description} -->\n")
                    f.write(f"<!-- Labels: {', '.join(generated.labels)} -->\n\n")
                    f.write(generated.html_content)
                console.print(f"Local preview saved to: [cyan]{preview_file}[/cyan]")

            # Publish if requested
            if publish:
                blogger = BloggerClient()
                result = blogger.publish_post(generated, is_draft=draft)
                status_text = "DRAFT" if draft else "LIVE"
                post_id = result.get("id", "N/A")
                console.print(f"Post created in Blogger as [bold green]{status_text}[/bold green]! ID: {post_id}")
            else:
                history_db.mark_source_processed(article.link, generated.title, category, status="GENERATED")

            processed_count += 1

        except Exception as e:
            logger.error(f"Error processing article '{article.title}': {e}")
            history_db.mark_story_failed(url_hash, str(e))
            console.print_exception()

    console.print(f"\n[bold green]Done! Processed {processed_count} article(s).[/bold green]")


@app.command(hidden=True)
def run_pipeline(
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="Specific category key or empty for all"
    ),
    draft: bool = typer.Option(
        True, "--draft/--live", help="Publish as Draft (default) or Live"
    ),
    max_per_category: Optional[int] = typer.Option(
        None, "--max", "-m", help="Max articles to publish per category (defaults to MAX_POSTS_PER_RUN)"
    ),
):
    """Run the complete end-to-end automated pipeline (Fetch -> Rank -> Generate -> Publish)."""
    print_banner()
    _require_legacy_news_publishing_enabled()
    config = load_feeds_config()
    fetcher = RSSFetcher(config)
    writer = SEOWriter()
    blogger = BloggerClient()

    actual_max = max_per_category if max_per_category is not None else settings.max_posts_per_run
    categories_to_run = [category] if (category and category.strip()) else list(config.categories.keys())
    published_records = []

    for cat_key in categories_to_run:
        console.print(f"\n[bold cyan]Processing Pipeline for Category:[/bold cyan] [bold yellow]{cat_key}[/bold yellow]")
        articles = fetcher.fetch_category(cat_key, max_items=actual_max * 4, deduplicate=True)

        if not articles:
            console.print(f"[dim]No new articles for {cat_key}. Skipping.[/dim]")
            continue

        ranked = StoryRanker.rank_and_select(articles, limit=actual_max)

        for article, score in ranked:
            url_hash = history_db.hash_url(article.link)
            history_db.mark_story_selected(url_hash, score)

            try:
                console.print(f"Generating content for: [white]{article.title[:60]}...[/white] (Score: {score:.2f})")
                generated = writer.write_article(article)
                history_db.update_story_status(url_hash, StoryStatus.GENERATED)

                res = blogger.publish_post(generated, is_draft=draft)
                status_str = "DRAFT" if draft else "LIVE"
                published_records.append({
                    "title": generated.title,
                    "category": cat_key,
                    "status": status_str,
                    "word_count": generated.word_count,
                    "url": res.get("url", "https://devicerank.blogspot.com"),
                })
            except Exception as e:
                logger.error(f"Pipeline error for {article.title}: {e}")
                history_db.mark_story_failed(url_hash, str(e))

    console.print(f"\n[bold green]Pipeline finished! Total posts created: {len(published_records)}[/bold green]")

    # GitHub Step Summary support
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## 🚀 DeviceRank AI Publisher Execution Summary\n\n")
                f.write(f"**Total Posts Processed:** {len(published_records)}\n\n")
                if published_records:
                    f.write("| Category | Title | Status | Words | Link |\n")
                    f.write("| :--- | :--- | :--- | :--- | :--- |\n")
                    for r in published_records:
                        f.write(f"| `{r['category']}` | {r['title']} | **{r['status']}** | {r['word_count']} | [View Post]({r['url']}) |\n")
                else:
                    f.write("*No new articles were due for publication in this run.*\n")
        except Exception as e:
            logger.debug(f"Could not write GitHub Step Summary: {e}")


@app.command(name="run-digest", hidden=True)
def run_digest(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        "-c",
        help="Optional category key; omit to use the latest stories across all categories",
    ),
    draft: bool = typer.Option(
        True,
        "--draft/--live",
        help="Publish as Draft (default) or Live",
    ),
    stories: Optional[int] = typer.Option(
        None,
        "--stories",
        "-n",
        help="Maximum stories in the digest, from 3 to 8 (defaults to slot-specific size)",
    ),
    slot: Optional[str] = typer.Option(
        None,
        "--slot",
        "-s",
        help="Publishing slot override: morning, midday, evening, or auto",
    ),
    slot_id: Optional[str] = typer.Option(
        None,
        "--slot-id",
        help="Explicit unique slot ID (e.g. 2026-08-29-morning)",
    ),
):
    """Fetch the latest news and publish one combined slot-formatted digest with deterministic titles and originality layers."""
    print_banner()
    _require_legacy_news_publishing_enabled()

    # 1. Resolve publishing slot and idempotency ID
    slot_override = slot if (slot and slot.lower() != "auto") else None
    slot_info = get_current_slot(slot_override=slot_override)
    if slot_id and slot_id.strip():
        slot_info.slot_id = slot_id.strip()

    # Slot-specific default sizing
    if slot_info.slot_type == SlotType.MORNING:
        default_story_count = 6
        min_required_clusters = 5
    elif slot_info.slot_type == SlotType.MIDDAY:
        default_story_count = 4
        min_required_clusters = 3
    else:  # Evening
        default_story_count = 5
        min_required_clusters = 4

    story_count = stories if stories is not None else default_story_count
    if not 3 <= story_count <= 8:
        raise typer.BadParameter("--stories must be between 3 and 8")

    console.print(
        Panel(
            f"[bold white]Slot ID:[/bold white] [cyan]{slot_info.slot_id}[/cyan]\n"
            f"[bold white]Slot Format:[/bold white] [green]{slot_info.slot_display}[/green] ({slot_info.time_window_utc})\n"
            f"[bold white]Target Stories:[/bold white] {story_count} (Min required: {min_required_clusters})\n"
            f"[dim]{slot_info.description}[/dim]",
            title="Publication Slot",
            border_style="magenta",
        )
    )

    # 2. Remote Ledger Reconciliation & Slot Idempotency / Draft Promotion Check
    blogger: Optional[BloggerClient] = None
    try:
        blogger = BloggerClient()
        synced = blogger.sync_remote_ledger(max_posts=25)
        if synced > 0:
            console.print(f"[dim]Synchronized {synced} post records from remote Blogger ledger.[/dim]")
    except Exception as e:
        if not draft and settings.blogger_blog_id:
            logger.error(f"Fatal remote ledger sync error during live execution: {e}")
            raise
        logger.debug(f"Remote ledger sync not available: {e}")

    # Check if slot is already LIVE
    if history_db.is_slot_published(slot_info.slot_id, live_only=True):
        console.print(
            f"\n[bold yellow]⚡ Slot '{slot_info.slot_id}' ({slot_info.slot_display}) is already published LIVE on Blogger.[/bold yellow]\n"
            "[green]Exiting cleanly with idempotent skip (0 duplicate posts created).[/green]"
        )
        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as summary_file:
                    summary_file.write(f"## DeviceRank Publisher — {slot_info.slot_display}\n\n")
                    summary_file.write(f"⚡ **Idempotent Skip**: Slot `{slot_info.slot_id}` was already LIVE. No duplicate action taken.\n")
            except Exception:
                pass
        return

    # Check if slot draft already exists and scheduled run is LIVE -> Promote draft!
    if not draft and history_db.is_slot_draft(slot_info.slot_id):
        draft_post = history_db.get_slot_post(slot_info.slot_id)
        draft_id = draft_post.get("blogger_post_id") if draft_post else None
        if draft_id and blogger:
            console.print(f"[bold cyan]Found existing draft {draft_id} for slot '{slot_info.slot_id}'. Promoting to LIVE...[/bold cyan]")
            promoted = blogger.publish_draft_post(draft_id)
            history_db.sync_remote_post(
                blogger_post_id=draft_id,
                title=draft_post.get("title", ""),
                slot_id=slot_info.slot_id,
                blogger_url=promoted.get("url"),
                status="LIVE",
            )
            console.print(f"[bold green]Successfully promoted draft to LIVE:[/bold green] {promoted.get('url')}")
            return

    # If draft mode and a draft already exists for this slot -> idempotent skip
    if draft and history_db.is_slot_published(slot_info.slot_id):
        console.print(
            f"\n[bold yellow]⚡ Draft for slot '{slot_info.slot_id}' ({slot_info.slot_display}) already exists.[/bold yellow]\n"
            "[green]Exiting cleanly with idempotent skip.[/green]"
        )
        return

    config = load_feeds_config()
    if category and category not in config.categories:
        valid = ", ".join(config.categories.keys())
        raise typer.BadParameter(f"Unknown category '{category}'. Choose one of: {valid}")

    fetcher = RSSFetcher(config)
    writer = SEOWriter()

    scope = category or "all categories"
    console.print(
        f"[bold cyan]Fetching fresh unprocessed stories from:[/bold cyan] "
        f"[green]{scope}[/green]"
    )
    if category:
        fetcher.fetch_category(category, max_items=story_count * 3, deduplicate=True)
    else:
        fetcher.fetch_all(max_per_category=story_count * 2, deduplicate=True)

    queued = history_db.get_candidate_stories(
        category=category,
        statuses=[StoryStatus.NEW, StoryStatus.FAILED],
        limit=200,
    )
    candidates = [_raw_article_from_queue(item) for item in queued]

    # 3. Semantic Topic Clustering (group same-event/product articles across feeds)
    clusters = TopicClusterer.cluster_articles(candidates)
    console.print(f"[dim]Grouped {len(candidates)} raw stories into {len(clusters)} distinct topic clusters.[/dim]")

    # 4. Filter out clusters matching topic fingerprints published in the last 72 hours
    recent_fps = history_db.get_recent_topic_fingerprints(hours=72)
    if recent_fps:
        initial_cluster_count = len(clusters)
        clusters = TopicClusterer.filter_by_recent_fingerprints(clusters, recent_fps)
        filtered_diff = initial_cluster_count - len(clusters)
        if filtered_diff > 0:
            console.print(f"[dim]Filtered out {filtered_diff} clusters covered in the last 72 hours.[/dim]")

    # Select top clusters (strictly 1 cluster per topic!)
    selected = StoryRanker.select_latest(
        clusters,
        limit=min(story_count, len(clusters)),
        max_per_source=2,
    )

    if len(selected) < min_required_clusters:
        console.print(
            f"[yellow]Only {len(selected)} unique topic clusters are available. "
            f"At least {min_required_clusters} are required for {slot_info.slot_display}, "
            "so this run will not publish a partial digest.[/yellow]"
        )
        return

    selected_clusters = [cluster for cluster, _score in selected]
    selected_hashes = []
    for cluster, score in selected:
        for art in cluster.articles:
            url_hash = history_db.hash_url(art.link)
            selected_hashes.append(url_hash)
            history_db.mark_story_selected(url_hash, score)

        sources_str = ", ".join(cluster.source_names)
        console.print(
            f"[dim]Selected Cluster:[/dim] {cluster.canonical_article.title[:75]} "
            f"[dim](Corroborated by: {sources_str})[/dim]"
        )

    try:
        # 5. Content Generation with Deterministic Titles & Originality Layer
        generated = writer.write_digest(selected_clusters, slot_info=slot_info)
        for url_hash in selected_hashes:
            history_db.update_story_status(url_hash, StoryStatus.GENERATED)

        # 6. Publication to Blogger with Slot ID & Topic Fingerprints tracking
        blogger_client = blogger or BloggerClient()
        topic_fps = [c.fingerprint for c in selected_clusters if c.fingerprint]
        result = blogger_client.publish_post(
            generated,
            is_draft=draft,
            slot_id=slot_info.slot_id,
            topic_fingerprints=topic_fps,
        )
        status_text = "DRAFT" if draft else "LIVE"
        result_url = result.get("url", "https://devicerank.blogspot.com")

        console.print(
            f"\n[bold green]Created one {status_text} {slot_info.slot_display} with "
            f"{len(selected_clusters)} clusters:[/bold green]\n"
            f"[bold white]Title:[/bold white] {generated.title}\n"
            f"[bold white]Labels:[/bold white] {', '.join(generated.labels)}\n"
            f"[bold white]URL:[/bold white] {result_url}"
        )

        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary_file:
                summary_file.write(f"## DeviceRank {slot_info.slot_display}\n\n")
                summary_file.write(f"- **Slot ID:** `{slot_info.slot_id}`\n")
                summary_file.write(f"- **Title:** {generated.title}\n")
                summary_file.write(f"- **Clusters/Stories:** {len(selected_clusters)}\n")
                summary_file.write(f"- **Labels:** `{', '.join(generated.labels)}`\n")
                summary_file.write(f"- **Status:** {status_text}\n")
                summary_file.write(f"- **Link:** [View post]({result_url})\n")
    except Exception as exc:
        logger.error(f"Digest pipeline error: {exc}")
        for url_hash in selected_hashes:
            history_db.mark_story_failed(url_hash, str(exc))
        raise


@app.command()
def queue(
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="Filter by category"
    ),
    limit: int = typer.Option(15, "--limit", "-l", help="Number of queue items to display"),
):
    """View current stories in the database queue and their lifecycle statuses."""
    print_banner()
    items = history_db.get_candidate_stories(
        category=category,
        statuses=[StoryStatus.NEW, StoryStatus.SELECTED, StoryStatus.GENERATED, StoryStatus.PUBLISHING, StoryStatus.FAILED],
        limit=limit,
    )

    if not items:
        console.print("[yellow]The story queue is currently empty.[/yellow]")
        return

    table = Table(title="Story Queue Status", header_style="bold magenta")
    table.add_column("Status", width=10)
    table.add_column("Category", width=12)
    table.add_column("Score", width=6)
    table.add_column("Source", width=14)
    table.add_column("Title", min_width=30)
    table.add_column("Published Date", width=18)

    for item in items:
        status_style = {
            StoryStatus.NEW: "cyan",
            StoryStatus.SELECTED: "yellow",
            StoryStatus.GENERATED: "blue",
            StoryStatus.PUBLISHING: "magenta",
            StoryStatus.PUBLISHED: "green",
            StoryStatus.FAILED: "red",
        }.get(item["status"], "white")

        pub_str = str(item.get("published_date") or "")[:16] or "N/A"
        score_str = f"{item['score']:.2f}" if item.get("score") else "-"

        table.add_row(
            f"[{status_style}]{item['status']}[/{status_style}]",
            item["category"],
            score_str,
            item["source_name"],
            item["title"][:50],
            pub_str,
        )

    console.print(table)


@app.command()
def auth():
    """Run Google Blogger OAuth authentication and generate credentials."""
    print_banner()
    authenticate_blogger_oauth()


@app.command(name="export-secrets")
def export_secrets(
    unmask: bool = typer.Option(
        False, "--unmask", help="Display full unmasked secret values locally"
    )
):
    """Display values for configuring GitHub Actions Secrets (masked by default)."""
    print_banner()
    export_github_secrets_info(unmask=unmask)


@app.command()
def stats():
    """Display statistics about queue states, processed sources, and published posts."""
    print_banner()
    st = history_db.get_stats()

    console.print(Panel(
        f"[bold]Total Ingested Sources:[/bold] {st['total_sources_ingested']}\n"
        f"[bold]Total Posts Created:[/bold] {st['total_posts_created']}\n"
        f"[bold]Queue Breakdown:[/bold] {st.get('queue_breakdown', {})}\n"
        f"[bold]Status Breakdown:[/bold] {st['status_breakdown']}\n"
        f"[bold]Category Breakdown:[/bold] {st['category_breakdown']}",
        title="DeviceRank Publisher Stats",
        border_style="green",
    ))

    recent = history_db.get_recent_posts(limit=5)
    if recent:
        table = Table(title="Recent Posts History", header_style="bold magenta")
        table.add_column("ID", width=4)
        table.add_column("Category", width=12)
        table.add_column("Title", min_width=30)
        table.add_column("Status", width=8)
        table.add_column("Words", width=8)
        table.add_column("Date", width=18)

        for p in recent:
            table.add_row(
                str(p["id"]),
                p["category"],
                p["title"][:50],
                p["status"],
                str(p["word_count"]),
                str(p["created_at"])[:16],
            )
        console.print(table)


if __name__ == "__main__":
    app()

"""DeviceRank AI Publisher CLI Application.

Unified entry point for RSS fetching, Gemini SEO generation, Blogger publishing,
and automated orchestration.
"""

import os
import sys
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
from src.db.history import history_db
from src.fetchers.rss_fetcher import RSSFetcher
from src.publishers.blogger_client import BloggerClient
from src.publishers.oauth_helper import authenticate_blogger_oauth, export_github_secrets_info
from src.utils.logger import console, display_articles_table, logger, print_banner

app = typer.Typer(
    name="devicerank",
    help="DeviceRank AI Publisher - Automated SEO Publishing Engine for Blogger",
    add_completion=False,
)


@app.command()
def categories():
    """List all configured content categories and their active RSS feeds."""
    print_banner()
    config = load_feeds_config()

    table = Table(title="Configured Content Categories", header_style="bold magenta")
    table.add_column("Key", style="cyan", width=14)
    table.add_column("Display Name", style="white", width=24)
    table.add_column("Blogger Label", style="green", width=16)
    table.add_column("Active Feeds", style="yellow")

    for key, cat in config.categories.items():
        feed_names = ", ".join(f.name for f in cat.feeds if f.enabled)
        table.add_row(key, cat.name, cat.blogger_label, feed_names)

    console.print(table)


@app.command()
def fetch(
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="Category key (e.g., tech_news, seo_tips, gadgets, monetization)"
    ),
    limit: int = typer.Option(3, "--limit", "-l", help="Number of items to fetch per feed"),
    include_processed: bool = typer.Option(
        False, "--all", "-a", help="Include already processed articles"
    ),
):
    """Fetch and display latest trending articles without generating posts."""
    print_banner()
    fetcher = RSSFetcher()
    dedup = not include_processed

    if category:
        articles = fetcher.fetch_category(category, max_items=limit, deduplicate=dedup)
        display_articles_table(articles, title=f"Latest Articles: {category}")
    else:
        results = fetcher.fetch_all(max_per_category=limit, deduplicate=dedup)
        for cat_key, arts in results.items():
            display_articles_table(arts, title=f"Category: {cat_key} ({len(arts)} new)")


@app.command()
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
    limit: int = typer.Option(1, "--limit", "-l", help="Number of posts to generate"),
    save_html: bool = typer.Option(
        True, "--save/--no-save", help="Save generated HTML file locally for preview"
    ),
):
    """Generate SEO-optimized articles using Gemini AI and optionally publish to Blogger."""
    print_banner()

    fetcher = RSSFetcher()
    writer = SEOWriter()

    console.print(f"[bold cyan]Fetching fresh stories for category:[/bold cyan] [green]{category}[/green]")
    articles = fetcher.fetch_category(category, max_items=limit * 2, deduplicate=True)

    if not articles:
        console.print("[yellow]No new unprocessed articles found for this category.[/yellow]")
        return

    processed_count = 0
    output_dir = settings.project_root / "output"
    output_dir.mkdir(exist_ok=True)

    for article in articles[:limit]:
        console.print(Panel(f"[bold white]{article.title}[/bold white]\n[dim]{article.link}[/dim]", title="Source Story", border_style="blue"))

        try:
            generated = writer.write_article(article)

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
                console.print(f"Post created in Blogger as [bold green]{status_text}[/bold green]! ID: {result.get('id')}")
            else:
                # Still record in history as processed
                history_db.mark_source_processed(article.link, generated.title, category, status="GENERATED")

            processed_count += 1

        except Exception as e:
            logger.error(f"Error processing article '{article.title}': {e}")
            console.print_exception()

    console.print(f"\n[bold green]Done! Processed {processed_count} article(s).[/bold green]")


@app.command()
def run_pipeline(
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="Specific category or None for all"
    ),
    draft: bool = typer.Option(
        True, "--draft/--live", help="Publish as Draft (default) or Live"
    ),
    max_per_category: int = typer.Option(
        1, "--max", "-m", help="Max articles to publish per category"
    ),
):
    """Run the complete end-to-end automated pipeline (Fetch -> Generate -> Publish)."""
    print_banner()
    config = load_feeds_config()
    fetcher = RSSFetcher(config)
    writer = SEOWriter()
    blogger = BloggerClient()

    categories_to_run = [category] if category else list(config.categories.keys())
    published_records = []

    for cat_key in categories_to_run:
        console.print(f"\n[bold cyan]Processing Pipeline for Category:[/bold cyan] [bold yellow]{cat_key}[/bold yellow]")
        articles = fetcher.fetch_category(cat_key, max_items=max_per_category * 2, deduplicate=True)

        if not articles:
            console.print(f"[dim]No new articles for {cat_key}. Skipping.[/dim]")
            continue

        for article in articles[:max_per_category]:
            try:
                console.print(f"Generating content for: [white]{article.title[:60]}...[/white]")
                generated = writer.write_article(article)
                res = blogger.publish_post(generated, is_draft=draft)
                published_records.append({
                    "title": generated.title,
                    "category": cat_key,
                    "status": "DRAFT" if draft else "LIVE",
                    "word_count": generated.word_count,
                    "url": res.get("url", "https://devicerank.blogspot.com"),
                })
            except Exception as e:
                logger.error(f"Pipeline error for {article.title}: {e}")

    console.print(f"\n[bold green]Pipeline finished! Total posts created: {len(published_records)}[/bold green]")

    # If running inside GitHub Actions, generate GitHub Step Summary
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


@app.command()
def auth():
    """Run Google Blogger OAuth authentication and generate credentials."""
    print_banner()
    authenticate_blogger_oauth()


@app.command(name="export-secrets")
def export_secrets():
    """Display values for configuring GitHub Actions Secrets."""
    print_banner()
    export_github_secrets_info()


@app.command()
def stats():
    """Display statistics about processed sources and published posts."""
    print_banner()
    st = history_db.get_stats()

    console.print(Panel(
        f"[bold]Total Ingested Sources:[/bold] {st['total_sources_ingested']}\n"
        f"[bold]Total Posts Created:[/bold] {st['total_posts_created']}\n"
        f"[bold]Status Breakdown:[/bold] {st['status_breakdown']}\n"
        f"[bold]Category Breakdown:[/bold] {st['category_breakdown']}",
        title="DeviceRank Publisher Stats",
        border_style="green"
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

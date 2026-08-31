"""CLI for the isolated, weekly Reddit topic-signal tutorial publisher."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel

from config.settings import settings
from src.agents.reddit_writer import RedditTutorialWriter
from src.db.history import history_db
from src.fetchers.reddit_fetcher import RedditTopicFetcher, parse_subreddits
from src.publishers.blogger_client import BloggerClient
from src.utils.logger import console, logger


app = typer.Typer(
    name="reddit-weekly",
    help="Create an original beginner tutorial from an ephemeral Reddit topic signal.",
    add_completion=False,
)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def _weekly_run_id(now: Optional[datetime] = None) -> str:
    local_now = (now or datetime.now(timezone.utc)).astimezone(IST)
    iso_year, iso_week, _ = local_now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}-reddit-weekly"


def _require_reddit_access_configuration() -> None:
    if not settings.reddit_use_rights_confirmed:
        raise typer.BadParameter(
            "Reddit access is disabled. Set REDDIT_USE_RIGHTS_CONFIRMED=true only after "
            "confirming that Reddit approved this API use and that you have any required "
            "commercial/content rights."
        )
    missing = [
        name
        for name, value in (
            ("REDDIT_CLIENT_ID", settings.reddit_client_id),
            ("REDDIT_CLIENT_SECRET", settings.reddit_client_secret),
            ("REDDIT_USER_AGENT", settings.reddit_user_agent),
        )
        if not value
    ]
    if missing:
        raise typer.BadParameter(
            "Missing external Reddit Data API configuration: " + ", ".join(missing)
        )


@app.command("run")
def run(
    subreddits: Optional[str] = typer.Option(
        None,
        "--subreddits",
        help="Comma-separated subreddit allowlist; defaults to REDDIT_SUBREDDITS.",
    ),
    publish: bool = typer.Option(
        True,
        "--publish/--no-publish",
        help="Create a Blogger post or only generate a local preview.",
    ),
    draft: bool = typer.Option(
        True,
        "--draft/--live",
        help="Create a Blogger draft by default; use --live only after review.",
    ),
    save_html: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save the generated HTML to output/.",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Idempotency key; defaults to the current IST ISO week.",
    ),
    post_limit: int = typer.Option(
        settings.reddit_post_limit,
        "--post-limit",
        min=5,
        max=100,
        help="Top weekly titles fetched per subreddit.",
    ),
    max_topic_checks: int = typer.Option(
        settings.reddit_max_topic_checks,
        "--max-topic-checks",
        min=1,
        max=10,
        help="Maximum ranked topic signals Gemini may screen.",
    ),
):
    """Generate one original weekly tutorial without reproducing Reddit content."""
    _require_reddit_access_configuration()
    subreddit_names = parse_subreddits(subreddits or settings.reddit_subreddits)
    resolved_run_id = (run_id or "").strip() or (_weekly_run_id() if publish else None)

    blogger: Optional[BloggerClient] = None
    if publish:
        blogger = BloggerClient()
        blogger.sync_remote_ledger(max_posts=150)
        if resolved_run_id and history_db.is_slot_published(resolved_run_id, live_only=True):
            console.print(
                f"[yellow]Weekly run '{resolved_run_id}' is already live. No duplicate was created.[/yellow]"
            )
            return
        if resolved_run_id and history_db.is_slot_draft(resolved_run_id):
            existing = history_db.get_slot_post(resolved_run_id)
            existing_id = existing.get("blogger_post_id") if existing else None
            if not draft and existing_id:
                promoted = blogger.publish_draft_post(
                    existing_id,
                    minimum_image_count=settings.reddit_image_count,
                )
                history_db.sync_remote_post(
                    blogger_post_id=existing_id,
                    title=existing.get("title", ""),
                    category=existing.get("category", "weekly_explainer"),
                    slot_id=resolved_run_id,
                    blogger_url=promoted.get("url"),
                    status="LIVE",
                )
                console.print(
                    f"[bold green]Published reviewed weekly draft:[/bold green] {promoted.get('url')}"
                )
                return
            console.print(
                f"[yellow]Draft for weekly run '{resolved_run_id}' already exists. No duplicate was created.[/yellow]"
            )
            return

    fetcher = RedditTopicFetcher(
        client_id=settings.reddit_client_id or "",
        client_secret=settings.reddit_client_secret or "",
        user_agent=settings.reddit_user_agent or "",
        timeout_seconds=settings.http_timeout_seconds,
    )
    signals = fetcher.fetch_weekly_topics(subreddit_names, limit_per_subreddit=post_limit)
    available_signals = [
        signal for signal in signals if not history_db.is_url_published(signal.source_id)
    ]
    if not available_signals:
        console.print("[yellow]No unused weekly topic signals were available.[/yellow]")
        return

    writer = RedditTutorialWriter()
    selected_signal = None
    selected_brief = None
    for signal in available_signals[:max_topic_checks]:
        try:
            brief = writer.analyze_signal(signal)
        except ValueError as exc:
            logger.info("Rejected Reddit topic signal %s: %s", signal.post_id, exc)
            continue
        if brief.suitable:
            selected_signal = signal
            selected_brief = brief
            break
        logger.info(
            "Skipped unsuitable Reddit topic signal %s: %s",
            signal.post_id,
            brief.reason,
        )
    if not selected_signal or not selected_brief:
        console.print(
            "[yellow]None of the screened topic signals was suitable for an evergreen beginner guide.[/yellow]"
        )
        return

    generated = writer.write_tutorial(
        signal=selected_signal,
        brief=selected_brief,
        required_image_count=settings.reddit_image_count if publish else 0,
    )

    preview_path: Optional[Path] = None
    if save_html:
        output_dir = settings.project_root / "output"
        output_dir.mkdir(exist_ok=True)
        preview_path = output_dir / f"{resolved_run_id or _weekly_run_id()}-preview.html"
        with open(preview_path, "w", encoding="utf-8") as preview_file:
            preview_file.write(f"<!-- Title: {generated.title} -->\n")
            preview_file.write(
                f"<!-- Meta Description: {generated.meta_description} -->\n"
            )
            preview_file.write(f"<!-- Focus Keyword: {generated.focus_keyword} -->\n")
            preview_file.write(f"<!-- Labels: {', '.join(generated.labels)} -->\n\n")
            preview_file.write(generated.html_content)

    result = None
    if publish and blogger:
        result = blogger.publish_post(
            generated,
            is_draft=draft,
            slot_id=resolved_run_id,
            topic_fingerprints=[selected_signal.post_id],
        )

    status_text = "PREVIEW" if not publish else ("DRAFT" if draft else "LIVE")
    result_url = result.get("url") if result else None
    console.print(
        Panel(
            f"[bold]{generated.title}[/bold]\n"
            f"Status: {status_text}\n"
            f"Words: {generated.word_count}\n"
            f"Images: {generated.image_count}\n"
            f"Topic source: anonymized r/{selected_signal.subreddit} signal"
            + (f"\nPreview: {preview_path}" if preview_path else "")
            + (f"\nURL: {result_url}" if result_url else ""),
            title="Weekly Beginner Tutorial",
            border_style="green",
        )
    )

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write("## DeviceRank Weekly Topic-Signal Publisher\n\n")
            summary_file.write(f"- **Title:** {generated.title}\n")
            summary_file.write(f"- **Status:** {status_text}\n")
            summary_file.write(f"- **Words:** {generated.word_count}\n")
            summary_file.write(f"- **Images:** {generated.image_count}\n")
            summary_file.write(f"- **Run ID:** {resolved_run_id or 'preview'}\n")
            if result_url:
                summary_file.write(f"- **Link:** [View post]({result_url})\n")


if __name__ == "__main__":
    app()

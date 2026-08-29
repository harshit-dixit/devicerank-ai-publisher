"""Logging and terminal UI utilities for DeviceRank AI Publisher."""

import logging
import sys
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

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

console = Console(safe_box=True)


def setup_logger(name: str = "devicerank", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns a rich logger."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    return logging.getLogger(name)


logger = setup_logger()


def print_banner():
    """Prints a styled banner for the CLI."""
    banner_text = """[bold cyan]DeviceRank Evergreen Publisher[/bold cyan]
[dim]Helpful how-to publishing for devicerank.blogspot.com[/dim]"""
    console.print(Panel(banner_text, border_style="cyan", expand=False))


def display_articles_table(articles: list, title: str = "Fetched Articles"):
    """Displays a formatted table of fetched articles."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("Category", style="cyan", width=14)
    table.add_column("Source", style="green", width=16)
    table.add_column("Title", style="white", min_width=30)
    table.add_column("Has Image", style="yellow", width=10)

    for idx, art in enumerate(articles, 1):
        has_img = "Yes" if getattr(art, "image_url", None) else "No"
        table.add_row(
            str(idx),
            getattr(art, "category", "N/A"),
            getattr(art, "source_name", "N/A"),
            getattr(art, "title", "Untitled")[:60] + ("..." if len(getattr(art, "title", "")) > 60 else ""),
            has_img,
        )

    console.print(table)

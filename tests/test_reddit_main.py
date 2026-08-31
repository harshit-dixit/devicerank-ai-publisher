from datetime import datetime, timezone

from src.reddit_main import _weekly_run_id


def test_weekly_run_id_uses_iso_week_for_idempotency():
    assert _weekly_run_id(datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)) == (
        "2026-W36-reddit-weekly"
    )

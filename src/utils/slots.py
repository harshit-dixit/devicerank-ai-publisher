"""Slot management, scheduling metadata, and deterministic title generation for DeviceRank AI Publisher."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class SlotType(str, Enum):
    MORNING = "morning"
    MIDDAY = "midday"
    EVENING = "evening"


@dataclass
class SlotInfo:
    """Metadata representing an 8-hour publishing slot."""

    slot_type: SlotType
    slot_id: str
    slot_display: str
    description: str
    time_window_utc: str


def get_current_slot(dt: Optional[datetime] = None, slot_override: Optional[str] = None) -> SlotInfo:
    """Calculates the publishing slot and unique slot ID based on UTC time or override.
    
    Slot boundaries (UTC):
    - 00:00 to 07:59 UTC -> Morning Brief (approx 05:30 - 13:30 IST)
    - 08:00 to 15:59 UTC -> Midday Brief  (approx 13:30 - 21:30 IST)
    - 16:00 to 23:59 UTC -> Evening Brief (approx 21:30 - 05:30 IST)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    date_str = dt.strftime("%Y-%m-%d")

    if slot_override and slot_override.lower() in ("morning", "midday", "evening"):
        slot_type = SlotType(slot_override.lower())
    else:
        hour = dt.hour
        if 0 <= hour < 8:
            slot_type = SlotType.MORNING
        elif 8 <= hour < 16:
            slot_type = SlotType.MIDDAY
        else:
            slot_type = SlotType.EVENING

    slot_id = f"{date_str}-{slot_type.value}"

    display_names = {
        SlotType.MORNING: "Morning Brief",
        SlotType.MIDDAY: "Midday Brief",
        SlotType.EVENING: "Evening Brief",
    }

    descriptions = {
        SlotType.MORNING: "Five to six overnight developments, hardware/software releases, and rapid intelligence.",
        SlotType.MIDDAY: "Deep synthesis of major developing tech stories corroborated across multi-source clusters.",
        SlotType.EVENING: "Buyer impact, privacy implications, upgrade significance, and DeviceRank buying verdicts.",
    }

    windows = {
        SlotType.MORNING: "00:00 - 07:59 UTC",
        SlotType.MIDDAY: "08:00 - 15:59 UTC",
        SlotType.EVENING: "16:00 - 23:59 UTC",
    }

    return SlotInfo(
        slot_type=slot_type,
        slot_id=slot_id,
        slot_display=display_names[slot_type],
        description=descriptions[slot_type],
        time_window_utc=windows[slot_type],
    )


def build_deterministic_title(
    topic_phrases: List[str],
    slot_display: str = "Morning Brief",
    max_length: int = 85,
) -> str:
    """Builds a deterministic, standardized title from three topic phrases and slot display.
    
    Grammar: {Topic 1}, {Topic 2} & {Topic 3} — DeviceRank {Slot} Brief
    Example: Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief
    """
    clean_topics: List[str] = []
    for raw in topic_phrases:
        if not raw:
            continue
        cleaned = str(raw).strip().strip('"').strip("'").strip()
        # Remove trailing punctuation or dashes
        cleaned = re.sub(r"[\s\-_—,]+$", "", cleaned)
        if cleaned:
            clean_topics.append(cleaned)

    # Fallback topics if fewer than 3 provided
    while len(clean_topics) < 3:
        fallbacks = ["Tech Updates", "Hardware News", "AI Developments", "Industry Shifts"]
        for f in fallbacks:
            if f not in clean_topics:
                clean_topics.append(f)
                break

    t1, t2, t3 = clean_topics[0], clean_topics[1], clean_topics[2]
    suffix = f" — DeviceRank {slot_display}"

    full_title = f"{t1}, {t2} & {t3}{suffix}"

    # If within limit, return
    if len(full_title) <= max_length:
        return full_title

    # If too long, abbreviate topics cleanly at word boundaries
    avail_topics_len = max_length - len(suffix) - 5
    if avail_topics_len < 20:
        avail_topics_len = 20

    max_per_topic = max(12, avail_topics_len // 3)

    def _truncate_topic(t: str, limit: int) -> str:
        if len(t) <= limit:
            return t
        truncated = t[:limit]
        # Truncate at last space if possible
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        return truncated.rstrip(" ,-")

    t1 = _truncate_topic(t1, max_per_topic)
    t2 = _truncate_topic(t2, max_per_topic)
    t3 = _truncate_topic(t3, max_per_topic)

    title = f"{t1}, {t2} & {t3}{suffix}"
    if len(title) > max_length:
        title = title[:max_length - 3].rstrip() + "..."
    return title


def get_standardized_labels(slot_display: str, category_label: Optional[str] = None) -> List[str]:
    """Returns exactly 4 controlled, standardized taxonomy labels for every digest post."""
    primary_category = category_label or "Tech News"
    return [
        primary_category,
        "DeviceRank Brief",
        slot_display,
        "Tech Digest",
    ]

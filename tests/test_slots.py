"""Tests for slot calculation, time windows, and deterministic title generation."""

from datetime import datetime, timezone
from src.utils.slots import SlotType, build_deterministic_title, get_current_slot, get_standardized_labels


def test_get_current_slot_boundaries():
    # Morning: 00:00 - 07:59 UTC
    dt_morning = datetime(2026, 8, 29, 3, 30, tzinfo=timezone.utc)
    slot_m = get_current_slot(dt_morning)
    assert slot_m.slot_type == SlotType.MORNING
    assert slot_m.slot_id == "2026-08-29-morning"
    assert slot_m.slot_display == "Morning Brief"

    # Midday: 08:00 - 15:59 UTC
    dt_midday = datetime(2026, 8, 29, 11, 45, tzinfo=timezone.utc)
    slot_d = get_current_slot(dt_midday)
    assert slot_d.slot_type == SlotType.MIDDAY
    assert slot_d.slot_id == "2026-08-29-midday"
    assert slot_d.slot_display == "Midday Brief"

    # Evening: 16:00 - 23:59 UTC
    dt_evening = datetime(2026, 8, 29, 19, 20, tzinfo=timezone.utc)
    slot_e = get_current_slot(dt_evening)
    assert slot_e.slot_type == SlotType.EVENING
    assert slot_e.slot_id == "2026-08-29-evening"
    assert slot_e.slot_display == "Evening Brief"


def test_get_current_slot_override():
    dt = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    slot = get_current_slot(dt, slot_override="evening")
    assert slot.slot_type == SlotType.EVENING
    assert slot.slot_id == "2026-08-29-evening"
    assert slot.slot_display == "Evening Brief"


def test_build_deterministic_title_grammar():
    topics = ["Pixel 11", "DLSS 5", "iOS 27"]
    title = build_deterministic_title(topics, "Night Brief")
    assert title == "Pixel 11, DLSS 5 & iOS 27 — DeviceRank Night Brief"

    # With morning brief
    title_m = build_deterministic_title(topics, "Morning Brief")
    assert title_m == "Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief"


def test_build_deterministic_title_length_capping():
    long_topics = [
        "Samsung Galaxy S26 Ultra Super Edition with Advanced Snapdragon 8 Gen 5",
        "Meta Massive 18 Billion Dollar Legal Settlement in California District Court",
        "OpenAI Revolutionary Next Generation Search Engine Powered by GPT-5 Reasoning",
    ]
    title = build_deterministic_title(long_topics, "Morning Brief", max_length=68)
    assert len(title) <= 68
    assert " — DeviceRank Morning Brief" in title


def test_get_standardized_labels():
    labels = get_standardized_labels("Morning Brief", "Tech News")
    assert len(labels) == 4
    assert labels == ["Tech News", "DeviceRank Brief", "Morning Brief", "Tech Digest"]

    labels_gadgets = get_standardized_labels("Evening Brief", "Gadgets")
    assert len(labels_gadgets) == 4
    assert labels_gadgets == ["Gadgets", "DeviceRank Brief", "Evening Brief", "Tech Digest"]

"""Tests for Slack Block Kit builders."""

import pytest
from watercooler_dashboard.blocks import build_dashboard_blocks, _get_status_emoji


def test_build_dashboard_blocks_empty():
    """Test building dashboard blocks with no threads."""
    blocks = build_dashboard_blocks([])
    assert len(blocks) >= 2  # Header + empty message
    assert blocks[0]["type"] == "header"


def test_build_dashboard_blocks_with_threads():
    """Test building dashboard blocks with sample threads."""
    threads = [
        {
            "topic": "test-thread",
            "status": "OPEN",
            "ball_owner": "Alice",
            "created": "2025-10-29T00:00:00Z",
            "last_update": "2025-10-29T01:00:00Z",
            "entry_count": 3,
            "has_new": True,
        }
    ]
    blocks = build_dashboard_blocks(threads)
    assert len(blocks) > 2
    assert blocks[0]["type"] == "header"


def test_get_status_emoji():
    """Test status emoji mapping."""
    assert _get_status_emoji("OPEN") == ":large_green_circle:"
    assert _get_status_emoji("IN_REVIEW") == ":large_yellow_circle:"
    assert _get_status_emoji("BLOCKED") == ":red_circle:"
    assert _get_status_emoji("CLOSED") == ":white_check_mark:"
    assert _get_status_emoji("UNKNOWN") == ":question:"

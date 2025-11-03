"""Build Slack Block Kit components for the dashboard."""

from typing import Any
from datetime import datetime


def build_dashboard_blocks(threads_data: list[dict[str, Any]]) -> list[dict]:
    """Build Slack Block Kit blocks for the dashboard view.

    Args:
        threads_data: List of thread metadata dictionaries.

    Returns:
        List of Block Kit block dictionaries.
    """
    blocks = []

    # Header
    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Watercooler Dashboard",
                "emoji": True,
            },
        }
    )

    if not threads_data:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No threads found. Start a conversation in your Watercooler threads repository._",
                },
            }
        )
        return blocks

    # Summary stats
    active_count = sum(1 for t in threads_data if t["status"] == "OPEN")
    review_count = sum(1 for t in threads_data if t["status"] == "IN_REVIEW")
    blocked_count = sum(1 for t in threads_data if t["status"] == "BLOCKED")

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{len(threads_data)} total threads* | {active_count} active | {review_count} in review | {blocked_count} blocked",
            },
        }
    )

    blocks.append({"type": "divider"})

    # Group threads by status
    status_groups = {
        "OPEN": [],
        "IN_REVIEW": [],
        "BLOCKED": [],
        "CLOSED": [],
    }

    for thread in threads_data:
        status = thread["status"]
        if status not in status_groups:
            status_groups["OTHER"] = []
            status_groups["OTHER"].append(thread)
        else:
            status_groups[status].append(thread)

    # Render each status group
    for status, threads in status_groups.items():
        if not threads:
            continue

        # Status header
        emoji = _get_status_emoji(status)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{emoji} {status}*",
                },
            }
        )

        # Thread cards
        for thread in threads:
            blocks.extend(_build_thread_blocks(thread))

        blocks.append({"type": "divider"})

    # Refresh button
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Refresh Dashboard",
                        "emoji": True,
                    },
                    "action_id": "refresh_dashboard",
                }
            ],
        }
    )

    return blocks


def _build_thread_blocks(thread: dict[str, Any]) -> list[dict]:
    """Build blocks for a single thread.

    Args:
        thread: Thread metadata dictionary.

    Returns:
        List of Block Kit blocks for the thread.
    """
    blocks = []

    # Thread name with NEW marker
    new_marker = " :sparkles: *NEW*" if thread["has_new"] else ""
    ball_marker = " :tennis:" if thread.get("has_ball") else ""
    topic_text = _escape_mrkdwn(thread.get("topic"))

    blocks.append(
        {
            "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{topic_text}*{new_marker}{ball_marker}",
                },
            }
        )

    # Thread details
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Ball: *{_escape_mrkdwn(thread.get('ball_owner'))}* | "
                        f"Entries: {thread['entry_count']} | Last: {_format_timestamp(thread['last_update'])}"
                    ),
                }
            ],
        }
    )

    return blocks


def _get_status_emoji(status: str) -> str:
    """Get emoji for a thread status.

    Args:
        status: Thread status string.

    Returns:
        Emoji string.
    """
    emoji_map = {
        "OPEN": ":large_green_circle:",
        "IN_REVIEW": ":large_yellow_circle:",
        "BLOCKED": ":red_circle:",
        "CLOSED": ":white_check_mark:",
    }
    return emoji_map.get(status, ":question:")


def _format_timestamp(timestamp: str | None) -> str:
    """Format a timestamp for display.

    Args:
        timestamp: ISO 8601 timestamp string.

    Returns:
        Formatted timestamp string.
    """
    if not timestamp:
        return "Unknown"

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return timestamp


def _escape_mrkdwn(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

"""Main Slack Bolt application for Watercooler Dashboard."""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from watercooler_dashboard.thread_parser import ThreadParser
from watercooler_dashboard.blocks import build_dashboard_blocks

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize Slack app
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))


@app.event("app_home_opened")
def update_home_tab(client, event, logger):
    """Update the App Home tab when a user opens it."""
    try:
        user_id = event["user"]
        logger.info(f"Home tab opened by user {user_id}")

        # Parse threads from the current context
        threads_base = os.getenv("WATERCOOLER_THREADS_BASE")
        parser = ThreadParser(threads_base=threads_base)
        threads_data = parser.get_all_threads()

        # Build Block Kit view
        blocks = build_dashboard_blocks(threads_data)

        # Publish the view
        client.views_publish(
            user_id=user_id,
            view={
                "type": "home",
                "blocks": blocks,
            },
        )
        logger.info(f"Successfully updated home tab for user {user_id}")

    except Exception as e:
        logger.error(f"Error updating home tab: {e}", exc_info=True)


@app.command("/watercooler-refresh")
def handle_refresh_command(ack, respond, client, context):
    """Handle the /watercooler-refresh slash command."""
    ack()
    try:
        user_id = context["user_id"]

        # Trigger a home tab refresh by calling the event handler
        parser = ThreadParser()
        threads_data = parser.get_all_threads()
        blocks = build_dashboard_blocks(threads_data)

        client.views_publish(
            user_id=user_id,
            view={
                "type": "home",
                "blocks": blocks,
            },
        )

        respond("Dashboard refreshed!")
    except Exception as e:
        logger.error(f"Error in refresh command: {e}", exc_info=True)
        respond(f"Error refreshing dashboard: {str(e)}")


def main():
    """Start the Slack app in Socket Mode."""
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN environment variable is required")

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is required")

    logger.info("Starting Watercooler Dashboard...")
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()

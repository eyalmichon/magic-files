"""Entry point — start the Telegram bot."""
from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler

from bot.config import get_settings
from bot.drive import get_service
from bot.handlers import build_conversation_handler, build_setup_handler, start

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def main() -> None:
    token = get_settings().telegram_bot_token

    logger.info("Authenticating with Google Drive...")
    get_service()
    logger.info("Drive auth OK")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(build_setup_handler())
    app.add_handler(build_conversation_handler())

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

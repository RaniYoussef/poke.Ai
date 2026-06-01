import logging

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from telegram_bot.config import settings
from telegram_bot.handlers.start import start_handler
from telegram_bot.handlers.message import message_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def main() -> None:
    builder = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN)

    if settings.PROXY_URL:
        builder = builder.proxy(settings.PROXY_URL)

    app = builder.build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logging.info("Poke.AI Telegram bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()

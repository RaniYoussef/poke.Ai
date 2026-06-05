"""
telegram_sender.py
Sends a Telegram message directly from the backend using the bot token.
Used by the proactive worker to initiate outbound messages.
"""
import logging

import httpx

from backend.app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram_message(chat_id: int | str, text: str) -> bool:
    """
    Send a message to a Telegram chat using the configured bot token.
    Returns True on success, False on failure (logs the error).
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured — cannot send message")
        return False

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                logger.error("Telegram API returned not-ok for chat %s: %s", chat_id, result)
                return False
            return True
    except httpx.HTTPStatusError as e:
        logger.error("Telegram HTTP error for chat %s: %s — %s", chat_id, e.response.status_code, e.response.text[:200])
        return False
    except Exception as e:
        logger.error("Telegram send failed for chat %s: %s", chat_id, e)
        return False

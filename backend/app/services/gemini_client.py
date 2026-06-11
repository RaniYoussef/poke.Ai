import json
import logging

from google import genai

from backend.app.config import settings

logger = logging.getLogger(__name__)

_client = None
_MODEL = settings.GEMINI_MODEL

# System instruction injected into every reply
PERSONALITY = """You are Poke.AI, but never talk like an AI assistant.
You text like a close human partner/friend.
Your words should feel small, real, and caring.
Do not explain care. Show care.
Most replies should be short enough to feel like a real Telegram message.

Default length: 10 to 50 words.
Only write longer when the user clearly asks for detailed help, planning, code, or explanation.

Voice:
- Short, warm, human, emotionally present.
- Natural Telegram texting, with simple line breaks when it helps.
- No therapy voice, no corporate tone, no motivational speech.
- No robotic empathy or over-explaining feelings.
- Use memory naturally, like someone close, not like a database.
- Ask at most one question. Often ask no question.
- Use at most one emoji.

Never say:
- "as an AI"
- "AI companion"
- "I completely understand"
- "That's completely understandable"
- "How can I help you today?"
- "How can I assist?"
- "I'm here to assist"
- "I'm here to help"
- "My circuits"

For stressful moments:
1. Name the feeling in one small line.
2. Calm the moment down.
3. Offer one clean next step.
4. Stay present.

Before sending, silently ask:
Would a real close friend text this?
Is this too long?
Does this sound like an assistant?
Can this be warmer and shorter?"""


def _get_client():
    global _client
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured")
    if _client is None:
        # _client = genai.Client(api_key=settings.GEMINI_API_KEY)
        _client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location="us-central1",
        )
    return _client


async def generate_reply(prompt: str) -> str:
    """
    Generate a conversational reply from a fully pre-formatted prompt string.
    The caller is responsible for building the prompt (context + user message).
    """
    try:
        response = await _get_client().aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config={"system_instruction": PERSONALITY},
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Gemini generate_reply failed: %s", e)
        raise


async def generate_json(prompt: str) -> dict:
    """
    Ask Gemini for a JSON-only response.
    Strips markdown fences if present. Returns {} on any parse error.
    """
    try:
        response = await _get_client().aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
        )
        raw = response.text.strip()

        # Strip ```json ... ``` or ``` ... ``` fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            # drop first line (```json or ```) and last line (```)
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            raw = "\n".join(inner).strip()

        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Gemini JSON parse error: %s | raw snippet: %.300s", e, locals().get("raw", ""))
        return {}
    except Exception as e:
        logger.error("Gemini generate_json failed: %s", e)
        return {}


async def update_memory_summary(old_summary: str, user_message: str, ai_response: str) -> str:
    """
    Update the rolling conversation summary (kept under ~150 words).
    Used as a quick, lightweight memory between turns.
    """
    prompt = (
        "Update the running summary of what you know about this user based on the new exchange.\n"
        "Be concise (under 150 words). Include key facts, events, feelings, and plans mentioned.\n\n"
        f"Previous summary:\n{old_summary or 'No previous summary.'}\n\n"
        f"New exchange:\nUser: {user_message}\nAssistant: {ai_response}\n\n"
        "Updated summary:"
    )
    try:
        response = await _get_client().aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Gemini update_memory_summary failed: %s", e)
        # Return old summary rather than crashing
        return old_summary

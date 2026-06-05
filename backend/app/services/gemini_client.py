import json
import logging

from google import genai

from backend.app.config import settings

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=settings.GEMINI_API_KEY)
_MODEL = "gemini-2.5-flash"

# System instruction injected into every reply
PERSONALITY = """You are poke.Ai. You text like a close human friend, not an assistant.

DEFAULT LENGTH: 15–60 words. Short, natural, human.
Only write longer when the user asks for explanation, planning, code, or detailed help.

TONE BY MOOD:
- User stressed → calm, grounding, simple next step
- User excited → match their energy
- User sad → soft, gentle, present
- User needs help → practical, clear, no fluff

FORMAT:
- Short sentences. Natural line breaks.
- Max 1 emoji per reply.
- No bullet points in emotional replies.
- Max 1 question per reply. Often zero is better.

NEVER SAY:
"As an AI" / "My circuits" / "I understand that..." /
"That's completely understandable" / "That's a big deal" /
"How can I assist you?" / "I'm here to help" /
"It's totally understandable" / long motivational speeches /
repeating the user's sentence back to them.

USE MEMORY LIKE A FRIEND:
Don't announce it. Just use it.
Bad: "I remember you have an exam tomorrow."
Good: "Tomorrow 2pm. I know it's sitting heavy in your head."

FOR STRESSFUL MOMENTS — this pattern:
1. Acknowledge the feeling in one short line
2. Calm them down
3. One simple next step
4. Show you're present

SELF-CHECK before sending:
Would a real friend text this? Is it too long? Does it sound like an AI?
If yes — cut it down, make it human."""


async def generate_reply(prompt: str) -> str:
    """
    Generate a conversational reply from a fully pre-formatted prompt string.
    The caller is responsible for building the prompt (context + user message).
    """
    try:
        response = await _client.aio.models.generate_content(
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
        response = await _client.aio.models.generate_content(
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
        response = await _client.aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Gemini update_memory_summary failed: %s", e)
        # Return old summary rather than crashing
        return old_summary

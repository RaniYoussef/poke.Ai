import vertexai
from vertexai.generative_models import GenerativeModel

from backend.app.config import settings

vertexai.init(project=settings.GOOGLE_CLOUD_PROJECT, location="us-central1")

_SYSTEM_PROMPT = (
    "You are Poke.AI, a warm, caring, and proactive AI friend. "
    "You remember things about the user and genuinely care about their wellbeing. "
    "Keep responses concise, friendly, and conversational. Never be robotic or formal."
)

_chat_model = GenerativeModel(
    model_name="gemini-2.0-flash",
    system_instruction=_SYSTEM_PROMPT,
)

_summary_model = GenerativeModel(model_name="gemini-2.0-flash")


async def generate_reply(memory_summary: str, recent_messages: list[dict], user_message: str) -> str:
    parts = []

    if memory_summary:
        parts.append(f"[What I know about you]\n{memory_summary}\n")

    if recent_messages:
        parts.append("[Recent conversation]")
        for msg in recent_messages:
            label = "You" if msg["role"] == "user" else "Me"
            parts.append(f"{label}: {msg['content']}")

    parts.append(f"\nUser: {user_message}")

    response = await _chat_model.generate_content_async("\n".join(parts))
    return response.text.strip()


async def update_memory_summary(old_summary: str, user_message: str, ai_response: str) -> str:
    prompt = (
        "Update the running summary of what you know about this user based on the new exchange.\n"
        "Be concise (under 150 words). Include key facts, events, feelings, and plans mentioned.\n\n"
        f"Previous summary:\n{old_summary or 'No previous summary.'}\n\n"
        f"New exchange:\nUser: {user_message}\nAssistant: {ai_response}\n\n"
        "Updated summary:"
    )
    response = await _summary_model.generate_content_async(prompt)
    return response.text.strip()

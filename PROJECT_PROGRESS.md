# Poke.AI Project Progress

Last updated: June 5, 2026

Branch snapshot: `rani'sbranch`, created from the current `origin/main` at commit `dad5fca`.

## Product Vision

Poke.AI is being built as a proactive AI friend/life companion that users interact with through Telegram. The core idea is not just to answer messages, but to remember useful context about the user and reach out first when something important deserves a follow-up.

## What We Have Built

### 1. Project Foundation

The repo now has a clear Python backend structure:

- `backend/` contains the FastAPI app, API routes, services, and workers.
- `telegram_bot/` contains the Telegram bot entry point and handlers.
- `db/` contains the shared MongoDB connection.
- `requirements.txt` pins the main dependencies.
- `.env.example` documents the basic runtime configuration shape.

The current stack is:

- FastAPI for the backend API.
- MongoDB with Motor for async persistence.
- `python-telegram-bot` for the Telegram interface.
- Google Gemini via `google-genai` for AI replies, memory summaries, and structured extraction.
- HTTPX for backend-to-Telegram and bot-to-backend calls.

### 2. MongoDB Integration

The project has a shared async MongoDB connection in `db/mongodb.py`.

The backend checks MongoDB during startup and through `/health`. It also creates useful indexes for the main collections:

- `messages`
- `memories`
- `events`
- `proactive_tasks`
- `memory_profiles`

This gives the project a real persistence layer for users, conversations, memories, events, and future proactive messages.

### 3. FastAPI Backend

The backend app is wired in `backend/app/main.py` with version `0.2.0`.

Implemented routes include:

- `GET /` - basic backend running message.
- `GET /health` - confirms MongoDB connectivity.
- `POST /test/user` - creates a test user.
- `POST /users/telegram` - upserts a Telegram user.
- `POST /messages` - stores a user or assistant message.
- `GET /messages/{telegram_id}` - returns recent messages.
- `POST /memories` - stores a memory.
- `GET /memories/{telegram_id}` - returns important memories.
- `POST /events` - stores an event.
- `GET /events/{telegram_id}` - returns user events.
- `POST /proactive-tasks` - creates a proactive task.
- `GET /proactive-tasks/pending` - returns due pending tasks.
- `PATCH /proactive-tasks/{task_id}/sent` - marks a task as sent.
- `POST /agent/process-message` - runs the main AI conversation flow.

### 4. Telegram Bot

The Telegram bot is implemented in `telegram_bot/`.

Completed behavior:

- Starts polling through `telegram_bot/bot.py`.
- Supports `/start`.
- Saves or updates Telegram user data in MongoDB.
- Handles normal text messages.
- Calls the backend agent endpoint at `/agent/process-message`.
- Sends the generated AI reply back to the Telegram user.
- Supports an optional proxy URL through configuration.

This means Telegram is already connected as the main user-facing channel.

### 5. AI Reply Pipeline

The main reactive conversation flow lives in `backend/app/services/agent_processor.py`.

For each user message, it:

- Verifies the Telegram user exists.
- Saves the incoming user message.
- Builds user context from MongoDB.
- Formats the context into a Gemini-ready prompt.
- Generates a reply using Gemini.
- Saves the assistant reply.
- Starts background post-processing for memory updates and extraction.

There is also a fallback response if Gemini fails, so the bot does not fully break during an AI call error.

### 6. Context Building

The context builder in `backend/app/services/context_builder.py` gathers the information needed for personalized replies:

- User profile data.
- Rolling long-term conversation summary.
- Memory profile fields like likes, dislikes, goals, emotional notes, and communication style.
- Important memories.
- Recent conversation messages.
- Open events that still need follow-up.
- Pending proactive tasks.

This context is formatted into a clean prompt rather than dumping raw database documents into Gemini.

### 7. Gemini Integration

The Gemini client in `backend/app/services/gemini_client.py` currently uses `gemini-2.5-flash`.

Implemented AI helpers:

- `generate_reply()` for normal conversational replies.
- `generate_json()` for structured extraction.
- `update_memory_summary()` for maintaining a rolling conversation summary.

The assistant personality prompt is already defined. It guides Poke.AI to respond like a close friend: short, natural, warm, and not assistant-like.

### 8. Memory Extraction System

After each exchange, `backend/app/services/memory_extractor.py` asks Gemini to extract useful structured information.

It can save:

- New memories.
- Profile updates.
- Events.
- Proactive tasks.
- Mood/emotional pattern memories.

This is the foundation for Poke.AI becoming more personal over time.

### 9. Rolling Conversation Summary

After a message exchange, the backend asks Gemini to update a concise running summary of the user and stores it as a `conversation_summary` memory.

This helps the system keep long-term continuity without sending the full chat history every time.

### 10. Proactive Worker

The proactive worker is implemented in `backend/app/workers/proactive_worker.py`.

Completed behavior:

- Finds due pending proactive tasks.
- Atomically marks a task as `sending` to avoid duplicate sends.
- Checks cooldown rules based on the user's `proactivity_level`.
- Builds proactive context.
- Uses Gemini to generate a short natural outbound Telegram message.
- Sends the message through the Telegram Bot API.
- Saves the proactive message in the message history.
- Marks the task as `sent` or `failed`.

The worker can run as a standalone process through `backend/app/workers/run_worker.py`. There is also a setting to run it inside FastAPI for development.

## End-to-End Flow Achieved

The project now supports this main flow:

1. User opens Telegram and sends `/start`.
2. Bot saves the user in MongoDB.
3. User sends a text message.
4. Bot forwards the message to the backend agent endpoint.
5. Backend saves the message.
6. Backend builds context from user data, memories, messages, events, and tasks.
7. Gemini generates a personalized reply.
8. Backend saves the reply.
9. Bot sends the reply back to the user.
10. Background processing updates the summary and extracts memories/events/tasks.
11. Proactive worker can later send follow-up messages when tasks become due.

## Current Limitations And Gaps

These are the main things not fully finished yet:

- No automated tests are currently included.
- The model and schema files are mostly empty placeholders.
- There is no authentication or API protection yet.
- There is no frontend/admin dashboard yet.
- `.env.example` does not currently list `GEMINI_API_KEY`, although the backend config expects it.
- The README still marks Gemini setup as a TODO.
- There is no migration system or strict database schema enforcement.
- Event time extraction depends on Gemini returning parseable datetime values.
- The proactive worker exists, but still needs end-to-end testing with real Telegram and Gemini credentials.

## Ready For The Next Task

The foundation is strong enough to move into the next development step. Good next targets would be:

- Add automated tests for the backend routes and agent services.
- Fix and complete environment documentation.
- Add stronger schema/model definitions.
- Run a full Telegram-to-backend-to-Gemini integration test.
- Improve proactive task scheduling and event time parsing.
- Add user settings for tone, timezone, and proactivity level.
- Build an admin/debug view for users, messages, memories, events, and proactive tasks.

## Notes

The ignored local `backend/.env` file was not documented or copied here because it may contain secrets. This progress document is based on the tracked project files and the current clean branch state.

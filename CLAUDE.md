# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Locally (requires .env)
python -m bot.main

# Via Docker
docker build -t resume-bot . && docker run --env-file .env resume-bot
```

There are no automated tests. Manual testing is done by sending `/start` to `@test_hhbothhv23_bot` on Telegram.

## Deploy

Push to `main` → GitHub Actions (`deploy.yml`) → Railway auto-deploys via GraphQL `serviceInstanceDeploy` with the commit SHA. Railway project: service ID `9af035bf-5518-42b1-8e7b-4026f7f18547`, environment `f70012a2-0289-47af-aaf1-28f4ae18935e`.

## Architecture

**Entry point:** `bot/main.py` — creates `Bot` + `Dispatcher`, registers 3 routers, starts long-polling.

**FSM flow (aiogram 3.x):**
1. `/start` → `OnboardingStates` (desired position → name → contacts → city → optional file upload)
2. After onboarding → `InterviewStates` (6 stages: summary → work experience loop → skills → education → extras)
3. After interview → `ResumeStates` (viewing draft, editing)

All FSM state is stored in aiogram `MemoryStorage` (lost on restart) **and** persisted to `rb_users.interview_state` JSONB via `db.save_interview_state()` after each stage. On `/start`, the bot detects existing profiles and skips onboarding.

**Supabase (database.py):**
- Supabase Python client is synchronous — all calls are wrapped in `asyncio.to_thread()`.
- **Critical pattern:** never use `.maybe_single().execute()` — it returns `None` (not an object with `.data`) when no row is found. Always use `.limit(1).execute()` and check `result.data or []`.
- All tables use the `rb_` prefix (the Supabase project `hh-bot` already has unrelated tables without this prefix).
- `rb_users.id` is the Telegram user ID (BIGINT), not a UUID.

**AI (services/ai_service.py):**
- All OpenAI calls go through the internal `_chat()` helper which retries on `APITimeoutError`/`RateLimitError`.
- `gpt-4o-mini` for fast/cheap tasks (skill suggestions, clarifying questions, position titles).
- `gpt-4o` for generation tasks (resume generation, resume improvement, file parsing).
- `_RESUME_SYSTEM_PROMPT` defines the strict quality rules for generated resumes (ATS-optimized, ≥70% bullets with metrics, Russian hh.ru format).
- `generate_resume_text(interview_data)` is the main alias used by `interview.py`; it assembles `profile_data` from raw FSM state and calls `generate_resume()`.
- `_build_plain_resume()` in `interview.py` is the AI-free fallback if OpenAI fails.

**Schema/handler mismatch to know about:** `rb_candidate_profiles` has `email` and `phone` columns, but onboarding collects them as a single free-text string. That string is stored in `raw_data->contacts` (JSONB), not in the `email`/`phone` columns. Do not try to pass a `contacts` key directly to Supabase insert/update — it will throw a column-not-found error.

## Key files

| File | Purpose |
|------|---------|
| `bot/states.py` | All FSM state classes |
| `bot/database.py` | All Supabase operations |
| `bot/services/ai_service.py` | All OpenAI calls + resume quality rules |
| `bot/handlers/start.py` | `/start`, onboarding FSM, `/export`, `/delete_profile` |
| `bot/handlers/interview.py` | 6-stage interview FSM, AI resume generation trigger |
| `bot/handlers/resume.py` | Resume editing commands (`короче`, `формальнее`, etc.) |
| `migrations/001_initial.sql` | Schema — already applied to Supabase, do not re-run |
| `admin/index.html` | Static funnel dashboard (calls Supabase REST directly) |

## Environment

All config is loaded from `.env` via `pydantic-settings`. Required vars: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `ADMIN_TELEGRAM_ID`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`. See `.env.example`.

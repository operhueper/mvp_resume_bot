# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Locally (requires .env)
python3 -m bot.main

# Via Docker
docker build -t resume-bot . && docker run --env-file .env resume-bot
```

There are no automated tests. Manual testing is done by sending `/start` to `@test_hhbothhv23_bot` on Telegram.

## Running the dashboard

```bash
cd dashboard
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

The dashboard is a separate FastAPI service (`dashboard/`) deployed independently on Railway. It uses `psycopg2` (synchronous, direct Postgres connection) — not the Supabase Python client. Additional env vars required: `SUPABASE_DB_URL` (Postgres connection string), `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `SECRET_KEY`.

## Deploy

Push to `main` → GitHub Actions (`deploy.yml`) → Railway auto-deploys both services via GraphQL `serviceInstanceDeploy` with the commit SHA.
- Bot service ID: `9af035bf-5518-42b1-8e7b-4026f7f18547`
- Environment: `f70012a2-0289-47af-aaf1-28f4ae18935e`

## Architecture

**Entry point:** `bot/main.py` — creates `Bot` + `Dispatcher`, registers 4 routers (start → interview → resume → fallback), sets Telegram bot commands via `set_my_commands`, starts long-polling.

**FSM flow (aiogram 3.x):**
1. `/start` → `OnboardingStates` (choosing_path → coaching_questions? → desired_position → name → city → upload_resume_prompt)
2. After onboarding or file upload → `InterviewStates` (summary → work experience loop → skills → education → extras → generation)
3. After interview → `ResumeStates` (viewing draft, editing)
4. File upload path uses `ImprovementStates.reviewing_parsed_data` briefly before routing to `show_block_selection`

`InterviewStates` also has two navigation states:
- `block_selection` — hub where user picks which section to fill/edit (shown by `/back` command, "Продолжить интервью", and after validation). Displays `_validation_issues` from state if present.
- `generation_confirm` — waiting after pre-generation validation warning

All FSM state is stored in aiogram `MemoryStorage` (lost on restart) **and** persisted to `rb_users.interview_state` JSONB via `db.save_interview_state()` after each stage. On `/start`, the bot detects existing profiles and shows the block selection hub instead of restarting.

**Internal FSM state keys (prefixed with `_`):**
These are transient flags stored alongside user data in FSM state. Never shown to users:
- `_skip_validation` — set to `True` after validation warning shown; prevents re-running validation on retry. Reset to `False` when user edits any block via `block_selection` callbacks, and after successful resume generation.
- `_validation_issues` — list of strings from last `validate_resume_data()` run; shown in `show_block_selection` as warnings. Cleared alongside `_skip_validation`.
- `_skills_append_mode` — merge new skills with existing instead of replacing.
- `_pending_summary`, `_pending_responsibilities`, `_pending_skills` — hold the value during quality warnings (short text check).
- `_ai_summary_draft` — draft from `generate_summary_help()` pending user accept/decline.

**Block selection hub (`show_block_selection` in `interview.py`):**
Central navigation function. A block is considered "filled" only if it's non-empty AND not the string `"нет"`/`"no"` — so education or extras entered as "нет" correctly show ➕. The "▶️ Создать резюме" button appears only when the three required blocks (summary, work_exp, skills) are all filled.

**Skills input (`InterviewStates.skills_input`):**
Uses `parse_skills_from_text()` (AI) to extract skills from any input format — comma lists, numbered lists, plain sentences, pasted text. `_skills_append_mode=True` merges new skills with existing (deduped by lowercase).

**Achievement nudges:**
`handle_we_achievements` nudges up to `MAX_ACHIEVEMENT_NUDGES=2` times when the answer lacks quantification. A response counts as "has numbers" only if it contains `%` or a 2+ digit standalone number (`\b\d{2,}\b`) — a single digit like "5" is not enough.

**HH API (`services/hh_service.py`):**
- `analyze_market_salary(query)` — fetches salary data from active vacancies (area=113, Russia).
- `get_skills_for_position(position)` — fetches each vacancy individually to extract `key_skills`. Can make up to 21 HTTP requests per call (1 search + 20 detail pages). Falls back silently; interview.py falls back to AI suggestions if HH returns < 5 skills.

**Pre-generation validation:**
`validate_resume_data()` (gpt-4o-mini) checks content quality. Issues are stored in `_validation_issues`. "✏️ Исправить" shows block_selection with issues listed. "▶️ Создать как есть" proceeds immediately. Both `_skip_validation` and `_validation_issues` are reset when user edits any block or after successful generation — so editing data always re-triggers validation on the next generation attempt.

**Telegram message length:**
All resume output uses `_send_long_message()` (defined in interview.py and resume.py) which splits text into ≤4000-char chunks. kwargs like `reply_markup` are attached only to the last chunk.

**Context-aware routing after summary edit:**
`cb_summary_ok` checks if `work_experiences` already exist. If yes (user is re-editing from block_selection), returns to block_selection instead of starting the work experience flow. `_save_summary` does NOT reset `work_experiences` when they already exist.

**Supabase (database.py):**
- Supabase Python client is synchronous — all calls are wrapped in `asyncio.to_thread()`.
- **Critical pattern:** never use `.maybe_single().execute()` — it returns `None` (not an object with `.data`) when no row is found. Always use `.limit(1).execute()` and check `result.data or []`.
- All tables use the `rb_` prefix (the Supabase project `hh-bot` already has unrelated tables without this prefix).
- `rb_users.id` is the Telegram user ID (BIGINT), not a UUID.

**AI (services/ai_service.py):**
- All OpenAI calls go through the internal `_chat()` helper which retries on `APITimeoutError`/`RateLimitError`.
- `gpt-4o-mini` — fast/cheap: skill suggestions, clarifying questions, position titles, skills parsing, summary draft, pre-gen validation, gender detection.
- `gpt-4o` — generation: resume generation, resume improvement, file parsing.
- Resume generation prompt (`_build_resume_system_prompt`) explicitly forbids inventing metrics — only numbers explicitly stated by the candidate may appear in bullets.
- `_build_plain_resume()` in `interview.py` is the AI-free fallback if OpenAI fails.

**Schema/handler mismatch to know about:** `rb_candidate_profiles` has `email` and `phone` columns, but onboarding does not collect them — contacts are added by the user manually after receiving the resume. Do not pass a `contacts` key directly to Supabase insert/update — it will throw a column-not-found error.

## Key files

| File | Purpose |
|------|---------|
| `bot/states.py` | All FSM state classes |
| `bot/database.py` | All Supabase operations (bot) |
| `bot/services/ai_service.py` | All OpenAI calls + resume quality rules + validation |
| `bot/services/hh_service.py` | HH.ru API: vacancy search, salary data, skills extraction |
| `bot/handlers/start.py` | `/start`, onboarding FSM, file upload, block selection triggers |
| `bot/handlers/interview.py` | Interview FSM, `show_block_selection`, `/back`, `/save`, resume generation |
| `bot/handlers/resume.py` | Resume editing commands (`короче`, `формальнее`, etc.) |
| `dashboard/app.py` | FastAPI admin dashboard (funnel stats, user/resume views) |
| `dashboard/db.py` | Direct psycopg2 queries for dashboard |
| `migrations/001_initial.sql` | Schema — already applied to Supabase, do not re-run |

## Environment

All bot config is loaded from `.env` via `pydantic-settings`. Required vars: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `ADMIN_TELEGRAM_ID`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`. Dashboard additionally needs: `SUPABASE_DB_URL`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `SECRET_KEY`. See `.env.example`.

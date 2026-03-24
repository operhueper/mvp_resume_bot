"""
Admin dashboard for Resume Bot.
Run: uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import auth
import db as database

app = FastAPI(title="Resume Bot Admin Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_current_user(request: Request) -> str | None:
    token = request.cookies.get("session")
    if not token:
        return None
    return auth.verify_session_token(token)


def login_required(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Jinja2 template filters / globals
# ---------------------------------------------------------------------------

def format_dt(value: str | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            # Parse ISO format, strip microseconds if present
            value = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
        else:
            dt = value
        return dt.strftime(fmt)
    except Exception:
        return str(value)


def time_ago(value: str | None) -> str:
    if not value:
        return "—"
    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        now = datetime.now(timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 60:
            return f"{diff}с назад"
        if diff < 3600:
            return f"{diff // 60}м назад"
        if diff < 86400:
            return f"{diff // 3600}ч назад"
        days = diff // 86400
        if days < 30:
            return f"{days}д назад"
        return format_dt(value, "%d.%m.%Y")
    except Exception:
        return str(value)


def stage_label(stage: str | None) -> str:
    labels = {
        "onboarding": "Онбординг",
        "interview": "Интервью",
        "draft": "Черновик",
        "exported": "Экспорт",
    }
    return labels.get(stage or "", stage or "—")


def truncate(value: str | None, length: int = 100) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= length:
        return value
    return value[:length] + "…"


_KNOWN_EVENTS = {
    "bot_started", "onboarding_completed", "interview_started",
    "interview_completed", "resume_generated", "resume_exported", "profile_deleted",
}


def event_badge_class(event_type: str | None) -> str:
    """Return CSS class suffix for a given event type."""
    t = event_type or ""
    return t if t in _KNOWN_EVENTS else "default"


templates.env.filters["format_dt"] = format_dt
templates.env.filters["time_ago"] = time_ago
templates.env.filters["stage_label"] = stage_label
templates.env.filters["truncate_text"] = truncate
templates.env.filters["event_badge_class"] = event_badge_class


# ---------------------------------------------------------------------------
# Health check (no auth)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
        token = auth.create_session_token(username)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            "session",
            token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,
        )
        return response
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Неверный логин или пароль"},
        status_code=401,
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@login_required
async def index(request: Request):
    stats = database.get_overview_stats()
    funnel = database.get_funnel_stats()
    events = database.get_recent_events(limit=20)

    # Build funnel steps (cumulative, each step includes all further stages)
    total_users = stats["total_users"] or 1
    onboarding_n = sum(funnel.values())
    interview_n = funnel.get("interview", 0) + funnel.get("draft", 0) + funnel.get("exported", 0)
    draft_n = funnel.get("draft", 0) + funnel.get("exported", 0)
    exported_n = funnel.get("exported", 0)

    funnel_steps = [
        {"label": "Онбординг", "count": onboarding_n, "color": "#3b82f6"},
        {"label": "Интервью", "count": interview_n, "color": "#f59e0b"},
        {"label": "Черновик", "count": draft_n, "color": "#10b981"},
        {"label": "Экспорт", "count": exported_n, "color": "#8b5cf6"},
    ]
    base = funnel_steps[0]["count"] or 1
    for step in funnel_steps:
        step["pct"] = round(step["count"] / base * 100) if base else 0
        step["bar_pct"] = step["pct"]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "active_page": "dashboard",
        "stats": stats,
        "funnel": funnel,
        "funnel_steps": funnel_steps,
        "events": events,
    })


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

@app.get("/users", response_class=HTMLResponse)
@login_required
async def users_list(request: Request):
    users = database.get_all_users()
    return templates.TemplateResponse("users.html", {
        "request": request,
        "active_page": "users",
        "users": users,
    })


# ---------------------------------------------------------------------------
# User detail
# ---------------------------------------------------------------------------

@app.get("/users/{user_id}", response_class=HTMLResponse)
@login_required
async def user_detail(request: Request, user_id: int):
    user = database.get_user_by_id(user_id)
    if not user:
        return HTMLResponse("<h1>Пользователь не найден</h1>", status_code=404)

    profile = database.get_candidate_profile(user_id)
    work_experiences = []
    skills = []
    education = []

    if profile:
        profile_id = profile["id"]
        work_experiences = database.get_work_experiences(profile_id)
        skills = database.get_skills(profile_id)
        education = database.get_education(profile_id)

    resumes = database.get_user_resumes(user_id)

    # Extract interview extras from interview_state JSONB
    interview_state = user.get("interview_state") or {}
    extras = interview_state.get("extras", "")

    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "active_page": "users",
        "user": user,
        "profile": profile,
        "work_experiences": work_experiences,
        "skills": skills,
        "education": education,
        "resumes": resumes,
        "extras": extras,
        "interview_state": interview_state,
    })


# ---------------------------------------------------------------------------
# Resumes list
# ---------------------------------------------------------------------------

@app.get("/resumes", response_class=HTMLResponse)
@login_required
async def resumes_list(request: Request):
    resumes = database.get_all_resumes()
    return templates.TemplateResponse("resumes.html", {
        "request": request,
        "active_page": "resumes",
        "resumes": resumes,
    })


# ---------------------------------------------------------------------------
# Resume detail
# ---------------------------------------------------------------------------

@app.get("/resumes/{resume_id}", response_class=HTMLResponse)
@login_required
async def resume_detail(request: Request, resume_id: str):
    resume = database.get_resume_by_id(resume_id)
    if not resume:
        return HTMLResponse("<h1>Резюме не найдено</h1>", status_code=404)

    return templates.TemplateResponse("resume_detail.html", {
        "request": request,
        "active_page": "resumes",
        "resume": resume,
    })

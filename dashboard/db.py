"""
Supabase queries for the admin dashboard.
Uses httpx directly against Supabase REST API to avoid SDK dependency conflicts.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

_SUPABASE_URL: str = ""
_SUPABASE_KEY: str = ""


def _headers() -> dict[str, str]:
    global _SUPABASE_URL, _SUPABASE_KEY
    if not _SUPABASE_URL:
        _SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
        _SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    return {
        "apikey": _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _get(table: str, params: dict | None = None) -> list[dict]:
    url = f"{_SUPABASE_URL}/rest/v1/{table}"
    resp = httpx.get(url, headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _count(table: str, params: dict | None = None) -> int:
    url = f"{_SUPABASE_URL}/rest/v1/{table}"
    headers = {**_headers(), "Prefer": "count=exact"}
    resp = httpx.head(url, headers=headers, params=params or {}, timeout=15)
    content_range = resp.headers.get("content-range", "0/0")
    try:
        return int(content_range.split("/")[-1])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Overview / stats
# ---------------------------------------------------------------------------

def get_overview_stats() -> dict[str, Any]:
    total_users = _count("rb_users")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    users_today = _count("rb_users", {"created_at": f"gte.{today_start.isoformat()}"})

    total_resumes = _count("rb_resumes")

    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    resumes_this_week = _count("rb_resumes", {"created_at": f"gte.{week_start.isoformat()}"})

    return {
        "total_users": total_users,
        "users_today": users_today,
        "total_resumes": total_resumes,
        "resumes_this_week": resumes_this_week,
    }


def get_funnel_stats() -> dict[str, int]:
    rows = _get("rb_users", {"select": "current_stage"})
    counts: dict[str, int] = {"onboarding": 0, "interview": 0, "draft": 0, "exported": 0}
    for row in rows:
        stage = row.get("current_stage") or "onboarding"
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def get_recent_events(limit: int = 20) -> list[dict]:
    return _get("rb_events", {
        "select": "id,event_type,user_id,metadata,created_at",
        "order": "created_at.desc",
        "limit": limit,
    })


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

def get_all_users() -> list[dict]:
    return _get("rb_users", {
        "select": "id,telegram_username,current_stage,created_at,last_active_at",
        "order": "created_at.desc",
    })


# ---------------------------------------------------------------------------
# User detail
# ---------------------------------------------------------------------------

def get_user_by_id(user_id: int) -> dict | None:
    rows = _get("rb_users", {"id": f"eq.{user_id}", "limit": 1})
    return rows[0] if rows else None


def get_candidate_profile(user_id: int) -> dict | None:
    rows = _get("rb_candidate_profiles", {"user_id": f"eq.{user_id}", "limit": 1})
    return rows[0] if rows else None


def get_work_experiences(profile_id: str) -> list[dict]:
    return _get("rb_work_experiences", {
        "profile_id": f"eq.{profile_id}",
        "order": "order_index.asc",
    })


def get_skills(profile_id: str) -> list[dict]:
    return _get("rb_skills", {"profile_id": f"eq.{profile_id}"})


def get_education(profile_id: str) -> list[dict]:
    return _get("rb_education", {"profile_id": f"eq.{profile_id}"})


def get_user_resumes(user_id: int) -> list[dict]:
    return _get("rb_resumes", {
        "user_id": f"eq.{user_id}",
        "order": "created_at.desc",
    })


# ---------------------------------------------------------------------------
# Resumes list
# ---------------------------------------------------------------------------

def get_all_resumes() -> list[dict]:
    resumes = _get("rb_resumes", {
        "select": "id,user_id,title,version,created_at,content",
        "order": "created_at.desc",
    })
    if not resumes:
        return []

    user_ids = list({r["user_id"] for r in resumes if r.get("user_id")})
    ids_param = f"in.({','.join(str(i) for i in user_ids)})"

    users = _get("rb_users", {"id": ids_param, "select": "id,telegram_username"})
    users_map = {u["id"]: u for u in users}

    profiles = _get("rb_candidate_profiles", {
        "user_id": ids_param,
        "select": "user_id,full_name,desired_position",
    })
    profiles_map = {p["user_id"]: p for p in profiles}

    for r in resumes:
        uid = r.get("user_id")
        r["user"] = users_map.get(uid, {})
        r["profile"] = profiles_map.get(uid, {})

    return resumes


# ---------------------------------------------------------------------------
# Resume detail
# ---------------------------------------------------------------------------

def get_resume_by_id(resume_id: str) -> dict | None:
    rows = _get("rb_resumes", {"id": f"eq.{resume_id}", "limit": 1})
    if not rows:
        return None
    resume = rows[0]

    uid = resume.get("user_id")
    if uid:
        resume["user"] = get_user_by_id(uid) or {}
        resume["profile"] = get_candidate_profile(uid) or {}

    return resume

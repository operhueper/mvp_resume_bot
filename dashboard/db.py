"""
Supabase queries for the admin dashboard.
All calls are synchronous (Supabase Python client is sync).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# Overview / stats
# ---------------------------------------------------------------------------

def get_overview_stats() -> dict[str, Any]:
    db = get_client()

    # Total users
    total_result = db.table("rb_users").select("*", count="exact").execute()
    total_users = total_result.count or 0

    # Users today (created_at >= today 00:00 UTC)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_result = (
        db.table("rb_users")
        .select("*", count="exact")
        .gte("created_at", today_start.isoformat())
        .execute()
    )
    users_today = today_result.count or 0

    # Total resumes
    resumes_result = db.table("rb_resumes").select("*", count="exact").execute()
    total_resumes = resumes_result.count or 0

    # Resumes this week
    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    resumes_week_result = (
        db.table("rb_resumes")
        .select("*", count="exact")
        .gte("created_at", week_start.isoformat())
        .execute()
    )
    resumes_this_week = resumes_week_result.count or 0

    return {
        "total_users": total_users,
        "users_today": users_today,
        "total_resumes": total_resumes,
        "resumes_this_week": resumes_this_week,
    }


def get_funnel_stats() -> dict[str, int]:
    db = get_client()
    result = db.table("rb_users").select("current_stage").execute()
    rows = result.data or []
    counts: dict[str, int] = {
        "onboarding": 0,
        "interview": 0,
        "draft": 0,
        "exported": 0,
    }
    for row in rows:
        stage = row.get("current_stage", "onboarding") or "onboarding"
        if stage in counts:
            counts[stage] += 1
        else:
            counts[stage] = counts.get(stage, 0) + 1
    return counts


def get_recent_events(limit: int = 20) -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_events")
        .select("id, event_type, user_id, metadata, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

def get_all_users() -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_users")
        .select("id, telegram_username, current_stage, created_at, last_active_at")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


# ---------------------------------------------------------------------------
# User detail
# ---------------------------------------------------------------------------

def get_user_by_id(user_id: int) -> dict | None:
    db = get_client()
    result = (
        db.table("rb_users")
        .select("*")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def get_candidate_profile(user_id: int) -> dict | None:
    db = get_client()
    result = (
        db.table("rb_candidate_profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def get_work_experiences(profile_id: str) -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_work_experiences")
        .select("*")
        .eq("profile_id", profile_id)
        .order("order_index")
        .execute()
    )
    return result.data or []


def get_skills(profile_id: str) -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_skills")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
    )
    return result.data or []


def get_education(profile_id: str) -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_education")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
    )
    return result.data or []


def get_user_resumes(user_id: int) -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_resumes")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


# ---------------------------------------------------------------------------
# Resumes list
# ---------------------------------------------------------------------------

def get_all_resumes() -> list[dict]:
    db = get_client()
    result = (
        db.table("rb_resumes")
        .select("id, user_id, title, version, created_at, content")
        .order("created_at", desc=True)
        .execute()
    )
    resumes = result.data or []

    # Enrich with user info
    if resumes:
        user_ids = list({r["user_id"] for r in resumes if r.get("user_id")})
        users_result = (
            db.table("rb_users")
            .select("id, telegram_username")
            .in_("id", user_ids)
            .execute()
        )
        users_map = {u["id"]: u for u in (users_result.data or [])}

        profiles_result = (
            db.table("rb_candidate_profiles")
            .select("user_id, full_name, desired_position")
            .in_("user_id", user_ids)
            .execute()
        )
        profiles_map = {p["user_id"]: p for p in (profiles_result.data or [])}

        for r in resumes:
            uid = r.get("user_id")
            r["user"] = users_map.get(uid, {})
            r["profile"] = profiles_map.get(uid, {})

    return resumes


# ---------------------------------------------------------------------------
# Resume detail
# ---------------------------------------------------------------------------

def get_resume_by_id(resume_id: str) -> dict | None:
    db = get_client()
    result = (
        db.table("rb_resumes")
        .select("*")
        .eq("id", resume_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    resume = rows[0]

    # Attach user info
    uid = resume.get("user_id")
    if uid:
        user = get_user_by_id(uid)
        resume["user"] = user or {}
        profile = get_candidate_profile(uid)
        resume["profile"] = profile or {}

    return resume

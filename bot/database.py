"""
Supabase client wrapper — Resume Bot (rb_ table prefix).

Uses rb_users, rb_candidate_profiles, rb_work_experiences, rb_skills,
rb_education, rb_resumes, rb_events.

user_id throughout == telegram_id (BIGINT).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client
from bot.config import settings

logger = logging.getLogger(__name__)


def _make_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

async def get_or_create_user(telegram_id: int, telegram_username: str | None = None) -> dict:
    """Return existing rb_users row or create a new one."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_users")
            .select("*")
            .eq("id", telegram_id)
            .limit(1)
            .execute()
        )
        rows = result.data if result and result.data else []
        if rows:
            await asyncio.to_thread(
                lambda: db.table("rb_users")
                .update({"last_active_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", telegram_id)
                .execute()
            )
            return rows[0]

        insert_result = await asyncio.to_thread(
            lambda: db.table("rb_users")
            .insert({
                "id": telegram_id,
                "telegram_username": telegram_username,
                "current_stage": "onboarding",
            })
            .execute()
        )
        return insert_result.data[0]
    except Exception as exc:
        logger.error("get_or_create_user error: %s", exc)
        raise


async def update_user_stage(user_id: int, stage: str) -> None:
    """Update user funnel stage. Valid stages: onboarding, interview, draft, exported."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_users")
            .update({"current_stage": stage})
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error("update_user_stage error: %s", exc)


# ---------------------------------------------------------------------------
# Interview state persistence
# ---------------------------------------------------------------------------

async def save_interview_state(user_id: int, state_data: dict) -> None:
    """Persist FSM interview progress to rb_users.interview_state JSONB column."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_users")
            .update({"interview_state": state_data})
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error("save_interview_state error: %s", exc)


async def get_interview_state(user_id: int) -> dict | None:
    """Retrieve persisted interview state, or None if not set."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_users")
            .select("interview_state")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data if result and result.data else []
        if rows:
            return rows[0].get("interview_state") or {}
        return None
    except Exception as exc:
        logger.error("get_interview_state error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Candidate profiles
# ---------------------------------------------------------------------------

async def save_candidate_profile(user_id: int, profile_data: dict) -> str:
    """Upsert candidate profile, return profile UUID."""
    db = get_client()
    try:
        existing = await asyncio.to_thread(
            lambda: db.table("rb_candidate_profiles")
            .select("id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        existing_rows = existing.data if existing and existing.data else []
        if existing_rows:
            existing_data = existing_rows[0]
            profile_id = existing_data["id"]
            await asyncio.to_thread(
                lambda: db.table("rb_candidate_profiles")
                .update({**profile_data, "updated_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", profile_id)
                .execute()
            )
            return profile_id

        payload = {"user_id": user_id, **profile_data}
        result = await asyncio.to_thread(
            lambda: db.table("rb_candidate_profiles").insert(payload).execute()
        )
        return result.data[0]["id"]
    except Exception as exc:
        logger.error("save_candidate_profile error: %s", exc)
        raise


async def get_candidate_profile(user_id: int) -> dict | None:
    """Return the candidate profile for the given user, or None."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_candidate_profiles")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data if result and result.data else []
        return rows[0] if rows else None
    except Exception as exc:
        logger.error("get_candidate_profile error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Work experience
# ---------------------------------------------------------------------------

async def clear_work_experiences(profile_id: str) -> None:
    """Delete all work experiences for a profile (before re-inserting)."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_work_experiences")
            .delete()
            .eq("profile_id", profile_id)
            .execute()
        )
    except Exception as exc:
        logger.error("clear_work_experiences error: %s", exc)


async def save_work_experience(profile_id: str, experience: dict) -> str:
    """Insert a work experience record, return UUID."""
    db = get_client()
    try:
        payload = {"profile_id": profile_id, **experience}
        result = await asyncio.to_thread(
            lambda: db.table("rb_work_experiences").insert(payload).execute()
        )
        return result.data[0]["id"]
    except Exception as exc:
        logger.error("save_work_experience error: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

async def clear_skills(profile_id: str) -> None:
    """Delete all skills for a profile (before re-inserting)."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_skills")
            .delete()
            .eq("profile_id", profile_id)
            .execute()
        )
    except Exception as exc:
        logger.error("clear_skills error: %s", exc)


async def save_skill(profile_id: str, name: str, category: str = "hard") -> str:
    """Insert a skill, return UUID."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_skills")
            .insert({"profile_id": profile_id, "name": name, "category": category})
            .execute()
        )
        return result.data[0]["id"]
    except Exception as exc:
        logger.error("save_skill error: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------

async def clear_education(profile_id: str) -> None:
    """Delete all education records for a profile (before re-inserting)."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_education")
            .delete()
            .eq("profile_id", profile_id)
            .execute()
        )
    except Exception as exc:
        logger.error("clear_education error: %s", exc)


async def save_education(profile_id: str, data: dict) -> str:
    """Insert an education record, return UUID."""
    db = get_client()
    try:
        payload = {"profile_id": profile_id, **data}
        result = await asyncio.to_thread(
            lambda: db.table("rb_education").insert(payload).execute()
        )
        return result.data[0]["id"]
    except Exception as exc:
        logger.error("save_education error: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Resumes
# ---------------------------------------------------------------------------

async def create_resume(user_id: int, profile_id: str, title: str, content: str) -> str:
    """Create a resume document, return UUID."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_resumes")
            .insert({
                "user_id": user_id,
                "profile_id": profile_id,
                "title": title,
                "content": content,
            })
            .execute()
        )
        return result.data[0]["id"]
    except Exception as exc:
        logger.error("create_resume error: %s", exc)
        raise


async def get_resumes(user_id: int) -> list[dict]:
    """Return all resumes for a user, newest first."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_resumes")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error("get_resumes error: %s", exc)
        return []


async def update_resume(resume_id: str, content: str) -> bool:
    """Update resume content."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_resumes")
            .update({"content": content, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", resume_id)
            .execute()
        )
        return True
    except Exception as exc:
        logger.error("update_resume error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Profile deletion
# ---------------------------------------------------------------------------

async def delete_user_profile(user_id: int) -> bool:
    """
    Delete all resume-bot data for a user.
    Cascade handles most children; we reset the user row.
    """
    db = get_client()
    try:
        profile = await get_candidate_profile(user_id)
        if profile:
            profile_id = profile["id"]
            for table in ("rb_work_experiences", "rb_skills", "rb_education"):
                await asyncio.to_thread(
                    lambda t=table: db.table(t)
                    .delete()
                    .eq("profile_id", profile_id)
                    .execute()
                )
            await asyncio.to_thread(
                lambda: db.table("rb_resumes").delete().eq("user_id", user_id).execute()
            )
            await asyncio.to_thread(
                lambda: db.table("rb_candidate_profiles").delete().eq("user_id", user_id).execute()
            )
        await asyncio.to_thread(
            lambda: db.table("rb_users")
            .update({"current_stage": "onboarding", "interview_state": {}})
            .eq("id", user_id)
            .execute()
        )
        return True
    except Exception as exc:
        logger.error("delete_user_profile error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

async def log_event(user_id: int, event_type: str, metadata: dict | None = None) -> None:
    """Append an event row (non-critical — never raises)."""
    db = get_client()
    try:
        await asyncio.to_thread(
            lambda: db.table("rb_events")
            .insert({
                "user_id": user_id,
                "event_type": event_type,
                "metadata": metadata or {},
            })
            .execute()
        )
    except Exception as exc:
        logger.error("log_event (non-critical): %s", exc)


async def get_funnel_stats() -> dict[str, Any]:
    """Return counts of rb_users per funnel stage."""
    db = get_client()
    try:
        result = await asyncio.to_thread(
            lambda: db.table("rb_users").select("current_stage").execute()
        )
        rows = result.data or []
        stats: dict[str, int] = {}
        for row in rows:
            stage = row.get("current_stage", "unknown")
            stats[stage] = stats.get(stage, 0) + 1
        return stats
    except Exception as exc:
        logger.error("get_funnel_stats error: %s", exc)
        return {}

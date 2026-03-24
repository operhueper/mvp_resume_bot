"""
Full interview flow: 6 stages with FSM state persistence.

Stage map
---------
1/6  Summary / about section          InterviewStates.summary
2/6  Work experience — company        InterviewStates.work_experience_company
     Work experience — role           InterviewStates.work_experience_role
     Work experience — dates          InterviewStates.work_experience_dates
     Work experience — responsibilities
     Work experience — achievements   (up to 2 follow-up prompts)
     Work experience — confirm        InterviewStates.work_experience_confirm
3/6  Additional jobs prompt           (re-enters work_experience_company loop or moves on)
4/6  Skills                           InterviewStates.skills_input
5/6  Education                        InterviewStates.education_input
6/6  Extras / preferences             InterviewStates.extras_input
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import bot.database as db
from bot.states import InterviewStates, ResumeStates

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOTAL_STAGES = 6

# How many times we nudge the user for a quantified achievement before giving up
MAX_ACHIEVEMENT_NUDGES = 2

# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------


def _yes_no_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Всё верно", callback_data=yes_data),
                InlineKeyboardButton(text="Исправить", callback_data=no_data),
            ]
        ]
    )


def _more_jobs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить ещё место работы", callback_data="add_more_job"),
                InlineKeyboardButton(text="Перейти к навыкам", callback_data="skip_more_jobs"),
            ]
        ]
    )


def _skill_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Навыки верны", callback_data="skills_confirmed"),
                InlineKeyboardButton(text="Добавить ещё", callback_data="skills_add_more"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Stage label helper
# ---------------------------------------------------------------------------


def _stage_label(stage_num: int) -> str:
    return f"Этап {stage_num}/{TOTAL_STAGES}"


# ---------------------------------------------------------------------------
# Off-topic guard
# ---------------------------------------------------------------------------


def _is_off_topic(text: str) -> bool:
    """Heuristic: if the message is very short and looks like a question or random text."""
    text = text.strip().lower()
    off_topic_starters = ("кто ты", "что ты", "погода", "привет", "как дела", "анекдот")
    return any(text.startswith(s) for s in off_topic_starters)


async def _redirect_off_topic(message: Message, state: FSMContext, stage_num: int) -> None:
    await message.answer(
        f"Я специализируюсь на создании резюме. "
        f"Давайте продолжим — мы на {_stage_label(stage_num)}."
    )


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------


async def _persist_state(state: FSMContext, user_id: str) -> None:
    """Dump current FSM data to DB for recovery across bot restarts."""
    data = await state.get_data()
    try:
        await db.save_interview_state(user_id, data)
    except Exception as exc:
        logger.warning("Could not persist interview state: %s", exc)


# ---------------------------------------------------------------------------
# Stage 1 — Summary
# ---------------------------------------------------------------------------


async def ask_summary(message: Message, state: FSMContext) -> None:
    """Entry point called from start.py after onboarding completes."""
    data = await state.get_data()
    desired_position = data.get("desired_position", "желаемой позиции")
    await state.set_state(InterviewStates.summary)
    await message.answer(
        f"{_stage_label(1)}\n\n"
        f"Расскажите о себе в 3–5 предложениях: кто вы как специалист, "
        f"сколько лет опыта, чем занимаетесь сейчас и что ищете в роли {desired_position}.\n\n"
        "Это будет раздел «О себе» в вашем резюме."
    )


@router.message(InterviewStates.summary)
async def handle_summary(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, напишите текст ответа.")
        return

    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 1)
        return

    await state.update_data(summary=text, work_experiences=[], achievement_nudges=0, current_job_index=0)
    data = await state.get_data()
    await _persist_state(state, data["user_id"])

    await message.answer(
        f"Раздел «О себе»:\n\n{text}",
        reply_markup=_yes_no_keyboard("summary_ok", "summary_redo"),
    )


@router.callback_query(F.data == "summary_ok")
async def cb_summary_ok(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_company)
    await callback.message.answer(
        f"{_stage_label(2)}\n\n"
        "Начнём с опыта работы. Укажите название компании (последнее или текущее место работы)."
    )


@router.callback_query(F.data == "summary_redo")
async def cb_summary_redo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.summary)
    await callback.message.answer(
        "Хорошо. Расскажите о себе заново — 3–5 предложений о вашем опыте и целях."
    )


# ---------------------------------------------------------------------------
# Stage 2 — Work experience (looped)
# ---------------------------------------------------------------------------


@router.message(InterviewStates.work_experience_company)
async def handle_we_company(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите название компании.")
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 2)
        return

    await state.update_data(current_company=text, achievement_nudges=0)
    await state.set_state(InterviewStates.work_experience_role)
    await message.answer("Какую должность вы занимали в этой компании?")


@router.message(InterviewStates.work_experience_role)
async def handle_we_role(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите название должности.")
        return

    await state.update_data(current_role=text)
    await state.set_state(InterviewStates.work_experience_dates)
    await message.answer(
        "Укажите период работы. Например: «март 2021 — февраль 2024» или «2019 — по настоящее время»."
    )


@router.message(InterviewStates.work_experience_dates)
async def handle_we_dates(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите период работы.")
        return

    await state.update_data(current_dates=text)
    await state.set_state(InterviewStates.work_experience_responsibilities)
    await message.answer(
        "Опишите ваши основные обязанности на этой позиции. "
        "Перечислите 3–6 ключевых задач."
    )


@router.message(InterviewStates.work_experience_responsibilities)
async def handle_we_responsibilities(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите ваши обязанности.")
        return

    await state.update_data(current_responsibilities=text)
    await state.set_state(InterviewStates.work_experience_achievements)
    await message.answer(
        "Какие результаты вы достигли на этой позиции? "
        "Постарайтесь упомянуть конкретные достижения."
    )


@router.message(InterviewStates.work_experience_achievements)
async def handle_we_achievements(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите ваши достижения или напишите «нет».")
        return

    data = await state.get_data()
    nudges: int = data.get("achievement_nudges", 0)

    # Check if the answer is vague (no digits, no percentages)
    has_numbers = any(ch.isdigit() for ch in text) or "%" in text
    text_lower = text.lower()
    is_declined = any(w in text_lower for w in ("нет", "не помню", "ничего", "без результ"))

    if not has_numbers and not is_declined and nudges < MAX_ACHIEVEMENT_NUDGES:
        nudges += 1
        await state.update_data(achievement_nudges=nudges)
        
        from bot.services.ai_service import clarify_achievement
        
        wait_msg = await message.answer("Анализирую ответ... 🤖")
        try:
            ai_question = await clarify_achievement(text, nudges)
            await wait_msg.delete()
            if not ai_question:
                # AI accepted it or gave up, move on
                await state.update_data(current_achievements=text)
                await message.answer("Хорошо, запишем как есть. Переходим к подтверждению блока.")
                await _show_we_block_for_confirmation(message, state)
                return
                
            await message.answer(ai_question)
            return
        except Exception as e:
            logger.error(f"Error AI clarify: {e}")
            await wait_msg.delete()
            
            # Fallback to hardcoded
            if nudges == 1:
                await message.answer(
                    "Не могли бы вы добавить конкретные цифры к этому результату? На сколько процентов/штук/часов вы улучшили показатели?"
                )
            else:
                await state.update_data(current_achievements=text)
                await message.answer("Хорошо, запишем как есть. Переходим к подтверждению блока.")
                await _show_we_block_for_confirmation(message, state)
            return

    await state.update_data(current_achievements=text)
    await _show_we_block_for_confirmation(message, state)


async def _show_we_block_for_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    block = (
        f"Компания: {data.get('current_company', '')}\n"
        f"Должность: {data.get('current_role', '')}\n"
        f"Период: {data.get('current_dates', '')}\n"
        f"Обязанности:\n{data.get('current_responsibilities', '')}\n"
        f"Достижения:\n{data.get('current_achievements', '')}"
    )
    await state.set_state(InterviewStates.work_experience_confirm)
    await message.answer(
        f"Блок опыта работы:\n\n{block}",
        reply_markup=_yes_no_keyboard("we_block_ok", "we_block_redo"),
    )


@router.callback_query(F.data == "we_block_ok")
async def cb_we_block_ok(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data["user_id"]

    # Accumulate this job into the list
    job = {
        "company": data.get("current_company", ""),
        "role": data.get("current_role", ""),
        "dates": data.get("current_dates", ""),
        "responsibilities": data.get("current_responsibilities", ""),
        "achievements": data.get("current_achievements", ""),
    }
    jobs: list = data.get("work_experiences", [])
    jobs.append(job)
    await state.update_data(work_experiences=jobs)

    # Persist to DB
    profile = await db.get_candidate_profile(user_id)
    if profile:
        try:
            await db.save_work_experience(profile["id"], job)
        except Exception as exc:
            logger.warning("Could not save work experience: %s", exc)

    await _persist_state(state, user_id)

    # Stage 3 — ask about additional jobs
    await callback.message.answer(
        f"{_stage_label(3)}\n\nЕсть ли другие места работы, которые стоит включить в резюме?",
        reply_markup=_more_jobs_keyboard(),
    )


@router.callback_query(F.data == "we_block_redo")
async def cb_we_block_redo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_company)
    await callback.message.answer(
        "Хорошо, заполним блок заново. Введите название компании."
    )


@router.callback_query(F.data == "add_more_job")
async def cb_add_more_job(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_company)
    await callback.message.answer(
        "Введите название следующей компании."
    )


@router.callback_query(F.data == "skip_more_jobs")
async def cb_skip_more_jobs(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _start_skills_stage(callback.message, state)


# ---------------------------------------------------------------------------
# Stage 4 — Skills
# ---------------------------------------------------------------------------


async def _start_skills_stage(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    desired_position = data.get("desired_position", "")

    # Try to get AI suggestions if ai_service is available
    suggestions_text = ""
    try:
        from bot.services.ai_service import suggest_skills
        suggestions = await suggest_skills(desired_position)
        if suggestions:
            suggestions_text = "\n\nВозможные навыки для вашей специализации:\n" + ", ".join(suggestions)
    except Exception:
        pass

    await state.set_state(InterviewStates.skills_input)
    await message.answer(
        f"{_stage_label(4)}\n\n"
        f"Перечислите ваши ключевые навыки через запятую. "
        f"Укажите как профессиональные, так и инструментальные навыки."
        f"{suggestions_text}"
    )


@router.message(InterviewStates.skills_input)
async def handle_skills(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите навыки через запятую.")
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 4)
        return

    skills = [s.strip() for s in text.split(",") if s.strip()]
    await state.update_data(skills=skills)
    await message.answer(
        f"Навыки:\n{', '.join(skills)}",
        reply_markup=_skill_confirm_keyboard(),
    )


@router.callback_query(F.data == "skills_confirmed")
async def cb_skills_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data["user_id"]

    # Persist skills to DB
    profile = await db.get_candidate_profile(user_id)
    if profile:
        for skill_name in data.get("skills", []):
            try:
                await db.save_skill(profile["id"], skill_name, "general")
            except Exception as exc:
                logger.warning("Could not save skill '%s': %s", skill_name, exc)

    await _persist_state(state, user_id)
    await state.set_state(InterviewStates.education_input)
    await callback.message.answer(
        f"{_stage_label(5)}\n\n"
        "Укажите ваше образование: учебное заведение, специальность и год окончания."
    )


@router.callback_query(F.data == "skills_add_more")
async def cb_skills_add_more(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer(
        "Добавьте навыки через запятую:"
    )
    # Stay in skills_input state to receive additional skills
    await state.set_state(InterviewStates.skills_input)


# ---------------------------------------------------------------------------
# Stage 5 — Education
# ---------------------------------------------------------------------------


@router.message(InterviewStates.education_input)
async def handle_education(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите ваше образование или напишите «нет».")
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 5)
        return

    await state.update_data(education=text)
    data = await state.get_data()
    user_id = data["user_id"]

    profile = await db.get_candidate_profile(user_id)
    if profile:
        try:
            await db.save_education(profile["id"], {"description": text})
        except Exception as exc:
            logger.warning("Could not save education: %s", exc)

    await _persist_state(state, user_id)
    await state.set_state(InterviewStates.extras_input)
    await message.answer(
        f"{_stage_label(6)}\n\n"
        "Последний этап. Укажите дополнительную информацию:\n"
        "— Желаемый уровень зарплаты\n"
        "— Предпочтения по формату работы (офис / удалённо / гибрид)\n"
        "— Готовность к командировкам\n"
        "— Любая другая важная информация\n\n"
        "Если ничего из этого не актуально, напишите «нет»."
    )


# ---------------------------------------------------------------------------
# Stage 6 — Extras
# ---------------------------------------------------------------------------


@router.message(InterviewStates.extras_input)
async def handle_extras(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, ответьте на вопрос или напишите «нет».")
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 6)
        return

    await state.update_data(extras=text)
    data = await state.get_data()
    user_id = data["user_id"]
    await _persist_state(state, user_id)

    await message.answer(
        "Отлично. Все данные собраны. Формирую резюме — это займёт несколько секунд..."
    )
    await _generate_resume(message, state)


# ---------------------------------------------------------------------------
# Resume generation
# ---------------------------------------------------------------------------


async def _generate_resume(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data["user_id"]

    try:
        from bot.services.ai_service import generate_resume_text
        resume_text = await generate_resume_text(data)
    except Exception as exc:
        logger.error("AI resume generation failed: %s", exc)
        resume_text = _build_plain_resume(data)

    profile = await db.get_candidate_profile(user_id)
    profile_id = profile["id"] if profile else None
    desired_position = data.get("desired_position", "Резюме")

    try:
        resume_id = await db.create_resume(
            user_id=user_id,
            profile_id=profile_id,
            title=f"{desired_position} — {data.get('full_name', '')}",
            content=resume_text,
        )
        await state.update_data(resume_id=resume_id)
        await db.update_user_stage(user_id, "draft")
        await db.log_event(user_id, "resume_generated", {"resume_id": resume_id})
    except Exception as exc:
        logger.error("Could not save resume: %s", exc)

    await state.set_state(ResumeStates.viewing_draft)
    await message.answer(
        f"Ваше резюме готово:\n\n{resume_text}\n\n"
        "Вы можете отредактировать его командами:\n"
        "«короче» — сделать резюме короче\n"
        "«формальнее» — более официальный стиль\n"
        "«перепиши блок» — переписать конкретный раздел\n"
        "«добавь навык» — добавить навык\n\n"
        "Или отправьте /export, чтобы получить финальную версию."
    )


def _build_plain_resume(data: dict) -> str:
    """Fallback: assemble resume text from raw interview data without AI."""
    lines: list[str] = []

    name = data.get("full_name", "")
    position = data.get("desired_position", "")
    contacts = data.get("contacts", "")
    city = data.get("city", "")

    if name:
        lines.append(name)
    if position:
        lines.append(position)
    if contacts:
        lines.append(contacts)
    if city:
        lines.append(city)

    lines.append("")

    summary = data.get("summary", "")
    if summary:
        lines.append("О СЕБЕ")
        lines.append(summary)
        lines.append("")

    jobs: list[dict] = data.get("work_experiences", [])
    if jobs:
        lines.append("ОПЫТ РАБОТЫ")
        for job in jobs:
            lines.append(f"{job.get('company', '')} — {job.get('role', '')} ({job.get('dates', '')})")
            if job.get("responsibilities"):
                lines.append("Обязанности:")
                lines.append(job["responsibilities"])
            if job.get("achievements"):
                lines.append("Достижения:")
                lines.append(job["achievements"])
            lines.append("")

    skills: list[str] = data.get("skills", [])
    if skills:
        lines.append("НАВЫКИ")
        lines.append(", ".join(skills))
        lines.append("")

    education = data.get("education", "")
    if education and education.lower() != "нет":
        lines.append("ОБРАЗОВАНИЕ")
        lines.append(education)
        lines.append("")

    extras = data.get("extras", "")
    if extras and extras.lower() != "нет":
        lines.append("ДОПОЛНИТЕЛЬНО")
        lines.append(extras)

    return "\n".join(lines)

"""
Resume viewing, editing, and export handlers.

Editing commands (case-insensitive, matched anywhere in the message):
  «короче»          — shorten the resume
  «формальнее»      — make the tone more formal
  «перепиши блок»   — rewrite a specific block (bot asks which one)
  «добавь навык»    — append a skill to the skills section
"""

from __future__ import annotations

import logging
import re

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import bot.database as db
from bot.states import ResumeStates

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------


def _resume_action_keyboard(resume_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сделать короче", callback_data=f"resume_shorter:{resume_id}"),
                InlineKeyboardButton(text="Формальнее", callback_data=f"resume_formal:{resume_id}"),
            ],
            [
                InlineKeyboardButton(text="Перепиши блок", callback_data=f"resume_rewrite:{resume_id}"),
                InlineKeyboardButton(text="Добавить навык", callback_data=f"resume_add_skill:{resume_id}"),
            ],
            [
                InlineKeyboardButton(text="Создать резюме для другой позиции", callback_data="resume_new_position"),
            ],
        ]
    )


def _position_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="resume_position_ok"),
                InlineKeyboardButton(text="Изменить", callback_data="resume_position_change"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Utility: get current resume content
# ---------------------------------------------------------------------------


async def _get_current_resume(state: FSMContext) -> tuple[str | None, str | None]:
    """Return (resume_id, content) from state, or (None, None) if not found."""
    data = await state.get_data()
    resume_id: str | None = data.get("resume_id")
    if not resume_id:
        return None, None

    user_id = data.get("user_id")
    resumes = await db.get_resumes(user_id)
    for r in resumes:
        if str(r["id"]) == str(resume_id):
            return resume_id, r.get("content", "")

    # Fallback: return latest
    if resumes:
        r = resumes[0]
        return r["id"], r.get("content", "")
    return None, None


# ---------------------------------------------------------------------------
# Viewing draft
# ---------------------------------------------------------------------------


@router.message(ResumeStates.viewing_draft)
async def handle_viewing_draft(message: Message, state: FSMContext) -> None:
    """
    While the user is in viewing_draft state, accept text editing commands.
    """
    text = (message.text or "").strip().lower()

    if "короче" in text:
        await _apply_edit_command(message, state, "shorter")
        return
    if "формальнее" in text:
        await _apply_edit_command(message, state, "formal")
        return
    if "перепиши блок" in text:
        await state.set_state(ResumeStates.editing)
        await state.update_data(pending_edit="rewrite_block")
        await message.answer(
            "Укажите, какой блок переписать. Например: «О себе», «Опыт работы», «Навыки»."
        )
        return
    if "добавь навык" in text or "добавить навык" in text:
        await state.set_state(ResumeStates.editing)
        await state.update_data(pending_edit="add_skill")
        await message.answer("Введите навык или несколько навыков через запятую, которые нужно добавить.")
        return

    # Not a recognised command — show the current draft again with action keyboard
    resume_id, content = await _get_current_resume(state)
    if content:
        await message.answer(
            f"Ваше текущее резюме:\n\n{content}",
            reply_markup=_resume_action_keyboard(resume_id),
        )
    else:
        await message.answer(
            "Резюме не найдено. Пройдите интервью, чтобы создать резюме (/start)."
        )


# ---------------------------------------------------------------------------
# Inline keyboard edit commands
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("resume_shorter:"))
async def cb_resume_shorter(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _apply_edit_command(callback.message, state, "shorter")


@router.callback_query(F.data.startswith("resume_formal:"))
async def cb_resume_formal(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _apply_edit_command(callback.message, state, "formal")


@router.callback_query(F.data.startswith("resume_rewrite:"))
async def cb_resume_rewrite(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ResumeStates.editing)
    await state.update_data(pending_edit="rewrite_block")
    await callback.message.answer(
        "Укажите, какой блок переписать. Например: «О себе», «Опыт работы», «Навыки»."
    )


@router.callback_query(F.data.startswith("resume_add_skill:"))
async def cb_resume_add_skill(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ResumeStates.editing)
    await state.update_data(pending_edit="add_skill")
    await callback.message.answer(
        "Введите навык или несколько навыков через запятую."
    )


# ---------------------------------------------------------------------------
# Editing state — receives the clarification text
# ---------------------------------------------------------------------------


@router.message(ResumeStates.editing)
async def handle_editing(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, введите текст.")
        return

    data = await state.get_data()
    pending_edit: str = data.get("pending_edit", "")
    resume_id, current_content = await _get_current_resume(state)

    if not current_content:
        await message.answer("Резюме не найдено. Нажмите /start, чтобы начать заново.")
        await state.set_state(ResumeStates.viewing_draft)
        return

    await message.answer("Обрабатываю запрос...")

    try:
        from bot.services.ai_service import edit_resume
        new_content = await edit_resume(
            current_content=current_content,
            command=pending_edit,
            user_input=text,
        )
    except Exception as exc:
        logger.error("AI edit failed: %s", exc)
        new_content = _apply_edit_locally(current_content, pending_edit, text)

    if resume_id:
        await db.update_resume(resume_id, new_content)
        user_id = data.get("user_id")
        await db.log_event(user_id, "resume_edited", {"command": pending_edit})

    await state.set_state(ResumeStates.viewing_draft)
    await message.answer(
        f"Обновлённое резюме:\n\n{new_content}",
        reply_markup=_resume_action_keyboard(resume_id),
    )


def _apply_edit_locally(content: str, command: str, user_input: str) -> str:
    """
    Minimal local fallback when AI is unavailable.
    """
    if command == "add_skill":
        skills_header = "НАВЫКИ"
        if skills_header in content:
            # Append after the skills section header's first line
            parts = content.split(skills_header, 1)
            skill_block_lines = parts[1].split("\n")
            # Find the line with existing skills (first non-empty line after header)
            for i, line in enumerate(skill_block_lines):
                if line.strip():
                    skill_block_lines[i] = line.rstrip() + ", " + user_input
                    break
            return parts[0] + skills_header + "\n".join(skill_block_lines)
        else:
            return content + f"\n\nНАВЫКИ\n{user_input}"

    # For shorter / formal / rewrite_block: return content unchanged as fallback
    return content


# ---------------------------------------------------------------------------
# Apply edit command (shorter / formal)
# ---------------------------------------------------------------------------


async def _apply_edit_command(message: Message, state: FSMContext, command: str) -> None:
    resume_id, current_content = await _get_current_resume(state)
    if not current_content:
        await message.answer("Резюме не найдено.")
        return

    await message.answer("Обрабатываю...")

    try:
        from bot.services.ai_service import edit_resume
        new_content = await edit_resume(
            current_content=current_content,
            command=command,
            user_input="",
        )
    except Exception as exc:
        logger.error("AI edit command failed: %s", exc)
        new_content = current_content  # Fallback: no change

    if resume_id:
        await db.update_resume(resume_id, new_content)
        data = await state.get_data()
        user_id = data.get("user_id")
        await db.log_event(user_id, "resume_edited", {"command": command})

    await state.set_state(ResumeStates.viewing_draft)
    await message.answer(
        f"Обновлённое резюме:\n\n{new_content}",
        reply_markup=_resume_action_keyboard(resume_id),
    )


# ---------------------------------------------------------------------------
# Create resume for a different position
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "resume_new_position")
async def cb_resume_new_position(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ResumeStates.selecting_position_title)
    await callback.message.answer(
        "Для какой позиции создать резюме? Введите название должности."
    )


@router.message(ResumeStates.selecting_position_title)
async def handle_new_position_title(message: Message, state: FSMContext) -> None:
    position = (message.text or "").strip()
    if not position:
        await message.answer("Введите название позиции.")
        return

    data = await state.get_data()
    user_id = data.get("user_id")

    await message.answer(f"Создаю резюме для позиции «{position}»...")

    _, current_content = await _get_current_resume(state)

    try:
        from bot.services.ai_service import adapt_resume_for_position
        new_content = await adapt_resume_for_position(current_content or "", position)
    except Exception as exc:
        logger.error("AI adapt failed: %s", exc)
        new_content = current_content or ""

    profile = await db.get_candidate_profile(user_id)
    profile_id = profile["id"] if profile else None

    try:
        resume_id = await db.create_resume(
            user_id=user_id,
            profile_id=profile_id,
            title=f"{position} — {data.get('full_name', '')}",
            content=new_content,
        )
        await state.update_data(resume_id=resume_id)
        await db.log_event(user_id, "resume_created_for_position", {"position": position})
    except Exception as exc:
        logger.error("Could not save new resume: %s", exc)
        resume_id = None

    await state.set_state(ResumeStates.viewing_draft)
    await message.answer(
        f"Резюме для позиции «{position}»:\n\n{new_content}",
        reply_markup=_resume_action_keyboard(resume_id) if resume_id else None,
    )

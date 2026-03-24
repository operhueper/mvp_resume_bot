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
from aiogram.filters import Command, StateFilter
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
# Utility: send long text in ≤4000-char chunks (Telegram limit is 4096)
# ---------------------------------------------------------------------------

async def _send_long_message(message: Message, text: str, **kwargs) -> None:
    """Split text into ≤4000-char chunks; kwargs (e.g. reply_markup) go on the last chunk only."""
    limit = 4000
    chunks = [text[i : i + limit] for i in range(0, len(text), limit)]
    for i, chunk in enumerate(chunks):
        await message.answer(chunk, **(kwargs if i == len(chunks) - 1 else {}))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOTAL_STAGES = 6

# How many times we nudge the user for a quantified achievement before giving up
MAX_ACHIEVEMENT_NUDGES = 2

# Help trigger keywords (case-insensitive)
_HELP_KEYWORDS = {"пример", "помогите", "помощь", "не знаю", "?", "help"}

# Stage-specific help examples
_HELP_EXAMPLES: dict[str, str] = {
    "summary": (
        "Вот пример раздела «О себе»:\n\n"
        "_«Менеджер по продажам с 6-летним опытом в B2B. Специализируюсь на работе с крупными "
        "корпоративными клиентами. За последние 2 года увеличил объём продаж отдела на 45%. "
        "Ищу позицию в компании с амбициозными целями роста.»_\n\n"
        "Теперь напишите о себе:"
    ),
    "work_experience_company": (
        "Например: ООО «Альфа», Яндекс, ИП Иванов И.И.\n\nВведите название компании:"
    ),
    "work_experience_role": (
        "Например: Менеджер по продажам, Senior Python Developer, Руководитель отдела маркетинга\n\n"
        "Введите Вашу должность:"
    ),
    "work_experience_dates": (
        "Например: январь 2020 — март 2023 или 2019 — по настоящее время\n\nВведите период работы:"
    ),
    "work_experience_responsibilities": (
        "Например:\n"
        "• Управлял командой из 8 человек\n"
        "• Вёл переговоры с ключевыми клиентами\n"
        "• Разрабатывал стратегию продаж на квартал\n\n"
        "Опишите Ваши обязанности:"
    ),
    "work_experience_achievements": (
        "Например:\n"
        "• Увеличил выручку отдела на 35% за год\n"
        "• Сократил цикл сделки с 30 до 18 дней\n"
        "• Привлёк 12 новых крупных клиентов\n\n"
        "Опишите Ваши достижения:"
    ),
    "skills_input": (
        "Например: Python, SQL, Jira, управление проектами, Agile, Excel, аналитика данных\n\n"
        "Введите Ваши навыки через запятую:"
    ),
    "education_input": (
        "Например: МГУ им. Ломоносова, факультет экономики, бакалавр, 2018\n\n"
        "Введите информацию об образовании:"
    ),
    "extras_input": (
        "Например: Желаемая зарплата от 120 000 ₽, удалённая работа, готов к командировкам раз в квартал\n\n"
        "Введите дополнительную информацию:"
    ),
}


def _is_help_request(text: str) -> bool:
    """Return True if user is asking for help/example."""
    return text.strip().lower() in _HELP_KEYWORDS


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


def _warning_keyboard(continue_data: str, edit_data: str, edit_label: str = "✏️ Дополнить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=edit_label, callback_data=edit_data),
                InlineKeyboardButton(text="➡️ Продолжить", callback_data=continue_data),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Stage label helper
# ---------------------------------------------------------------------------


def _stage_label(stage_num: int) -> str:
    return f"Этап {stage_num}/{TOTAL_STAGES}"


# ---------------------------------------------------------------------------
# Block selection — navigation hub
# ---------------------------------------------------------------------------

_BLOCK_LABELS = {
    "summary": "О себе",
    "work_exp": "Опыт работы",
    "skills": "Навыки",
    "education": "Образование",
    "extras": "Доп. информация",
}

_REQUIRED_BLOCKS = {"summary", "work_exp", "skills"}


async def show_block_selection(message: Message, state: FSMContext) -> None:
    """Show the block navigation hub: which sections are filled, which are missing."""
    data = await state.get_data()
    await state.set_state(InterviewStates.block_selection)

    _empty_values = {"нет", "no", ""}

    filled = {
        "summary": bool(data.get("summary")),
        "work_exp": bool(data.get("work_experiences")),
        "skills": bool(data.get("skills")),
        "education": bool(data.get("education")) and data.get("education", "").strip().lower() not in _empty_values,
        "extras": bool(data.get("extras")) and data.get("extras", "").strip().lower() not in _empty_values,
    }

    lines = []
    validation_issues = data.get("_validation_issues", [])
    if validation_issues:
        issues_text = "\n".join(f"⚠️ {issue}" for issue in validation_issues)
        lines.append(f"Замечания к данным:\n{issues_text}\n")

    lines.append("Ваш прогресс по разделам:\n")
    keyboard: list[list[InlineKeyboardButton]] = []

    for key, label in _BLOCK_LABELS.items():
        icon = "✅" if filled[key] else "➕"
        lines.append(f"{icon} {label}")
        if key == "work_exp" and filled[key]:
            btn_label = f"➕ Добавить ещё: {label}"
        else:
            btn_label = f"{'✏️ Изменить' if filled[key] else '➕ Добавить'}: {label}"
        keyboard.append([InlineKeyboardButton(text=btn_label, callback_data=f"block_sel_{key}")])

    required_done = all(filled[k] for k in _REQUIRED_BLOCKS)
    if required_done:
        keyboard.append([InlineKeyboardButton(text="▶️ Создать резюме", callback_data="block_sel_generate")])

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


# ---------------------------------------------------------------------------
# /back and /save commands (available during interview via Telegram menu)
# ---------------------------------------------------------------------------


@router.message(Command("back"), StateFilter(InterviewStates))
async def cmd_back(message: Message, state: FSMContext) -> None:
    """Return to the block selection hub from any interview state."""
    await show_block_selection(message, state)


@router.message(Command("save"), StateFilter(InterviewStates))
async def cmd_save(message: Message, state: FSMContext) -> None:
    """Force-persist current interview state to DB."""
    data = await state.get_data()
    user_id = data.get("user_id")
    if user_id:
        try:
            await _persist_state(state, user_id)
            await message.answer("✅ Прогресс сохранён. Вы можете вернуться в любой момент.")
        except Exception as exc:
            logger.error("Could not save state: %s", exc)
            await message.answer("Не удалось сохранить прогресс. Попробуйте ещё раз.")
    else:
        await message.answer("Нет данных для сохранения. Начните с /start.")


# ---------------------------------------------------------------------------
# Block selection callbacks
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "block_sel_summary", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_summary(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing = data.get("summary", "")
    await state.update_data(_skip_validation=False, _validation_issues=[])
    await state.set_state(InterviewStates.summary)
    if existing:
        await callback.message.answer(
            f"Текущий раздел «О себе»:\n\n{existing}\n\n"
            "Напишите новый текст, чтобы заменить его, или «помощь» — и ИИ составит черновик:"
        )
    else:
        await ask_summary(callback.message, state)


@router.callback_query(F.data == "block_sel_work_exp", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_work_exp(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing_jobs = data.get("work_experiences", [])
    await state.update_data(_skip_validation=False, _validation_issues=[])
    await state.set_state(InterviewStates.work_experience_company)
    if existing_jobs:
        companies = ", ".join(j.get("company", "?") for j in existing_jobs)
        await callback.message.answer(
            f"Уже добавлены: {companies}.\n\nВведите название следующей компании, чтобы добавить новое место работы:"
        )
    else:
        await callback.message.answer(
            f"{_stage_label(2)}\n\nНачнём с опыта работы. Укажите название компании (последнее или текущее место работы)."
        )


@router.callback_query(F.data == "block_sel_skills", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_skills(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing = data.get("skills", [])
    await state.update_data(_skills_append_mode=bool(existing), _skip_validation=False, _validation_issues=[])
    await state.set_state(InterviewStates.skills_input)
    if existing:
        await callback.message.answer(
            f"Текущие навыки: {', '.join(existing)}\n\n"
            "Введите дополнительные навыки в любом формате — они добавятся к существующим:"
        )
    else:
        await _start_skills_stage(callback.message, state)


@router.callback_query(F.data == "block_sel_education", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_education(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing = data.get("education", "")
    await state.update_data(_skip_validation=False, _validation_issues=[])
    await state.set_state(InterviewStates.education_input)
    if existing:
        await callback.message.answer(
            f"Текущее образование:\n{existing}\n\nВведите новый текст, чтобы обновить раздел:"
        )
    else:
        await callback.message.answer(
            f"{_stage_label(5)}\n\nУкажите Ваше образование: учебное заведение, специальность и год окончания."
        )


@router.callback_query(F.data == "block_sel_extras", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_extras(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing = data.get("extras", "")
    await state.update_data(_skip_validation=False, _validation_issues=[])
    await state.set_state(InterviewStates.extras_input)
    if existing:
        await callback.message.answer(
            f"Текущие доп. данные:\n{existing}\n\nВведите новый текст, чтобы обновить раздел:"
        )
    else:
        await callback.message.answer(
            f"{_stage_label(6)}\n\nУкажите дополнительную информацию:\n"
            "— Желаемый уровень зарплаты\n"
            "— Предпочтения по формату работы\n"
            "— Готовность к командировкам\n\n"
            "Если не актуально — напишите «нет»."
        )


@router.callback_query(F.data == "block_sel_generate", StateFilter(InterviewStates.block_selection))
async def cb_block_sel_generate(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer("Формирую резюме — это займёт несколько секунд...")
    await _generate_resume(callback.message, state)


@router.message(InterviewStates.block_selection)
async def handle_block_selection_text(message: Message, state: FSMContext) -> None:
    """If user types anything in block selection state, re-show the menu."""
    await show_block_selection(message, state)


# ---------------------------------------------------------------------------
# Off-topic guard
# ---------------------------------------------------------------------------


def _is_off_topic(text: str) -> bool:
    """Heuristic: only flag short messages that are entirely off-topic greetings/questions."""
    text = text.strip().lower()
    # Only treat short messages (< 8 words) as off-topic to avoid false positives
    # on legitimate interview answers containing these substrings
    if len(text.split()) > 7:
        return False
    import re
    off_topic_patterns = (
        r"^привет[!.]?$", r"^здравствуй", r"^как дела", r"^кто ты", r"^что ты умеешь",
        r"^расскажи анекдот", r"^какая погода",
    )
    return any(re.search(p, text) for p in off_topic_patterns)


async def _redirect_off_topic(message: Message, state: FSMContext, stage_num: int) -> None:
    await message.answer(
        f"Я специализируюсь на создании резюме. "
        f"Давайте продолжим — мы на {_stage_label(stage_num)}."
    )


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------


async def _persist_state(state: FSMContext, user_id: int) -> None:
    """Dump current FSM data to DB for recovery across bot restarts."""
    data = await state.get_data()
    # Filter out transient internal keys (prefixed with _) to avoid
    # persisting stale flags like _skip_validation, _pending_*, etc.
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    try:
        await db.save_interview_state(user_id, clean)
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
        f"Расскажите о себе в 3–5 предложениях: кто Вы как специалист, "
        f"сколько лет опыта, чем занимаетесь сейчас и что ищете в роли {desired_position}.\n\n"
        "Это будет раздел «О себе» в Вашем резюме."
    )


@router.message(InterviewStates.summary)
async def handle_summary(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, напишите текст ответа.")
        return

    if _is_help_request(text):
        data = await state.get_data()
        wait_msg = await message.answer("Составляю черновик «О себе» на основе Вашего профиля... 🤖")
        try:
            from bot.services.ai_service import generate_summary_help
            draft = await generate_summary_help(
                position=data.get("desired_position", ""),
                name=data.get("full_name", ""),
                work_experiences=data.get("work_experiences"),
            )
            await wait_msg.delete()
            await state.update_data(_ai_summary_draft=draft)
            await message.answer(
                f"Вот черновик «О себе»:\n\n{draft}\n\n"
                "Замените [УКАЖИТЕ ЦИФРУ/ФАКТ] на реальные данные и выберите действие:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Использовать этот вариант", callback_data="summary_ai_accept")],
                    [InlineKeyboardButton(text="✏️ Написать самому", callback_data="summary_ai_decline")],
                ]),
            )
        except Exception as exc:
            logger.error("generate_summary_help failed: %s", exc)
            await wait_msg.delete()
            await message.answer(_HELP_EXAMPLES["summary"])
        return

    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 1)
        return

    # Quality warning if too short
    if len(text.split()) < 25:
        await state.update_data(_pending_summary=text)
        await message.answer(
            "Раздел «О себе» выглядит кратким. Рекомендуем 3–5 предложений, "
            "чтобы привлечь внимание работодателя.",
            reply_markup=_warning_keyboard(
                continue_data="summary_warning_continue",
                edit_data="summary_warning_edit",
            ),
        )
        return

    await _save_summary(message, state, text)


async def _save_summary(message: Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    update: dict = {"summary": text, "achievement_nudges": 0}
    # Only initialise work_experiences on first save — don't wipe existing jobs on re-edit
    if not data.get("work_experiences"):
        update["work_experiences"] = []
    await state.update_data(**update)
    data = await state.get_data()
    await _persist_state(state, data["user_id"])

    await message.answer(
        f"Раздел «О себе»:\n\n{text}",
        reply_markup=_yes_no_keyboard("summary_ok", "summary_redo"),
    )


@router.callback_query(F.data == "summary_warning_continue", StateFilter(InterviewStates.summary))
async def cb_summary_warning_continue(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    text = data.get("_pending_summary", "")
    await _save_summary(callback.message, state, text)


@router.callback_query(F.data == "summary_warning_edit", StateFilter(InterviewStates.summary))
async def cb_summary_warning_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.summary)
    await callback.message.answer(
        "Расскажите о себе подробнее — 3–5 предложений о Вашем опыте и целях:"
    )


@router.callback_query(F.data == "summary_ok", StateFilter(InterviewStates.summary))
async def cb_summary_ok(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    # If work experience already exists (user is editing via block_selection) — go back there.
    # Otherwise continue the linear interview flow.
    if data.get("work_experiences"):
        await show_block_selection(callback.message, state)
    else:
        await state.set_state(InterviewStates.work_experience_company)
        await callback.message.answer(
            f"{_stage_label(2)}\n\n"
            "Начнём с опыта работы. Укажите название компании (последнее или текущее место работы)."
        )


@router.callback_query(F.data == "summary_redo", StateFilter(InterviewStates.summary))
async def cb_summary_redo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.summary)
    await callback.message.answer(
        "Хорошо. Расскажите о себе заново — 3–5 предложений о Вашем опыте и целях."
    )


@router.callback_query(F.data == "summary_ai_accept", StateFilter(InterviewStates.summary))
async def cb_summary_ai_accept(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    draft = data.get("_ai_summary_draft", "")
    if draft:
        await _save_summary(callback.message, state, draft)
    else:
        await state.set_state(InterviewStates.summary)
        await callback.message.answer("Черновик не найден. Напишите текст раздела «О себе» самостоятельно:")


@router.callback_query(F.data == "summary_ai_decline", StateFilter(InterviewStates.summary))
async def cb_summary_ai_decline(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.summary)
    await callback.message.answer("Хорошо, напишите свой вариант раздела «О себе» (3–5 предложений):")


# ---------------------------------------------------------------------------
# Stage 2 — Work experience (looped)
# ---------------------------------------------------------------------------


@router.message(InterviewStates.work_experience_company)
async def handle_we_company(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите название компании.")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["work_experience_company"])
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 2)
        return

    await state.update_data(current_company=text, achievement_nudges=0)
    await state.set_state(InterviewStates.work_experience_role)
    await message.answer("Какую должность Вы занимали в этой компании?")


@router.message(InterviewStates.work_experience_role)
async def handle_we_role(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите название должности.")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["work_experience_role"])
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
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["work_experience_dates"])
        return

    await state.update_data(current_dates=text)
    await state.set_state(InterviewStates.work_experience_responsibilities)
    await message.answer(
        "Опишите Ваши основные обязанности на этой позиции. "
        "Перечислите 3–6 ключевых задач."
    )


@router.message(InterviewStates.work_experience_responsibilities)
async def handle_we_responsibilities(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите Ваши обязанности.")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["work_experience_responsibilities"])
        return

    # Quality warning if too short
    if len(text.split()) < 15:
        await state.update_data(_pending_responsibilities=text)
        await message.answer(
            "Обязанности описаны кратко. Постарайтесь перечислить 3–6 конкретных задач.",
            reply_markup=_warning_keyboard(
                continue_data="responsibilities_warning_continue",
                edit_data="responsibilities_warning_edit",
            ),
        )
        return

    await _save_responsibilities(message, state, text)


async def _save_responsibilities(message: Message, state: FSMContext, text: str) -> None:
    await state.update_data(current_responsibilities=text)
    await state.set_state(InterviewStates.work_experience_achievements)
    await message.answer(
        "Какие результаты Вы достигли на этой позиции? "
        "Постарайтесь упомянуть конкретные достижения."
    )


@router.callback_query(F.data == "responsibilities_warning_continue", StateFilter(InterviewStates.work_experience_responsibilities))
async def cb_responsibilities_warning_continue(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    text = data.get("_pending_responsibilities", "")
    await _save_responsibilities(callback.message, state, text)


@router.callback_query(F.data == "responsibilities_warning_edit", StateFilter(InterviewStates.work_experience_responsibilities))
async def cb_responsibilities_warning_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_responsibilities)
    await callback.message.answer(
        "Перечислите 3–6 ключевых задач на этой позиции подробнее:"
    )


@router.message(InterviewStates.work_experience_achievements)
async def handle_we_achievements(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите Ваши достижения или напишите «нет».")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["work_experience_achievements"])
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 2)
        return

    data = await state.get_data()
    nudges: int = data.get("achievement_nudges", 0)

    # Require a meaningful number: percentage, or a 2+ digit number
    import re
    has_numbers = bool(re.search(r'\d+\s*%', text)) or bool(re.search(r'\b\d{2,}\b', text)) or "%" in text
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

            # Enhanced nudge message for attempt 2
            if nudges == 2:
                await message.answer(
                    "Отличный результат! Можете уточнить цифры? Это сделает резюме намного сильнее.\n\n"
                    "Например:\n"
                    "• На сколько % вырос показатель?\n"
                    "• За какой период достигнут результат?\n"
                    "• Для скольких клиентов / пользователей?\n\n"
                    "Может быть: на 15%? на 25%? на 30%? Вы помните точнее?"
                )
            else:
                await message.answer(ai_question)
            return
        except Exception as e:
            logger.error(f"Error AI clarify: {e}")
            await wait_msg.delete()

            # Fallback to hardcoded
            if nudges == 1:
                await message.answer(
                    "Не могли бы Вы добавить конкретные цифры к этому результату? "
                    "На сколько процентов/штук/часов Вы улучшили показатели?"
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


@router.message(InterviewStates.work_experience_confirm)
async def handle_we_confirm_text(message: Message, state: FSMContext) -> None:
    """User typed text instead of pressing confirmation buttons — re-show."""
    await message.answer(
        "Пожалуйста, выберите действие с помощью кнопок выше: «Всё верно» или «Исправить».",
        reply_markup=_yes_no_keyboard("we_block_ok", "we_block_redo"),
    )


@router.callback_query(F.data == "we_block_ok", StateFilter(InterviewStates.work_experience_confirm))
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

    # Persist to DB — clear old records before first insert in this session
    profile = await db.get_candidate_profile(user_id)
    if profile:
        try:
            if len(jobs) == 1:
                await db.clear_work_experiences(profile["id"])
            await db.save_work_experience(profile["id"], job)
        except Exception as exc:
            logger.warning("Could not save work experience: %s", exc)

    await _persist_state(state, user_id)

    # Stage 3 — ask about additional jobs
    await callback.message.answer(
        f"{_stage_label(3)}\n\nЕсть ли другие места работы, которые стоит включить в резюме?",
        reply_markup=_more_jobs_keyboard(),
    )


@router.callback_query(F.data == "we_block_redo", StateFilter(InterviewStates.work_experience_confirm))
async def cb_we_block_redo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_company)
    await callback.message.answer(
        "Хорошо, заполним блок заново. Введите название компании."
    )


@router.callback_query(F.data == "add_more_job", StateFilter(InterviewStates.work_experience_confirm))
async def cb_add_more_job(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(InterviewStates.work_experience_company)
    await callback.message.answer(
        "Введите название следующей компании."
    )


@router.callback_query(F.data == "skip_more_jobs", StateFilter(InterviewStates.work_experience_confirm))
async def cb_skip_more_jobs(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _start_skills_stage(callback.message, state)


# ---------------------------------------------------------------------------
# Stage 4 — Skills
# ---------------------------------------------------------------------------


async def _start_skills_stage(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    desired_position = data.get("desired_position", "")

    # Step 1: Try to get skills from HH vacancies
    skills_list: list[str] = []
    source = ""
    try:
        from bot.services.hh_service import get_skills_for_position
        hh_skills = await get_skills_for_position(desired_position)
        if len(hh_skills) >= 5:
            skills_list = hh_skills
            source = "hh"
    except Exception as exc:
        logger.warning("HH skills fetch failed: %s", exc)

    # Step 2: Fall back to AI suggestions if HH returned too few
    if len(skills_list) < 5:
        try:
            from bot.services.ai_service import suggest_skills
            ai_skills = await suggest_skills(desired_position)
            if ai_skills:
                skills_list = ai_skills
                source = "ai"
        except Exception as exc:
            logger.warning("AI skill suggestions failed: %s", exc)

    await state.set_state(InterviewStates.skills_input)

    if skills_list:
        numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(skills_list))
        await message.answer(
            f"{_stage_label(4)}\n\n"
            f"Вот навыки, которые часто встречаются для этой роли:\n{numbered}\n\n"
            "Введите навыки, которыми Вы владеете (через запятую), или добавьте свои:"
        )
    else:
        # Step 3: Manual fallback
        await message.answer(
            f"{_stage_label(4)}\n\n"
            "Перечислите Ваши ключевые навыки через запятую. "
            "Укажите как профессиональные, так и инструментальные навыки."
        )


@router.message(InterviewStates.skills_input)
async def handle_skills(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите навыки в любом формате.")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["skills_input"])
        return
    if _is_off_topic(text):
        await _redirect_off_topic(message, state, 4)
        return

    # AI-parse skills from any format
    wait_msg = await message.answer("Обрабатываю навыки... 🤖")
    try:
        from bot.services.ai_service import parse_skills_from_text
        new_skills = await parse_skills_from_text(text)
    except Exception as exc:
        logger.warning("parse_skills_from_text failed, falling back to split: %s", exc)
        new_skills = [s.strip() for s in text.split(",") if s.strip()]
    await wait_msg.delete()

    # Append mode: merge with existing skills (dedup)
    data = await state.get_data()
    if data.get("_skills_append_mode"):
        existing = data.get("skills", [])
        existing_lower = {s.lower() for s in existing}
        added = [s for s in new_skills if s.lower() not in existing_lower]
        skills = existing + added
        await state.update_data(_skills_append_mode=False)
    else:
        skills = new_skills

    # Quality warning if too few skills
    if len(skills) < 5:
        await state.update_data(_pending_skills=skills)
        await message.answer(
            "Указано мало навыков. Для сильного резюме рекомендуется 6–10. Хотите добавить ещё?",
            reply_markup=_warning_keyboard(
                continue_data="skills_warning_continue",
                edit_data="skills_warning_edit",
                edit_label="➕ Добавить навыки",
            ),
        )
        return

    await _save_skills(message, state, skills)


async def _save_skills(message: Message, state: FSMContext, skills: list[str]) -> None:
    await state.update_data(skills=skills)
    await message.answer(
        f"Навыки:\n{', '.join(skills)}",
        reply_markup=_skill_confirm_keyboard(),
    )


@router.callback_query(F.data == "skills_warning_continue", StateFilter(InterviewStates.skills_input))
async def cb_skills_warning_continue(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    skills = data.get("_pending_skills", [])
    await _save_skills(callback.message, state, skills)


@router.callback_query(F.data == "skills_warning_edit", StateFilter(InterviewStates.skills_input))
async def cb_skills_warning_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    existing = data.get("_pending_skills", [])
    # Keep pending skills and switch to append mode
    await state.update_data(skills=existing, _skills_append_mode=True)
    await state.set_state(InterviewStates.skills_input)
    await callback.message.answer(
        "Добавьте ещё навыки в любом формате — они присоединятся к уже введённым:"
    )


@router.callback_query(F.data == "skills_confirmed", StateFilter(InterviewStates.skills_input))
async def cb_skills_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data["user_id"]

    # Persist skills to DB — clear old records first to avoid duplicates
    profile = await db.get_candidate_profile(user_id)
    if profile:
        await db.clear_skills(profile["id"])
        for skill_name in data.get("skills", []):
            try:
                await db.save_skill(profile["id"], skill_name, "general")
            except Exception as exc:
                logger.warning("Could not save skill '%s': %s", skill_name, exc)

    await _persist_state(state, user_id)
    await state.set_state(InterviewStates.education_input)
    await callback.message.answer(
        f"{_stage_label(5)}\n\n"
        "Укажите Ваше образование: учебное заведение, специальность и год окончания."
    )


@router.callback_query(F.data == "skills_add_more", StateFilter(InterviewStates.skills_input))
async def cb_skills_add_more(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    # Enable append mode so new skills are merged with existing
    await state.update_data(_skills_append_mode=True)
    await state.set_state(InterviewStates.skills_input)
    await callback.message.answer(
        "Добавьте ещё навыки в любом формате — можно вставить список, они добавятся к уже введённым:"
    )


# ---------------------------------------------------------------------------
# Stage 5 — Education
# ---------------------------------------------------------------------------


@router.message(InterviewStates.education_input)
async def handle_education(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, опишите Ваше образование или напишите «нет».")
        return
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["education_input"])
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
            await db.clear_education(profile["id"])
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
    if _is_help_request(text):
        await message.answer(_HELP_EXAMPLES["extras_input"])
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
    from bot.handlers.start import main_keyboard

    data = await state.get_data()
    user_id = data["user_id"]

    # Validate required sections before generating
    # Onboarding fields (can't navigate there from here)
    onboarding_missing = []
    if not data.get("full_name"):
        onboarding_missing.append("имя")
    if not data.get("desired_position"):
        onboarding_missing.append("желаемая должность")
    if not data.get("city"):
        onboarding_missing.append("город")

    if onboarding_missing:
        await message.answer(
            f"⚠️ Отсутствует базовая информация: {', '.join(onboarding_missing)}. "
            "Пожалуйста, начните с /start."
        )
        return

    # Interview fields — show navigation buttons for missing ones
    interview_nav: list[tuple[str, str]] = []  # (label, callback_data)
    if not data.get("summary"):
        interview_nav.append(("О себе", "block_sel_summary"))
    if not data.get("work_experiences"):
        interview_nav.append(("Опыт работы", "block_sel_work_exp"))
    if not data.get("skills"):
        interview_nav.append(("Навыки", "block_sel_skills"))

    education = data.get("education", "")
    if not education or education.lower() in ["", "нет"]:
        await message.answer("💡 Образование не указано — рекомендуем добавить для полноты резюме.")

    if interview_nav:
        missing_labels = ", ".join(label for label, _ in interview_nav)
        buttons = [
            [InlineKeyboardButton(text=f"➕ {label}", callback_data=cb)]
            for label, cb in interview_nav
        ]
        await message.answer(
            f"⚠️ Для создания резюме не хватает: {missing_labels}.\n"
            "Выберите раздел для заполнения:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await state.set_state(InterviewStates.block_selection)
        return

    # Validate data quality before generating
    skip_validation = data.get("_skip_validation", False)
    if not skip_validation:
        try:
            from bot.services.ai_service import validate_resume_data
            issues = await validate_resume_data(data)
            if issues:
                issues_text = "\n".join(f"• {issue}" for issue in issues)
                await state.update_data(_skip_validation=True, _validation_issues=issues)
                await state.set_state(InterviewStates.generation_confirm)
                await message.answer(
                    f"⚠️ Перед генерацией мы заметили несколько вопросов к данным:\n\n{issues_text}\n\n"
                    "Рекомендуем исправить — ИИ сможет сделать более качественное резюме. "
                    "Или создайте резюме с текущими данными.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✏️ Исправить данные", callback_data="validation_fix")],
                        [InlineKeyboardButton(text="▶️ Создать резюме как есть", callback_data="validation_proceed")],
                    ]),
                )
                return
        except Exception as exc:
            logger.warning("validate_resume_data failed, skipping: %s", exc)

    # Detect gender for correct verb forms in О СЕБЕ
    gender = "male"
    full_name = data.get("full_name", "")
    if full_name:
        try:
            from bot.services.ai_service import detect_gender
            gender = await detect_gender(full_name)
        except Exception as exc:
            logger.warning("Gender detection failed, defaulting to male: %s", exc)

    try:
        from bot.services.ai_service import generate_resume_text
        resume_text = await generate_resume_text(data, gender=gender)
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

    # Clear validation state — fresh slate for next generation attempt
    await state.update_data(_validation_issues=[], _skip_validation=False)
    await state.set_state(ResumeStates.viewing_draft)

    footer = (
        "\n\n"
        "Вы можете отредактировать его командами:\n"
        "«короче» — сделать резюме короче\n"
        "«формальнее» — более официальный стиль\n"
        "«перепиши блок» — переписать конкретный раздел\n"
        "«добавь навык» — добавить навык\n\n"
        "📋 Не забудьте добавить контактные данные (email и телефон) — работодатели должны знать, как с Вами связаться.\n\n"
        "🤖 Резюме создано с помощью ИИ — прочитайте его перед размещением: проверьте точность дат, формулировок и цифр. "
        "На собеседовании Вас могут попросить подтвердить указанные навыки и достижения — будьте готовы рассказать о них подробнее."
    )
    await _send_long_message(
        message,
        f"Ваше резюме готово:\n\n{resume_text}{footer}",
        reply_markup=main_keyboard(),
    )


@router.callback_query(F.data == "validation_proceed", StateFilter(InterviewStates.generation_confirm))
async def cb_validation_proceed(callback: CallbackQuery, state: FSMContext) -> None:
    """User chose to generate resume despite validation warnings."""
    await callback.answer()
    await callback.message.answer("Формирую резюме — это займёт несколько секунд...")
    await _generate_resume(callback.message, state)


@router.callback_query(F.data == "validation_fix", StateFilter(InterviewStates.generation_confirm))
async def cb_validation_fix(callback: CallbackQuery, state: FSMContext) -> None:
    """User wants to fix data — show block selection with validation issues highlighted."""
    await callback.answer()
    # Keep _skip_validation=True so returning here doesn't trigger another validation loop
    await show_block_selection(callback.message, state)


@router.message(InterviewStates.generation_confirm)
async def handle_generation_confirm_text(message: Message, state: FSMContext) -> None:
    """Re-show options if user types something in generation_confirm state."""
    await message.answer(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Исправить данные", callback_data="validation_fix")],
            [InlineKeyboardButton(text="▶️ Создать резюме как есть", callback_data="validation_proceed")],
        ]),
    )


def _build_plain_resume(data: dict) -> str:
    """Fallback: assemble resume text from raw interview data without AI."""
    lines: list[str] = []

    name = data.get("full_name", "")
    position = data.get("desired_position", "")
    city = data.get("city", "")

    if name:
        lines.append(name)
    if position:
        lines.append(position)
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

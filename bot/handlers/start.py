"""
/start handler, onboarding FSM, and persistent reply keyboard button handlers.
"""

from __future__ import annotations

import html
import logging

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
import os
import fitz
import docx

import bot.database as db
from bot.states import OnboardingStates, InterviewStates, ImprovementStates, ResumeStates

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Persistent reply keyboard (shown after onboarding and after resume)
# ---------------------------------------------------------------------------

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📄 Моё резюме"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="🔄 Начать заново"), KeyboardButton(text="🗑 Удалить профиль")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить всё", callback_data="confirm_delete"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_delete"),
            ]
        ]
    )


def _choose_path_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я знаю, кем хочу работать", callback_data="path_know")],
            [InlineKeyboardButton(text="Напиши 2 вопроса, чтобы помочь выбрать", callback_data="path_help")],
        ]
    )


def _resume_actions_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard shown alongside the main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Продолжить интервью", callback_data="menu_continue_interview")],
            [InlineKeyboardButton(text="Удалить профиль", callback_data="menu_delete_profile")],
        ]
    )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        telegram_username=message.from_user.username,
    )
    user_id = user["id"]
    await db.log_event(user_id, "bot_started")

    # If the user already has a profile, show the main menu
    profile = await db.get_candidate_profile(user_id)
    if profile:
        await state.clear()
        await state.update_data(user_id=user_id)
        await message.answer(
            "С возвращением! Ваш профиль уже существует.\n"
            "Выберите действие с помощью кнопок меню.",
            reply_markup=main_keyboard(),
        )
        await message.answer(
            "Или выберите из дополнительных опций:",
            reply_markup=_resume_actions_keyboard(),
        )
        return

    # New user — start onboarding
    await state.clear()
    await state.update_data(user_id=user_id)
    await state.set_state(OnboardingStates.choosing_path)
    await message.answer(
        "Знакомо?\n\n"
        "— Отправили 50 резюме — ни одного ответа\n"
        "— Не знаете, как описать свой опыт в цифрах\n"
        "— 75% резюме отсеивает робот ещё до живого HR\n\n"
        "Я помогу это исправить. За 7 минут мы вместе создадим резюме, "
        "которое проходит AI-скрининг и попадает к нужным людям — бесплатно.\n\n"
        "Вы уже знаете, на какую должность будете откликаться, или Вам нужна помощь с выбором?",
        reply_markup=_choose_path_keyboard(),
    )


# ---------------------------------------------------------------------------
# Onboarding: path selection
# ---------------------------------------------------------------------------

@router.message(OnboardingStates.choosing_path)
async def onboarding_choosing_path_text(message: Message, state: FSMContext) -> None:
    """User typed text instead of pressing a button — re-show the keyboard."""
    await message.answer(
        "Пожалуйста, выберите один из вариантов ниже:",
        reply_markup=_choose_path_keyboard(),
    )


@router.callback_query(F.data == "path_know", StateFilter(OnboardingStates.choosing_path, default_state))
async def onboarding_path_know(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(OnboardingStates.waiting_desired_position)
    await callback.message.answer("Отлично! На какую позицию Вы ищете работу? (введите название текстом)")


@router.callback_query(F.data == "path_help", StateFilter(OnboardingStates.choosing_path, default_state))
async def onboarding_path_help(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(OnboardingStates.coaching_questions)
    await callback.message.answer(
        "Давайте подберём идеальную профессию!\n\n"
        "Ответьте на пару простых вопросов:\n"
        "1. Что Вам больше всего нравится делать (например: общаться с людьми, копаться в таблицах, управлять процессами)?\n"
        "2. Что у Вас получается лучше всего? (в чём Ваша супер-сила по мнению коллег/друзей?)"
    )


@router.message(OnboardingStates.coaching_questions)
async def onboarding_handle_coaching(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, расскажите немного о себе текстом.")
        return

    from bot.services.ai_service import _chat

    prompt = (
        f"Пользователь ищет работу. Он описал свои интересы: '{text}'. "
        "Выдели до 3 ключевых запросов (каждый до 3 слов) для поиска вакансий на hh.ru. "
        "Каждый запрос на новой строке. Если направление одно — выведи один запрос."
    )

    wait_msg = await message.answer("Анализирую Ваши сильные стороны на рынке (hh.ru)... 🤖")

    try:
        from bot.services.hh_service import analyze_market_salary
        raw_queries = await _chat(
            system="Ты точный алгоритм извлечения ключевых слов.",
            user=prompt,
            model="gpt-4o-mini",
            temperature=0.1,
        )
        queries = [q.strip('\'". \n') for q in raw_queries.strip().splitlines() if q.strip()]
        queries = queries[:3]  # safety cap
        if not queries:
            queries = [raw_queries.strip('\'". \n')]

        # Fetch HH data for each query, merge titles
        all_titles: list[str] = []
        seen_titles_lower: set[str] = set()
        primary_hh_data: dict | None = None

        for q in queries:
            hh_data = await analyze_market_salary(q)
            if primary_hh_data is None and hh_data.get("titles"):
                primary_hh_data = hh_data
            for t in hh_data.get("titles", []):
                t_lower = t.lower()
                if t_lower not in seen_titles_lower:
                    seen_titles_lower.add(t_lower)
                    all_titles.append(t)

        await wait_msg.delete()

        queries_display = "», «".join(queries)

        if not all_titles:
            await message.answer(f"ИИ сгенерировал вот такой странный запрос для поиска: «{queries_display}». На hh.ru по нему ничего нормального нет.\n\nНапишите ниже какую-нибудь должность вручную для старта:")
            await state.set_state(OnboardingStates.waiting_desired_position)
            return

        titles_str = "\n".join(f"• {t}" for t in all_titles[:6])
        salary_text = primary_hh_data["text"] if primary_hh_data else ""
        await message.answer(f"Я проанализировал рынок hh.ru по запросам «{queries_display}»!\nВам отлично подойдут такие роли:\n{titles_str}\n\n{salary_text}\n\nНапишите ниже должность, которую Вы выбираете для этого резюме:")
        await state.set_state(OnboardingStates.waiting_desired_position)
    except Exception as e:
        logger.error(f"Error in coaching: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await message.answer("Извините, произошёл сбой при анализе рынка. Напишите желаемую должность вручную:")
        await state.set_state(OnboardingStates.waiting_desired_position)


# ---------------------------------------------------------------------------
# Onboarding: collect desired position
# ---------------------------------------------------------------------------

@router.message(OnboardingStates.waiting_desired_position)
async def onboarding_desired_position(message: Message, state: FSMContext) -> None:
    position = message.text.strip() if message.text else ""
    if not position:
        await message.answer("Пожалуйста, введите название желаемой позиции текстом.")
        return

    await state.update_data(desired_position=position)
    await state.set_state(OnboardingStates.waiting_name)
    await message.answer("Как Вас зовут? Укажите имя и фамилию.")


@router.message(OnboardingStates.waiting_name)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Пожалуйста, введите имя и фамилию текстом.")
        return

    await state.update_data(full_name=name)
    # contacts step is removed — go directly to city
    await state.set_state(OnboardingStates.waiting_city)
    await message.answer("В каком городе Вы находитесь или ищете работу?")


@router.message(OnboardingStates.waiting_city)
async def onboarding_city(message: Message, state: FSMContext) -> None:
    city = message.text.strip() if message.text else ""
    if not city:
        await message.answer("Пожалуйста, введите название города.")
        return

    # Strip everything after comma or question mark — likely noise
    for sep in (",", "?", "!"):
        if sep in city:
            city = city.split(sep)[0].strip()

    if len(city) > 60 or len(city.split()) > 4:
        await message.answer(
            "Похоже, это не название города. Введите только город, например: «Москва» или «Санкт-Петербург»."
        )
        return

    await state.update_data(city=city)
    data = await state.get_data()
    user_id = data["user_id"]

    # Save base profile (no contacts field)
    try:
        profile_id = await db.save_candidate_profile(
            user_id=user_id,
            profile_data={
                "desired_position": data.get("desired_position", ""),
                "full_name": data.get("full_name", ""),
                "city": city,
                "raw_data": {},
            },
        )
        await state.update_data(profile_id=profile_id)
        await db.update_user_stage(user_id, "interview")
    except Exception:
        await message.answer(
            "Произошла ошибка при сохранении данных. Попробуйте ещё раз или нажмите /start."
        )
        return

    await state.set_state(OnboardingStates.upload_resume_prompt)
    await message.answer(
        "Если у Вас есть существующее резюме (PDF или DOCX), отправьте его — "
        "я использую его как основу. Если резюме нет, напишите «нет», и мы начнём с нуля."
    )


@router.message(OnboardingStates.upload_resume_prompt)
async def onboarding_upload_prompt(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip().lower()

    if message.document:
        await state.set_state(OnboardingStates.processing_upload)
        processing_msg = await message.answer(
            "Файл получен. Анализирую резюме с помощью ИИ... Это займёт около 10-15 секунд 🤖"
        )
        await _process_uploaded_document(message, state, bot, processing_msg)
        return

    _SKIP_WORDS = {"нет", "no", "пропустить", "без файла", "нету", "не буду", "не надо"}
    if text in _SKIP_WORDS:
        await _start_interview(message, state)
        return

    # User typed something that's not a file and not a skip command
    await message.answer(
        "Чтобы прикрепить файл — нажмите скрепку 📎 и выберите PDF или DOCX.\n"
        "Если резюме нет — напишите «нет», и мы начнём с нуля."
    )


@router.message(OnboardingStates.processing_upload, F.text)
async def onboarding_processing_text(message: Message, state: FSMContext) -> None:
    """User typed text while file is being processed — tell them to wait."""
    await message.answer(
        "Файл обрабатывается. Подождите немного или отправьте другой файл (PDF / DOCX)."
    )


@router.message(OnboardingStates.processing_upload, F.document)
async def onboarding_document(message: Message, state: FSMContext, bot: Bot) -> None:
    processing_msg = await message.answer(
        "Файл получен. Анализирую резюме с помощью ИИ... Это займёт около 10-15 секунд 🤖"
    )
    await _process_uploaded_document(message, state, bot, processing_msg)


async def _process_uploaded_document(message: Message, state: FSMContext, bot: Bot, processing_msg: Message) -> None:
    document = message.document
    file_id = document.file_id
    file_name = document.file_name or "resume"
    ext = file_name.split(".")[-1].lower()

    if ext not in ["pdf", "docx"]:
        await processing_msg.edit_text("Извините, я поддерживаю только форматы PDF и DOCX. Отправьте файл в нужном формате или напишите «нет», чтобы продолжить вручную.")
        await state.set_state(OnboardingStates.upload_resume_prompt)
        return

    try:
        file = await bot.get_file(file_id)
        file_path = file.file_path

        # Download file to a local temporary path
        local_path = f"/tmp/{file_id}.{ext}"
        await bot.download_file(file_path, local_path)

        text = ""
        try:
            if ext == "pdf":
                doc = fitz.open(local_path)
                for page in doc:
                    text += page.get_text()
                doc.close()
            elif ext == "docx":
                doc = docx.Document(local_path)
                for para in doc.paragraphs:
                    text += para.text + "\n"
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

        if len(text.strip()) < 50:
            await processing_msg.edit_text("Не удалось извлечь достаточно текста из файла (возможно, это отсканированная картинка). Давайте продолжим вручную.")
            await _start_interview(message, state)
            return

        from bot.services.ai_service import parse_resume_file
        parsed_data = await parse_resume_file(text)

        # Keep existing fields and merge new ones
        # Normalize work experience field names to match interview flow schema
        raw_jobs = parsed_data.get("work_experiences", [])
        normalized_jobs = []
        for job in raw_jobs:
            normalized_jobs.append({
                "company": job.get("company", ""),
                "role": job.get("position", job.get("role", "")),
                "dates": job.get("dates", ""),
                "responsibilities": job.get("description", job.get("responsibilities", "")),
                "achievements": job.get("achievements", ""),
            })
            # Build dates from start_date/end_date if "dates" is empty
            if not normalized_jobs[-1]["dates"]:
                start = job.get("start_date", "")
                end = job.get("end_date", "")
                if start or end:
                    normalized_jobs[-1]["dates"] = f"{start} — {end}".strip(" —")

        merged_data = {
            "summary": parsed_data.get("summary", ""),
            "work_experiences": normalized_jobs,
            "skills": parsed_data.get("skills", []),
            "education": "",
        }

        # Format education from list of dicts to string
        ed_list = parsed_data.get("education", [])
        if ed_list and isinstance(ed_list, list):
            ed_strs = []
            for ed in ed_list:
                ed_strs.append(f"{ed.get('institution', '')} - {ed.get('degree', '')} ({ed.get('year_end', '')})")
            merged_data["education"] = "\n".join(ed_strs)

        await state.update_data(parsed_from_file=True, **merged_data)

        # Persist state so parsed data survives bot restarts
        data = await state.get_data()
        uid = data.get("user_id")
        if uid:
            from bot.handlers.interview import _persist_state
            await _persist_state(state, uid)

        await processing_msg.edit_text("Я извлёк основные данные. Сейчас проведу быстрый аудит Вашего резюме на соответствие требованиям рынка (ATS)... 🤖")
        from bot.services.ai_service import evaluate_parsed_resume
        critique = await evaluate_parsed_resume(merged_data)

        from bot.states import ImprovementStates
        await message.answer(
            f"Вот что у нас получилось:\n\n{critique}\n\nДавайте перейдём к исправлению слабых мест?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Да, начнём исправлять", callback_data="start_improving")],
                [InlineKeyboardButton(text="Нет, оставить как есть", callback_data="skip_improving")]
            ])
        )
        await state.set_state(ImprovementStates.reviewing_parsed_data)

    except Exception as e:
        logger.error(f"Error parsing document: {e}")
        await processing_msg.edit_text(
            "Произошла ошибка при анализе файла. Давайте продолжим вручную — это займёт 5–7 минут."
        )
        await _start_interview(message, state)


@router.callback_query(F.data == "start_improving", StateFilter(ImprovementStates.reviewing_parsed_data))
async def cb_start_improving(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from bot.handlers.interview import show_block_selection
    await callback.message.answer(
        "Данные из Вашего резюме загружены. Проверьте каждый раздел и при необходимости отредактируйте."
    )
    await show_block_selection(callback.message, state)


@router.callback_query(F.data == "skip_improving", StateFilter(ImprovementStates.reviewing_parsed_data))
async def cb_skip_improving(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from bot.handlers.interview import show_block_selection
    await show_block_selection(callback.message, state)


async def _start_interview(message: Message, state: FSMContext) -> None:
    """Transition to the first interview state."""
    from bot.handlers.interview import ask_summary, _persist_state
    # Persist onboarding data so it survives bot restarts before first interview step
    data = await state.get_data()
    user_id = data.get("user_id")
    if user_id:
        await _persist_state(state, user_id)
    await state.set_state(InterviewStates.summary)
    await ask_summary(message, state)


# ---------------------------------------------------------------------------
# Main menu callbacks (inline keyboard from profile page)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu_continue_interview", StateFilter(default_state))
async def cb_continue_interview(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    saved = await db.get_interview_state(user_id)
    if saved:
        await state.update_data(**saved)

    # Restore onboarding fields (full_name, desired_position, city) from profile
    # in case interview_state JSONB pre-dates their inclusion or was saved without them.
    profile = await db.get_candidate_profile(user_id)
    if profile:
        patch = {k: profile[k] for k in ("full_name", "desired_position", "city", "profile_id") if profile.get(k)}
        if patch:
            await state.update_data(**patch)

    from bot.handlers.interview import show_block_selection
    await show_block_selection(callback.message, state)


@router.callback_query(F.data == "menu_delete_profile")
async def cb_menu_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer(
        "Вы уверены, что хотите удалить все данные профиля? "
        "Это действие нельзя отменить.",
        reply_markup=_confirm_delete_keyboard(),
    )


@router.callback_query(F.data == "confirm_delete")
async def cb_confirm_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        user_id = user["id"]

    success = await db.delete_user_profile(user_id)
    await state.clear()
    await state.update_data(user_id=user_id)

    if success:
        await callback.message.answer(
            "Все данные Вашего профиля удалены. "
            "Чтобы начать заново, отправьте /start.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await callback.message.answer(
            "Произошла ошибка при удалении. Попробуйте позже."
        )


@router.callback_query(F.data == "cancel_delete")
async def cb_cancel_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Удаление отменено.")
    await callback.message.answer("Удаление отменено. Ваши данные в сохранности.")


# ---------------------------------------------------------------------------
# Auto-continue inline buttons (for fallback handler in main.py)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "autocontinue_resume", StateFilter(default_state))
async def cb_autocontinue_resume(callback: CallbackQuery, state: FSMContext) -> None:
    """Restore FSM state from DB and show block selection."""
    await callback.answer()
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    saved = await db.get_interview_state(user_id)
    if saved:
        await state.update_data(**saved)

    # Restore onboarding fields from profile (same logic as cb_continue_interview)
    profile = await db.get_candidate_profile(user_id)
    if profile:
        patch = {k: profile[k] for k in ("full_name", "desired_position", "city", "profile_id") if profile.get(k)}
        if patch:
            await state.update_data(**patch)

    from bot.handlers.interview import show_block_selection
    await show_block_selection(callback.message, state)


@router.callback_query(F.data == "autocontinue_restart", StateFilter(default_state))
async def cb_autocontinue_restart(callback: CallbackQuery, state: FSMContext) -> None:
    """Restart from scratch."""
    await callback.answer()
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        user_id = user["id"]

    await state.clear()
    await state.update_data(user_id=user_id)
    await state.set_state(OnboardingStates.choosing_path)
    await callback.message.answer(
        "Хорошо, начнём заново!\n\n"
        "Вы уже знаете, на какую должность будете откликаться, или Вам нужна помощь с выбором?",
        reply_markup=_choose_path_keyboard(),
    )


# ---------------------------------------------------------------------------
# Persistent keyboard button handlers
# ---------------------------------------------------------------------------

@router.message(F.text == "📄 Моё резюме")
async def btn_my_resume(message: Message, state: FSMContext) -> None:
    """Show the latest saved resume."""
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    resumes = await db.get_resumes(user_id)
    if not resumes:
        await message.answer(
            "У Вас нет сохранённых резюме. Пройдите интервью, чтобы создать первое резюме.",
            reply_markup=main_keyboard(),
        )
        return

    latest = resumes[0]
    content = latest.get("content", "")
    title = latest.get("title", "Резюме")
    full_text = f"<b>{html.escape(title)}</b>\n\n{html.escape(content)}"
    limit = 4000
    chunks = [full_text[i : i + limit] for i in range(0, len(full_text), limit)]
    for i, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode="HTML",
            reply_markup=main_keyboard() if i == len(chunks) - 1 else None,
        )


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message, state: FSMContext) -> None:
    """Show help text."""
    await message.answer(
        "Я помогу создать профессиональное резюме для hh.ru. "
        "Отвечайте на мои вопросы, и я составлю резюме.\n\n"
        "Если затрудняетесь — напишите «пример» или «помогите» и я покажу образец ответа.\n\n"
        "Кнопки меню:\n"
        "📄 Моё резюме — показать последнее сохранённое резюме\n"
        "🔄 Начать заново — вернуться в главное меню\n"
        "🗑 Удалить профиль — удалить все Ваши данные",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "🔄 Начать заново")
async def btn_restart(message: Message, state: FSMContext) -> None:
    """Return to main menu for existing user."""
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    profile = await db.get_candidate_profile(user_id)
    if profile:
        await state.clear()
        await state.update_data(user_id=user_id)
        await message.answer(
            "С возвращением! Ваш профиль уже существует.\n"
            "Выберите действие с помощью кнопок меню.",
            reply_markup=main_keyboard(),
        )
        await message.answer(
            "Или выберите из дополнительных опций:",
            reply_markup=_resume_actions_keyboard(),
        )
    else:
        # No profile yet — restart full onboarding
        await state.clear()
        await state.update_data(user_id=user_id)
        await state.set_state(OnboardingStates.choosing_path)
        await message.answer(
            "Вы уже знаете, на какую должность будете откликаться, или Вам нужна помощь с выбором?",
            reply_markup=_choose_path_keyboard(),
        )


@router.message(F.text == "🗑 Удалить профиль")
async def btn_delete_profile(message: Message, state: FSMContext) -> None:
    """Show delete confirmation inline buttons."""
    await message.answer(
        "Вы уверены, что хотите удалить все данные профиля? "
        "Это действие нельзя отменить.",
        reply_markup=_confirm_delete_keyboard(),
    )

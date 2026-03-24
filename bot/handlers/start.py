"""
/start, /help, /export, /delete_profile handlers.
"""

from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import bot.database as db
from bot.states import OnboardingStates, InterviewStates, ResumeStates

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Продолжить интервью", callback_data="menu_continue_interview")],
            [InlineKeyboardButton(text="Мои резюме", callback_data="menu_my_resumes")],
            [InlineKeyboardButton(text="Удалить профиль", callback_data="menu_delete_profile")],
        ]
    )


def _confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить всё", callback_data="confirm_delete"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_delete"),
            ]
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
        await state.update_data(user_id=user_id)
        await message.answer(
            "С возвращением. Ваш профиль уже существует.\n"
            "Выберите действие:",
            reply_markup=_main_menu_keyboard(),
        )
        return

    # New user — start onboarding
    await state.clear()
    await state.update_data(user_id=user_id)
    await state.set_state(OnboardingStates.waiting_desired_position)
    await message.answer(
        "Знакомо?\n\n"
        "— Отправил 50 резюме — ни одного ответа\n"
        "— Не знаешь, как описать свой опыт в цифрах\n"
        "— 75% резюме отсеивает робот ещё до живого HR\n\n"
        "Я помогу это исправить. За 7 минут мы вместе создадим резюме, "
        "которое проходит AI-скрининг и попадает к нужным людям — бесплатно.\n\n"
        "Для начала: на какую позицию вы ищете работу?"
    )


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
    await message.answer("Как вас зовут? Укажите имя и фамилию.")


@router.message(OnboardingStates.waiting_name)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Пожалуйста, введите имя и фамилию текстом.")
        return

    await state.update_data(full_name=name)
    await state.set_state(OnboardingStates.waiting_contacts)
    await message.answer(
        "Укажите ваш email и номер телефона.\n"
        "Можно в одном сообщении, например:\n"
        "ivan@example.com, +7 999 123-45-67"
    )


@router.message(OnboardingStates.waiting_contacts)
async def onboarding_contacts(message: Message, state: FSMContext) -> None:
    contacts = message.text.strip() if message.text else ""
    if not contacts:
        await message.answer("Пожалуйста, введите контактные данные текстом.")
        return

    await state.update_data(contacts=contacts)
    await state.set_state(OnboardingStates.waiting_city)
    await message.answer("В каком городе вы находитесь или ищете работу?")


@router.message(OnboardingStates.waiting_city)
async def onboarding_city(message: Message, state: FSMContext) -> None:
    city = message.text.strip() if message.text else ""
    if not city:
        await message.answer("Пожалуйста, введите название города.")
        return

    await state.update_data(city=city)
    data = await state.get_data()
    user_id = data["user_id"]

    # Save base profile
    try:
        profile_id = await db.save_candidate_profile(
            user_id=user_id,
            profile_data={
                "desired_position": data.get("desired_position", ""),
                "full_name": data.get("full_name", ""),
                "contacts": data.get("contacts", ""),
                "city": city,
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
        "Если у вас есть существующее резюме (PDF или DOCX), отправьте его — "
        "я использую его как основу. Если резюме нет, напишите «нет», и мы начнём с нуля."
    )


@router.message(OnboardingStates.upload_resume_prompt)
async def onboarding_upload_prompt(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()

    if message.document:
        await state.set_state(OnboardingStates.processing_upload)
        await message.answer(
            "Файл получен. Обработка... Это займёт несколько секунд."
        )
        # Actual parsing is handled in the document handler below
        return

    # User skipped upload
    await _start_interview(message, state)


@router.message(OnboardingStates.processing_upload, F.document)
async def onboarding_document(message: Message, state: FSMContext) -> None:
    # Placeholder: in production, download & parse with PyMuPDF / python-docx
    await message.answer(
        "Файл получен. К сожалению, автоматический разбор файла ещё в разработке. "
        "Давайте продолжим интервью вручную — это займёт 5–7 минут."
    )
    await _start_interview(message, state)


async def _start_interview(message: Message, state: FSMContext) -> None:
    """Transition to the first interview state."""
    from bot.handlers.interview import ask_summary
    await state.set_state(InterviewStates.summary)
    await ask_summary(message, state)


# ---------------------------------------------------------------------------
# Main menu callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu_continue_interview")
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
    await state.set_state(InterviewStates.summary)

    from bot.handlers.interview import ask_summary
    await ask_summary(callback.message, state)


@router.callback_query(F.data == "menu_my_resumes")
async def cb_my_resumes(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    resumes = await db.get_resumes(user_id)
    if not resumes:
        await callback.message.answer("У вас пока нет сохранённых резюме.")
        return

    lines = ["Ваши резюме:\n"]
    for i, r in enumerate(resumes, 1):
        lines.append(f"{i}. {r.get('title', 'Без названия')} — {r.get('created_at', '')[:10]}")
    lines.append("\nОтправьте /export для получения последнего резюме.")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "menu_delete_profile")
async def cb_menu_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer(
        "Вы уверены, что хотите удалить все данные профиля? "
        "Это действие нельзя отменить.",
        reply_markup=_confirm_delete_keyboard(),
    )


# ---------------------------------------------------------------------------
# /delete_profile command
# ---------------------------------------------------------------------------

@router.message(Command("delete_profile"))
async def cmd_delete_profile(message: Message, state: FSMContext) -> None:
    await message.answer(
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
            "Все данные вашего профиля удалены. "
            "Чтобы начать заново, отправьте /start."
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
# /export
# ---------------------------------------------------------------------------

@router.message(Command("export"))
async def cmd_export(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
        user_id = user["id"]
        await state.update_data(user_id=user_id)

    resumes = await db.get_resumes(user_id)
    if not resumes:
        await message.answer(
            "У вас нет сохранённых резюме. Пройдите интервью, чтобы создать первое резюме."
        )
        return

    latest = resumes[0]
    content = latest.get("content", "")
    title = latest.get("title", "Резюме")
    await message.answer(f"<b>{title}</b>\n\n{content}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Доступные команды:\n\n"
        "/start — начать или перезапустить бота\n"
        "/export — получить последнее резюме текстом\n"
        "/delete_profile — удалить все ваши данные\n"
        "/help — показать эту справку\n\n"
        "Во время интервью отвечайте на вопросы текстом. "
        "Для редактирования резюме используйте команды: «короче», «формальнее», «перепиши блок»."
    )

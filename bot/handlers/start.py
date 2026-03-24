"""
/start, /help, /export, /delete_profile handlers.
"""

from __future__ import annotations

import logging

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import os
import fitz
import docx

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


def _choose_path_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я знаю, кем хочу работать", callback_data="path_know")],
            [InlineKeyboardButton(text="Напиши 2 вопроса, чтобы помочь выбрать", callback_data="path_help")],
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
    await state.set_state(OnboardingStates.choosing_path)
    await message.answer(
        "Знакомо?\n\n"
        "— Отправил 50 резюме — ни одного ответа\n"
        "— Не знаешь, как описать свой опыт в цифрах\n"
        "— 75% резюме отсеивает робот ещё до живого HR\n\n"
        "Я помогу это исправить. За 7 минут мы вместе создадим резюме, "
        "которое проходит AI-скрининг и попадает к нужным людям — бесплатно.\n\n"
        "Вы уже знаете, на какую должность будете откликаться, или вам нужна помощь с выбором?",
        reply_markup=_choose_path_keyboard()
    )


# ---------------------------------------------------------------------------
# Onboarding: collect desired position
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "path_know")
async def onboarding_path_know(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(OnboardingStates.waiting_desired_position)
    await callback.message.answer("Отлично! На какую позицию вы ищете работу? (введите название текстом)")


@router.callback_query(F.data == "path_help")
async def onboarding_path_help(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(OnboardingStates.coaching_questions)
    await callback.message.answer(
        "Давайте подберём идеальную профессию! \n\n"
        "Ответьте на пару простых вопросов:\n"
        "1. Что вам больше всего нравится делать (например: общаться с людьми, копаться в таблицах, управлять процессами)?\n"
        "2. Что у вас получается лучше всего? (в чем ваша супер-сила по мнению коллег/друзей?)"
    )


@router.message(OnboardingStates.coaching_questions)
async def onboarding_handle_coaching(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, расскажите немного о себе текстом.")
        return
        
    # Temporary mock implementation for fast AI response
    from bot.services.ai_service import _chat
    
    prompt = f"Пользователь ищет работу. Он описал свои интересы: '{text}'. Выдели ровно 1 ключевой запрос (до 3 слов) для поиска вакансий на hh.ru по этой специальности. Выведи только этот короткий запрос."
    
    wait_msg = await message.answer("Анализирую ваши сильные стороны на рынке (hh.ru)... 🤖")
    
    try:
        from bot.services.ai_service import _chat
        from bot.services.hh_service import analyze_market_salary
        query = await _chat(
            system="Ты точный алгоритм извлечения ключевых слов.",
            user=prompt, 
            model="gpt-4o-mini", 
            temperature=0.1
        )
        query = query.strip('\'". \n')
        
        hh_data = await analyze_market_salary(query)
        await wait_msg.delete()
        
        if not hh_data.get("titles"):
            await message.answer(f"ИИ сгенерировал вот такой странный запрос для поиска: «{query}». На hh.ru по нему ничего нормального нет.\n\nНапишите ниже какую-нибудь должность вручную для старта:")
            await state.set_state(OnboardingStates.waiting_desired_position)
            return

        titles_str = "\n".join(f"• {t}" for t in hh_data["titles"][:3])
        await message.answer(f"Я проанализировал рынок hh.ru по запросу «{query}»!\nВам отлично подойдут такие роли:\n{titles_str}\n\n{hh_data['text']}\n\nНапишите ниже должность, которую вы выбираете для этого резюме:")
        await state.set_state(OnboardingStates.waiting_desired_position)
    except Exception as e:
        logger.error(f"Error in coaching: {e}")
        try:
            await wait_msg.delete()
        except:
            pass
        q_text = locals().get('query', 'неизвестно')
        await message.answer(f"Извините, произошел сбой при обращении к рынку (запрос: {q_text}, ошибка: {e}). Напишите должность вручную:")
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
                "city": city,
                "raw_data": {"contacts": data.get("contacts", "")},
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
async def onboarding_upload_prompt(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip().lower()

    if message.document:
        await state.set_state(OnboardingStates.processing_upload)
        processing_msg = await message.answer(
            "Файл получен. Анализирую резюме с помощью ИИ... Это займёт около 10-15 секунд 🤖"
        )
        await _process_uploaded_document(message, state, bot, processing_msg)
        return

    # User skipped upload
    await _start_interview(message, state)


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
        if ext == "pdf":
            doc = fitz.open(local_path)
            for page in doc:
                text += page.get_text()
            doc.close()
        elif ext == "docx":
            doc = docx.Document(local_path)
            for para in doc.paragraphs:
                text += para.text + "\n"
                
        os.remove(local_path)
        
        if len(text.strip()) < 50:
            await processing_msg.edit_text("Не удалось извлечь достаточно текста из файла (возможно, это отсканированная картинка). Давайте продолжим вручную.")
            await _start_interview(message, state)
            return
            
        from bot.services.ai_service import parse_resume_file
        parsed_data = await parse_resume_file(text)
        
        # Save parsed data to state
        data = await state.get_data()
        
        # Keep existing fields and merge new ones
        merged_data = {
            "summary": parsed_data.get("summary", ""),
            "work_experiences": parsed_data.get("work_experiences", []),
            "skills": parsed_data.get("skills", []),
            "education": "", # Simplify for now
        }
        
        # Format education from list of dicts to string
        ed_list = parsed_data.get("education", [])
        if ed_list and isinstance(ed_list, list):
            ed_strs = []
            for ed in ed_list:
                ed_strs.append(f"{ed.get('institution', '')} - {ed.get('degree', '')} ({ed.get('year_end', '')})")
            merged_data["education"] = "\n".join(ed_strs)
            
        await state.update_data(parsed_from_file=True, **merged_data)
        
        await processing_msg.edit_text("Я извлек основные данные. Сейчас я проведу быстрый аудит вашего резюме на соответствие требованиям рынка (ATS)... 🤖")
        from bot.services.ai_service import evaluate_parsed_resume
        critique = await evaluate_parsed_resume(merged_data)
        
        from bot.states import ImprovementStates
        await message.answer(
            f"Вот что у нас получилось:\n\n{critique}\n\nДавайте перейдем к исправлению слабых мест?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Да, начнем исправлять", callback_data="start_improving")],
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


@router.callback_query(F.data == "start_improving")
async def cb_start_improving(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    we = data.get("work_experiences", [])
    
    if we:
        last_job = we[0]
        await state.update_data(
            current_company=last_job.get("company", ""),
            current_role=last_job.get("position", ""),
            current_dates=f"{last_job.get('start_date', '')} - {last_job.get('end_date', '')}",
            current_responsibilities=last_job.get("description", "")
        )
        from bot.states import InterviewStates
        await state.set_state(InterviewStates.work_experience_achievements)
        await callback.message.answer(
            f"Начнем с последнего места работы: {last_job.get('company', 'компания')}.\n"
            "Здесь не хватает измеримых достижений.\n"
            "Какими результатами вы больше всего гордитесь на этой позиции? (На сколько процентов выросли продажи, сколько времени сэкономили и т.д.)"
        )
    else:
        from bot.handlers.interview import _start_skills_stage
        await callback.message.answer("Опыта работы не найдено, давайте перейдем к навыкам.")
        await _start_skills_stage(callback.message, state)


@router.callback_query(F.data == "skip_improving")
async def cb_skip_improving(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from bot.states import InterviewStates
    await state.set_state(InterviewStates.extras_input)
    await callback.message.answer(
        "Последний этап. Укажите дополнительную информацию:\n"
        "— Желаемый уровень зарплаты\n"
        "— Формат работы\n"
        "— Если ничего не нужно, напишите «нет»."
    )


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

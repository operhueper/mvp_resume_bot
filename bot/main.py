import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.handlers import start, interview, resume
import bot.database as db

# ---------------------------------------------------------------------------
# Fallback router — registered LAST (lowest priority)
# ---------------------------------------------------------------------------

fallback_router = Router()


@fallback_router.message(StateFilter(default_state))
async def fallback_no_state(message: Message, state: FSMContext) -> None:
    """
    Catch all messages when user is not in any FSM state.
    If user has saved interview_state in DB, offer to continue or restart.
    Otherwise, show welcome message.
    """
    # Ignore persistent keyboard button presses — they are handled by start.py
    text = (message.text or "").strip()
    if text in ("📄 Моё резюме", "❓ Помощь", "🔄 Начать заново", "🗑 Удалить профиль"):
        return

    user = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        telegram_username=message.from_user.username,
    )
    user_id = user["id"]
    await state.update_data(user_id=user_id)

    saved = await db.get_interview_state(user_id)
    has_state = bool(saved and any(v for v in saved.values() if v))

    if has_state:
        await message.answer(
            "С возвращением! Вы можете продолжить с того места, где остановились.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="▶️ Продолжить", callback_data="autocontinue_resume"),
                        InlineKeyboardButton(text="🆕 Начать заново", callback_data="autocontinue_restart"),
                    ]
                ]
            ),
        )
    else:
        from bot.handlers.start import _choose_path_keyboard
        await message.answer(
            "Привет! Я помогу создать профессиональное резюме для hh.ru.\n\n"
            "Нажмите /start, чтобы начать, или выберите опцию:",
            reply_markup=_choose_path_keyboard(),
        )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    # Register routers in priority order — fallback_router LAST
    dp.include_router(start.router)
    dp.include_router(interview.router)
    dp.include_router(resume.router)
    dp.include_router(fallback_router)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())

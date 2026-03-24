"""
Middleware for the resume bot.

VoiceToTextMiddleware — intercepts voice messages in any state,
transcribes them via OpenAI Whisper, and injects the transcription as
message.text so all existing text handlers work without modification.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


class VoiceToTextMiddleware(BaseMiddleware):
    """Transcribe incoming voice messages and inject transcription as message.text."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        if not event.voice:
            return await handler(event, data)

        bot = data.get("bot")
        if not bot:
            return await handler(event, data)

        try:
            tg_file = await bot.get_file(event.voice.file_id)
            buf = await bot.download_file(tg_file.file_path)
            audio_bytes = buf.read() if hasattr(buf, "read") else bytes(buf)

            from bot.services.ai_service import transcribe_voice
            transcription = await transcribe_voice(audio_bytes)

            if not transcription:
                await event.answer("Не удалось распознать голосовое сообщение. Попробуйте написать текстом.")
                return

            # Notify user what was recognised
            await event.answer(f"🎙 Распознано: «{transcription}»")

            # Inject transcription as text so all text handlers process it normally
            event.text = transcription

        except Exception as exc:
            logger.warning("Voice transcription failed: %s", exc)
            await event.answer("Не удалось распознать голосовое. Попробуйте написать текстом.")
            return

        return await handler(event, data)

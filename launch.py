import logging
import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from openai import AsyncOpenAI

# Імпортуємо роутер та функцію on_startup з нашого основного файлу
from med_bot_aiogram import router, on_startup 

# Налаштування логування
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")


# !!! ТИМЧАСОВО ДОДАЙТЕ ЦЕЙ РЯДОК ДЛЯ ПЕРЕВІРКИ (опціонально) !!!
# Якщо хочете перевірити, що скрипт бачить ключ OpenRouter
print(f"Using API key (OpenRouter): {OPENROUTER_API_KEY[:5]}...{OPENROUTER_API_KEY[-5:]}")
# !!! НЕ ЗАБУДЬТЕ ВИДАЛИТИ ЦЕЙ РЯДОК ПІСЛЯ ПЕРЕВІРКИ !!!


async def main():
    # Ініціалізація клієнта OpenAI
    # !!! ВИПРАВЛЕНО: Використовуємо AsyncOpenAI замість OpenAI !!!
    openai_client = AsyncOpenAI( # <--- Ось тут зміна!
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1/"
    )

    # Ініціалізація бота та диспетчера
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Підключаємо роутер з обробниками з іншого файлу
    dp.include_router(router)
    # Реєструємо функцію, яка виконається при старті
    dp.startup.register(on_startup)

    try:
        print(f"Starting bot @{(await bot.get_me()).username}...")
        await bot.delete_webhook(drop_pending_updates=True)
        # Передаємо openai_client в усі хендлери через start_polling
        await dp.start_polling(bot, openai_client=openai_client)

    except Exception as e:
        logging.exception("Bot polling stopped due to an error:")
    finally:
        await bot.session.close()
        print("Bot session closed.")

if __name__ == "__main__":
    asyncio.run(main())
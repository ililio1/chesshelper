import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = '8449137700:AAEaGnBplBuYKlBcoQtn-TltQJ5dZomDxNk'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Профиль👤"),       KeyboardButton(text="Анализ игр🔍")],
        [KeyboardButton(text="Помощь")]
    ],
    resize_keyboard=True
)

profile_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Привязать Lichess"), KeyboardButton(text="Привязать Chess.com")],
        [KeyboardButton(text="Назад")]
    ],
    resize_keyboard=True
)

analysis_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Синхронизировать игры"), KeyboardButton(text="Мои ошибки")],
        [KeyboardButton(text="Назад")]
    ],
    resize_keyboard=True
)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет:",reply_markup=main_kb)

@dp.message(F.text == "Профиль")
async def open_profile(message: Message):
    await message.answer("Раздел «Профиль». Выберите действие:",reply_markup=profile_kb)

@dp.message(F.text == "Анализ игр")
async def open_analysis(message: Message):
    await message.answer("Раздел «Анализ игр». Выберите действие:",reply_markup=analysis_kb)

@dp.message(F.text == "Помощь")
async def help_command(message: Message):
    text = (
        "Я помогу вам:\n"
        "• Профиль — привязать Lichess/Chess.com\n"
        "• Анализ игр — синхронизировать партии и смотреть ошибки\n"
        "• Назад — вернуться в главное меню"
    )
    await message.answer(text, reply_markup=main_kb)

@dp.message(F.text == "Привязать Lichess")
async def bind_lichess(message: Message):
    await message.answer("Введите ваш ник на Lichess:", reply_markup=profile_kb)

@dp.message(F.text == "Привязать Chess.com")
async def bind_chesscom(message: Message):
    await message.answer("Введите ваш ник на Chess.com:", reply_markup=profile_kb)

@dp.message(F.text == "Синхронизировать игры")
async def sync_games(message: Message):
    await message.answer("Загружаю ваши партии…", reply_markup=analysis_kb)
    # сюда вставьте вашу логику geteval + сохранение в БД

@dp.message(F.text == "Мои ошибки")
async def show_errors(message: Message):
    await message.answer("Генерирую задачи по вашим ошибкам…", reply_markup=analysis_kb)
    # сюда выборку из БД и отправку задач

@dp.message(F.text == "Назад")
async def go_back(message: Message):
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb)

# Ловим всё, что не подошло под фильтры
@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Не понял вас. Пожалуйста, выберите пункт меню.",
        reply_markup=main_kb
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


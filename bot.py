import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from loadgames import getlastlichessgames, getlastchesscomgames
from stockfishanalyse import findmove, geteval

from connection import (
    init_db, upsert_user, get_user_nicks,
    save_game, load_games, save_blunders, load_blunders,
    get_fen_at_move
)

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = '8449137700:AAEaGnBplBuYKlBcoQtn-TltQJ5dZomDxNk'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

init_db()

pending_binding: dict[int, str] = {}

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
    await message.answer("Добро пожаловать в ChessHelper! Здесь вы можете анализировать свои партии. Для начала работы привяжите свой аккаунт lichess и/или chess.com в разделе профиль",reply_markup=main_kb)

@dp.message(F.text == "Профиль👤")
async def open_profile(message: Message):
    lichess_nick, chesscom_nick = get_user_nicks(message.chat.id)
    await message.answer(f"Пользователь: {message.chat.id}\n Профиль lichess: {lichess_nick}\n Профиль chess.com: {chesscom_nick}",reply_markup=profile_kb)

@dp.message(F.text == "Анализ игр🔍")
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
async def on_bind_lichess(message: Message):
    pending_binding[message.chat.id] = "lichess"
    await message.answer("Введите ваш никнейм на Lichess:", reply_markup=profile_kb)

@dp.message(F.text == "Привязать Chess.com")
async def on_bind_chesscom(message: Message):
    pending_binding[message.chat.id] = "chesscom"
    await message.answer("Введите ваш никнейм на Chess.com:", reply_markup=profile_kb)

@dp.message(lambda msg: msg.chat.id in pending_binding and pending_binding[msg.chat.id] == "lichess")
async def bind_lichess_nick(message: Message):
    nick = message.text.strip()
    upsert_user(message.chat.id, lichess=nick)
    pending_binding.pop(message.chat.id, None)
    await message.answer(f"Lichess успешно привязан: {nick}", reply_markup=profile_kb)

@dp.message(lambda msg: msg.chat.id in pending_binding and pending_binding[msg.chat.id] == "chesscom")
async def bind_chesscom_nick(message: Message):
    nick = message.text.strip()
    upsert_user(message.chat.id, chesscom=nick)
    pending_binding.pop(message.chat.id, None)
    await message.answer(f"Chess.com успешно привязан: {nick}", reply_markup=profile_kb)

@dp.message(F.text == "Синхронизировать игры")
async def sync_games(message: Message):
    chat_id = message.chat.id
    lichess_nick, chesscom_nick = get_user_nicks(chat_id)
    if not (lichess_nick or chesscom_nick):
        return await message.answer("Для синхронизации игр необходимо привязать хотя бы один профиль", reply_markup=analysis_kb)
    await message.answer("Загружаю ваши партии", reply_markup=analysis_kb)
    pgn_list = list()
    if lichess_nick:
        pgn_list += getlastlichessgames(lichess_nick, max_games=30, period=7)
    if chesscom_nick:
        pgn_list += getlastchesscomgames(chesscom_nick, max_games=30, period=7)
    await message.answer("Партии загружены", reply_markup=analysis_kb)




@dp.message(F.text == "Мои ошибки")
async def show_errors(message: Message):
    await message.answer("Генерирую задачи по вашим ошибкам...", reply_markup=analysis_kb)

@dp.message(F.text == "Назад")
async def go_back(message: Message):
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb)

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


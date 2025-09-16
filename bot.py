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
    await message.answer(f"Пользователь: {message.chat.id}\nПрофиль lichess: {lichess_nick}\nПрофиль chess.com: {chesscom_nick}",reply_markup=profile_kb)

@dp.message(F.text == "Анализ игр🔍")
async def open_analysis(message: Message):
    await message.answer("Раздел «Анализ игр». Выберите действие:",reply_markup=analysis_kb)

@dp.message(F.text == "Помощь")
async def help_command(message: Message):
    text = (
        "Навигация по разделам:\n"
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
        return await message.answer("Сначала привяжите хотя бы один профиль.",reply_markup=analysis_kb)

    await message.answer("Начинаю синхронизацию…", reply_markup=analysis_kb)

    jobs: list[tuple[str, str]] = []
    if lichess_nick:
        lichess_pgns = getlastlichessgames(lichess_nick, max_games=30, period=7)
        jobs += [("lichess", p) for p in lichess_pgns]
    if chesscom_nick:
        chesscom_pgns = getlastchesscomgames(chesscom_nick, max_games=30, period=7)
        jobs += [("chesscom", p) for p in chesscom_pgns]

    new_game_count = 0
    total_blunders = 0
    loop = asyncio.get_running_loop()

    for source, pgn in jobs:
        game_id, is_new = save_game(chat_id, source, pgn)
        if not is_new:
            continue
        new_game_count += 1

        try:
            evals    = await loop.run_in_executor(None, geteval, pgn)
            bad_idxs = await loop.run_in_executor(None, findmove, evals)
        except Exception as e:
            logging.exception(f"Ошибка анализа партии {game_id}: {e}")
            continue

        blunders = []
        for idx in bad_idxs:
            fen = get_fen_at_move(pgn, idx)
            blunders.append((idx, fen))

        save_blunders(game_id, blunders)
        total_blunders += len(blunders)

    await message.answer(f"🆕 Новых партий синхронизировано: {new_game_count}\n"f"❌ Новых ошибок добавлено: {total_blunders}",reply_markup=analysis_kb)

@dp.message(F.text == "Мои ошибки")
async def show_errors(message: Message):

    chat_id = message.chat.id
    rows = load_blunders(chat_id)
    if not rows:
        return await message.answer(
            "Ошибок не найдено. Сначала выполните «Синхронизировать игры».",
            reply_markup=analysis_kb
        )

    for row in rows:
        blunder_id = row["blunder_id"]
        source     = row["source"]       # "lichess" или "chesscom"
        idx0       = row["move_index"]   # 0-based индекс хода
        move_num   = idx0 + 1            # 1-based

        fen_before = row["fen_before"]
        text = (
            f"#{blunder_id} | источник: {source}\n"
            f"Ход №{move_num}\n"
            f"<code>{fen_before}</code>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=analysis_kb)


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


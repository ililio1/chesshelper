import asyncio
import logging
import io
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import chess
import chess.pgn

from boardrender import render_board_png, render_move_gif, render_line_gif
from loadgames import getlastlichessgames, getlastchesscomgames
from stockfishanalyse import (
    findmove,
    geteval,
    stockfish_best_move,
    evaluate_move,
)
from connection import (
    init_db,
    upsert_user,
    get_user_nicks,
    get_all_users,
    save_game,
    save_blunders,
    load_unsolved_blunders,
    get_game_pgn,
    mark_blunder_solved,
    get_fen_at_move,
    get_blunder_id,
    update_blunder_assets,
)

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = "8449137700:AAEaGnBplBuYKlBcoQtn-TltQJ5dZomDxNk"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

init_db()

# Константы для параллелизма
MAX_CONCURRENT_GAMES = 3
MAX_CONCURRENT_BLUNDERS = 4

# Пул потоков для фонового рендеринга GIF
RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=4)

pending_binding: dict[int, str] = {}

# ------------------------------------
# Клавиатуры с эмодзи
# ------------------------------------
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🔍 Анализ игр")],
        [KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)

profile_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔗 Привязать Lichess"), KeyboardButton(text="🔗 Привязать Chesscom")],
        [KeyboardButton(text="🏠 Назад")],
    ],
    resize_keyboard=True,
)

analysis_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔄 Синхронизировать"), KeyboardButton(text="📋 Мои ошибки")],
        [KeyboardButton(text="🏠 Назад")],
    ],
    resize_keyboard=True,
)

class ErrorsSG(StatesGroup):
    WAIT_ANSWER = State()
    WAIT_FIX = State()

# ----------------- Async-обёртки движка -----------------
async def _engine_best_move_async(fen: str) -> Optional[chess.Move]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, stockfish_best_move, fen)

async def _engine_evaluate_move_async(fen: str, move: chess.Move, depth: int = 15) -> int:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, evaluate_move, fen, move, depth)

async def _engine_geteval_async(pgn: str) -> list[int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, geteval, pgn)

async def _engine_findmove_async(evals: list[int]) -> list[int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, findmove, evals)

# ----------------- Проверка ников -----------------
async def lichess_user_exists(nick: str) -> bool:
    if not nick:
        return False
    url = f"https://lichess.org/api/user/{nick}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return resp.status == 200

async def chesscom_user_exists(nick: str) -> bool:
    if not nick:
        return False
    url = f"https://api.chess.com/pub/player/{nick.lower()}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return resp.status == 200

# ----------------- Вспомогательные функции -----------------
def _pretty_source_name(source: str) -> str:
    return "chess.com" if source == "chesscom" else "lichess"

def _get_move_from_pgn(pgn: str, move_idx: int) -> Optional[chess.Move]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if not game:
        return None
    for i, mv in enumerate(game.mainline_moves()):
        if i == move_idx:
            return mv
    return None

def _calc_played_san_and_opponent(pgn: str, move_idx: int, user_color: str) -> tuple[str, str]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if not game:
        return "?", "?"
    white = game.headers.get("White", "")
    black = game.headers.get("Black", "")
    opponent = black if user_color == "w" else white
    board = game.board()
    for i, mv in enumerate(game.mainline_moves()):
        if i == move_idx:
            try:
                san = board.san(mv)
            except:
                san = "?"
            return san, opponent
        board.push(mv)
    return "?", opponent

async def _best_line_by_iterating(fen: str, plies: int = 6) -> list[chess.Move]:
    board = chess.Board(fen)
    line: list[chess.Move] = []
    for _ in range(plies):
        mv = await _engine_best_move_async(board.fen())
        if not mv or mv not in board.legal_moves:
            break
        line.append(mv)
        board.push(mv)
    return line

# --------------- Фоновый рендер GIF ----------------
def _render_all_gifs_sync(
    blunder_id: int,
    fen_before: str,
    bad_move: Optional[chess.Move],
    best_move: Optional[chess.Move],
    cont_line: list[chess.Move],
):
    gif_error_w = gif_error_b = gif_best_w = gif_best_b = gif_cont_w = gif_cont_b = None
    try:
        if bad_move:
            w = render_move_gif(fen_before, bad_move, flip=False)
            b = render_move_gif(fen_before, bad_move, flip=True)
            gif_error_w, gif_error_b = w.getvalue(), b.getvalue()
    except Exception:
        pass
    try:
        if best_move:
            w = render_move_gif(fen_before, best_move, flip=False)
            b = render_move_gif(fen_before, best_move, flip=True)
            gif_best_w, gif_best_b = w.getvalue(), b.getvalue()
    except Exception:
        pass
    try:
        if cont_line:
            board_after = chess.Board(fen_before)
            if bad_move:
                board_after.push(bad_move)
            fen_after = board_after.fen()
            w = render_line_gif(fen_after, cont_line, flip=False)
            b = render_line_gif(fen_after, cont_line, flip=True)
            gif_cont_w, gif_cont_b = w.getvalue(), b.getvalue()
    except Exception:
        pass
    update_blunder_assets(
        blunder_id=blunder_id,
        best_move_uci=(best_move.uci() if best_move else None),
        cont_line_uci=(" ".join(m.uci() for m in cont_line) if cont_line else None),
        gif_error_w=gif_error_w,
        gif_error_b=gif_error_b,
        gif_best_w=gif_best_w,
        gif_best_b=gif_best_b,
        gif_cont_w=gif_cont_w,
        gif_cont_b=gif_cont_b,
    )

async def _render_and_save_gifs_async(
    blunder_id: int,
    fen_before: str,
    bad_move: Optional[chess.Move],
    best_move: Optional[chess.Move],
    cont_line: list[chess.Move],
):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        RENDER_EXECUTOR,
        _render_all_gifs_sync,
        blunder_id,
        fen_before,
        bad_move,
        best_move,
        cont_line,
    )

# --------------- Параллельный анализ партий и blunders ----------------
async def process_blunder(
    game_id: int,
    idx: int,
    fen_before: str,
    pgn: str,
    sem_bl: asyncio.Semaphore
):
    async with sem_bl:
        bl_id = get_blunder_id(game_id, idx)
        bad_move = _get_move_from_pgn(pgn, idx)
        best_move = await _engine_best_move_async(fen_before)
        cont_line: list[chess.Move] = []
        try:
            board_after = chess.Board(fen_before)
            if bad_move:
                board_after.push(bad_move)
            cont_line = await _best_line_by_iterating(board_after.fen(), plies=6)
        except Exception:
            pass

        update_blunder_assets(
            blunder_id=bl_id,
            best_move_uci=(best_move.uci() if best_move else None),
            cont_line_uci=(" ".join(m.uci() for m in cont_line) if cont_line else None),
            gif_error_w=None, gif_error_b=None,
            gif_best_w=None, gif_best_b=None,
            gif_cont_w=None, gif_cont_b=None,
        )

        asyncio.create_task(
            _render_and_save_gifs_async(bl_id, fen_before, bad_move, best_move, cont_line)
        )

async def analyse_game(
    chat_id: int,
    source: str,
    pgn: str,
    sem_games: asyncio.Semaphore
) -> tuple[int, int]:
    async with sem_games:
        game_id, is_new = save_game(chat_id, source, pgn)
        if not is_new:
            return 0, 0
        try:
            evals = await _engine_geteval_async(pgn)
            bad_idxs = await _engine_findmove_async(evals)
        except Exception:
            return 1, 0

        bls = []
        for idx in bad_idxs:
            try:
                fen = get_fen_at_move(pgn, idx)
                bls.append((idx, fen))
            except:
                continue
        if not bls:
            return 1, 0

        save_blunders(game_id, bls)
        sem_bl = asyncio.Semaphore(MAX_CONCURRENT_BLUNDERS)
        await asyncio.gather(*[
            process_blunder(game_id, idx, fen, pgn, sem_bl)
            for idx, fen in bls
        ])
        return 1, len(bls)

async def sync_for_user(
    chat_id: int,
    period_days: int = 7,
    max_games: int = 30,
    silent: bool = False
) -> dict[str, int]:
    lichess_nick, chesscom_nick = get_user_nicks(chat_id)
    sem_games = asyncio.Semaphore(MAX_CONCURRENT_GAMES)
    tasks = []

    if lichess_nick:
        for pgn in getlastlichessgames(lichess_nick, max_games=max_games, period=period_days):
            tasks.append(analyse_game(chat_id, "lichess", pgn, sem_games))

    if chesscom_nick:
        for pgn in getlastchesscomgames(chesscom_nick, max_games=max_games, period=period_days):
            tasks.append(analyse_game(chat_id, "chesscom", pgn, sem_games))

    results = await asyncio.gather(*tasks)
    new_games = sum(r[0] for r in results)
    new_blunders = sum(r[1] for r in results)

    if silent and (new_games or new_blunders):
        try:
            await bot.send_message(
                chat_id,
                f"🔄 Автосинхронизация завершена:\n"
                f"• Новые партии: {new_games}\n"
                f"• Новые ошибки: {new_blunders}",
                reply_markup=analysis_kb
            )
        except:
            pass

    return {"new_games": new_games, "new_blunders": new_blunders}

async def auto_sync_loop():
    await asyncio.sleep(5)
    while True:
        users = get_all_users()
        for u in users:
            try:
                await sync_for_user(u["chat_id"], silent=True)
            except:
                pass
        await asyncio.sleep(8 * 3600)

# -------------------- Хендлеры Telegram --------------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я — ChessHelper, твой шахматный ассистент.\n\n"
        "🔗 Привяжи аккаунты Lichess/Chesscom в «Профиль».\n"
        "🔍 Анализируй партии, решай ошибки и развивай свой уровень!",
        reply_markup=main_kb,
    )

@dp.message(F.text == "👤 Профиль")
async def open_profile(message: Message):
    l, c = get_user_nicks(message.chat.id)
    await message.answer(
        f"👤 Твой профиль:\n"
        f"• ID: {message.chat.id}\n"
        f"• Lichess: {l or 'не привязан'}\n"
        f"• Chesscom: {c or 'не привязан'}",
        reply_markup=profile_kb,
    )

@dp.message(F.text == "🔍 Анализ игр")
async def open_analysis(message: Message):
    await message.answer(
        "⚙️ Раздел «Анализ игр»:\n"
        "• 🔄 Синхронизировать — загрузить новые партии\n"
        "• 📋 Мои ошибки — решать задачи",
        reply_markup=analysis_kb,
    )

@dp.message(F.text == "❓ Помощь")
async def help_command(message: Message):
    await message.answer(
        "❓ Помощь:\n"
        "• /start — перезапустить бота\n"
        "• 👤 Профиль — привязать аккаунты\n"
        "• 🔍 Анализ игр — синхронизировать и решать ошибки\n"
        "• 🏠 Назад — в главное меню",
        reply_markup=main_kb,
    )

@dp.message(F.text == "🔗 Привязать Lichess")
async def on_bind_lichess(message: Message):
    pending_binding[message.chat.id] = "lichess"
    await message.answer("✏️ Введи ник на Lichess:", reply_markup=profile_kb)

@dp.message(lambda m: m.chat.id in pending_binding and pending_binding[m.chat.id] == "lichess")
async def bind_lichess(m: Message):
    nick = (m.text or "").strip()
    if not await lichess_user_exists(nick):
        return await m.answer("❌ Lichess не найден. Попробуй снова.", reply_markup=profile_kb)
    upsert_user(m.chat.id, lichess=nick)
    pending_binding.pop(m.chat.id, None)
    await m.answer(f"✅ Lichess привязан: `{nick}`", reply_markup=profile_kb)

@dp.message(F.text == "🔗 Привязать Chesscom")
async def on_bind_chesscom(message: Message):
    pending_binding[message.chat.id] = "chesscom"
    await message.answer("✏️ Введи ник на Chesscom:", reply_markup=profile_kb)

@dp.message(lambda m: m.chat.id in pending_binding and pending_binding[m.chat.id] == "chesscom")
async def bind_chesscom(m: Message):
    nick = (m.text or "").strip()
    if not await chesscom_user_exists(nick):
        return await m.answer("❌ Chesscom не найден. Попробуй снова.", reply_markup=profile_kb)
    upsert_user(m.chat.id, chesscom=nick)
    pending_binding.pop(m.chat.id, None)
    await m.answer(f"✅ Chesscom привязан: `{nick}`", reply_markup=profile_kb)

@dp.message(F.text == "🔄 Синхронизировать")
async def sync_games(m: Message):
    await m.answer("⏱️ Запускаю синхронизацию… Это может занять несколько минут.", reply_markup=analysis_kb)
    res = await sync_for_user(m.chat.id)
    await m.answer(
        "✅ Синхронизация завершена:\n"
        f"• Новые партии: {res['new_games']}\n"
        f"• Новые ошибки: {res['new_blunders']}\n"
        "⚙️ GIF генерируются в фоне.",
        reply_markup=analysis_kb,
    )

@dp.message(F.text == "📋 Мои ошибки")
async def show_errors(message: Message, state: FSMContext):
    chat_id = message.chat.id
    l, c = get_user_nicks(chat_id)
    rows = load_unsolved_blunders(chat_id)
    if not rows:
        return await message.answer("📭 Задач нет. Синхронизируй партии.", reply_markup=analysis_kb)

    user_blunders = []
    for r in rows:
        game_id, idx, fen, src = (
            r["game_id"], r["move_index"], r["fen_before"], r["source"]
        )
        pgn = get_game_pgn(game_id)
        if not pgn:
            continue
        game = chess.pgn.read_game(io.StringIO(pgn))
        if not game:
            continue

        white = game.headers.get("White", "").lower()
        black = game.headers.get("Black", "").lower()
        nick = ((l if src == "lichess" else c) or "").lower()

        if nick not in (white, black):
            continue
        color = "w" if nick == white else "b"
        if fen.split()[1] != color:
            continue

        user_blunders.append({
            "idx": len(user_blunders),
            "blunder_id": r["blunder_id"],
            "game_id": game_id,
            "move_idx": idx,
            "fen": fen,
            "source": src,
            "user_color": color,
            "gif_error_w": r["gif_error_w"],
            "gif_error_b": r["gif_error_b"],
            "gif_best_w":  r["gif_best_w"],
            "gif_best_b":  r["gif_best_b"],
            "gif_cont_w":  r["gif_cont_w"],
            "gif_cont_b":  r["gif_cont_b"],
            "best_move_uci": r["best_move_uci"],
            "cont_line_uci": r["cont_line_uci"],
        })

    if not user_blunders:
        return await message.answer("📭 Нет задач для твоего цвета.", reply_markup=analysis_kb)

    attempts = {b["blunder_id"]: 0 for b in user_blunders}
    await state.update_data(errors=user_blunders, current_idx=0, attempts=attempts)
    await state.set_state(ErrorsSG.WAIT_ANSWER)
    await _send_error_card(bot, chat_id, user_blunders[0])

async def _send_error_card(bot: Bot, chat_id: int, err: dict):
    pgn = get_game_pgn(err["game_id"])
    flip = (err["user_color"] == "b")
    blob = err["gif_error_b"] if flip else err["gif_error_w"]

    if blob:
        file_obj = BufferedInputFile(blob, filename="move.gif")
    else:
        move = _get_move_from_pgn(pgn, err["move_idx"])
        if move:
            gif = render_move_gif(err["fen"], move, square_size=200, flip=flip)
            file_obj = BufferedInputFile(gif.getvalue(), filename=gif.name)
        else:
            png = render_board_png(err["fen"], square_size=200, flip=flip)
            file_obj = BufferedInputFile(png.getvalue(), filename=png.name)

    san, opp = _calc_played_san_and_opponent(pgn, err["move_idx"], err["user_color"])
    move_no = err["move_idx"] // 2 + 1
    src = _pretty_source_name(err["source"])

    caption = (
        f"⚠️ Ошибка против «{opp}» на {src}\n"
        f"Ход №{move_no}: вы сыграли «{san}», позиция ухудшилась.\n\n"
        "Выберите действие:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📌 Решение", callback_data=f"soln:{err['idx']}"),
            InlineKeyboardButton(text="📈 Продолжение", callback_data=f"cont:{err['idx']}"),
        ],
        [InlineKeyboardButton(text="🛠 Исправить ход", callback_data=f"try:{err['idx']}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
    ])
    await bot.send_document(chat_id, document=file_obj, caption=caption, reply_markup=kb)

# -------- Callback-хендлеры --------
@dp.callback_query(F.data == "back_to_main")
async def on_back_to_main(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await state.clear()
    try:
        await query.message.delete()
    except:
        pass
    await query.message.answer("🏠 Главное меню", reply_markup=main_kb)

@dp.callback_query(F.data.startswith("soln:"))
async def on_show_solution(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("📭 Задач нет.")
    idx = int(query.data.split(":", 1)[1])
    if idx < 0 or idx >= len(errors):
        return await query.message.answer("❗ Недоступно.")
    err = errors[idx]
    flip = err["user_color"] == "b"
    blob = err["gif_best_b"] if flip else err["gif_best_w"]
    if not blob:
        return await query.message.answer("⏳ Решение ещё не готово.")
    animation = BufferedInputFile(blob, filename="best.gif")
    mark_blunder_solved(err["blunder_id"])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Следующая задача", callback_data=f"next:{idx}")]
    ])
    await query.message.answer_animation(animation, caption="💡 Лучший ход:", reply_markup=kb)
    await state.update_data(current_idx=idx)

@dp.callback_query(F.data.startswith("cont:"))
async def on_cont(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("📭 Задач нет.")
    idx = int(query.data.split(":", 1)[1])
    if idx < 0 or idx >= len(errors):
        return await query.message.answer("❗ Недоступно.")
    err = errors[idx]
    flip = err["user_color"] == "b"
    blob = err["gif_cont_b"] if flip else err["gif_cont_w"]
    if not blob:
        return await query.message.answer("⏳ Продолжение ещё не готово.")
    animation = BufferedInputFile(blob, filename="cont.gif")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Вернуться к задаче", callback_data=f"back_to_task:{idx}")]
    ])
    await query.message.answer_animation(animation, caption="📈 Продолжение движка:", reply_markup=kb)

@dp.callback_query(F.data.startswith("back_to_task:"))
async def on_back_to_task(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.delete()
    except:
        pass

@dp.callback_query(F.data.startswith("try:"))
async def on_try(query: CallbackQuery, state: FSMContext):
    await query.answer()
    idx = int(query.data.split(":", 1)[1])
    await state.update_data(current_idx=idx)
    await state.set_state(ErrorsSG.WAIT_FIX)
    await query.message.answer("✏️ Введи ход в SAN (например Nf3). 🏠 Назад — отмена.", reply_markup=None)

@dp.callback_query(F.data.startswith("next:"))
async def on_next_task(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    prev = int(query.data.split(":", 1)[1])
    nxt = prev + 1
    if nxt >= len(errors):
        await query.message.answer("🎉 Это была последняя задача.", reply_markup=analysis_kb)
        return await state.clear()
    await state.update_data(current_idx=nxt)
    await _send_error_card(bot, query.message.chat.id, errors[nxt])

# --------- Текстовые ответы ---------
@dp.message(ErrorsSG.WAIT_ANSWER)
async def process_user_attempt(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt == "🏠 Назад":
        await state.clear()
        return await message.answer("🏠 Главное меню", reply_markup=main_kb)

    data = await state.get_data()
    errors = data.get("errors", [])
    idx = data.get("current_idx", 0)
    if not errors:
        await state.clear()
        return await message.answer("📭 Нет задач.", reply_markup=analysis_kb)

    err = errors[idx]
    solved = False
    if txt:
        try:
            b = chess.Board(err["fen"])
            mv = b.parse_san(txt)
            solved = mv.uci() == err.get("best_move_uci")
        except:
            try:
                mv = chess.Move.from_uci(txt.lower())
                solved = mv.uci() == err.get("best_move_uci")
            except:
                solved = False

    if solved:
        mark_blunder_solved(err["blunder_id"])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Следующая задача", callback_data=f"next:{idx}")]
        ])
        return await message.answer("✅ Верно!", reply_markup=kb)

    attempts = data.get("attempts", {})
    attempts[err["blunder_id"]] = attempts.get(err["blunder_id"], 0) + 1
    await state.update_data(attempts=attempts)
    return await message.answer(
        "❌ Неверно. Повтори попытку или нажми «📌 Решение» / «🛠 Исправить ход».",
    )

@dp.message(ErrorsSG.WAIT_FIX)
async def process_fix_input(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt == "🏠 Назад":
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("↩️ Возврат к задаче.", reply_markup=analysis_kb)

    data = await state.get_data()
    idx = data["current_idx"]
    err = data["errors"][idx]
    best_uci = err.get("best_move_uci")
    if not best_uci:
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("⚠️ Эталонный ход недоступен.")

    b = chess.Board(err["fen"])
    try:
        mv = b.parse_san(txt)
    except:
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("❗ Ошибка парсинга. Попробуй снова в SAN.")

    if mv.uci() == best_uci:
        mark_blunder_solved(err["blunder_id"])
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Следующая задача", callback_data=f"next:{idx}")]
        ])
        return await message.answer("✅ Отлично!", reply_markup=kb)

    try:
        user_score = await _engine_evaluate_move_async(err["fen"], mv)
        best_score = await _engine_evaluate_move_async(err["fen"], chess.Move.from_uci(best_uci))
    except:
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("⚠️ Не удалось оценить ход. Повтори попытку.")

    diff = best_score - user_score
    await state.set_state(ErrorsSG.WAIT_ANSWER)
    if diff <= 50:
        mark_blunder_solved(err["blunder_id"])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Следующая задача", callback_data=f"next:{idx}")]
        ])
        return await message.answer("✅ Достаточно хорошо!", reply_markup=kb)

    return await message.answer(f"❌ Уступаешь на {diff} ц.п. Попробуй снова или «📌 Решение».")

# --------- Глобальная «Назад» и fallback ---------
@dp.message(F.text == "🏠 Назад")
async def go_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Главное меню", reply_markup=main_kb)

@dp.message()
async def fallback(message: Message):
    await message.answer("🤔 Не понял. Используй меню ниже ⬇️", reply_markup=main_kb)

# ----------------- Запуск -----------------
async def main():
    asyncio.create_task(auto_sync_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

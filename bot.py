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
    load_games,
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

# Пул потоков для фонового рендеринга GIF (регулируй под CPU/память)
RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=4)

pending_binding: dict[int, str] = {}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Профиль👤"), KeyboardButton(text="Анализ игр🔍")],
        [KeyboardButton(text="Помощь")],
    ],
    resize_keyboard=True,
)

profile_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Привязать Lichess"), KeyboardButton(text="Привязать Chess.com")],
        [KeyboardButton(text="Назад")],
    ],
    resize_keyboard=True,
)

analysis_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Синхронизировать игры"), KeyboardButton(text="Мои ошибки")],
        [KeyboardButton(text="Назад")],
    ],
    resize_keyboard=True,
)

class ErrorsSG(StatesGroup):
    WAIT_ANSWER = State()
    WAIT_FIX = State()

# ===================== Async-обёртки движка =====================
async def _engine_best_move_async(fen: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, stockfish_best_move, fen)

async def _engine_evaluate_move_async(fen: str, move: chess.Move, depth: int = 15):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, evaluate_move, fen, move, depth)

async def _engine_geteval_async(pgn: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, geteval, pgn)

async def _engine_findmove_async(evaluations):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, findmove, evaluations)

# ===================== Проверка ников =====================
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

# ===================== Вспомогательные =====================
def _pretty_source_name(source: str) -> str:
    return "chess.com" if source == "chesscom" else "lichess"

def _get_move_from_pgn(pgn: str, move_idx: int) -> chess.Move | None:
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
    white_hdr = game.headers.get("White", "")
    black_hdr = game.headers.get("Black", "")
    opponent = black_hdr if user_color == "w" else white_hdr
    board = game.board()
    for i, move in enumerate(game.mainline_moves()):
        if i == move_idx:
            try:
                san = board.san(move)
            except Exception:
                san = "?"
            return san, opponent
        board.push(move)
    return "?", opponent

async def _best_line_by_iterating(fen: str, plies: int = 6) -> list[chess.Move]:
    board = chess.Board(fen)
    line: list[chess.Move] = []
    for _ in range(plies):
        mv = await _engine_best_move_async(board.fen())
        if mv is None or mv not in board.legal_moves:
            break
        line.append(mv)
        board.push(mv)
    return line

# ===================== Фоновый рендер и сохранение GIF =====================

def _render_all_gifs_sync(
    blunder_id: int,
    fen_before: str,
    bad_move: Optional[chess.Move],
    best_move: Optional[chess.Move],
    cont_line: list[chess.Move],
):
    """Синхронный рендер всех GIF (в пуле потоков), затем сохранение в БД."""
    gif_error_w = gif_error_b = gif_best_w = gif_best_b = gif_cont_w = gif_cont_b = None

    try:
        if bad_move:
            gif_w = render_move_gif(fen_before, bad_move, square_size=200, flip=False)
            gif_b = render_move_gif(fen_before, bad_move, square_size=200, flip=True)
            gif_error_w, gif_error_b = gif_w.getvalue(), gif_b.getvalue()
    except Exception as e:
        logging.exception(f"Render gif_error failed: blunder {blunder_id}: {e}")

    try:
        if best_move:
            gif_w = render_move_gif(fen_before, best_move, square_size=200, flip=False)
            gif_b = render_move_gif(fen_before, best_move, square_size=200, flip=True)
            gif_best_w, gif_best_b = gif_w.getvalue(), gif_b.getvalue()
    except Exception as e:
        logging.exception(f"Render gif_best failed: blunder {blunder_id}: {e}")

    try:
        if cont_line:
            board_after_bad = chess.Board(fen_before)
            if bad_move:
                board_after_bad.push(bad_move)
            fen_after_bad = board_after_bad.fen()

            gif_w = render_line_gif(fen_after_bad, cont_line, square_size=200, flip=False)
            gif_b = render_line_gif(fen_after_bad, cont_line, square_size=200, flip=True)
            gif_cont_w, gif_cont_b = gif_w.getvalue(), gif_b.getvalue()
    except Exception as e:
        logging.exception(f"Render gif_cont failed: blunder {blunder_id}: {e}")

    update_blunder_assets(
        blunder_id=blunder_id,
        best_move_uci=(best_move.uci() if best_move else None),
        cont_line_uci=(" ".join(m.uci() for m in cont_line) if cont_line else None),
        gif_error_w=gif_error_w, gif_error_b=gif_error_b,
        gif_best_w=gif_best_w, gif_best_b=gif_best_b,
        gif_cont_w=gif_cont_w, gif_cont_b=gif_cont_b,
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

# ===================== Привязка и меню =====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Добро пожаловать в ChessHelper! Здесь вы можете анализировать свои партии. "
        "Для начала работы привяжите свой аккаунт lichess и/или chess.com в разделе профиль",
        reply_markup=main_kb,
    )

@dp.message(F.text == "Профиль👤")
async def open_profile(message: Message):
    lichess_nick, chesscom_nick = get_user_nicks(message.chat.id)
    await message.answer(
        f"Пользователь: {message.chat.id}\n"
        f"Профиль lichess: {lichess_nick or 'не привязан'}\n"
        f"Профиль chess.com: {chesscom_nick or 'не привязан'}",
        reply_markup=profile_kb,
    )

@dp.message(F.text == "Анализ игр🔍")
async def open_analysis(message: Message):
    await message.answer("Раздел «Анализ игр». Выберите действие:", reply_markup=analysis_kb)

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
    nick = (message.text or "").strip()
    if not await lichess_user_exists(nick):
        return await message.answer("Пользователь Lichess не найден. Проверьте ник и попробуйте снова.", reply_markup=profile_kb)
    upsert_user(message.chat.id, lichess=nick)
    pending_binding.pop(message.chat.id, None)
    await message.answer(f"Lichess успешно привязан: {nick}", reply_markup=profile_kb)

@dp.message(lambda msg: msg.chat.id in pending_binding and pending_binding[msg.chat.id] == "chesscom")
async def bind_chesscom_nick(message: Message):
    nick = (message.text or "").strip()
    if not await chesscom_user_exists(nick):
        return await message.answer("Пользователь Chess.com не найден. Проверьте ник и попробуйте снова.", reply_markup=profile_kb)
    upsert_user(message.chat.id, chesscom=nick)
    pending_binding.pop(message.chat.id, None)
    await message.answer(f"Chess.com успешно привязан: {nick}", reply_markup=profile_kb)

# ===================== Синхронизация (ручная) =====================

@dp.message(F.text == "Синхронизировать игры")
async def sync_games(message: Message):
    result = await sync_for_user(message.chat.id)
    await message.answer(
        f"🆕 Новых партий синхронизировано: {result['new_games']}\n"
        f"❌ Новых ошибок добавлено: {result['new_blunders']}\n"
        f"⚙️ Рендеринг GIF выполняется в фоне — карточки появятся готовыми.",
        reply_markup=analysis_kb,
    )

# ===================== Синхронизация (ядро и фон) =====================

async def sync_for_user(chat_id: int, period_days: int = 7, max_games: int = 30, silent: bool = False):
    lichess_nick, chesscom_nick = get_user_nicks(chat_id)
    jobs: list[tuple[str, str]] = []

    if lichess_nick:
        lichess_pgns = getlastlichessgames(lichess_nick, max_games=max_games, period=period_days)
        jobs += [("lichess", p) for p in lichess_pgns]
    if chesscom_nick:
        chesscom_pgns = getlastchesscomgames(chesscom_nick, max_games=max_games, period=period_days)
        jobs += [("chesscom", p) for p in chesscom_pgns]

    new_game_count = 0
    new_blunders = 0
    background_tasks: list[asyncio.Task] = []

    for source, pgn in jobs:
        game_id, is_new = save_game(chat_id, source, pgn)
        if not is_new:
            continue
        new_game_count += 1

        try:
            evals = await _engine_geteval_async(pgn)
            bad_idxs = await _engine_findmove_async(evals)
        except Exception as e:
            logging.exception(f"Ошибка анализа партии {game_id}: {e}")
            continue

        blunders_to_save = []
        for idx in bad_idxs:
            try:
                fen_before = get_fen_at_move(pgn, idx)
            except Exception:
                continue
            blunders_to_save.append((idx, fen_before))

        if not blunders_to_save:
            continue

        save_blunders(game_id, blunders_to_save)
        new_blunders += len(blunders_to_save)

        # Подготовка данных и фоновая отрисовка для каждого blunder
        game = chess.pgn.read_game(io.StringIO(pgn))
        main_moves = list(game.mainline_moves()) if game else []

        for (idx, fen_before) in blunders_to_save:
            bl_id = get_blunder_id(game_id, idx)
            if bl_id is None:
                continue

            # Ошибочный ход (из PGN)
            bad_move = main_moves[idx] if 0 <= idx < len(main_moves) else None

            # Лучший ход
            try:
                best_move = await _engine_best_move_async(fen_before)
            except Exception:
                best_move = None

            # Продолжение после ошибочного хода
            cont_line: list[chess.Move] = []
            try:
                board_after_bad = chess.Board(fen_before)
                if bad_move:
                    board_after_bad.push(bad_move)
                cont_line = await _best_line_by_iterating(board_after_bad.fen(), plies=6)
            except Exception:
                cont_line = []

            # Сразу сохраняем метаданные (best_uci, cont_uci), чтобы кнопки работали без движка
            update_blunder_assets(
                blunder_id=bl_id,
                best_move_uci=(best_move.uci() if best_move else None),
                cont_line_uci=(" ".join(m.uci() for m in cont_line) if cont_line else None),
                gif_error_w=None, gif_error_b=None,
                gif_best_w=None, gif_best_b=None,
                gif_cont_w=None, gif_cont_b=None,
            )

            # Запускаем фоновый рендер (несколько потоков параллельно)
            background_tasks.append(asyncio.create_task(
                _render_and_save_gifs_async(bl_id, fen_before, bad_move, best_move, cont_line)
            ))

    # Не ждём завершения фоновых задач для UX — они продолжают работать.
    if silent and (new_game_count or new_blunders):
        try:
            await bot.send_message(
                chat_id,
                f"Автосинхронизация завершена.\nНовые партии: {new_game_count}\nНовых ошибок: {new_blunders}\n"
                f"⚙️ Рендеринг GIF выполняется в фоне.",
                reply_markup=analysis_kb
            )
        except Exception:
            pass

    return {"new_games": new_game_count, "new_blunders": new_blunders}

async def auto_sync_loop():
    await asyncio.sleep(5)  # задержка после старта
    while True:
        users = get_all_users()
        for u in users:
            try:
                await sync_for_user(u["chat_id"], silent=True)
            except Exception as e:
                logging.exception(f"Auto-sync failed for chat {u['chat_id']}: {e}")
        await asyncio.sleep(8 * 3600)  # каждые 8 часов

# ===================== Раздел «Мои ошибки» (использование готовых GIF) =====================

@dp.message(F.text == "Мои ошибки")
async def show_errors(message: Message, state: FSMContext):
    chat_id = message.chat.id
    lichess_nick, chesscom_nick = get_user_nicks(chat_id)
    rows = load_unsolved_blunders(chat_id)
    if not rows:
        return await message.answer(
            "Нерешённых задач не найдено. Синхронизируйте новые партии или продолжайте позже.",
            reply_markup=analysis_kb,
        )

    user_blunders: list[dict] = []
    for r in rows:
        game_id, move_idx, fen_before, source = (
            r["game_id"],
            r["move_index"],
            r["fen_before"],
            r["source"],
        )
        pgn = get_game_pgn(game_id)
        if not pgn:
            continue
        game = chess.pgn.read_game(io.StringIO(pgn))
        if not game:
            continue

        white_hdr = game.headers.get("White", "").lower()
        black_hdr = game.headers.get("Black", "").lower()
        user_nick = (
            (lichess_nick if source == "lichess" else chesscom_nick) or ""
        ).lower()

        if user_nick == white_hdr:
            user_color = "w"
        elif user_nick == black_hdr:
            user_color = "b"
        else:
            continue

        if fen_before.split()[1] != user_color:
            continue

        user_blunders.append(
            {
                "idx": len(user_blunders),
                "blunder_id": r["blunder_id"],
                "game_id": game_id,
                "move_idx": move_idx,
                "fen": fen_before,
                "source": source,
                "user_color": user_color,

                # Загруженные активы
                "gif_error_w": r["gif_error_w"],
                "gif_error_b": r["gif_error_b"],
                "gif_best_w":  r["gif_best_w"],
                "gif_best_b":  r["gif_best_b"],
                "gif_cont_w":  r["gif_cont_w"],
                "gif_cont_b":  r["gif_cont_b"],

                "best_move_uci": r["best_move_uci"],
                "cont_line_uci": r["cont_line_uci"],
            }
        )

    if not user_blunders:
        return await message.answer(
            "Промахов за вашу сторону среди нерешённых задач не найдено.",
            reply_markup=analysis_kb,
        )

    attempts = {b["blunder_id"]: 0 for b in user_blunders}
    await state.update_data(errors=user_blunders, current_idx=0, attempts=attempts)
    await state.set_state(ErrorsSG.WAIT_ANSWER)
    await _send_error_card(message.bot, chat_id, user_blunders[0])

async def _send_error_card(bot: Bot, chat_id: int, err: dict):
    pgn  = get_game_pgn(err["game_id"])
    flip = (err["user_color"] == "b")
    gif_blob = err["gif_error_b"] if flip else err["gif_error_w"]

    if gif_blob:
        file_obj = BufferedInputFile(gif_blob, filename="move.gif")
    else:
        # Фоллбек на лету (если фон ещё не дорисовал GIF)
        move = _get_move_from_pgn(pgn, err["move_idx"])
        if move:
            gif = render_move_gif(err["fen"], move, square_size=200, flip=flip)
            file_obj = BufferedInputFile(gif.getvalue(), filename=gif.name)
        else:
            png = render_board_png(err["fen"], square_size=200, flip=flip)
            file_obj = BufferedInputFile(png.getvalue(), filename=png.name)

    played_san, opponent = _calc_played_san_and_opponent(pgn, err["move_idx"], err["user_color"])
    move_no = err["move_idx"] // 2 + 1
    src     = _pretty_source_name(err["source"])

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📌 Показать решение",    callback_data=f"soln:{err['idx']}"),
                InlineKeyboardButton(text="📈 Показать продолжение", callback_data=f"cont:{err['idx']}"),
            ],
            [InlineKeyboardButton(text="🛠 Исправить ошибку", callback_data=f"try:{err['idx']}")]
        ]
    )

    caption = (
        f"Игра против «{opponent}» на {src}\n"
        f"Ход №{move_no}. В партии вы сыграли «{played_san}», что ухудшило позицию.\n"
        "Выберите действие:"
    )

    await bot.send_document(chat_id=chat_id, document=file_obj, caption=caption, reply_markup=kb)

# ===================== Действия по задачам (callback-хендлеры без привязки к FSM) =====================

@dp.callback_query(F.data.startswith("soln:"))
async def on_show_solution(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("Список задач пуст. Откройте «Мои ошибки».")
    idx = int(query.data.split(":")[1])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    err = errors[idx]
    flip = err["user_color"] == "b"

    gif_blob = err["gif_best_b"] if flip else err["gif_best_w"]
    if not gif_blob:
        return await query.message.answer("Для этой позиции пока нет готового решения. Подождите немного и попробуйте снова.")
    animation = BufferedInputFile(gif_blob, filename="best.gif")

    mark_blunder_solved(err["blunder_id"])
    next_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")
    ]])
    await query.message.answer_animation(animation, caption="💡 Лучший ход:", reply_markup=next_kb)
    await state.update_data(current_idx=idx)

@dp.callback_query(F.data.startswith("cont:"))
async def on_cont(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("Список задач пуст. Откройте «Мои ошибки».")
    idx = int(query.data.split(":")[1])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")
    err = errors[idx]
    flip = err["user_color"] == "b"

    gif_blob = err["gif_cont_b"] if flip else err["gif_cont_w"]
    if not gif_blob:
        return await query.message.answer("Продолжение пока не готово. Подождите немного и попробуйте снова.")

    animation = BufferedInputFile(gif_blob, filename="cont.gif")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")
    ]])
    await query.message.answer_animation(animation, caption="📈 Продолжение движка:", reply_markup=kb)

@dp.callback_query(F.data.startswith("try:"))
async def on_try(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("Список задач пуст. Откройте «Мои ошибки».")
    idx = int(query.data.split(":")[1])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    await state.update_data(current_idx=idx)
    await state.set_state(ErrorsSG.WAIT_FIX)
    await query.message.answer(
        "Введите ваш ход в формате SAN (например Nf3). Для отмены нажмите «Назад».",
        reply_markup=None,
    )

@dp.callback_query(F.data.startswith("next:"))
async def on_next_task(query: CallbackQuery, state: FSMContext):
    await query.answer()
    data = await state.get_data()
    errors = data.get("errors", [])
    if not errors:
        return await query.message.answer("Список задач пуст. Откройте «Мои ошибки».")
    prev_idx = int(query.data.split(":")[1])
    next_idx = prev_idx + 1

    if next_idx >= len(errors):
        await query.message.answer("Это была последняя задача.", reply_markup=analysis_kb)
        return await state.clear()

    await state.update_data(current_idx=next_idx)
    await _send_error_card(query.bot, query.message.chat.id, errors[next_idx])

# ===================== Ввод ответов и фиксация =====================

@dp.message(ErrorsSG.WAIT_ANSWER)
async def process_user_attempt(message: Message, state: FSMContext):
    if (message.text or "").strip() == "Назад":
        await state.clear()
        await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb)
        return

    data = await state.get_data()
    errors = data.get("errors", [])
    current_idx = data.get("current_idx", 0)
    attempts = data.get("attempts", {})
    if not errors:
        await state.clear()
        return await message.answer("Задач нет. Вернитесь в меню.", reply_markup=analysis_kb)

    err = errors[current_idx]
    user_text = (message.text or "").strip()
    solved = False
    if user_text:
        try:
            b2 = chess.Board(err["fen"])
            mv = b2.parse_san(user_text)
            best_uci = err.get("best_move_uci")
            solved = (best_uci is not None) and (mv == chess.Move.from_uci(best_uci))
        except Exception:
            try:
                mv = chess.Move.from_uci(user_text.lower())
                best_uci = err.get("best_move_uci")
                solved = (best_uci is not None) and (mv.uci() == best_uci)
            except Exception:
                solved = False

    if solved:
        mark_blunder_solved(err["blunder_id"])
        verdict = "✅ Верно!"
        next_kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{current_idx}")
            ]]
        )
        await message.answer(verdict, reply_markup=next_kb)
        return

    attempts[err["blunder_id"]] = attempts.get(err["blunder_id"], 0) + 1
    await state.update_data(attempts=attempts)
    hint = "❌ Неверно. Попробуйте ещё раз или нажмите «📌 Показать решение» / «🛠 Исправить ошибку»."
    await message.answer(hint)

@dp.message(ErrorsSG.WAIT_FIX)
async def process_fix_input(message: Message, state: FSMContext):
    if (message.text or "").strip() == "Назад":
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        await message.answer("Возврат к списку задач.", reply_markup=analysis_kb)
        return

    data = await state.get_data()
    idx = data["current_idx"]
    errors = data["errors"]
    err = errors[idx]
    fen = err["fen"]
    best_uci = err.get("best_move_uci")

    if not best_uci:
        await state.set_state(ErrorsSG.WAIT_ANSWER)  # гарантируем возврат, чтобы кнопки работали
        return await message.answer("Для этой позиции пока нет эталонного хода. Попробуйте позже.")

    board = chess.Board(fen)
    user_input = (message.text or "").strip()

    try:
        user_move = board.parse_san(user_input)
    except Exception:
        # Всегда возвращаемся в WAIT_ANSWER, чтобы inline-кнопки не отваливались
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("Не удалось распознать ход. Попробуйте ещё раз в SAN (например, Nf3, exd5, O-O).")

    # Если пользователь попал точно в лучший ход — принять сразу
    if user_move == chess.Move.from_uci(best_uci):
        mark_blunder_solved(err["blunder_id"])
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")
        ]])
        return await message.answer("✅ Отлично, ход достаточно хорош!", reply_markup=kb)

    try:
        user_score = await _engine_evaluate_move_async(fen, user_move)
        best_score = await _engine_evaluate_move_async(fen, chess.Move.from_uci(best_uci))
    except Exception as e:
        logging.exception(f"Engine error: {e}")
        await state.set_state(ErrorsSG.WAIT_ANSWER)
        return await message.answer("Не удалось оценить ход. Попробуйте ещё раз.")

    diff = best_score - user_score
    threshold = 50
    await state.set_state(ErrorsSG.WAIT_ANSWER)  # ключевой фикс: всегда возвращаемся в ожидание ответа
    if diff <= threshold:
        mark_blunder_solved(err["blunder_id"])
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")
        ]])
        return await message.answer("✅ Отлично, ход достаточно хорош!", reply_markup=kb)
    else:
        return await message.answer(f"❌ Ваш ход уступает лучшему на {diff} ц.п. Попробуйте другой вариант или нажмите «📌 Показать решение».")

# ===================== Глобальная кнопка «Назад» =====================

@dp.message(F.text == "Назад")
async def go_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb)
    return

# ===================== Fallback =====================

@dp.message()
async def fallback(message: Message):
    await message.answer("Не понял вас. Пожалуйста, выберите пункт меню.", reply_markup=main_kb)

# ===================== Entry =====================

async def main():
    # запускаем планировщик авто‑синхронизации
    asyncio.create_task(auto_sync_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import io

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
from stockfishanalyse import findmove, geteval, stockfish_best_move

from connection import (
    init_db, upsert_user, get_user_nicks,
    save_game, load_games, save_blunders,
    load_unsolved_blunders, get_game_pgn,
    mark_blunder_solved, get_fen_at_move
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


class ErrorsSG(StatesGroup):
    WAIT_ANSWER = State()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Добро пожаловать в ChessHelper! Здесь вы можете анализировать свои партии. "
        "Для начала работы привяжите свой аккаунт lichess и/или chess.com в разделе профиль",
        reply_markup=main_kb
    )


@dp.message(F.text == "Профиль👤")
async def open_profile(message: Message):
    lichess_nick, chesscom_nick = get_user_nicks(message.chat.id)
    await message.answer(
        f"Пользователь: {message.chat.id}\n"
        f"Профиль lichess: {lichess_nick or 'не привязан'}\n"
        f"Профиль chess.com: {chesscom_nick or 'не привязан'}",
        reply_markup=profile_kb
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

    await message.answer(f"🆕 Новых партий синхронизировано: {new_game_count}\n"
                         f"❌ Новых ошибок добавлено: {total_blunders}",reply_markup=analysis_kb)


def _calc_played_san_and_opponent(pgn: str, move_idx: int, user_color: str) -> tuple[str, str]:
    """
    Возвращает (played_san, opponent_name)
    played_san — SAN сыгранного в партии хода на индексе move_idx.
    opponent_name — ник соперника из PGN.
    """
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


def _pretty_source_name(source: str) -> str:
    return "chess.com" if source == "chesscom" else "lichess"


def _get_move_from_pgn(pgn: str, move_idx: int) -> chess.Move | None:
    """
    По PGN и индексу полухода возвращает объект chess.Move (ошибочный ход пользователя).
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if not game:
        return None
    for i, mv in enumerate(game.mainline_moves()):
        if i == move_idx:
            return mv
    return None


def _best_line_by_iterating(fen: str, plies: int = 4) -> list[chess.Move]:
    """
    Строит продолжение на plies полуходов, последовательно вызывая stockfish_best_move.
    Не требует изменения stockfishanalyse.
    """
    board = chess.Board(fen)
    line: list[chess.Move] = []
    for _ in range(plies):
        mv = stockfish_best_move(board.fen())
        if mv is None or mv not in board.legal_moves:
            break
        line.append(mv)
        board.push(mv)
    return line


async def _send_error_card(bot: Bot, chat_id: int, err: dict):
    """
    Отправляет задачу:
    - GIF анимация ошибочного хода (до → после),
    - кнопки: Показать решение, Показать продолжение, Исправить ошибку.
    """
    pgn  = get_game_pgn(err["game_id"])
    move = _get_move_from_pgn(pgn, err["move_idx"])
    flip = (err["user_color"] == "b")

    # Анимация ошибки: если не удалось получить ход — fallback на статичный PNG
    if move:
        gif = render_move_gif(err["fen"], move, square_size=200, flip=flip)
        media = BufferedInputFile(gif.getvalue(), filename=gif.name)
        send_animation = True
    else:
        png = render_board_png(err["fen"], square_size=200, flip=flip)
        media = BufferedInputFile(png.getvalue(), filename=png.name)
        send_animation = False

    played_san, opponent = _calc_played_san_and_opponent(pgn, err["move_idx"], err["user_color"])
    move_no = err["move_idx"] // 2 + 1
    src     = _pretty_source_name(err["source"])

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📌 Показать решение",    callback_data=f"soln:{err['idx']}"),
                InlineKeyboardButton(text="📈 Показать продолжение", callback_data=f"cont:{err['idx']}"),
            ],
            [
                InlineKeyboardButton(text="🛠 Исправить ошибку", callback_data=f"try:{err['idx']}")
            ]
        ]
    )

    caption = (
        f"Игра против «{opponent}» на {src}\n"
        f"Ход №{move_no}. В партии вы сыграли «{played_san}», что ухудшило позицию.\n"
        "Выберите действие:"
    )

    if send_animation:
        await bot.send_animation(chat_id=chat_id, animation=media, caption=caption, reply_markup=kb)
    else:
        await bot.send_photo(chat_id=chat_id, photo=media, caption=caption, reply_markup=kb)


@dp.message(F.text == "Мои ошибки")
async def show_errors(message: Message, state: FSMContext):
    chat_id = message.chat.id
    lichess_nick, chesscom_nick = get_user_nicks(chat_id)
    rows = load_unsolved_blunders(chat_id)
    if not rows:
        return await message.answer(
            "Нерешённых задач не найдено. Синхронизируйте новые партии или продолжайте позже.",
            reply_markup=analysis_kb
        )

    user_blunders: list[dict] = []
    for r in rows:
        game_id, move_idx, fen_before, source = (
            r["game_id"], r["move_index"], r["fen_before"], r["source"]
        )

        pgn = get_game_pgn(game_id)
        if not pgn:
            continue
        game = chess.pgn.read_game(io.StringIO(pgn))
        if not game:
            continue

        white_hdr = game.headers.get("White", "").lower()
        black_hdr = game.headers.get("Black", "").lower()
        user_nick = (lichess_nick if source == "lichess" else chesscom_nick or "").lower()

        if user_nick == white_hdr:
            user_color = "w"
        elif user_nick == black_hdr:
            user_color = "b"
        else:
            continue

        # убеждаемся, что ходит пользователь (FEN before move)
        if fen_before.split()[1] != user_color:
            continue

        user_blunders.append({
            "idx": len(user_blunders),  # индекс для текущей сессии
            "blunder_id": r["blunder_id"],
            "game_id": game_id,
            "move_idx": move_idx,
            "fen": fen_before,
            "source": source,
            "user_color": user_color
        })

    if not user_blunders:
        return await message.answer(
            "Промахов за вашу сторону среди нерешённых задач не найдено.",
            reply_markup=analysis_kb
        )

    await state.update_data(errors=user_blunders, current_idx=0)
    await state.set_state(ErrorsSG.WAIT_ANSWER)
    await _send_error_card(message.bot, chat_id, user_blunders[0])


@dp.message(ErrorsSG.WAIT_ANSWER)
async def process_user_attempt(message: Message, state: FSMContext):
    """
    Остаётся в качестве резервного пути ввода.
    Основной UX теперь через «🛠 Исправить ошибку» (кнопки SAN).
    """
    data = await state.get_data()
    errors = data.get("errors", [])
    current_idx = data.get("current_idx", 0)
    if not errors:
        await state.clear()
        return await message.answer("Задач нет. Вернитесь в меню.", reply_markup=analysis_kb)

    err = errors[current_idx]
    board = chess.Board(err["fen"])
    try:
        best = stockfish_best_move(err["fen"])
        best_san = board.san(best)
    except Exception as e:
        logging.exception(f"Engine error: {e}")
        return await message.answer("Не удалось вычислить лучший ход для этой позиции.", reply_markup=analysis_kb)

    user_text = (message.text or "").strip()
    solved = False
    if user_text:
        try:
            b2 = chess.Board(err["fen"])
            mv = b2.parse_san(user_text)
            solved = (mv == best)
        except Exception:
            try:
                mv = chess.Move.from_uci(user_text.lower())
                solved = (mv == best)
            except Exception:
                solved = False

    if solved:
        mark_blunder_solved(err["blunder_id"])
        verdict = "✅ Верно!"
    else:
        verdict = (
            "❌ Неверно. Правильный ход: "
            f"{best_san}\n\nСовет: используйте кнопку «🛠 Исправить ошибку» для выбора хода из списка."
        )

    next_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к следующей задаче",
                    callback_data=f"next:{current_idx}"
                )
            ]
        ]
    )
    await message.answer(verdict, reply_markup=next_kb)


@dp.callback_query(F.data.startswith("soln:"), ErrorsSG.WAIT_ANSWER)
async def on_show_solution(query: CallbackQuery, state: FSMContext):
    await query.answer()
    _, idx_str = query.data.split(":")
    idx = int(idx_str)

    data = await state.get_data()
    errors = data.get("errors", [])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    err = errors[idx]
    flip = (err["user_color"] == "b")

    try:
        best = stockfish_best_move(err["fen"])
        gif = render_move_gif(err["fen"], best, square_size=200, flip=flip)
    except Exception as e:
        logging.exception(f"Engine error: {e}")
        return await query.message.answer("Не удалось вычислить лучший ход для этой позиции.")

    mark_blunder_solved(err["blunder_id"])

    animation = BufferedInputFile(gif.getvalue(), filename=gif.name)
    next_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")]
        ]
    )
    await query.message.answer_animation(animation, caption="💡 Лучший ход:", reply_markup=next_kb)
    await state.update_data(current_idx=idx)


@dp.callback_query(F.data.startswith("cont:"), ErrorsSG.WAIT_ANSWER)
async def on_cont(query: CallbackQuery, state: FSMContext):
    await query.answer()
    _, idx_str = query.data.split(":")
    idx = int(idx_str)

    data = await state.get_data()
    errors = data.get("errors", [])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    err = errors[idx]
    flip = (err["user_color"] == "b")

    # 2–3 хода вперёд (4–6 полуходов). По умолчанию 4 полухода.
    line = _best_line_by_iterating(err["fen"], plies=4)
    if not line:
        return await query.message.answer("Не удалось построить продолжение для этой позиции.")

    gif = render_line_gif(err["fen"], line, square_size=200, flip=flip)
    animation = BufferedInputFile(gif.getvalue(), filename=gif.name)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")]
        ]
    )
    await query.message.answer_animation(animation, caption="📈 Продолжение движка:", reply_markup=kb)


@dp.callback_query(F.data.startswith("try:"), ErrorsSG.WAIT_ANSWER)
async def on_try(query: CallbackQuery, state: FSMContext):
    """
    Упрощённый ввод: предлагаем список допустимых ходов SAN (ограниченно).
    Выбираем первые N легальных ходов в SAN — без ручного ввода.
    """
    await query.answer()
    _, idx_str = query.data.split(":")
    idx = int(idx_str)

    data = await state.get_data()
    errors = data.get("errors", [])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    err = errors[idx]
    board = chess.Board(err["fen"])

    # Собираем до 10 легальных ходов в SAN (сортируем для стабильности)
    san_moves: list[str] = []
    for mv in board.legal_moves:
        try:
            san_moves.append(board.san(mv))
        except Exception:
            continue
        if len(san_moves) >= 10:
            break
    san_moves = sorted(set(san_moves))[:10]

    if not san_moves:
        return await query.message.answer("Нет доступных ходов для выбора.")

    await state.update_data(current_idx=idx, try_moves=san_moves)

    # Кнопки в 2 колонки
    rows = []
    for i in range(0, len(san_moves), 2):
        row = [
            InlineKeyboardButton(text=san_moves[i], callback_data=f"mv:{idx}:{san_moves[i]}")
        ]
        if i + 1 < len(san_moves):
            row.append(InlineKeyboardButton(text=san_moves[i+1], callback_data=f"mv:{idx}:{san_moves[i+1]}"))
        rows.append(row)

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await query.message.answer("Выберите ваш ход:", reply_markup=kb)


@dp.callback_query(F.data.startswith("mv:"), ErrorsSG.WAIT_ANSWER)
async def on_move_selected(query: CallbackQuery, state: FSMContext):
    await query.answer()
    _, idx_str, san = query.data.split(":", 2)
    idx = int(idx_str)

    data = await state.get_data()
    errors = data.get("errors", [])
    if not (0 <= idx < len(errors)):
        return await query.message.answer("Эта задача уже недоступна.")

    err = errors[idx]
    board = chess.Board(err["fen"])
    best = stockfish_best_move(err["fen"])
    best_san = board.san(best)

    if san == best_san:
        mark_blunder_solved(err["blunder_id"])
        verdict = "✅ Верно!"
    else:
        verdict = f"❌ Неверно. Правильный ход: {best_san}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к следующей задаче", callback_data=f"next:{idx}")]
        ]
    )
    await query.message.answer(verdict, reply_markup=kb)


@dp.callback_query(F.data.startswith("next:"), ErrorsSG.WAIT_ANSWER)
async def on_next_task(query: CallbackQuery, state: FSMContext):
    await query.answer()
    _, idx_str = query.data.split(":")
    prev_idx = int(idx_str)

    data = await state.get_data()
    errors = data.get("errors", [])
    next_idx = prev_idx + 1

    if next_idx >= len(errors):
        await query.message.answer("Это была последняя задача.", reply_markup=analysis_kb)
        return await state.clear()

    await state.update_data(current_idx=next_idx)
    await _send_error_card(query.bot, query.message.chat.id, errors[next_idx])


@dp.message(F.text == "Назад")
async def go_back(message: Message, state: FSMContext):
    await state.clear()
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

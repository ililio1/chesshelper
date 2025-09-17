import sqlite3
import chess
import chess.pgn
import io

DB_PATH = "bot.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    with conn:
        # Базовая схема
        with open("schema.sql", encoding="utf-8") as f:
            conn.executescript(f.read())
        # Миграция: добавляем флаг solved в blunders, если ещё нет
        cols = conn.execute("PRAGMA table_info(blunders)").fetchall()
        col_names = {c[1] for c in cols}
        if "solved" not in col_names:
            conn.execute("ALTER TABLE blunders ADD COLUMN solved INTEGER NOT NULL DEFAULT 0")
    conn.close()

def upsert_user(chat_id: int, lichess: str = None, chesscom: str = None):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO users(chat_id, lichess_nick, chesscom_nick)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              lichess_nick  = COALESCE(excluded.lichess_nick, users.lichess_nick),
              chesscom_nick = COALESCE(excluded.chesscom_nick, users.chesscom_nick),
              updated_at    = CURRENT_TIMESTAMP
        """, (chat_id, lichess, chesscom))
    conn.close()

def get_user_nicks(chat_id: int) -> tuple[str, str]:
    conn = get_connection()
    row = conn.execute(
        "SELECT lichess_nick, chesscom_nick FROM users WHERE chat_id = ?",
        (chat_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None, None
    return row["lichess_nick"], row["chesscom_nick"]

def save_game(chat_id: int, source: str, pgn: str) -> tuple[int, bool]:
    conn = get_connection()
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO games(chat_id, source, pgn) VALUES(?,?,?)",
            (chat_id, source, pgn)
        )
        if cur.rowcount:  # ряд вставлен
            return cur.lastrowid, True

        row = conn.execute(
            "SELECT game_id FROM games WHERE chat_id = ? AND pgn = ?",
            (chat_id, pgn)
        ).fetchone()
        return row["game_id"], False

def load_games(chat_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT game_id, source, pgn FROM games "
        "WHERE chat_id = ? "
        "ORDER BY synced_at DESC "
        "LIMIT 50",
        (chat_id,)
    ).fetchall()
    conn.close()
    return rows

def save_blunders(game_id: int, blunder_list: list[tuple[int, str]]):
    conn = get_connection()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO blunders(game_id, move_index, fen_before) VALUES(?,?,?)",
            [(game_id, idx, fen) for idx, fen in blunder_list]
        )

def load_blunders(chat_id: int):
    """Старый интерфейс (без фильтрации по solved), оставляю на месте, если где-то ещё используется."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT b.blunder_id, b.game_id, b.move_index, b.fen_before, b.solved, g.source "
        "FROM blunders b "
        "JOIN games g ON g.game_id = b.game_id "
        "WHERE g.chat_id = ? "
        "ORDER BY b.detected_at DESC ",
        (chat_id,)
    ).fetchall()
    conn.close()
    return rows

def load_unsolved_blunders(chat_id: int):
    """Новые задачи — только нерешённые записи."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT b.blunder_id, b.game_id, b.move_index, b.fen_before, b.solved, g.source "
        "FROM blunders b "
        "JOIN games g ON g.game_id = b.game_id "
        "WHERE g.chat_id = ? AND b.solved = 0 "
        "ORDER BY b.detected_at DESC ",
        (chat_id,)
    ).fetchall()
    conn.close()
    return rows

def mark_blunder_solved(blunder_id: int):
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE blunders SET solved = 1 WHERE blunder_id = ?",
            (blunder_id,)
        )

def get_fen_at_move(pgn: str, move_idx: int) -> str:
    pgn_io = io.StringIO(pgn)
    game = chess.pgn.read_game(pgn_io)
    if game is None:
        raise ValueError("Невалидный PGN")
    board = game.board()
    for i, move in enumerate(game.mainline_moves()):
        if i == move_idx:
            return board.fen()
        board.push(move)
    return board.fen()

def get_game_pgn(game_id: int) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT pgn FROM games WHERE game_id = ?",
        (game_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return row["pgn"]

import sqlite3
import json
import loadgames
import stockfishanalyse


def get_connection(path="bot.db"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    with conn:
        conn.executescript(open("schema.sql", encoding="utf-8").read())

def save_game(conn, chat_id: int, pgn_str: str) -> int:
    cur = conn.execute(
        "INSERT INTO games(chat_id, pgn) VALUES(?, ?)",
        (chat_id, pgn_str)
    )
    return cur.lastrowid

def load_games(conn, chat_id: int):
    return conn.execute(
        "SELECT game_id, pgn FROM games WHERE chat_id = ? ORDER BY synced_at DESC",
        (chat_id,)
    ).fetchall()
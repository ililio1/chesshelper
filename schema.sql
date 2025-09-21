-- schema.sql

CREATE TABLE IF NOT EXISTS users (
  chat_id        INTEGER PRIMARY KEY,
  lichess_nick   TEXT,
  chesscom_nick  TEXT,
  updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS games (
  game_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id      INTEGER       NOT NULL,
  source       TEXT          NOT NULL,
  pgn          TEXT          NOT NULL,
  synced_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(chat_id) REFERENCES users(chat_id),
  UNIQUE(chat_id, pgn)
);

CREATE TABLE IF NOT EXISTS blunders (
  blunder_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id           INTEGER       NOT NULL,
  move_index        INTEGER       NOT NULL,
  fen_before        TEXT          NOT NULL,
  detected_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
  solved            INTEGER       NOT NULL DEFAULT 0,

  -- Новые поля:
  best_move_uci     TEXT,
  cont_line_uci     TEXT,
  gif_error_w       BLOB,
  gif_error_b       BLOB,
  gif_best_w        BLOB,
  gif_best_b        BLOB,
  gif_cont_w        BLOB,
  gif_cont_b        BLOB,

  FOREIGN KEY(game_id) REFERENCES games(game_id),
  UNIQUE(game_id, move_index)
);

-- Рекомендуемые индексы
CREATE INDEX IF NOT EXISTS idx_games_chat ON games(chat_id, synced_at DESC);
CREATE INDEX IF NOT EXISTS idx_blunders_game ON blunders(game_id, move_index);
CREATE INDEX IF NOT EXISTS idx_blunders_solved ON blunders(solved, detected_at DESC);

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
  blunder_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id      INTEGER       NOT NULL,
  move_index   INTEGER       NOT NULL,
  fen_before   TEXT          NOT NULL,
  detected_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(game_id) REFERENCES games(game_id),
  UNIQUE(game_id, move_index)
);

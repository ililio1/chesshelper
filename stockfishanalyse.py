import chess
import chess.engine
import chess.pgn
import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Если запускаем на Windows (локально) - ищем exe, если на Linux (сервер) - ищем бинарник без расширения
if sys.platform == "win32":
    ENGINE_BINARY = "stockfish-windows-x86-64-avx2.exe"
else:
    # Имя файла, который мы зальем на сервер (об этом ниже)
    ENGINE_BINARY = "stockfish_linux_x64"

ENGINE_PATH = os.path.join(BASE_DIR, "stockfish", ENGINE_BINARY)

def geteval(strgame):

    engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
    pgn = io.StringIO(strgame)
    game = chess.pgn.read_game(pgn)

    board = chess.Board()
    evaluations = list()

    for move in game.mainline_moves():
        info = engine.analyse(board,limit=chess.engine.Limit(depth=15),info=chess.engine.INFO_SCORE)
        evaluation = info["score"].pov(board.turn).score(mate_score=100000)
        evaluations.append(evaluation)

        board.push(move)
    engine.close()
    return evaluations

def findmove(evaluations):

    base_thresh = 150
    scale_factor = 0.5
    max_thresh = 500

    blunders = list()
    for i in range(len(evaluations) - 1):
        evalnow = evaluations[i]
        evalafter = evaluations[i+1] * (-1)
        deltaeval = abs(evalafter - evalnow)

        if abs(evalnow) >= 750 and abs(evalafter) >= 750 and evalnow * evalafter > 0:
            continue

        if abs(evalafter) > 10000 and evalnow * evalafter > 0:
            continue

        if abs(evalnow) < base_thresh:
            threshold = base_thresh
        else:
            threshold = min(max_thresh, abs(evalnow) * scale_factor + 75)

        if deltaeval >= threshold:
            blunders.append(i)
            print(i)

    return blunders

def stockfish_best_move(fen, time_limit = 0.1) -> chess.Move:
    board = chess.Board(fen)

    with chess.engine.SimpleEngine.popen_uci(ENGINE_PATH) as engine:
        result = engine.play(board,chess.engine.Limit(time=time_limit))
    return result.move

def evaluate_move(fen: str, move: chess.Move, depth: int = 15) -> int:

    board = chess.Board(fen)
    board.push(move)
    with chess.engine.SimpleEngine.popen_uci(ENGINE_PATH) as engine:
        info = engine.analyse(
            board,
            limit=chess.engine.Limit(depth=depth),
            info=chess.engine.INFO_SCORE
        )
    score = info["score"].pov(board.turn).score(mate_score=100000)
    return score if score is not None else 0

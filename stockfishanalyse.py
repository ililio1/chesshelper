import chess
import chess.engine
import chess.pgn
import io

ENGINE_PATH = "D:\\ChessHelper\\stockfish\\stockfish-windows-x86-64-avx2.exe"

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

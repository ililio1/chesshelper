import os
from PIL import Image, ImageDraw
from io import BytesIO
import chess

_piece_images: dict[str, Image.Image] = {}
def _load_piece_images():
    base = os.path.join(os.path.dirname(__file__), "assets", "pieces")
    for color in ("w", "b"):
        for p in ("p","n","b","r","q","k"):
            name = f"{color}{p}"
            _piece_images[name] = Image.open(
                os.path.join(base, name + ".png")
            ).convert("RGBA")

_load_piece_images()

def render_board_png(fen: str, square_size: int = 200, flip: bool = False) -> BytesIO:
    board = chess.Board(fen)
    bs = square_size
    img = Image.new("RGBA", (8 * bs, 8 * bs), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    light, dark = "#F0D9B5", "#B58863"
    for rank in range(8):
        for file in range(8):
            color = light if (file + rank) % 2 == 0 else dark
            x0, y0 = file * bs, rank * bs
            x1, y1 = x0 + bs, y0 + bs
            draw.rectangle([x0, y0, x1, y1], fill=color)

            if not flip:
                actual_file = file
                actual_rank = 7 - rank
            else:
                actual_file = 7 - file
                actual_rank = rank

            sq = chess.square(actual_file, actual_rank)
            piece = board.piece_at(sq)
            if piece:
                key = f"{'w' if piece.color else 'b'}{piece.symbol().lower()}"
                icon = _piece_images[key]
                # position to paste
                img.paste(icon, (x0, y0), icon)

    buf = BytesIO()
    buf.name = "board.png"
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def render_move_gif(
    fen_before: str,
    move: chess.Move,
    square_size: int = 200,
    flip: bool = False,
    duration: int = 800
) -> BytesIO:
    """
    Создаёт двухкадровый GIF-анимацию:
    - кадр 1: доска до хода
    - кадр 2: доска после хода (move)
    duration — задержка между кадрами в миллисекундах.
    Возвращает BytesIO с именем 'move.gif'.
    """
    # Кадр до хода
    buf1 = render_board_png(fen_before, square_size, flip)
    im1  = Image.open(buf1)

    # Кадр после хода
    board_after = chess.Board(fen_before)
    board_after.push(move)
    buf2 = render_board_png(board_after.fen(), square_size, flip)
    im2  = Image.open(buf2)

    # Сборка GIF
    gif_buf = BytesIO()
    gif_buf.name = "move.gif"
    im1.save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=[im2],
        loop=0,             # зацикливать анимацию
        duration=duration   # миллисекунд между кадрами
    )
    gif_buf.seek(0)
    return gif_buf


def render_line_gif(
    fen_start: str,
    moves: list[chess.Move],
    square_size: int = 200,
    flip: bool = False,
    duration: int = 600
) -> BytesIO:
    """
    Генерирует GIF-анимацию из серии полуходов:
    - первый кадр: начальная позиция fen_start
    - последующие кадры: после каждого хода из списка moves
    duration — задержка между кадрами в миллисекундах.
    Возвращает BytesIO с именем 'line.gif'.
    """
    frames: list[Image.Image] = []
    board = chess.Board(fen_start)

    # Кадр с начальной позицией
    frames.append(Image.open(render_board_png(fen_start, square_size, flip)))

    # Кадры после каждого полухода
    for mv in moves:
        board.push(mv)
        frames.append(Image.open(render_board_png(board.fen(), square_size, flip)))

    # Сборка GIF
    gif_buf = BytesIO()
    gif_buf.name = "line.gif"
    frames[0].save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=duration
    )
    gif_buf.seek(0)
    return gif_buf


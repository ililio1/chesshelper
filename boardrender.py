import os
from PIL import Image, ImageDraw
from io import BytesIO
import chess

_piece_images: dict[str, Image.Image] = {}

def _load_piece_images():
    base = os.path.join(os.path.dirname(__file__), "assets", "pieces")
    for color in ("w", "b"):
        for p in ("p", "n", "b", "r", "q", "k"):
            name = f"{color}{p}"
            img_path = os.path.join(base, name + ".png")
            icon = Image.open(img_path).convert("RGBA")
            _piece_images[name] = icon

_load_piece_images()

def _get_scaled_icon(key: str, square_size: int) -> Image.Image:
    icon = _piece_images[key]
    if icon.width == square_size and icon.height == square_size:
        return icon
    return icon.resize((square_size, square_size), Image.LANCZOS)

def _render_board_image(fen: str, square_size: int, flip: bool) -> Image.Image:
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
                icon = _get_scaled_icon(key, bs)
                img.alpha_composite(icon, (x0, y0))

    return img

def render_board_png(
    fen: str,
    square_size: int = 200,
    flip: bool = False
) -> BytesIO:
    img = _render_board_image(fen, square_size, flip)
    buf = BytesIO()
    img.save(buf, "PNG")
    buf.name = "board.png"
    buf.seek(0)
    return buf


import random
from PIL import Image, ImageDraw


def _render_board_with_indicator(fen: str, square_size: int, flip: bool, progress: float) -> Image.Image:
    """Рисует доску и добавляет маленькую полоску прогресса внизу (1 пиксель высотой)."""
    img = _render_board_image(fen, square_size, flip)
    draw = ImageDraw.Draw(img)

    # Рисуем тонкую полоску в самом низу, которая меняет длину
    # Это создает значительное изменение в данных кадра для видеокодека
    width = img.width
    height = img.height
    indicator_width = int(width * progress)

    # Рисуем линию цветом, который почти совпадает с клеткой, но технически другой
    draw.line([(0, height - 1), (indicator_width, height - 1)], fill=(181, 136, 99, 255), width=1)
    return img


def render_move_gif(
        fen_before: str,
        move: chess.Move,
        square_size: int = 200,
        flip: bool = False,
        frame_duration: int = 250,  # Уменьшаем длительность одного кадра
        pause_after: int = 2000
) -> BytesIO:
    # Генерируем много кадров, чтобы видео длилось ~3 секунды
    # Даже для одного хода мы сделаем "анимацию"
    frames = []

    # 4 кадра для начальной позиции (прогресс-бар движется)
    for i in range(4):
        frames.append(_render_board_with_indicator(fen_before, square_size, flip, i / 20))

    # Позиция после хода
    board_after = chess.Board(fen_before)
    board_after.push(move)
    fen_after = board_after.fen()

    # 8 кадров финальной позиции (полоска доходит до конца)
    # Это создаст "движение", которое Telegram не сможет проигнорировать
    for i in range(5, 15):
        frames.append(_render_board_with_indicator(fen_after, square_size, flip, i / 15))

    gif_buf = BytesIO()
    gif_buf.name = "move.gif"
    frames[0].save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=150,  # Константный и быстрый FPS (около 7 кадров в сек)
        disposal=2,
    )
    gif_buf.seek(0)
    return gif_buf


def render_line_gif(
        fen_start: str,
        moves: list[chess.Move],
        square_size: int = 200,
        flip: bool = False,
        frame_duration: int = 400,
        pause_after: int = 2000
) -> BytesIO:
    board = chess.Board(fen_start)
    all_fens = [fen_start]
    for mv in moves:
        board.push(mv)
        all_fens.append(board.fen())

    frames = []
    total_steps = len(all_fens) + 10  # Запас для паузы

    # Для каждого FEN делаем 2 кадра с разным прогресс-баром
    for idx, fen in enumerate(all_fens):
        progress = (idx + 1) / total_steps
        frames.append(_render_board_with_indicator(fen, square_size, flip, progress))
        frames.append(_render_board_with_indicator(fen, square_size, flip, progress + 0.01))

    # Пауза в конце: делаем 10 уникальных кадров (полоска чуть-чуть дрожит)
    last_fen = all_fens[-1]
    for i in range(10):
        v = 0.9 + (i * 0.01)
        frames.append(_render_board_with_indicator(last_fen, square_size, flip, min(v, 1.0)))

    gif_buf = BytesIO()
    gif_buf.name = "line.gif"
    frames[0].save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=200,
        disposal=2,
    )
    gif_buf.seek(0)
    return gif_buf
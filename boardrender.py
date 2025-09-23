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

def render_move_gif(
    fen_before: str,
    move: chess.Move,
    square_size: int = 200,
    flip: bool = False,
    frame_duration: int = 800,
    pause_after: int = 2000
) -> BytesIO:
    im1 = _render_board_image(fen_before, square_size, flip)
    board_after = chess.Board(fen_before)
    board_after.push(move)
    im2 = _render_board_image(board_after.fen(), square_size, flip)

    pause_copies = max(1, int(round(pause_after / frame_duration)))
    frames = [im1, im2] + [im2] * pause_copies

    gif_buf = BytesIO()
    gif_buf.name = "move.gif"
    frames[0].save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=frame_duration,
        disposal=2,
    )
    gif_buf.seek(0)
    return gif_buf

def render_line_gif(
    fen_start: str,
    moves: list[chess.Move],
    square_size: int = 200,
    flip: bool = False,
    frame_duration: int = 600,
    pause_after: int = 2000
) -> BytesIO:

    frames: list[Image.Image] = []
    board = chess.Board(fen_start)

    frames.append(_render_board_image(fen_start, square_size, flip))
    for mv in moves:
        board.push(mv)
        frames.append(_render_board_image(board.fen(), square_size, flip))

    pause_copies = max(1, int(round(pause_after / frame_duration)))
    frames += [frames[-1]] * pause_copies

    gif_buf = BytesIO()
    gif_buf.name = "line.gif"
    frames[0].save(
        gif_buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=frame_duration,
        disposal=2,
    )
    gif_buf.seek(0)
    return gif_buf

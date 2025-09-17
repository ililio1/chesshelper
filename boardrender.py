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

def render_board_png(fen: str, square_size: int = 60, flip: bool = False) -> BytesIO:
    board = chess.Board(fen)
    bs = square_size
    img = Image.new("RGBA", (8 * bs, 8 * bs), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    light, dark = "#F0D9B5", "#B58863"
    for rank in range(8):
        for file in range(8):
            # цвет клетки
            color = light if (file + rank) % 2 == 0 else dark
            x0, y0 = file * bs, rank * bs
            x1, y1 = x0 + bs, y0 + bs
            draw.rectangle([x0, y0, x1, y1], fill=color)

            # координаты для piece_at
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

    # ничего не поворачиваем, иконки встанут правильно
    buf = BytesIO()
    buf.name = "board.png"
    img.save(buf, "PNG")
    buf.seek(0)
    return buf

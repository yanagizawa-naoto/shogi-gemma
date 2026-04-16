"""Wrapper around python-shogi providing GameState with move history."""
from __future__ import annotations
from dataclasses import dataclass, field
from copy import deepcopy
import shogi


# Kanji rendering of pieces. Lowercase = WHITE (gote / 後手).
PIECE_KANJI = {
    'P': '歩', 'L': '香', 'N': '桂', 'S': '銀', 'G': '金', 'B': '角', 'R': '飛', 'K': '王',
    '+P': 'と', '+L': '杏', '+N': '圭', '+S': '全', '+B': '馬', '+R': '龍',
}
# WHITE king is shown as 玉 to differentiate (cosmetic only).
PIECE_KANJI_WHITE_OVERRIDE = {'K': '玉'}

# Hand piece order (drop priority by tradition: 飛角金銀桂香歩)
HAND_ORDER = ['R', 'B', 'G', 'S', 'N', 'L', 'P']
HAND_KANJI = {p: PIECE_KANJI[p] for p in HAND_ORDER}


def board_to_ascii(board: shogi.Board) -> str:
    """Human/LLM readable board with files 9..1 and ranks a..i."""
    lines = ["  9 8 7 6 5 4 3 2 1"]
    rank_chars = "abcdefghi"
    for r in range(9):
        cells = []
        for f in range(9):
            sq = r * 9 + f
            piece = board.piece_at(sq)
            if piece is None:
                cells.append(".")
            else:
                sym = piece.symbol()
                cells.append(sym)
        lines.append(f"{rank_chars[r]} {' '.join(cells)}")

    # Hand pieces
    def hand_str(color):
        h = board.pieces_in_hand[color]
        if not any(h.get(pt, 0) for pt in h):
            return "なし"
        parts = []
        for pt in [shogi.ROOK, shogi.BISHOP, shogi.GOLD, shogi.SILVER, shogi.KNIGHT, shogi.LANCE, shogi.PAWN]:
            n = h.get(pt, 0)
            if n > 0:
                sym = shogi.PIECE_SYMBOLS[pt].upper()
                parts.append(f"{sym}{n}")
        return " ".join(parts) or "なし"

    lines.append("")
    lines.append(f"先手(▲) 持ち駒: {hand_str(shogi.BLACK)}")
    lines.append(f"後手(△) 持ち駒: {hand_str(shogi.WHITE)}")
    return "\n".join(lines)


def piece_kanji(piece: shogi.Piece) -> tuple[str, bool]:
    """Returns (kanji, is_white)."""
    sym = piece.symbol()  # e.g. 'P', '+p', 'r'
    is_white = sym[-1].islower()
    sym_upper = sym.upper()
    if is_white and sym_upper == 'K':
        kanji = PIECE_KANJI_WHITE_OVERRIDE['K']
    else:
        kanji = PIECE_KANJI.get(sym_upper, sym_upper)
    return kanji, is_white


@dataclass
class GameState:
    board: shogi.Board = field(default_factory=shogi.Board)
    history: list[dict] = field(default_factory=list)

    @property
    def is_over(self) -> bool:
        return self.board.is_game_over() or len(self.history) >= 400

    @property
    def turn(self) -> int:
        return self.board.turn

    def legal_usi(self) -> list[str]:
        return [m.usi() for m in self.board.legal_moves]

    def play(self, usi: str, *, reasoning: str = "", raw_text: str = "",
             comment: str = "", retries: int = 0, forced_fallback: bool = False) -> None:
        move = shogi.Move.from_usi(usi)
        if move not in self.board.legal_moves:
            raise ValueError(f"Illegal move {usi}")
        player = self.board.turn
        is_check = self.board.is_check()
        self.board.push(move)
        delivered_check = self.board.is_check()
        self.history.append({
            "player": player,
            "move": usi,
            "reasoning": reasoning,
            "comment": comment,
            "raw_text": raw_text,
            "board_after_sfen": self.board.sfen(),
            "retries": retries,
            "forced_fallback": forced_fallback,
            "delivered_check": delivered_check,
            "was_in_check_before": is_check,
        })

    def winner(self) -> int | None:
        """Returns the winner's color, or 0 for draw, or None if game still on."""
        if not self.is_over:
            return None
        if self.board.is_checkmate():
            # The side to move just got checkmated → other side wins
            return 1 - self.board.turn
        # draw / impasse / move limit
        return 0


def board_from_sfen(sfen: str) -> shogi.Board:
    return shogi.Board(sfen)

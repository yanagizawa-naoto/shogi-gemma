"""Streamlit UI for Gemma vs Gemma Shogi match (LINE-style chat bubbles)."""
from __future__ import annotations
import html
import time
import streamlit as st
import shogi
from dotenv import load_dotenv

from shogi_game import (
    GameState, board_to_ascii, board_from_sfen,
    PIECE_KANJI, PIECE_KANJI_WHITE_OVERRIDE, HAND_KANJI,
)
from agent import make_players

load_dotenv()
st.set_page_config(page_title="Gemma vs Gemma — Shogi", layout="wide")

SENTE = shogi.BLACK  # 先手 = Quick (●)
GOTE = shogi.WHITE   # 後手 = Thinker (○)


def init_state():
    if "game" not in st.session_state:
        st.session_state.game = GameState()
    if "players" not in st.session_state:
        quick, thinker = make_players()
        st.session_state.players = {SENTE: quick, GOTE: thinker}
    if "running" not in st.session_state:
        st.session_state.running = False
    if "error" not in st.session_state:
        st.session_state.error = None


GLOBAL_CSS = """
<style>
.shogi-board { border-collapse: collapse; margin: 0; }
.shogi-board td {
    width: 36px; height: 38px; background: #eecf8a;
    border: 1px solid #6b4a1a; text-align: center; vertical-align: middle;
    font-size: 18px; font-weight: bold; color: #1a1a1a;
    font-family: "Hiragino Mincho ProN", "Yu Mincho", serif;
}
.shogi-board td.last { background: #fff59d; box-shadow: inset 0 0 0 2px #f57f17; }
.shogi-board td .gote { display: inline-block; transform: rotate(180deg); color: #4d1f1f; }
.shogi-board td .sente { color: #1a1a1a; }
.shogi-board th {
    width: 22px; height: 18px; text-align: center;
    font-family: monospace; color: #555; padding: 1px; font-size: 11px;
    background: transparent;
}
.hand-row { font-family: "Hiragino Mincho ProN", serif; font-size: 16px;
    padding: 4px 8px; background: #f3e3b5; border: 1px solid #c5a056; border-radius: 4px;
    margin: 2px 0; }
.hand-row .label { font-size: 11px; color: #6b4a1a; margin-right: 6px; font-family: monospace; }
.hand-row .none { color: #aaa; font-size: 13px; }

/* Chat bubbles */
.bubble-row { display: flex; margin: 8px 0; align-items: flex-end; }
.bubble-row.left  { justify-content: flex-start; }
.bubble-row.right { justify-content: flex-end; }

.bubble-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: #fff; border: 2px solid #888;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; flex-shrink: 0; margin: 0 6px;
}
.bubble-avatar.sente { background: #111; color: #fff; }
.bubble-avatar.gote  { background: #fafafa; color: #111; border-color: #111; }

.bubble {
    padding: 9px 13px; border-radius: 18px;
    max-width: 80%; line-height: 1.45;
    box-shadow: 0 1px 2px rgba(0,0,0,0.12);
    font-size: 14px;
}
.bubble.quick   { background: #8de08a; color: #1b3a1a; }
.bubble.thinker { background: #a3d3f5; color: #0e2a44; }
.bubble.thinking.quick   { background: #d6f0d4; font-style: italic; opacity: 0.9; }
.bubble.thinking.thinker { background: #d8ebf8; font-style: italic; opacity: 0.9; }

.bubble .meta { font-size: 11px; opacity: 0.7; margin-bottom: 3px; }
.bubble .move-chip {
    display: inline-block; color: #fff;
    padding: 1px 8px; border-radius: 10px;
    font-family: monospace; font-weight: bold;
    margin-left: 6px; font-size: 12px;
}
.bubble.quick   .move-chip { background: #2e7d32; }
.bubble.thinker .move-chip { background: #1565c0; }
.bubble .check-flag {
    display: inline-block; background: #d32f2f; color: #fff;
    padding: 1px 6px; border-radius: 8px; font-size: 10px; margin-left: 4px;
}
.bubble .retry-flag {
    display: inline-block; background: #ffb74d; color: #4e2a00;
    padding: 1px 6px; border-radius: 8px; font-size: 10px; margin-left: 4px;
}
.bubble .fb-flag {
    display: inline-block; background: #e57373; color: #fff;
    padding: 1px 6px; border-radius: 8px; font-size: 10px; margin-left: 4px;
}
.bubble-content { white-space: pre-wrap; }
</style>
"""


def render_hand_html(board: shogi.Board, color: int) -> str:
    h = board.pieces_in_hand[color]
    pieces = []
    for pt in [shogi.ROOK, shogi.BISHOP, shogi.GOLD, shogi.SILVER, shogi.KNIGHT, shogi.LANCE, shogi.PAWN]:
        n = h.get(pt, 0)
        if n > 0:
            sym = shogi.PIECE_SYMBOLS[pt].upper()
            kanji = PIECE_KANJI[sym]
            count_str = "" if n == 1 else f"<sub>{n}</sub>"
            pieces.append(f"{kanji}{count_str}")
    label = "▲先手" if color == SENTE else "△後手"
    inner = " ".join(pieces) if pieces else '<span class="none">なし</span>'
    return f'<div class="hand-row"><span class="label">{label}持駒</span>{inner}</div>'


def usi_to_dest_square(usi: str) -> int | None:
    """Returns the destination square index (0-80) of a USI move."""
    if not usi:
        return None
    m = usi[-2:] if not usi.endswith("+") else usi[-3:-1]
    file_ch, rank_ch = m[0], m[1]
    if not ('1' <= file_ch <= '9' and 'a' <= rank_ch <= 'i'):
        return None
    file_idx = 9 - int(file_ch)  # USI file 9 -> index 0
    rank_idx = ord(rank_ch) - ord('a')
    return rank_idx * 9 + file_idx


def render_board_html(board: shogi.Board, last_dest: int | None = None) -> str:
    parts = ['<table class="shogi-board">']
    parts.append('<tr><th></th>' + ''.join(f'<th>{9 - f}</th>' for f in range(9)) + '</tr>')
    rank_kanji = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    for r in range(9):
        parts.append(f'<tr><th>{rank_kanji[r]}</th>')
        for f in range(9):
            sq = r * 9 + f
            piece = board.piece_at(sq)
            classes = []
            if last_dest is not None and last_dest == sq:
                classes.append("last")
            if piece is None:
                content = ''
            else:
                sym = piece.symbol()
                is_white = sym[-1].islower()
                sym_upper = sym.upper()
                if is_white and sym_upper == 'K':
                    kanji = PIECE_KANJI_WHITE_OVERRIDE['K']
                else:
                    kanji = PIECE_KANJI.get(sym_upper, sym_upper)
                cls = 'gote' if is_white else 'sente'
                content = f'<span class="{cls}">{kanji}</span>'
            cls_attr = f' class="{" ".join(classes)}"' if classes else ''
            parts.append(f'<td{cls_attr}>{content}</td>')
        parts.append('</tr>')
    parts.append('</table>')
    return '\n'.join(parts)


def bubble_html(h: dict) -> str:
    is_sente = h["player"] == SENTE
    side = "left" if is_sente else "right"
    avatar_cls = "sente" if is_sente else "gote"
    avatar_glyph = "▲" if is_sente else "△"
    name = "Quick" if is_sente else "Thinker"
    bubble_kind = "quick" if is_sente else "thinker"
    move = h["move"]
    summary = h.get("comment") or "(出力なし)"
    summary_safe = html.escape(summary)
    flags = ""
    if h.get("delivered_check"):
        flags += '<span class="check-flag">王手!</span>'
    if h.get("retries", 0) > 0:
        if h.get("forced_fallback"):
            flags += '<span class="fb-flag">⚠ FB</span>'
        else:
            flags += f'<span class="retry-flag">🔁{h["retries"]}</span>'

    bubble_inner = (
        f'<div class="meta">{name}</div>'
        f'<div class="bubble-content">{summary_safe}</div>'
        f'<div style="margin-top:4px"><span class="move-chip">{move}</span>{flags}</div>'
    )
    avatar = f'<div class="bubble-avatar {avatar_cls}">{avatar_glyph}</div>'
    bubble = f'<div class="bubble {bubble_kind}">{bubble_inner}</div>'
    if side == "left":
        return f'<div class="bubble-row left">{avatar}{bubble}</div>'
    else:
        return f'<div class="bubble-row right">{bubble}{avatar}</div>'


def thinking_bubble_html(player: int) -> str:
    is_sente = player == SENTE
    side = "left" if is_sente else "right"
    avatar_cls = "sente" if is_sente else "gote"
    avatar_glyph = "▲" if is_sente else "△"
    name = "Quick" if is_sente else "Thinker"
    bubble_kind = "quick" if is_sente else "thinker"
    bubble = f'<div class="bubble thinking {bubble_kind}"><div class="meta">{name}</div>考え中…</div>'
    avatar = f'<div class="bubble-avatar {avatar_cls}">{avatar_glyph}</div>'
    if side == "left":
        return f'<div class="bubble-row left">{avatar}{bubble}</div>'
    return f'<div class="bubble-row right">{bubble}{avatar}</div>'


def render_feed(game: GameState, thinking_player: int | None = None) -> str:
    parts = []
    if thinking_player is not None:
        parts.append(thinking_bubble_html(thinking_player))
    for h in reversed(game.history):
        parts.append(bubble_html(h))
    return "\n".join(parts)


def step_one_move(feed_placeholder, game: GameState):
    if game.is_over:
        st.session_state.running = False
        return
    legal = game.legal_usi()
    if not legal:
        st.session_state.running = False
        return

    player_color = game.turn
    player = st.session_state.players[player_color]
    feed_placeholder.markdown(
        render_feed(game, thinking_player=player_color), unsafe_allow_html=True,
    )

    def on_chunk(_d, _a):
        pass  # keep "thinking" bubble visible during streaming

    try:
        resp = player.choose_move_streaming(game.board, legal, on_chunk=on_chunk)
    except Exception as e:
        st.session_state.error = f"{player.name} エラー: {e}"
        st.session_state.running = False
        return

    if resp.move is None:
        st.session_state.error = f"{player.name}: 合法手を選べず"
        st.session_state.running = False
        return

    try:
        game.play(
            resp.move,
            reasoning=resp.reasoning, raw_text=resp.raw_text, comment=resp.comment,
            retries=resp.retries, forced_fallback=resp.forced_fallback,
        )
    except ValueError as e:
        st.session_state.error = f"内部エラー: {e}"
        st.session_state.running = False


def main():
    init_state()
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
    game: GameState = st.session_state.game

    col_left, col_right = st.columns([1, 1])

    with col_left:
        # Header
        if game.is_over:
            winner = game.winner()
            if winner == SENTE:
                st.markdown("### 🏆 ▲ Quick 勝ち")
            elif winner == GOTE:
                st.markdown("### 🏆 △ Thinker 勝ち")
            else:
                st.markdown(f"### 🤝 引き分け / 規定手数到達 ({len(game.history)}手)")
        else:
            turn = "▲ Quick" if game.turn == SENTE else "△ Thinker"
            check = " 王手中!" if game.board.is_check() else ""
            st.markdown(f"### 手数 {len(game.history)} → 次手番: **{turn}**{check}")

        # Hands (gote on top, sente at bottom — traditional view)
        st.markdown(render_hand_html(game.board, GOTE), unsafe_allow_html=True)

        last_dest = usi_to_dest_square(game.history[-1]["move"]) if game.history else None
        st.markdown(render_board_html(game.board, last_dest=last_dest), unsafe_allow_html=True)

        st.markdown(render_hand_html(game.board, SENTE), unsafe_allow_html=True)

        bcol1, bcol2, bcol3, bcol4 = st.columns(4)
        with bcol1:
            if st.button("▶", help="自動再生", disabled=st.session_state.running or game.is_over, use_container_width=True):
                st.session_state.running = True
                st.rerun()
        with bcol2:
            if st.button("⏸", help="停止", disabled=not st.session_state.running, use_container_width=True):
                st.session_state.running = False
                st.rerun()
        with bcol3:
            if st.button("⏭", help="1手進める", disabled=st.session_state.running or game.is_over, use_container_width=True):
                st.session_state._do_step_once = True
                st.rerun()
        with bcol4:
            if st.button("🔄", help="リセット", use_container_width=True):
                st.session_state.game = GameState()
                st.session_state.running = False
                st.session_state.error = None
                st.session_state.pop("_do_step_once", None)
                st.rerun()

        if st.session_state.error:
            st.error(st.session_state.error)

    with col_right:
        feed_placeholder = st.empty()
        feed_placeholder.markdown(render_feed(game), unsafe_allow_html=True)

        do_step_once = st.session_state.pop("_do_step_once", False)
        do_autoplay_step = st.session_state.running and not game.is_over

        if do_autoplay_step or do_step_once:
            if do_autoplay_step and game.history:
                time.sleep(1.5)
            step_one_move(feed_placeholder, game)
            st.rerun()


if __name__ == "__main__":
    main()

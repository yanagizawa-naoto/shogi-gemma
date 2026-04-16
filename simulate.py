"""Run N parallel Quick vs Thinker shogi matches with balanced sente assignment.

First half of games: Quick = sente (BLACK), Thinker = gote (WHITE)
Second half:        Thinker = sente (BLACK), Quick = gote (WHITE)

Concurrency limit is configurable to avoid saturating the endpoint.

Usage: uv run python simulate.py [N_GAMES] [CONCURRENCY]
  defaults: N_GAMES=100, CONCURRENCY=20
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
import httpx
from dotenv import load_dotenv
import shogi

from shogi_game import GameState, board_to_ascii
from agent import (
    QUICK_SYSTEM, THINKER_SYSTEM, QUICK_SCHEMA, THINKER_SCHEMA, USI_PATTERN,
)

load_dotenv()

BASE_URL = os.environ["GEMMA_BASE_URL"].rstrip("/")
API_KEY = os.environ["GEMMA_API_KEY"]
MODEL = os.environ["GEMMA_MODEL"]

CFG = {
    "quick": {
        "system": QUICK_SYSTEM, "schema": QUICK_SCHEMA, "schema_name": "quick_move",
        "max_tokens": 500, "temperature": 0.7,
    },
    "thinker": {
        "system": THINKER_SYSTEM, "schema": THINKER_SCHEMA, "schema_name": "thinker_move",
        "max_tokens": 2500, "temperature": 0.4,
    },
}

# Sentinel string for a drawn game (so it can't collide with shogi.BLACK=0).
DRAW = "draw"
CALL_TIMEOUT = 120.0  # hard timeout per API call (Thinker needs long)


def build_user_msg(board: shogi.Board, legal_usi: list[str]) -> str:
    color = "先手(▲)" if board.turn == shogi.BLACK else "後手(△)"
    legal_str = ", ".join(legal_usi)
    return (
        f"あなたは {color} の手番です。\n\n"
        f"現在の盤面:\n{board_to_ascii(board)}\n\n"
        f"合法手 ({len(legal_usi)}手): {legal_str}\n\n"
        f"この中から1つ選んで JSON で答えてください。"
    )


def extract_attempted(text: str) -> str:
    try:
        return str(json.loads(text).get("move", ""))
    except json.JSONDecodeError:
        m = re.search(USI_PATTERN.replace("^", "").replace("$", ""), text)
        return m.group(0) if m else "(parse-failed)"


async def call_model(client, style, board, legal_usi, stats, max_retries=2):
    cfg = CFG[style]
    user_msg = build_user_msg(board, legal_usi)
    messages = [
        {"role": "system", "content": cfg["system"]},
        {"role": "user", "content": user_msg},
    ]
    legal_set = set(legal_usi)

    for attempt in range(max_retries + 1):
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": cfg["max_tokens"],
            "temperature": cfg["temperature"],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": cfg["schema_name"], "schema": cfg["schema"]},
            },
        }
        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                client.post(f"{BASE_URL}/chat/completions", json=payload),
                timeout=CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            stats["call_timeouts"] = stats.get("call_timeouts", 0) + 1
            raise httpx.ReadTimeout(f"call_model timeout after {CALL_TIMEOUT}s")
        latency = time.monotonic() - t0
        stats["latency_sum"] += latency
        stats["latency_count"] += 1
        if resp.status_code != 200:
            stats["http_errors"] += 1
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp,
            )
        text = resp.json()["choices"][0]["message"]["content"] or ""
        stats["calls"] += 1

        try:
            obj = json.loads(text)
            move_str = obj.get("move", "")
        except json.JSONDecodeError:
            obj, move_str = {}, ""
        if move_str and move_str in legal_set:
            if attempt > 0:
                stats["retries"] += attempt
            return move_str, attempt, False

        attempted = extract_attempted(text)
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": (
                f"あなたが返した {attempted!r} は合法手ではありません。"
                f"合法手は次のいずれかだけです（USI表記）: {', '.join(legal_usi)}。"
                f"必ずこの中から1つだけ選び、同じ JSON フォーマットで再回答してください。"
            ),
        })

    stats["fallbacks"] += 1
    stats["retries"] += max_retries
    return legal_usi[0], max_retries, True


async def play_game(game_id, client, semaphore, side_assignment, stats, results):
    """side_assignment: dict mapping shogi.BLACK -> "quick"|"thinker", same for WHITE."""
    async with semaphore:
        g = GameState()
        move_count = 0
        try:
            while not g.is_over:
                legal = g.legal_usi()
                if not legal:
                    break
                style = side_assignment[g.turn]
                move_str, retries, fb = await call_model(
                    client, style, g.board, legal, stats,
                )
                g.play(move_str, retries=retries, forced_fallback=fb)
                move_count += 1
            w = g.winner()  # shogi.BLACK / shogi.WHITE / 'sennichite' / 'abandoned' / None
            if w == "sennichite":
                winner_style = "sennichite"
            elif w == "abandoned":
                winner_style = "abandoned"
            elif w in (shogi.BLACK, shogi.WHITE):
                winner_style = side_assignment[w]
            else:
                winner_style = "unknown"
            results[game_id] = {
                "winner_style": winner_style,
                "winner_color": w,
                "sente_style": side_assignment[shogi.BLACK],
                "moves": move_count,
                "error": None,
            }
        except Exception as e:
            stats["game_errors"] += 1
            results[game_id] = {
                "winner_style": None, "winner_color": None,
                "sente_style": side_assignment[shogi.BLACK],
                "moves": move_count,
                "error": f"{type(e).__name__}: {e}",
            }


async def monitor(stats, results, n_games, start_t):
    while True:
        await asyncio.sleep(10)
        done = sum(1 for v in results.values() if v is not None)
        elapsed = time.time() - start_t
        avg_latency = (
            stats["latency_sum"] / stats["latency_count"]
            if stats["latency_count"] else 0
        )
        rate = stats["calls"] / elapsed if elapsed > 0 else 0
        ws = Counter(v["winner_style"] for v in results.values() if v is not None)
        print(
            f"[{elapsed:5.0f}s] games={done}/{n_games} | "
            f"calls={stats['calls']} ({rate:.1f}/s, avg={avg_latency:.1f}s) | "
            f"retries={stats['retries']} fb={stats['fallbacks']} | "
            f"http_err={stats['http_errors']} call_to={stats['call_timeouts']} game_err={stats['game_errors']} | "
            f"Q={ws.get('quick', 0)} T={ws.get('thinker', 0)} "
            f"S={ws.get('sennichite', 0)} A={ws.get('abandoned', 0)}",
            flush=True,
        )
        if done >= n_games:
            return


async def main(n_games: int = 100, concurrency: int = 20):
    timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=300.0)
    limits = httpx.Limits(
        max_connections=concurrency + 20,
        max_keepalive_connections=concurrency + 20,
    )
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    stats = {
        "calls": 0, "retries": 0, "fallbacks": 0,
        "http_errors": 0, "game_errors": 0, "call_timeouts": 0,
        "latency_sum": 0.0, "latency_count": 0,
    }
    results: dict[int, dict | None] = {i: None for i in range(n_games)}

    # Balanced side assignment: first half Quick=sente, second half Thinker=sente.
    half = n_games // 2
    assignments = []
    for i in range(n_games):
        if i < half:
            assignments.append({shogi.BLACK: "quick", shogi.WHITE: "thinker"})
        else:
            assignments.append({shogi.BLACK: "thinker", shogi.WHITE: "quick"})

    print(f"=== Starting {n_games} shogi games (concurrency={concurrency}) ===", flush=True)
    print(f"Endpoint: {BASE_URL}", flush=True)
    print(f"Model: {MODEL}", flush=True)
    print(f"Balanced sides: {half} games Quick=sente, {n_games - half} games Thinker=sente", flush=True)
    start_t = time.time()

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, headers=headers, http2=False,
    ) as client:
        game_tasks = [
            asyncio.create_task(play_game(i, client, sem, assignments[i], stats, results))
            for i in range(n_games)
        ]
        mon_task = asyncio.create_task(monitor(stats, results, n_games, start_t))
        await asyncio.gather(*game_tasks)
        mon_task.cancel()

    elapsed = time.time() - start_t

    # Style-level tally (ignoring side)
    style_counter = Counter()
    # Per-side breakdowns
    when_quick_sente = Counter()   # outcomes when Quick=sente
    when_thinker_sente = Counter() # outcomes when Thinker=sente
    moves_by_winner: dict[str, list[int]] = {
        "quick": [], "thinker": [], "sennichite": [], "abandoned": [],
    }
    errors = []

    for r in results.values():
        if r["error"]:
            errors.append(r["error"])
            style_counter["error"] += 1
            continue
        w = r["winner_style"]
        style_counter[w] += 1
        if w in moves_by_winner:
            moves_by_winner[w].append(r["moves"])
        # Per-side
        if r["sente_style"] == "quick":
            when_quick_sente[w] += 1
        else:
            when_thinker_sente[w] += 1

    def avg_moves(lst):
        return f"{sum(lst)/len(lst):.1f}" if lst else "-"

    print()
    print("=" * 60)
    print(f"=== FINAL RESULTS ({n_games} games, {elapsed:.0f}s) ===")
    print("=" * 60)
    print(f"  ▲ Quick   wins (any side): {style_counter.get('quick', 0):>4}  (avg {avg_moves(moves_by_winner['quick'])} moves)")
    print(f"  △ Thinker wins (any side): {style_counter.get('thinker', 0):>4}  (avg {avg_moves(moves_by_winner['thinker'])} moves)")
    print(f"  🔁 Sennichite (千日手)     : {style_counter.get('sennichite', 0):>4}  (avg {avg_moves(moves_by_winner['sennichite'])} moves)")
    print(f"  🚫 Abandoned (1500手到達)   : {style_counter.get('abandoned', 0):>4}  (avg {avg_moves(moves_by_winner['abandoned'])} moves)")
    print(f"  ⚠️ Errors                  : {style_counter.get('error', 0):>4}")
    print()
    print("--- Breakdown by sente assignment ---")
    print(f"When Quick = sente ({sum(when_quick_sente.values())} games):")
    print(f"  Quick wins   : {when_quick_sente.get('quick', 0)}")
    print(f"  Thinker wins : {when_quick_sente.get('thinker', 0)}")
    print(f"  Sennichite   : {when_quick_sente.get('sennichite', 0)}")
    print(f"  Abandoned    : {when_quick_sente.get('abandoned', 0)}")
    print(f"When Thinker = sente ({sum(when_thinker_sente.values())} games):")
    print(f"  Quick wins   : {when_thinker_sente.get('quick', 0)}")
    print(f"  Thinker wins : {when_thinker_sente.get('thinker', 0)}")
    print(f"  Sennichite   : {when_thinker_sente.get('sennichite', 0)}")
    print(f"  Abandoned    : {when_thinker_sente.get('abandoned', 0)}")
    print()
    print(f"Total API calls : {stats['calls']}")
    print(f"Avg latency     : {stats['latency_sum'] / max(stats['latency_count'], 1):.2f}s")
    print(f"Throughput      : {stats['calls'] / elapsed:.1f} calls/s")
    print(f"Retries         : {stats['retries']}")
    print(f"Fallbacks       : {stats['fallbacks']}")
    print(f"HTTP errors     : {stats['http_errors']}")
    print(f"Call timeouts   : {stats['call_timeouts']}")
    print(f"Game errors     : {stats['game_errors']}")
    if errors:
        print()
        print("Sample errors:")
        for e in errors[:5]:
            print(f"  - {e}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    asyncio.run(main(n, c))

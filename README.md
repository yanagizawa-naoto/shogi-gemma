# Shogi: Gemma vs Gemma

Two Gemma-backed agents playing shogi (将棋) against each other, differentiated only by prompting.
Same architecture as the [othello-gemma](https://github.com/yanagizawa-naoto/othello-gemma) companion
project: JSON-schema-constrained moves, illegal-move retries, streaming LINE-style viewer, parallel
tournament runner.

## Players

Both players use the **same underlying Gemma model**. Differentiation:

| Player | Style | Prompt |
|---|---|---|
| **Quick** | Intuitive / immediate | No CoT. `{intent, move}` only. |
| **Thinker** | Deliberate / CoT | Considers king safety, piece activity, drops, captures. `{thinking, summary, move}`. |

Moves use **USI notation** (e.g. `7g7f`, `8h2b+` for promotion, `P*5e` for drops) enforced by a
regex schema:

```
^([1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])$
```

## Results: 100-game balanced tournament

Balanced sides: 50 games with Quick = sente (先手), 50 games with Thinker = sente. Concurrency capped
at 20 (not 100 — the endpoint hung at high concurrency with shogi's longer prompts).

**Note:** Only 65 of 100 games finished before the endpoint deadlocked and the run had to be stopped.
The remaining 35 games were aborted mid-play. The completion data is still meaningful:

| Outcome | Count | Share (of 65 completed) |
|---|---|---|
| 🤝 **Draws (400-move limit)** | **61** | **93.8%** |
| △ Thinker wins | 3 | 4.6% |
| ▲ Quick wins | 1 | 1.5% |
| ⚠ Errors | 0 | 0% |

### The big finding

**Gemma can't finish a shogi game.** In 94% of completed games, neither side could deliver checkmate
within 400 moves. This is a completely different picture from Othello, where 95% of games reached a
natural conclusion.

Why? Shogi requires:
- Reading concrete checkmate sequences (which Gemma can't do)
- Piece coordination across promotion / drops (rules Gemma understands loosely at best)
- Converting a material advantage into a mate (needs precise move-by-move planning)

So the games just meander. Random-ish moves from both sides rarely produce a mate net within the
move cap.

### Side note: a tally bug fixed here

An earlier version of `simulate.py` used `shogi.BLACK` (= 0) as a Counter key for Quick's wins.
Because `0` is also the sentinel for a draw in `GameState.winner()`, draws were being collapsed into
the Quick-wins bucket — inflating Quick's apparent win rate to ~78%. The current code uses a string
sentinel `"draw"` to separate them, which is how we learned the real answer is "almost always a
draw."

## Performance / stability

- Avg latency: 3.7 s per call at 20-way concurrency (vs 12 s at 100-way — less load pays off)
- Retries: 503 (about 2.5% of calls needed a retry because Gemma produced an illegal move)
- Fallbacks: 19 (retries exhausted; picked first legal move)
- HTTP errors: 0 at concurrency=20 (100-way shogi had many 502/524 — endpoint saturation)
- Total API calls: ~20,400 in 65 minutes before hang

## Quick start

```bash
git clone https://github.com/yanagizawa-naoto/shogi-gemma
cd shogi-gemma
cp .env.example .env
# edit .env with your endpoint details
uv sync
uv run streamlit run app.py            # interactive viewer
uv run python simulate.py 100 20       # 100 games, concurrency 20
```

### Required environment variables

See `.env.example`:

- `GEMMA_API_KEY`
- `GEMMA_BASE_URL` (OpenAI-compatible endpoint)
- `GEMMA_MODEL`

## Files

| File | Purpose |
|---|---|
| `shogi_game.py` | Wraps `python-shogi` with GameState and kanji piece rendering |
| `agent.py` | Quick / Thinker players with JSON-schema-constrained USI output + retry |
| `app.py` | Streamlit UI (kanji board, hand pieces, LINE-style chat bubbles) |
| `simulate.py` | Async parallel tournament (balanced-sides, concurrency-limited) |

## Known limitations

- Gemma's shogi rule knowledge is weak. Drops and promotions are the most common sources of retries.
- At 100-way concurrency the endpoint deadlocks on shogi's longer prompts (9×9 board + 30-100 legal
  moves + hand pieces every turn). Use ≤20.
- A 400-move cap is used to avoid infinite games, but it's hit by almost all games — see results.

## License

MIT

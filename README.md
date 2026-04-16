# Shogi: Gemma vs Gemma

Two Gemma-backed agents playing shogi (将棋) against each other, differentiated only by prompting.
Same architecture as the [othello-gemma](https://github.com/yanagizawa-naoto/othello-gemma),
[go-gemma](https://github.com/yanagizawa-naoto/go-gemma), and
[chess-gemma](https://github.com/yanagizawa-naoto/chess-gemma) companion projects.

Built on [python-shogi](https://github.com/gunyarakun/python-shogi) for legal-move generation,
checkmate/stalemate detection, and **fourfold-repetition (千日手 sennichite) detection**.

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

## Termination rules

Shogi has no general "draw by move count" in its real ruleset. The simulator respects that:

- **Checkmate / stalemate** → the side NOT to move wins (normal finish)
- **Fourfold repetition (千日手 sennichite)** → legitimate draw per shogi rules
- **1500-move safety cap** → reported as `abandoned` (neither a real win nor a real draw — Gemma
  simply failed to finish)

The 400-move cap used in earlier runs was discarded because it wrongly classified long meandering
games as "draws" when they were really just unfinished.

## Results: 100-game balanced tournament (1500-move cap + sennichite)

50 games with Quick = sente (先手), 50 with Thinker = sente. Concurrency 20. Wall-clock: ~129 min.

| Outcome | Count | Share (of 100) |
|---|---|---|
| **🔁 Sennichite (千日手)** | **52** | **52%** |
| △ Thinker wins | 32 | 32% |
| ▲ Quick wins | 13 | 13% |
| 🚫 Abandoned (1500-move cap) | 3 | 3% |
| ⚠ Errors | 0 | 0% |

### Decisive games: Thinker dominates

**Among the 45 games that reached checkmate**, Thinker won 32 (71%) vs Quick's 13 (29%).

### The first-move advantage is decisive when combined with CoT

| Sente side | Games | Quick wins | Thinker wins | Sennichite | Abandoned |
|---|---|---|---|---|---|
| **Quick = sente** | 50 | 11 (22%) | 9 (18%) | 27 (54%) | 3 (6%) |
| **Thinker = sente** | 50 | 2 (4%) | **23 (46%)** | 25 (50%) | 0 (0%) |

When **Thinker plays sente, it wins 11× more often than Quick does**. Even as gote, Thinker still
wins more than it loses. The CoT prompt's heuristics (king safety, piece activity) amplify the
existing sente advantage massively.

### What changed from the earlier (incorrect) 400-move run

The first version of this simulator used a 400-move cap and labeled everything that hit it as a
"draw". That was wrong — real shogi doesn't draw by move count. Fixing that exposed:

1. **Real sennichite is common (52%)** — true position-repetition draws per shogi rules.
2. **Gemma CAN deliver checkmate — 45% of the time.** The earlier "94% draws" figure was an
   artifact of the short cap. With room to actually play it out, nearly half the games reach mate.
3. **Thinker's advantage is large (2.5:1)** when measured on decisive games only.

Also fixed a tally bug: `shogi.BLACK == 0` collided with the draw sentinel `0`, so draws were
being counted as Quick wins, inflating Quick's rate by ~80%. Both fixes swung the picture from
"Quick wins almost everything" to "Thinker clearly better, but half the games end in real draws."

## Performance / stability

- Total API calls: 38,170
- Avg latency: 3.69 s
- Throughput: 4.9 calls/s
- Retries: 728 (1.9%)
- Fallbacks: 20 (retries exhausted → first legal move)
- HTTP errors: 0, call timeouts: 0, game errors: 0
- Avg decisive-game length: Quick wins 451 moves, Thinker wins 213 moves
- Avg sennichite length: 390 moves

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
| `shogi_game.py` | Wraps `python-shogi` with GameState, kanji rendering, and sennichite/abandoned detection |
| `agent.py` | Quick / Thinker players with JSON-schema-constrained USI output + retry |
| `app.py` | Streamlit UI (kanji board, hand pieces, LINE-style chat bubbles) |
| `simulate.py` | Async parallel tournament with per-call timeout and balanced sente assignment |

## Known limitations

- Gemma's shogi rule knowledge is weak. Drops and promotions are the most common sources of retries.
- At 100-way concurrency the endpoint deadlocks on shogi's longer prompts (9×9 board + 30-100 legal
  moves + hand pieces every turn). Use ≤20.
- Some games are won by accidental self-forcing positions rather than by strategic planning — the
  long Quick-wins average (451 moves) suggests many Quick wins happen because Thinker wanders into
  an awkward position rather than because Quick found a real attacking plan.

## License

MIT

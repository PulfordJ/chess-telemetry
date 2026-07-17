"""Statistical core: recency weighting, focus rankings, bootstrap confidence,
and rolling-window stability.

`games` everywhere is the output of db.analyzed_games_with_moves(): a list of
{"game": ..., "moves": [user moves]} dicts, newest first.
"""

import random
from datetime import datetime
from statistics import mean

PHASES = ("opening", "middlegame", "endgame")


def recency_weights(n: int, half_life: float) -> list[float]:
    """Weight per game, index 0 = most recent."""
    return [0.5 ** (i / half_life) for i in range(n)]


def game_acpl(g: dict) -> float:
    return mean(m["cpl"] for m in g["moves"])


def blunder_count(g: dict) -> int:
    return sum(1 for m in g["moves"] if m["error_class"] == "blunder")


def eligible(m: dict, min_eval: int | None) -> bool:
    """Whether a move counts toward study-focus scoring.

    Moves played from already-lost positions are excluded: what you do at -3 is
    not the leak that cost you the game, and counting it distorts the focus
    ranking. ACPL deliberately ignores this filter to stay comparable with the
    figures Lichess/Chess.com report.
    """
    if min_eval is None:
        return True
    return m["eval_before"] is not None and m["eval_before"] >= min_eval


def phase_scores(
    games: list[dict], weights: list[float] | None = None, min_eval: int | None = None
) -> dict:
    """Average win-probability loss per user move in each phase (exposure-
    normalized, so short endgames compare fairly against long middlegames)."""
    num = dict.fromkeys(PHASES, 0.0)
    den = dict.fromkeys(PHASES, 0.0)
    for i, g in enumerate(games):
        w = weights[i] if weights else 1.0
        for m in g["moves"]:
            if not eligible(m, min_eval):
                continue
            num[m["phase"]] += w * m["winp_loss"]
            den[m["phase"]] += w
    return {p: (num[p] / den[p] if den[p] else 0.0) for p in PHASES}


def motif_scores(
    games: list[dict], weights: list[float] | None = None, min_eval: int | None = None
) -> dict:
    """Per motif: weighted share of total win-probability loss from tagged
    errors, plus raw counts."""
    losses: dict[str, float] = {}
    counts: dict[str, int] = {}
    for i, g in enumerate(games):
        w = weights[i] if weights else 1.0
        for m in g["moves"]:
            if m["motif"] and eligible(m, min_eval):
                losses[m["motif"]] = losses.get(m["motif"], 0.0) + w * m["winp_loss"]
                counts[m["motif"]] = counts.get(m["motif"], 0) + 1
    total = sum(losses.values())
    shares = {k: (v / total if total else 0.0) for k, v in losses.items()}
    return {"shares": shares, "counts": counts}


def _top(scores: dict) -> str | None:
    if not scores or not any(scores.values()):
        return None
    return max(scores, key=scores.get)


def bootstrap_top1(
    games: list[dict],
    weights: list[float],
    score_fn,
    draws: int,
    rng: random.Random,
) -> dict[str, float]:
    """Resample games with replacement; return the fraction of draws in which
    each category ranked #1. Each game keeps its own recency weight."""
    n = len(games)
    counts: dict[str, int] = {}
    for _ in range(draws):
        idx = rng.choices(range(n), k=n)
        sample = [games[i] for i in idx]
        ws = [weights[i] for i in idx]
        top = _top(score_fn(sample, ws))
        if top is not None:
            counts[top] = counts.get(top, 0) + 1
    return {k: v / draws for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def bootstrap_phase_top1(games, weights, draws, rng, min_eval=None):
    return bootstrap_top1(
        games, weights, lambda gs, ws: phase_scores(gs, ws, min_eval), draws, rng
    )


def bootstrap_motif_top1(games, weights, draws, rng, min_eval=None):
    return bootstrap_top1(
        games, weights,
        lambda gs, ws: motif_scores(gs, ws, min_eval)["shares"], draws, rng,
    )


def rolling_windows(
    games: list[dict], window: int, step: int, min_eval: int | None = None
) -> list[dict]:
    """Chronological windows (oldest to newest) with per-window headline metrics."""
    chrono = list(reversed(games))
    out = []
    for start in range(0, max(0, len(chrono) - window + 1), step):
        chunk = list(reversed(chrono[start : start + window]))  # back to newest-first
        out.append({
            "from": chunk[-1]["game"]["played_at"][:10],
            "to": chunk[0]["game"]["played_at"][:10],
            "acpl": mean(game_acpl(g) for g in chunk),
            "blunders_per_game": mean(blunder_count(g) for g in chunk),
            "top_phase": _top(phase_scores(chunk, None, min_eval)),
            "top_motif": _top(motif_scores(chunk, None, min_eval)["shares"]),
        })
    return out


def stability_summary(windows: list[dict], key: str) -> str | None:
    """'endgame in 7/9 windows' style summary for a rolling-window column."""
    vals = [w[key] for w in windows if w[key] is not None]
    if not vals:
        return None
    top = max(set(vals), key=vals.count)
    return f"{top} in {vals.count(top)}/{len(vals)} windows"


def window_span_days(games: list[dict]) -> int:
    if len(games) < 2:
        return 0
    newest = datetime.fromisoformat(games[0]["game"]["played_at"])
    oldest = datetime.fromisoformat(games[-1]["game"]["played_at"])
    return (newest - oldest).days


def days_since_last_game(games: list[dict]) -> int | None:
    if not games:
        return None
    newest = datetime.fromisoformat(games[0]["game"]["played_at"])
    return (datetime.now(tz=newest.tzinfo) - newest).days

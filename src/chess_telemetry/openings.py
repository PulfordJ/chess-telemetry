"""Opening bucketing and masters-baseline edge math for `suggest`.

Pure logic: the masters lookup is injected as a callable(board) -> dict | None
(see explorer.masters_lookup), so everything here is unit-testable offline.
"""

import io

import chess.pgn


def bucket_name(opening_name: str) -> str:
    """Opening family: 'Sicilian Defense: Najdorf ...' -> 'Sicilian Defense'."""
    return opening_name.split(":", 1)[0].strip()


def expected_score(white: int, draws: int, black: int, color: str) -> float:
    total = white + draws + black
    if total == 0:
        return 0.5
    wins = white if color == "white" else black
    return (wins + draws / 2) / total


def actual_score(result: str) -> float:
    return {"win": 1.0, "draw": 0.5, "loss": 0.0}[result]


def game_record(
    pgn: str, color: str, result: str, lookup, *,
    depth_plies: int, min_master_games: int, min_anchor_ply: int = 4,
) -> dict | None:
    """Bucket one game by its anchor position in the masters explorer.

    Replays to `depth_plies` (or the game's end if shorter), then walks back
    a ply at a time until the masters DB has at least `min_master_games` at
    the position and names the opening. Returns None if the game leaves book
    before `min_anchor_ply` — the caller counts those as unbucketed.
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return None
    board = game.board()
    moves = []
    for move in game.mainline_moves():
        board.push(move)
        moves.append(move)
        if len(moves) >= depth_plies:
            break
    while len(moves) >= min_anchor_ply:
        stats = lookup(board)
        if stats and stats["total"] >= min_master_games and stats["name"]:
            return {
                "bucket": bucket_name(stats["name"]),
                "eco": stats["eco"],
                "color": color,
                "expected": expected_score(
                    stats["white"], stats["draws"], stats["black"], color
                ),
                "actual": actual_score(result),
            }
        board.pop()
        moves.pop()
    return None


def aggregate(records) -> dict[tuple[str, str], dict]:
    """(color, bucket) -> {"n","actual","expected","delta","eco"} (means)."""
    out: dict[tuple[str, str], dict] = {}
    for r in records:
        b = out.setdefault(
            (r["color"], r["bucket"]),
            {"n": 0, "actual": 0.0, "expected": 0.0, "eco": r["eco"]},
        )
        b["n"] += 1
        b["actual"] += r["actual"]
        b["expected"] += r["expected"]
    for b in out.values():
        b["actual"] /= b["n"]
        b["expected"] /= b["n"]
        b["delta"] = b["actual"] - b["expected"]
    return out


def edges(user_agg, opp_agg, *, min_games: int, shrink_k: float) -> list[dict]:
    """Match user buckets against the opponent's opposite-color buckets.

    edge = user overperformance minus opponent overperformance vs masters;
    ranking shrinks small samples toward zero so a 2-game fluke can't outrank
    a solid 20-game trend.
    """
    flip = {"white": "black", "black": "white"}
    rows = []
    for (color, bucket), u in user_agg.items():
        o = opp_agg.get((flip[color], bucket))
        if o is None or u["n"] < min_games or o["n"] < min_games:
            continue
        edge = u["delta"] - o["delta"]
        n_eff = min(u["n"], o["n"])
        rows.append({
            "color": color,
            "bucket": bucket,
            "eco": u["eco"],
            "user_n": u["n"], "user_actual": u["actual"],
            "user_expected": u["expected"], "user_delta": u["delta"],
            "opp_n": o["n"], "opp_actual": o["actual"],
            "opp_expected": o["expected"], "opp_delta": o["delta"],
            "edge": edge,
            "shrunk": edge * (n_eff / (n_eff + shrink_k)),
            "strict": u["delta"] > 0 and o["delta"] < 0,
        })
    rows.sort(key=lambda r: -r["shrunk"])
    return rows

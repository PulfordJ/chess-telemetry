"""Fetch a user's games from the Chess.com public API (monthly archives, threaded)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from .. import db

ARCHIVES_URL = "https://api.chess.com/pub/player/{username}/games/archives"
# Chess.com returns 403 to requests without an identifying User-Agent.
HEADERS = {"User-Agent": "chess-telemetry personal analysis tool"}

DRAW_RESULTS = {
    "agreed",
    "repetition",
    "stalemate",
    "insufficient",
    "50move",
    "timevsinsufficient",
}


def fetch(conn, username: str) -> int:
    with httpx.Client(headers=HEADERS, timeout=60.0) as client:
        resp = client.get(ARCHIVES_URL.format(username=username))
        resp.raise_for_status()
        archives = resp.json()["archives"]

        def get_month(url: str) -> list[dict]:
            r = client.get(url)
            r.raise_for_status()
            return r.json().get("games", [])

        with ThreadPoolExecutor(max_workers=6) as pool:
            months = list(pool.map(get_month, archives))

    new = 0
    for games in months:
        for g in games:
            parsed = _parse(g, username)
            if parsed and db.insert_game(conn, parsed):
                new += 1
    conn.commit()
    return new


def _parse(g: dict, username: str) -> dict | None:
    if g.get("rules") != "chess" or "pgn" not in g:
        return None
    white, black = g.get("white", {}), g.get("black", {})
    if username.lower() == white.get("username", "").lower():
        color, user_side, opp_side = "white", white, black
    elif username.lower() == black.get("username", "").lower():
        color, user_side, opp_side = "black", black, white
    else:
        return None
    r = user_side.get("result", "")
    result = "win" if r == "win" else ("draw" if r in DRAW_RESULTS else "loss")
    return {
        "platform": "chesscom",
        "platform_id": g.get("uuid") or g.get("url", ""),
        "played_at": datetime.fromtimestamp(
            g["end_time"], tz=timezone.utc
        ).isoformat(),
        "time_control": g.get("time_control", ""),
        "speed": g.get("time_class"),
        "rated": int(bool(g.get("rated"))),
        "color": color,
        "result": result,
        "user_rating": user_side.get("rating"),
        "opponent_rating": opp_side.get("rating"),
        "opponent": opp_side.get("username", "unknown"),
        "pgn": g["pgn"],
    }

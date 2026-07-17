"""Stream a user's games from the Lichess export API (NDJSON, constant memory)."""

import json
import os
from datetime import datetime, timezone

import httpx

from .. import db

EXPORT_URL = "https://lichess.org/api/games/user/{username}"
PERF_TYPES = "ultraBullet,bullet,blitz,rapid,classical,correspondence"


def fetch(conn, username: str) -> int:
    headers = {"Accept": "application/x-ndjson"}
    token = os.environ.get("LICHESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "pgnInJson": "true",
        "moves": "true",
        "perfType": PERF_TYPES,
    }
    new = 0
    with httpx.Client(timeout=httpx.Timeout(30.0, read=None)) as client:
        with client.stream(
            "GET", EXPORT_URL.format(username=username), params=params, headers=headers
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                game = _parse(json.loads(line), username)
                if game and db.insert_game(conn, game):
                    new += 1
    conn.commit()
    return new


def _parse(g: dict, username: str) -> dict | None:
    if g.get("variant", "standard") != "standard":
        return None
    if g.get("status") == "aborted" or "pgn" not in g:
        return None
    players = g.get("players", {})
    white = players.get("white", {}).get("user", {}).get("name", "")
    black = players.get("black", {}).get("user", {}).get("name", "")
    if username.lower() == white.lower():
        color, opponent_side = "white", players.get("black", {})
        user_side = players.get("white", {})
        opponent = black
    elif username.lower() == black.lower():
        color, opponent_side = "black", players.get("white", {})
        user_side = players.get("black", {})
        opponent = white
    else:
        return None
    winner = g.get("winner")
    result = "draw" if winner is None else ("win" if winner == color else "loss")
    clock = g.get("clock") or {}
    tc = (
        f"{clock['initial']}+{clock['increment']}"
        if "initial" in clock
        else g.get("speed", "")
    )
    return {
        "platform": "lichess",
        "platform_id": g["id"],
        "played_at": datetime.fromtimestamp(
            g["createdAt"] / 1000, tz=timezone.utc
        ).isoformat(),
        "time_control": tc,
        "speed": g.get("speed"),
        "rated": int(bool(g.get("rated"))),
        "color": color,
        "result": result,
        "user_rating": user_side.get("rating"),
        "opponent_rating": opponent_side.get("rating"),
        "opponent": opponent or "anonymous",
        "pgn": g["pgn"],
    }

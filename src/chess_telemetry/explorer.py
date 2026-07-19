"""Lichess masters opening-explorer client with a permanent SQLite cache."""

import os
import time

import chess
import httpx

from . import db

MASTERS_URL = "https://explorer.lichess.org/masters"
# Polite pacing for the free explorer API; only applies on cache misses.
REQUEST_DELAY = 0.75
RATE_LIMIT_SLEEP = 60.0

TOKEN_HELP = (
    "The Lichess opening explorer requires an API token. Create a personal "
    "token (no scopes needed) at https://lichess.org/account/oauth/token and "
    "export it as LICHESS_TOKEN."
)


def auth_headers() -> dict:
    token = os.environ.get("LICHESS_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def masters_lookup(conn, client: httpx.Client, board: chess.Board) -> dict | None:
    """Masters stats for a position: {"white","draws","black","total","eco","name"}.

    Cache-first (positions never change in the masters DB enough to matter);
    unknown positions are cached as zero-total so they are never re-queried.
    Returns None only when the position has no master games at all.
    """
    epd = board.epd()
    row = db.get_explorer_cache(conn, epd)
    if row is None:
        row = _fetch(conn, client, board, epd)
    total = row["white"] + row["draws"] + row["black"]
    if total == 0:
        return None
    return {
        "white": row["white"],
        "draws": row["draws"],
        "black": row["black"],
        "total": total,
        "eco": row["opening_eco"],
        "name": row["opening_name"],
    }


def _fetch(conn, client, board, epd):
    params = {"fen": board.fen(), "moves": "0", "topGames": "0"}
    resp = client.get(MASTERS_URL, params=params)
    if resp.status_code == 429:
        time.sleep(RATE_LIMIT_SLEEP)
        resp = client.get(MASTERS_URL, params=params)
    resp.raise_for_status()
    data = resp.json()
    opening = data.get("opening") or {}
    db.put_explorer_cache(
        conn, epd,
        data.get("white", 0), data.get("draws", 0), data.get("black", 0),
        opening.get("eco"), opening.get("name"),
    )
    time.sleep(REQUEST_DELAY)
    return db.get_explorer_cache(conn, epd)

"""SQLite persistence: games, per-move analysis, and the position-eval cache."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    time_control TEXT,
    speed TEXT,
    rated INTEGER,
    color TEXT,
    result TEXT,
    user_rating INTEGER,
    opponent_rating INTEGER,
    opponent TEXT,
    pgn TEXT NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0,  -- 0=pending, 1=done, 2=skipped
    UNIQUE (platform, platform_id)
);
CREATE INDEX IF NOT EXISTS idx_games_played_at ON games (played_at);

-- Engine evaluation cache. Scores are from the side-to-move's perspective.
-- Exactly one of (cp, mate) is set; mate=0 means side-to-move is checkmated.
CREATE TABLE IF NOT EXISTS evals (
    epd TEXT NOT NULL,
    engine TEXT NOT NULL,
    nodes INTEGER NOT NULL,
    cp INTEGER,
    mate INTEGER,
    best_move TEXT,
    pv TEXT,
    PRIMARY KEY (epd, engine, nodes)
);

-- Per-ply analysis. Evals/CPL are from the mover's perspective, clamped to +/-1000.
CREATE TABLE IF NOT EXISTS moves (
    game_id INTEGER NOT NULL REFERENCES games (id),
    ply INTEGER NOT NULL,
    san TEXT,
    uci TEXT,
    mover TEXT NOT NULL,          -- 'user' | 'opponent'
    phase TEXT NOT NULL,          -- 'opening' | 'middlegame' | 'endgame'
    eval_before INTEGER,
    eval_after INTEGER,
    cpl INTEGER,
    winp_before REAL,
    winp_after REAL,
    winp_loss REAL,
    error_class TEXT,             -- 'ok' | 'inaccuracy' | 'mistake' | 'blunder'
    motif TEXT,
    best_move TEXT,
    PRIMARY KEY (game_id, ply)
);

-- Games of scouted opponents (for `suggest`). Kept apart from `games`:
-- that table's result/color are from the configured user's POV and it is
-- keyed (platform, platform_id) only. Here result/color are from the
-- scouted opponent's POV.
CREATE TABLE IF NOT EXISTS opponent_games (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    username TEXT NOT NULL,       -- the scouted opponent (lowercased)
    played_at TEXT NOT NULL,
    speed TEXT,
    rated INTEGER,
    color TEXT,
    result TEXT,
    rating INTEGER,
    pgn TEXT NOT NULL,
    UNIQUE (platform, platform_id, username)
);
CREATE INDEX IF NOT EXISTS idx_opp_games_user ON opponent_games (platform, username);

-- Lichess masters opening-explorer cache: one row per position, ever.
CREATE TABLE IF NOT EXISTS explorer_cache (
    epd TEXT PRIMARY KEY,
    white INTEGER NOT NULL,
    draws INTEGER NOT NULL,
    black INTEGER NOT NULL,
    opening_eco TEXT,
    opening_name TEXT,
    fetched_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    if "analyzed_at" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN analyzed_at TEXT")
        conn.commit()


def insert_game(conn: sqlite3.Connection, g: dict) -> bool:
    """Insert a game if unseen. Returns True if newly inserted."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO games
           (platform, platform_id, played_at, time_control, speed, rated,
            color, result, user_rating, opponent_rating, opponent, pgn)
           VALUES (:platform, :platform_id, :played_at, :time_control, :speed,
                   :rated, :color, :result, :user_rating, :opponent_rating,
                   :opponent, :pgn)""",
        g,
    )
    return cur.rowcount > 0


def insert_opponent_game(conn: sqlite3.Connection, g: dict) -> bool:
    """Insert a scouted opponent's game if unseen. Returns True if newly inserted.

    Accepts the same dict the fetchers produce; `g["username"]` must be set to
    the scouted opponent, and color/result/user_rating are their POV values.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO opponent_games
           (platform, platform_id, username, played_at, speed, rated,
            color, result, rating, pgn)
           VALUES (:platform, :platform_id, :username, :played_at, :speed,
                   :rated, :color, :result, :user_rating, :pgn)""",
        g,
    )
    return cur.rowcount > 0


def opponent_game_rows(conn, platform: str, username: str):
    return conn.execute(
        "SELECT * FROM opponent_games WHERE platform=? AND username=? "
        "ORDER BY played_at DESC",
        (platform, username.lower()),
    ).fetchall()


def opponent_game_count(conn, platform: str, username: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM opponent_games WHERE platform=? AND username=?",
        (platform, username.lower()),
    ).fetchone()[0]


def user_game_rows(conn):
    """All user games (both platforms), newest first, for opening stats."""
    return conn.execute(
        "SELECT platform, color, result, pgn, played_at, speed "
        "FROM games ORDER BY played_at DESC"
    ).fetchall()


def get_explorer_cache(conn, epd: str):
    return conn.execute(
        "SELECT white, draws, black, opening_eco, opening_name "
        "FROM explorer_cache WHERE epd=?",
        (epd,),
    ).fetchone()


def put_explorer_cache(conn, epd, white, draws, black, eco, name):
    conn.execute(
        "INSERT OR IGNORE INTO explorer_cache "
        "(epd, white, draws, black, opening_eco, opening_name, fetched_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (epd, white, draws, black, eco, name,
         datetime.now(timezone.utc).isoformat()),
    )
    # Commit per position so an interrupted first run keeps its progress.
    conn.commit()


def get_cached_eval(conn, epd: str, engine: str, nodes: int):
    return conn.execute(
        "SELECT cp, mate, best_move, pv FROM evals WHERE epd=? AND engine=? AND nodes=?",
        (epd, engine, nodes),
    ).fetchone()


def put_cached_eval(conn, epd, engine, nodes, cp, mate, best_move, pv):
    conn.execute(
        "INSERT OR IGNORE INTO evals (epd, engine, nodes, cp, mate, best_move, pv) VALUES (?,?,?,?,?,?,?)",
        (epd, engine, nodes, cp, mate, best_move, pv),
    )


def unanalyzed_games(conn, limit: int):
    return conn.execute(
        "SELECT * FROM games WHERE analyzed=0 ORDER BY played_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def analyzed_game_rows(conn, limit: int | None = None):
    q = "SELECT * FROM games WHERE analyzed=1 ORDER BY played_at DESC"
    if limit:
        return conn.execute(q + " LIMIT ?", (limit,)).fetchall()
    return conn.execute(q).fetchall()


def eval_signature(conn):
    """The (engine, nodes) the cache was written with, for cache-only re-tag."""
    return conn.execute(
        "SELECT engine, nodes, COUNT(*) FROM evals GROUP BY engine, nodes "
        "ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()


def get_eval_pv(conn, epd: str):
    """Cached principal variation for a position, engine/node agnostic."""
    return conn.execute(
        "SELECT best_move, pv FROM evals WHERE epd=? AND pv IS NOT NULL LIMIT 1",
        (epd,),
    ).fetchone()


def error_moves(
    conn, error_classes, motifs, speeds, limit,
    min_winp_loss=0.0, min_eval_before=None,
):
    """User mistakes/blunders joined to their game, newest first, for drills.

    `min_winp_loss` drops positions where the mistake barely cost anything.
    `min_eval_before` drops positions that were already lost before the move
    (eval is centipawns from the user's perspective) — nothing worth drilling.
    """
    where = ["m.mover='user'", "m.winp_loss >= ?"]
    params: list = [min_winp_loss]
    if min_eval_before is not None:
        where.append("m.eval_before >= ?")
        params.append(min_eval_before)
    where.append(f"m.error_class IN ({','.join('?' * len(error_classes))})")
    params += error_classes
    if motifs:
        where.append(f"m.motif IN ({','.join('?' * len(motifs))})")
        params += motifs
    if speeds:
        where.append(f"g.speed IN ({','.join('?' * len(speeds))})")
        params += speeds
    params.append(limit)
    return conn.execute(
        f"""SELECT m.game_id, m.ply, m.san, m.cpl, m.winp_loss, m.motif, m.best_move,
                   g.pgn, g.platform, g.played_at, g.opponent, g.speed, g.color
            FROM moves m JOIN games g ON g.id = m.game_id
            WHERE {' AND '.join(where)}
            ORDER BY g.played_at DESC, m.ply
            LIMIT ?""",
        params,
    ).fetchall()


def save_move_rows(conn, game_id: int, rows: list[dict], status: int = 1):
    conn.execute("DELETE FROM moves WHERE game_id=?", (game_id,))
    conn.executemany(
        """INSERT INTO moves
           (game_id, ply, san, uci, mover, phase, eval_before, eval_after, cpl,
            winp_before, winp_after, winp_loss, error_class, motif, best_move)
           VALUES (:game_id, :ply, :san, :uci, :mover, :phase, :eval_before,
                   :eval_after, :cpl, :winp_before, :winp_after, :winp_loss,
                   :error_class, :motif, :best_move)""",
        rows,
    )
    conn.execute(
        "UPDATE games SET analyzed=?, analyzed_at=? WHERE id=?",
        (status, datetime.now(timezone.utc).isoformat(), game_id),
    )
    conn.commit()


def analyzed_games_with_moves(conn) -> list[dict]:
    """All analyzed games, newest first, each with its user-move analysis rows."""
    games = conn.execute(
        "SELECT * FROM games WHERE analyzed=1 ORDER BY played_at DESC"
    ).fetchall()
    out = []
    for g in games:
        moves = conn.execute(
            "SELECT * FROM moves WHERE game_id=? AND mover='user' ORDER BY ply",
            (g["id"],),
        ).fetchall()
        if moves:
            out.append({"game": dict(g), "moves": [dict(m) for m in moves]})
    return out

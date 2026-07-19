"""Opponent-specific opening suggestions: openings where you beat the masters
baseline and the scouted opponent falls short of it."""

import functools

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from . import db, explorer, openings
from .fetch import chesscom, lichess

DEFAULTS = {
    "depth_plies": 10,
    "min_master_games": 100,
    "min_anchor_ply": 1,
    "min_games": 5,
    "top": 10,
    "shrink_k": 5,
    "opponent_max_games": 500,
    "opponent_months": 12,
}


def run_suggest(conn, cfg: dict, args) -> None:
    console = Console()
    s = {**DEFAULTS, **cfg.get("suggest", {})}
    min_games = args.min_games or s["min_games"]
    top = args.top or s["top"]
    speeds = [x.strip() for x in args.speed.split(",")] if args.speed else None
    opponent = args.opponent.lower()

    if conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0:
        console.print("[red]No games in the database — run `fetch` first.[/red]")
        return

    _fetch_opponent(conn, console, args.platform, opponent, s, args.refresh)
    opp_rows = db.opponent_game_rows(conn, args.platform, opponent)
    if not opp_rows:
        console.print(f"[red]No games found for {opponent} on {args.platform}.[/red]")
        return

    user_rows = db.user_game_rows(conn)
    if speeds:
        user_rows = [r for r in user_rows if r["speed"] in speeds]
        opp_rows = [r for r in opp_rows if r["speed"] in speeds]
    user_rows = filter_repertoire(console, user_rows, cfg, args)

    with httpx.Client(timeout=30.0, headers=explorer.auth_headers()) as client:
        lookup = functools.partial(explorer.masters_lookup, conn, client)
        try:
            user_recs, user_skipped = _records(
                console, "your games", user_rows, lookup, s
            )
            opp_recs, opp_skipped = _records(
                console, f"{opponent}'s games", opp_rows, lookup, s
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                console.print(f"[red]{explorer.TOKEN_HELP}[/red]")
                return
            raise

    rows = openings.edges(
        openings.aggregate(user_recs), openings.aggregate(opp_recs),
        min_games=min_games, shrink_k=s["shrink_k"],
    )
    if args.color:
        rows = [r for r in rows if r["color"] == args.color]

    for color, label in (
        ("white", f"As White (vs {opponent}'s Black)"),
        ("black", f"As Black (vs {opponent}'s White)"),
    ):
        if args.color and color != args.color:
            continue
        _table(console, label, [r for r in rows if r["color"] == color], top)

    console.print(Panel(
        f"Edge = your score above the masters expected score, minus {opponent}'s. "
        "Green rows: you overperform AND they underperform. Ranking shrinks "
        f"small samples (k={s['shrink_k']}); trust the n columns over the deltas.\n"
        f"Unbucketed (out of book / thin masters data): "
        f"{user_skipped} of your games, {opp_skipped} of theirs.",
        title="How to read this", expand=False,
    ))


def filter_repertoire(console, rows, cfg, args):
    """Drop the user's games that deviate from the configured repertoire."""
    if getattr(args, "all_lines", False):
        return rows
    rep = openings.repertoire_from_config(cfg)
    if not any(rep.values()):
        return rows
    kept = []
    for r in rows:
        lines = rep[r["color"]]
        limit = max((len(line) for line in lines), default=0)
        if openings.matches_repertoire(
            openings.leading_sans(r["pgn"], limit), r["color"], lines
        ):
            kept.append(r)
    console.print(
        f"[dim]Repertoire filter: {len(kept)} of {len(rows)} of your games "
        "match config [repertoire] (--all-lines to disable).[/dim]"
    )
    return kept


def _fetch_opponent(conn, console, platform, opponent, s, refresh):
    stored = db.opponent_game_count(conn, platform, opponent)
    if stored and not refresh:
        console.print(
            f"[dim]{stored} stored games for {opponent} ({platform}) — "
            "use --refresh to re-fetch.[/dim]"
        )
        return
    console.print(f"Fetching {opponent}'s games from {platform}…")
    insert = lambda c, g: db.insert_opponent_game(c, {**g, "username": opponent})
    if platform == "lichess":
        new = lichess.fetch(
            conn, opponent, insert=insert, max_games=s["opponent_max_games"]
        )
    else:
        new = chesscom.fetch(conn, opponent, insert=insert, months=s["opponent_months"])
    total = db.opponent_game_count(conn, platform, opponent)
    console.print(f"[green]{new} new games[/green] ({total} stored)")


def _records(console, label, rows, lookup, s):
    """Bucket each game via the masters explorer; returns (records, skipped)."""
    recs, skipped = [], 0
    # ETA speeds up sharply once the run reaches already-cached positions;
    # the wide speed window keeps it from swinging on every cache hit.
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
        transient=True,
        speed_estimate_period=120,
    ) as progress:
        task = progress.add_task(f"Bucketing {label}…", total=len(rows))
        for r in rows:
            rec = openings.game_record(
                r["pgn"], r["color"], r["result"], lookup,
                depth_plies=s["depth_plies"],
                min_master_games=s["min_master_games"],
                min_anchor_ply=s["min_anchor_ply"],
            )
            if rec:
                recs.append(rec)
            else:
                skipped += 1
            progress.advance(task)
    return recs, skipped


def _table(console, title, rows, top):
    if not rows:
        console.print(f"[dim]{title}: no openings with enough games on both sides.[/dim]")
        return
    t = Table(title=title)
    t.add_column("Opening")
    t.add_column("ECO")
    t.add_column("You n", justify="right")
    t.add_column("You score", justify="right")
    t.add_column("You Δ", justify="right")
    t.add_column("Opp n", justify="right")
    t.add_column("Opp score", justify="right")
    t.add_column("Opp Δ", justify="right")
    t.add_column("Edge", justify="right")
    shown = [r for r in rows if r["edge"] > 0][:top]
    if not shown:
        console.print(f"[dim]{title}: no openings with a positive edge.[/dim]")
        return
    for r in shown:
        style = "green" if r["strict"] else None
        t.add_row(
            r["bucket"], r["eco"] or "—",
            str(r["user_n"]), f"{r['user_actual']:.0%}", f"{r['user_delta']:+.2f}",
            str(r["opp_n"]), f"{r['opp_actual']:.0%}", f"{r['opp_delta']:+.2f}",
            f"{r['edge']:+.2f}",
            style=style,
        )
    console.print(t)

"""Opening-prep report: rank your own openings vs the masters baseline to
show where prep time is best spent — weakest first."""

import functools
from math import sqrt

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db, explorer, openings
from .suggest import DEFAULTS, _records


def run_prep(conn, cfg: dict, args) -> None:
    console = Console()
    s = {**DEFAULTS, **cfg.get("suggest", {})}
    min_games = args.min_games or s["min_games"]
    speeds = [x.strip() for x in args.speed.split(",")] if args.speed else None

    rows = db.user_game_rows(conn)
    if speeds:
        rows = [r for r in rows if r["speed"] in speeds]
    if not rows:
        console.print("[red]No games in the database — run `fetch` first.[/red]")
        return

    with httpx.Client(timeout=30.0, headers=explorer.auth_headers()) as client:
        lookup = functools.partial(explorer.masters_lookup, conn, client)
        try:
            recs, skipped = _records(console, "your games", rows, lookup, s)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                console.print(f"[red]{explorer.TOKEN_HELP}[/red]")
                return
            raise

    by_family = openings.aggregate(recs)
    # Same aggregation keyed by the game's first move instead of the family.
    by_first = openings.aggregate([{**r, "bucket": r["first"]} for r in recs])

    for color in ("white", "black"):
        if args.color and color != args.color:
            continue
        prefix = "1." if color == "white" else "vs 1."
        title = (
            "As White — by your first move" if color == "white"
            else "As Black — by White's first move"
        )
        _table(console, title, "First move",
               _rows(by_first, color, min_games, prefix))
        title = f"As {color.capitalize()} — opening families, weakest first"
        _table(console, title, "Opening",
               _rows(by_family, color, min_games), eco=True)

    console.print(Panel(
        "Δ = your score minus the masters expected score from the same "
        "positions; the most negative rows are your best prep targets. "
        "± is one standard error — a Δ inside its ± band is noise. "
        f"Rows need at least {min_games} games (--min-games). "
        f"Unbucketed games: {skipped}.",
        title="How to read this", expand=False,
    ))


def _rows(agg, color: str, min_games: int, prefix: str = ""):
    out = []
    for (c, bucket), b in agg.items():
        if c != color or b["n"] < min_games:
            continue
        se = sqrt(b["actual"] * (1 - b["actual"]) / b["n"])
        out.append({**b, "label": f"{prefix}{bucket}", "se": se})
    out.sort(key=lambda r: r["delta"])
    return out


def _table(console, title, label_col, rows, eco=False):
    if not rows:
        console.print(f"[dim]{title}: no rows with enough games.[/dim]")
        return
    t = Table(title=title)
    t.add_column(label_col)
    if eco:
        t.add_column("ECO")
    t.add_column("n", justify="right")
    t.add_column("Score", justify="right")
    t.add_column("Masters", justify="right")
    t.add_column("Δ", justify="right")
    t.add_column("±", justify="right")
    for r in rows:
        style = "red" if r["delta"] < -r["se"] else (
            "green" if r["delta"] > r["se"] else None
        )
        cells = [r["label"]]
        if eco:
            cells.append(r["eco"] or "—")
        cells += [
            str(r["n"]), f"{r['actual']:.0%}", f"{r['expected']:.0%}",
            f"{r['delta']:+.2f}", f"{r['se']:.2f}",
        ]
        t.add_row(*cells, style=style)
    console.print(t)

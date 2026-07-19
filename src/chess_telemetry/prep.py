"""Opening-prep report: your openings as a move tree scored against the
masters baseline, so nested lines (Ruy Lopez under 2.Nf3, etc.) stay nested
and the weakest branches stand out."""

import functools
from math import sqrt

import chess
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

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
            white = [r for r in recs if r["color"] == "white"]
            black = [r for r in recs if r["color"] == "black"]
            if args.color != "black":
                _summary(console, "As White — by your first move", white,
                         lambda r: f"1.{r['sans'][0]}", min_games)
            if args.color != "white":
                _summary(console, "As Black — by White's first move", black,
                         lambda r: f"vs 1.{r['sans'][0]}", min_games)
                _summary(console, "As Black — by your reply", black,
                         lambda r: f"1.{r['sans'][0]} {r['sans'][1]}"
                         if len(r["sans"]) > 1 else None, min_games)
            for color, colored in (("white", white), ("black", black)):
                if args.color and color != args.color:
                    continue
                root = openings.move_tree(colored, min_games=min_games)
                label = f"As {color.capitalize()}"
                if args.tree:
                    _render(console, f"{label} — your move tree",
                            root, lookup, min_games)
                else:
                    lines = openings.notable_lines(root)
                    _lines_table(
                        console, f"{label} — weak lines (prep targets)",
                        [l for l in lines if l["delta"] < 0], lookup, "red",
                    )
                    _lines_table(
                        console, f"{label} — strong lines",
                        sorted((l for l in lines if l["delta"] > 0),
                               key=lambda l: -l["delta"]),
                        lookup, "green",
                    )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                console.print(f"[red]{explorer.TOKEN_HELP}[/red]")
                return
            raise

    console.print(Panel(
        "Δ = your score minus the masters expected score; ± is one standard "
        "error. Only lines whose Δ clears the ± band are listed, and a line "
        "is omitted when a shorter line already tells the same story. "
        f"Lines need at least {min_games} games (--min-games); use --tree "
        "for the full move tree. "
        f"Unbucketed games: {skipped}.",
        title="How to read this", expand=False,
    ))


def _summary(console, title, recs, keyfn, min_games):
    groups: dict[str, dict] = {}
    for r in recs:
        key = keyfn(r)
        if key is None:
            continue
        g = groups.setdefault(key, {"n": 0, "actual": 0.0, "expected": 0.0})
        g["n"] += 1
        g["actual"] += r["actual"]
        g["expected"] += r["expected"]
    rows = []
    for key, g in groups.items():
        if g["n"] < min_games:
            continue
        actual, expected = g["actual"] / g["n"], g["expected"] / g["n"]
        rows.append({
            "label": key, "n": g["n"], "actual": actual, "expected": expected,
            "delta": actual - expected,
            "se": sqrt(actual * (1 - actual) / g["n"]),
        })
    if not rows:
        return
    rows.sort(key=lambda r: r["delta"])
    t = Table(title=title)
    t.add_column("Moves")
    t.add_column("n", justify="right")
    t.add_column("Score", justify="right")
    t.add_column("Masters", justify="right")
    t.add_column("Δ", justify="right")
    t.add_column("±", justify="right")
    for r in rows:
        style = "red" if r["delta"] < -r["se"] else (
            "green" if r["delta"] > r["se"] else None
        )
        t.add_row(
            r["label"], str(r["n"]), f"{r['actual']:.0%}", f"{r['expected']:.0%}",
            f"{r['delta']:+.2f}", f"{r['se']:.2f}", style=style,
        )
    console.print(t)


def _lines_table(console, title, lines, lookup, style):
    if not lines:
        console.print(f"[dim]{title}: none beyond the noise band.[/dim]")
        return
    t = Table(title=title)
    t.add_column("Line")
    t.add_column("Opening")
    t.add_column("n", justify="right")
    t.add_column("Score", justify="right")
    t.add_column("Masters", justify="right")
    t.add_column("Δ", justify="right")
    t.add_column("±", justify="right")
    for l in lines:
        board = chess.Board()
        parts = []
        for i, san in enumerate(l["sans"]):
            _push(parts, i, san)
            board.push_san(san)
        stats = lookup(board)
        name = stats["name"] if stats and stats["name"] else "—"
        t.add_row(
            " ".join(parts), name, str(l["n"]), f"{l['actual']:.0%}",
            f"{l['expected']:.0%}", f"{l['delta']:+.2f}", f"{l['se']:.2f}",
            style=style,
        )
    console.print(t)


def _render(console, title, root, lookup, min_games):
    if not root["n"]:
        console.print(f"[dim]{title}: no games.[/dim]")
        return
    tree = Tree(f"[bold]{title}[/bold]  ({root['n']} games)")
    _add_children(tree, root, chess.Board(), 0, lookup)
    console.print(tree)


def _add_children(branch, node, board, ply, lookup):
    for san, child in sorted(
        node["children"].items(), key=lambda kv: kv[1]["delta"]
    ):
        line_board = board.copy()
        parts, at = [], ply
        _push(parts, at, san)
        line_board.push_san(san)
        at += 1
        # Collapse forced chains: while every game continues with one reply,
        # show the sequence as a single line instead of one level per ply.
        while len(child["children"]) == 1:
            (next_san, next_child), = child["children"].items()
            if next_child["n"] != child["n"]:
                break
            _push(parts, at, next_san)
            line_board.push_san(next_san)
            child = next_child
            at += 1
        sub = branch.add(_label(" ".join(parts), child, line_board, lookup))
        _add_children(sub, child, line_board, at, lookup)


def _push(parts, ply, san):
    if ply % 2 == 0:
        parts.append(f"{ply // 2 + 1}.{san}")
    elif not parts:
        parts.append(f"{ply // 2 + 1}...{san}")
    else:
        parts.append(san)


def _label(moves, node, board, lookup) -> str:
    se = sqrt(node["actual"] * (1 - node["actual"]) / node["n"])
    if node["delta"] < -se:
        style = "red"
    elif node["delta"] > se:
        style = "green"
    else:
        style = "default"
    stats = lookup(board)
    name = f"  [dim]{stats['name']}[/dim]" if stats and stats["name"] else ""
    return (
        f"[{style}]{moves}[/{style}]  "
        f"n={node['n']} {node['actual']:.0%} vs {node['expected']:.0%} "
        f"[{style}]Δ{node['delta']:+.2f}[/{style}]±{se:.2f}{name}"
    )

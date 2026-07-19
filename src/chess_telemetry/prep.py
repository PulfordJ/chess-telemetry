"""Opening-prep report: your openings as a move tree scored against the
masters baseline, so nested lines (Ruy Lopez under 2.Nf3, etc.) stay nested
and the weakest branches stand out."""

import functools
from math import sqrt

import chess
import httpx
from rich.console import Console
from rich.panel import Panel
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
            for color, title in (
                ("white", "As White — your move tree"),
                ("black", "As Black — your move tree"),
            ):
                if args.color and color != args.color:
                    continue
                root = openings.move_tree(
                    [r for r in recs if r["color"] == color], min_games=min_games
                )
                _render(console, title, root, lookup, min_games)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                console.print(f"[red]{explorer.TOKEN_HELP}[/red]")
                return
            raise

    console.print(Panel(
        "Each line aggregates every game that reached it, deeper lines are "
        "subsets of their parent. Δ = your score minus the masters expected "
        "score; ± is one standard error — a Δ inside its ± band is noise. "
        "Red = weak (prep target), green = strength. Branches with fewer "
        f"than {min_games} games (--min-games) are folded into their parent. "
        f"Unbucketed games: {skipped}.",
        title="How to read this", expand=False,
    ))


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

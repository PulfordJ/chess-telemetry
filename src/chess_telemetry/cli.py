"""CLI entry point: chess-telemetry {fetch|analyze|report}."""

import argparse
import os
import signal
import sys
import time
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from rich.console import Console

from . import analyze as analyze_mod
from . import db, drills, prep, report, suggest
from .engine import CacheOnlyEngine, Engine
from .fetch import chesscom, lichess

console = Console()


def load_config(path: str) -> tuple[dict, Path]:
    p = Path(path).resolve()
    if not p.exists():
        console.print(f"[red]Config not found: {p}[/red]")
        sys.exit(1)
    with open(p, "rb") as f:
        return tomllib.load(f), p.parent


def account_names(value) -> list[str]:
    """Normalize an [accounts] entry to a list of usernames.

    Accepts a single string (legacy), a list of strings, or None/"" (skip).
    """
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [v.strip() for v in value if v and v.strip()]


def cmd_fetch(conn, cfg):
    accounts = cfg["accounts"]
    total = 0
    for platform, fetcher in (("lichess", lichess), ("chesscom", chesscom)):
        usernames = account_names(accounts.get(platform))
        if not usernames:
            console.print(f"[dim]{platform}: no username configured, skipping[/dim]")
            continue
        for username in usernames:
            console.print(f"{platform}: fetching games for [bold]{username}[/bold]…")
            try:
                new = fetcher.fetch(conn, username)
                console.print(f"{platform}: [green]{new} new games[/green]")
                total += new
            except Exception as e:
                console.print(f"[red]{platform}: fetch failed for {username}: {e}[/red]")
    count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    console.print(f"Done. {total} new games this run, {count} total in database.")


def _fmt_eta(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def cmd_retag(conn, cfg):
    """Recompute motif tags on already-analyzed games using cached evals only —
    no Stockfish. Use after changing motif/classification logic."""
    sig = db.eval_signature(conn)
    if sig is None:
        console.print("[red]No cached evals — run `analyze` first.[/red]")
        return
    engine = CacheOnlyEngine(sig["engine"], sig["nodes"])
    games = db.analyzed_game_rows(conn)
    console.print(f"Re-tagging {len(games)} analyzed games from cache…")
    done = 0
    for row in games:
        try:
            analyze_mod.analyze_game(conn, engine, row, cfg)
            done += 1
        except RuntimeError as e:
            console.print(f"  [yellow]skipped game {row['id']}: {e}[/yellow]")
    console.print(f"Re-tagged {done} games.")


def cmd_analyze(conn, cfg, limit: int | None):
    limit = limit or cfg["analysis"]["backfill_limit"]
    pending = db.unanalyzed_games(conn, limit)
    if not pending:
        console.print("Nothing to analyze — all fetched games are done.")
        return
    e = cfg["engine"]
    engine = Engine(e["nodes"], e.get("threads", 4), e.get("hash_mb", 256))
    console.print(
        f"Analyzing {len(pending)} games with {engine.name} "
        f"({e['nodes']:,} nodes/position)…\n"
        "[dim]Pause anytime with Ctrl+C (or kill the process) — progress is "
        "saved per game; rerun `analyze` to resume.[/dim]"
    )
    # SIGTERM behaves like Ctrl+C so a plain `kill` also pauses cleanly.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    done = skipped = 0
    times: deque[float] = deque(maxlen=10)
    try:
        for row in pending:
            t0 = time.monotonic()
            ok = analyze_mod.analyze_game(conn, engine, row, cfg)
            if ok:
                done += 1
                times.append(time.monotonic() - t0)
            else:
                skipped += 1
            remaining = len(pending) - done - skipped
            eta = f", ETA {_fmt_eta(remaining * mean(times))}" if times and remaining else ""
            console.print(
                f"  [{done + skipped}/{len(pending)}] {row['played_at'][:10]} "
                f"{row['platform']} vs {row['opponent']} "
                f"({'ok' if ok else 'skipped'}, {time.monotonic() - t0:.0f}s{eta})"
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Paused — progress is saved; rerun `analyze` to resume.[/yellow]")
    finally:
        engine.close()
    console.print(f"Analyzed {done}, skipped {skipped}.")


def cmd_status(conn, cfg):
    limit = cfg["analysis"]["backfill_limit"]
    total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    analyzed = conn.execute("SELECT COUNT(*) FROM games WHERE analyzed=1").fetchone()[0]
    skipped = conn.execute("SELECT COUNT(*) FROM games WHERE analyzed=2").fetchone()[0]
    pending = len(db.unanalyzed_games(conn, limit))
    console.print(
        f"Games: {total} fetched, [green]{analyzed} analyzed[/green], "
        f"{skipped} skipped, [yellow]{pending} pending[/yellow] "
        f"(backfill cap {limit})"
    )
    stamps = [
        datetime.fromisoformat(r[0])
        for r in conn.execute(
            "SELECT analyzed_at FROM games WHERE analyzed_at IS NOT NULL "
            "ORDER BY analyzed_at DESC LIMIT 11"
        )
    ]
    if len(stamps) >= 2:
        # Gaps between consecutive completions; ignore pauses between runs.
        deltas = [
            (a - b).total_seconds()
            for a, b in zip(stamps, stamps[1:])
            if (a - b).total_seconds() < 600
        ]
        if deltas:
            rate = median(deltas)
            age = (datetime.now(timezone.utc) - stamps[0]).total_seconds()
            running = age < max(3 * rate, 120)
            console.print(
                f"Recent pace: {rate:.0f}s/game "
                f"(last game finished {_fmt_eta(age)} ago"
                f"{'' if running else ' — analysis looks paused'})"
            )
            if pending:
                console.print(f"ETA for remaining {pending}: ~{_fmt_eta(pending * rate)}")
            return
    if pending:
        console.print("No recent pace data — start `analyze` and check again.")


def cmd_drills(conn, cfg, args):
    error_classes = ["blunder"] if args.blunders_only else ["blunder", "mistake"]
    motifs = [m.strip() for m in args.motif.split(",")] if args.motif else None
    speeds = [s.strip() for s in args.speed.split(",")] if args.speed else None
    puzzles = drills.build_puzzles(
        conn, error_classes, motifs, speeds, args.limit,
        args.min_winp_loss, args.min_eval,
    )
    if not puzzles:
        console.print(
            "[yellow]No puzzles matched. Analyze more games, or loosen "
            "--motif/--speed/--blunders-only.[/yellow]"
        )
        return
    out = Path(args.output).resolve()
    out.write_text(drills.write_pgn(puzzles) + "\n")
    by_motif: dict[str, int] = {}
    for g in puzzles:
        key = g.headers["Event"].removeprefix("Drill: ")
        by_motif[key] = by_motif.get(key, 0) + 1
    breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(by_motif.items(), key=lambda kv: -kv[1]))
    console.print(f"[green]Wrote {len(puzzles)} puzzles[/green] to {out}")
    console.print(f"  {breakdown}")
    console.print(
        "  Import into a Lichess study (Study → ⋮ → Import PGN) or any PGN trainer. "
        "Each puzzle starts on the move you got wrong; the mainline is the engine's answer."
    )


def main():
    parser = argparse.ArgumentParser(prog="chess-telemetry")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="Pull new games from Lichess and Chess.com")
    p_analyze = sub.add_parser("analyze", help="Run engine analysis on fetched games")
    p_analyze.add_argument("--limit", type=int, help="Max games to analyze this run")
    p_analyze.add_argument(
        "--retag", action="store_true",
        help="Recompute motif tags on analyzed games from cache (no engine)",
    )
    p_report = sub.add_parser("report", help="Print the study-focus report")
    p_report.add_argument("--window", type=int, help="Headline window size override")
    p_report.add_argument("--speed", help="Filter to time control(s), e.g. rapid,blitz")
    p_report.add_argument(
        "--min-eval", type=int,
        help="Ignore moves from positions worse than this (centipawns, e.g. -300). "
             "Defaults to report.min_eval in config; pass a large negative to disable",
    )
    sub.add_parser("status", help="Show analysis progress and ETA")
    p_drills = sub.add_parser("drills", help="Export your blunders as a puzzle PGN")
    p_drills.add_argument("--motif", help="Comma-separated motifs, e.g. hung_piece,allowed_fork")
    p_drills.add_argument("--speed", help="Comma-separated speeds, e.g. rapid,blitz")
    p_drills.add_argument("--limit", type=int, default=60, help="Max puzzles (default 60)")
    p_drills.add_argument("--blunders-only", action="store_true", help="Exclude mistakes")
    p_drills.add_argument(
        "--min-winp-loss", type=float, default=10.0,
        help="Skip mistakes that cost less than this %% win probability (default 10)",
    )
    p_drills.add_argument(
        "--min-eval", type=int, default=-300,
        help="Skip positions you were already losing by more than this "
             "(centipawns, default -300 = 3 pawns down)",
    )
    p_drills.add_argument("--output", default="data/drills.pgn", help="Output PGN path")
    p_suggest = sub.add_parser(
        "suggest", help="Suggest openings to aim for against a specific opponent"
    )
    p_suggest.add_argument("--opponent", required=True, help="Opponent username")
    p_suggest.add_argument(
        "--platform", required=True, choices=["lichess", "chesscom"],
        help="Platform the opponent plays on",
    )
    p_suggest.add_argument("--color", choices=["white", "black"], help="Only one color")
    p_suggest.add_argument(
        "--min-games", type=int,
        help="Minimum games per opening for both you and the opponent",
    )
    p_suggest.add_argument("--top", type=int, help="Max openings per table")
    p_suggest.add_argument("--speed", help="Comma-separated speeds, e.g. rapid,blitz")
    p_suggest.add_argument(
        "--refresh", action="store_true", help="Re-fetch the opponent's games"
    )
    p_prep = sub.add_parser(
        "prep", help="Rank your own openings vs the masters baseline (prep targets)"
    )
    p_prep.add_argument("--color", choices=["white", "black"], help="Only one color")
    p_prep.add_argument("--min-games", type=int, help="Minimum games per row")
    p_prep.add_argument("--speed", help="Comma-separated speeds, e.g. rapid,blitz")
    p_prep.add_argument(
        "--tree", action="store_true",
        help="Show the full move tree instead of only notable lines",
    )
    args = parser.parse_args()

    cfg, root = load_config(args.config)
    # Token from git-ignored config, unless already exported in the shell.
    token = cfg.get("accounts", {}).get("lichess_token")
    if token and not os.environ.get("LICHESS_TOKEN"):
        os.environ["LICHESS_TOKEN"] = token
    conn = db.connect(root / "data" / "telemetry.db")
    try:
        if args.cmd == "fetch":
            cmd_fetch(conn, cfg)
        elif args.cmd == "analyze":
            if args.retag:
                cmd_retag(conn, cfg)
            else:
                cmd_analyze(conn, cfg, args.limit)
        elif args.cmd == "report":
            speeds = [s.strip() for s in args.speed.split(",")] if args.speed else None
            report.build_report(conn, cfg, args.window, speeds, args.min_eval)
        elif args.cmd == "status":
            cmd_status(conn, cfg)
        elif args.cmd == "drills":
            cmd_drills(conn, cfg, args)
        elif args.cmd == "suggest":
            suggest.run_suggest(conn, cfg, args)
        elif args.cmd == "prep":
            prep.run_prep(conn, cfg, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""Terminal report: headline focus areas with bootstrap confidence, accuracy
splits, motif breakdown, and rolling-window stability."""

import random
from collections import defaultdict
from statistics import mean

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db, stats

STUDY_ADVICE = {
    "opening": "You bleed evaluation early. Build a minimal repertoire (one system per color) and review the first 10 moves of every loss.",
    "middlegame": "Middlegame decision-making is the leak: study annotated master games and positional puzzle sets, not just tactics.",
    "endgame": "Endgame technique is the leak: prioritize theoretical endings (e.g. '100 Endgames You Must Know') and rook-ending drills.",
    "hung_piece": "Board vision: you leave or move pieces onto attacked squares. Enforce a pre-move blunder check (checks, captures, threats).",
    "allowed_fork": "Defensive tactics: you allow forks. Drill fork-themed puzzles from the *defending* side and scan opponent knight/pawn forks before moving.",
    "allowed_pin": "You walk into pins. Scan for enemy sliding pieces sharing a line with your king/queen before committing a move.",
    "allowed_skewer": "You allow skewers. Watch alignments of your high-value pieces on open lines.",
    "allowed_discovered": "You allow discovered attacks. Check what every enemy piece unmasks before you commit.",
    "missed_capture": "You miss winning captures. Slow down on forcing moves: checks, captures, threats — in that order.",
    "missed_fork": "You miss fork opportunities. Woodpecker-method repetition on fork puzzle sets will convert this fastest.",
    "missed_pin": "You miss pins. Add pin-themed puzzle sets to your tactics rotation.",
    "missed_skewer": "You miss skewers. Add skewer-themed puzzle sets to your tactics rotation.",
    "missed_discovered": "You miss discovered attacks. Practice puzzles tagged 'discovered attack'.",
    "missed_mate": "You miss forced mates. Do daily mate-in-2/3 drills to sharpen conversion.",
    "allowed_mate": "King safety: you walk into mating attacks. Before every move run checklist step 2 — scan checks against your own king and count attackers vs defenders around it.",
    "missed_skewer": "You miss skewers. Add skewer-themed puzzle sets to your tactics rotation.",
    "endgame_technique": "Endgame technique: you misplay technically won/drawn endings (king activity, opposition, pawn races). Drill king-and-pawn and rook endings.",
    "other": "Errors are mostly raw calculation slips rather than a single motif — deep-calculation training (longer puzzles, no-board visualization) fits best.",
}

# Maps each motif to a step of John's over-the-board checklist, so the report
# reports which step of his own process is failing most.
CHECKLIST = {
    "allowed_mate":       "2. Is my king safe?",
    "missed_mate":        "1. Is my opponent's king safe?",
    "hung_piece":         "5. What is hanging / en prise?",
    "allowed_fork":       "5. Threats — double attacks (defending)",
    "missed_fork":        "5. Threats — double attacks (attacking)",
    "allowed_pin":        "5.2 Pins (defending)",
    "missed_pin":         "5.2 Pins (attacking)",
    "allowed_skewer":     "5.1 Skewers (defending)",
    "missed_skewer":      "5.1 Skewers (attacking)",
    "allowed_discovered": "5.4 Discovered attacks (defending)",
    "missed_discovered":  "5.4 Discovered attacks (attacking)",
    "missed_capture":     "5. Win material / hanging (attacking)",
    "endgame_technique":  "6.3 Endgame technique",
    "other":              "Positional / uncategorized",
}


def build_report(
    conn, cfg: dict, window_override: int | None = None,
    speeds: list[str] | None = None, min_eval: int | None = None,
) -> None:
    console = Console()
    games = db.analyzed_games_with_moves(conn)
    if speeds:
        games = [g for g in games if g["game"]["speed"] in speeds]
    if not games:
        which = f" for speed {','.join(speeds)}" if speeds else ""
        console.print(f"[red]No analyzed games{which}. Run `fetch` then `analyze` first.[/red]")
        return

    r = cfg["report"]
    window = window_override or r["window"]
    if min_eval is None:
        min_eval = r.get("min_eval")
    head = games[:window]
    weights = stats.recency_weights(len(head), r["half_life"])
    rng = random.Random(42)

    if speeds:
        console.print(f"[cyan]Filtered to time control(s): {', '.join(speeds)}[/cyan]")
    _header(console, games, head, r)
    if min_eval is not None:
        console.print(
            f"[dim]Study-focus scoring ignores moves played from already-lost "
            f"positions (worse than {min_eval / 100:+.1f}). ACPL below is over "
            f"all moves, for comparability.[/dim]"
        )
    if len(head) < r["min_games"]:
        console.print(
            f"[yellow]Only {len(head)} analyzed games — below the minimum of "
            f"{r['min_games']} for reliable conclusions. Treat everything below "
            f"as provisional.[/yellow]\n"
        )

    phase = stats.phase_scores(head, weights, min_eval)
    phase_conf = stats.bootstrap_phase_top1(
        head, weights, r["bootstrap_draws"], rng, min_eval
    )
    motif = stats.motif_scores(head, weights, min_eval)
    motif_conf = stats.bootstrap_motif_top1(
        head, weights, r["bootstrap_draws"], rng, min_eval
    )

    _headline(console, phase, phase_conf, motif, motif_conf)
    _accuracy_tables(console, head, phase)
    _checklist_table(console, head, weights, min_eval)
    _motif_table(console, motif, motif_conf)
    _stability(console, games, r, min_eval)
    _recommendation(console, phase_conf, motif_conf)


def _header(console, games, head, r):
    span = stats.window_span_days(head)
    idle = stats.days_since_last_game(games)
    lines = [
        f"Analyzed games: [bold]{len(games)}[/bold] total, headline window = most recent [bold]{len(head)}[/bold]",
        f"Window span: {head[-1]['game']['played_at'][:10]} → {head[0]['game']['played_at'][:10]} ({span} days)",
    ]
    warnings = []
    if span > r["stale_days"]:
        warnings.append(
            f"window spans {span} days (> {r['stale_days']}) — early games may not reflect current strength"
        )
    if idle is not None and idle > 60:
        warnings.append(
            f"most recent game is {idle} days old — treat this as a baseline, re-run after ~10 fresh games"
        )
    body = "\n".join(lines)
    if warnings:
        body += "\n[yellow]⚠ " + "\n⚠ ".join(warnings) + "[/yellow]"
    console.print(Panel(body, title="Chess Telemetry", expand=False))


def _headline(console, phase, phase_conf, motif, motif_conf):
    t = Table(title="Headline focus areas (recency-weighted, bootstrap confidence)")
    t.add_column("Dimension")
    t.add_column("Top weakness", style="bold red")
    t.add_column("Confidence (#1 in resamples)")
    t.add_column("Runner-up")

    def fmt(conf):
        items = list(conf.items())
        top = f"{items[0][0]}" if items else "—"
        top_pct = f"{items[0][1]:.0%}" if items else "—"
        runner = f"{items[1][0]} ({items[1][1]:.0%})" if len(items) > 1 else "—"
        return top, top_pct, runner

    p_top, p_pct, p_run = fmt(phase_conf)
    m_top, m_pct, m_run = fmt(motif_conf)
    t.add_row("Game phase", p_top, p_pct, p_run)
    t.add_row("Tactical motif", m_top, m_pct, m_run)
    console.print(t)


def _accuracy_tables(console, head, phase):
    by_speed = defaultdict(list)
    for g in head:
        by_speed[g["game"]["speed"] or "unknown"].append(g)

    t = Table(title="Accuracy by time control (headline window)")
    t.add_column("Speed")
    t.add_column("Games", justify="right")
    t.add_column("ACPL", justify="right")
    t.add_column("Blunders/game", justify="right")
    for speed, gs in sorted(by_speed.items(), key=lambda kv: -len(kv[1])):
        t.add_row(
            speed,
            str(len(gs)),
            f"{mean(stats.game_acpl(g) for g in gs):.0f}",
            f"{mean(stats.blunder_count(g) for g in gs):.1f}",
        )
    console.print(t)

    t2 = Table(title="Win-probability loss per move, by phase (recency-weighted)")
    t2.add_column("Phase")
    t2.add_column("Avg winP loss/move", justify="right")
    for p in stats.PHASES:
        t2.add_row(p, f"{phase[p]:.2f}")
    console.print(t2)


def _checklist_table(console, head, weights, min_eval=None):
    """Aggregate error damage by the step of John's checklist it maps to."""
    dmg: dict[str, float] = {}
    cnt: dict[str, int] = {}
    for i, g in enumerate(head):
        w = weights[i]
        for m in g["moves"]:
            if not m["motif"] or not stats.eligible(m, min_eval):
                continue
            step = CHECKLIST.get(m["motif"], "Positional / uncategorized")
            dmg[step] = dmg.get(step, 0.0) + w * m["winp_loss"]
            cnt[step] = cnt.get(step, 0) + 1
    if not cnt:
        return
    total = sum(dmg.values())
    t = Table(title="Checklist focus — which step of your process is failing")
    t.add_column("Checklist step")
    t.add_column("Errors", justify="right")
    t.add_column("Share of win-prob damage", justify="right")
    for step, d in sorted(dmg.items(), key=lambda kv: -kv[1]):
        t.add_row(step, str(cnt[step]), f"{d / total:.0%}" if total else "—")
    console.print(t)


def _motif_table(console, motif, motif_conf):
    if not motif["counts"]:
        console.print("[dim]No tagged errors in the window.[/dim]")
        return
    t = Table(title="Tagged errors (mistakes/blunders in headline window)")
    t.add_column("Motif")
    t.add_column("Count", justify="right")
    t.add_column("Share of winP damage", justify="right")
    t.add_column("#1 in resamples", justify="right")
    for m, share in sorted(motif["shares"].items(), key=lambda kv: -kv[1]):
        t.add_row(
            m,
            str(motif["counts"].get(m, 0)),
            f"{share:.0%}",
            f"{motif_conf.get(m, 0.0):.0%}",
        )
    console.print(t)


def _stability(console, games, r, min_eval=None):
    windows = stats.rolling_windows(
        games, r["rolling_window"], r["rolling_step"], min_eval
    )
    if len(windows) < 2:
        console.print(
            "[dim]Not enough history for rolling-window stability analysis "
            f"(need ≥ {r['rolling_window'] + r['rolling_step']} games).[/dim]"
        )
        return
    t = Table(title=f"Stability: rolling {r['rolling_window']}-game windows (step {r['rolling_step']})")
    t.add_column("Window")
    t.add_column("ACPL", justify="right")
    t.add_column("Blunders/game", justify="right")
    t.add_column("Top phase")
    t.add_column("Top motif")
    for w in windows:
        t.add_row(
            f"{w['from']} → {w['to']}",
            f"{w['acpl']:.0f}",
            f"{w['blunders_per_game']:.1f}",
            w["top_phase"] or "—",
            w["top_motif"] or "—",
        )
    console.print(t)
    for key, label in (("top_phase", "Phase focus"), ("top_motif", "Motif focus")):
        s = stats.stability_summary(windows, key)
        if s:
            console.print(f"  [bold]{label} stability:[/bold] {s}")
    console.print()


def _recommendation(console, phase_conf, motif_conf):
    parts = []
    for conf in (phase_conf, motif_conf):
        if conf:
            top, pct = next(iter(conf.items()))
            advice = STUDY_ADVICE.get(top)
            if advice:
                qualifier = "" if pct >= 0.7 else " (low confidence — gather more games)"
                parts.append(f"• {advice}{qualifier}")
    if parts:
        console.print(Panel("\n".join(parts), title="Study recommendation", expand=False))

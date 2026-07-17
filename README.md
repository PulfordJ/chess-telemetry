# chess-telemetry

Automated chess game analysis: fetches your games from Lichess and Chess.com,
runs them through Stockfish, and produces a study-focus report — which game
phase and which tactical motifs are costing you the most, with bootstrap
confidence and stability tracking over time.

## Setup

1. `cp config.example.toml config.toml` and put your usernames in `[accounts]`
   (`config.toml` is git-ignored so your details stay local).
2. Optionally export `LICHESS_TOKEN` for faster fetching.

## Usage

Everything runs inside the Nix devShell (provides Python, uv, and a pinned Stockfish):

```sh
nix develop
uv sync
uv run chess-telemetry fetch          # pull games from both platforms
uv run chess-telemetry analyze        # engine analysis (resumable; --limit N)
uv run chess-telemetry analyze --retag  # re-tag motifs from cache (no engine)
uv run chess-telemetry status         # analysis progress + ETA
uv run chess-telemetry report         # study-focus report (--window N)
uv run chess-telemetry report --speed rapid,blitz   # slice by time control
uv run chess-telemetry drills         # export your blunders as a puzzle PGN
```

### Checklist mapping

Errors are tagged by tactical/strategic motif, and each motif maps to a step of
the over-the-board checklist (king safety, hanging pieces, pins/skewers,
discovered attacks, endgame technique). The report's "Checklist focus" table
shows which step of your own process is failing most, weighted by
win-probability damage. After changing tagging logic, `analyze --retag`
recomputes tags on existing games from cached evals in seconds.

### Drill deck

`drills` turns your own mistakes into an importable puzzle set. Each puzzle is
the position just before a blunder; the mainline is the engine's line from
there. Import the PGN into a Lichess study (Study → ⋮ → Import PGN) or any
trainer.

```sh
# Defensive vision (checklist #5): the solution is usually a quiet move
uv run chess-telemetry drills --motif hung_piece,allowed_fork,allowed_pin,allowed_skewer

# King safety (checklist #2): positions where you allowed a mate
uv run chess-telemetry drills --motif allowed_mate

# Attacking (missed tactics): the solution IS the tactic you didn't find
uv run chess-telemetry drills --motif missed_fork,missed_pin,missed_skewer,missed_capture
```

Both errors *you allowed* and tactics *you missed* are tracked (`allowed_*`
vs `missed_*`). By default, drills skip positions you were already losing
badly (`--min-eval -300`, i.e. 3+ pawns down) and mistakes that cost little
(`--min-winp-loss 10`), so every puzzle is worth solving. The report applies
the same already-lost filter to its study-focus rankings (`report.min_eval`),
while keeping ACPL over all moves for comparability with other sites.

Engine evaluations are cached in SQLite (`data/telemetry.db`), so re-analysis
and metric changes are cheap. `analyze` is safe to interrupt and resume.

## Metrics

- **ACPL** — average centipawn loss per move (comparability with Lichess et al.)
- **Win-probability loss** — drives the focus rankings; a 100 cp slip near
  equality matters far more than one at +8.
- **Motif tags** — hung piece, allowed/missed fork, pin, skewer, discovered
  attack, missed mate; classified from engine principal variations.
- **Bootstrap confidence** — how often each weakness ranks #1 across resamples.
- **Rolling windows** — how stable the recommendation has been over your history.

## Tests

```sh
uv run pytest
```

# chess-telemetry

Automated chess game analysis: fetches your games from Lichess and Chess.com,
runs them through Stockfish, and produces a study-focus report — which game
phase and which tactical motifs are costing you the most, with bootstrap
confidence and stability tracking over time.

## Setup

1. Install Nix — see [PulfordJ/install-nix](https://github.com/PulfordJ/install-nix).
2. `cp config.example.toml config.toml` and put your usernames in `[accounts]`
   (`config.toml` is git-ignored so your details stay local).
3. Optionally export `LICHESS_TOKEN` for faster fetching.

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

### Example output

```
$ uv run chess-telemetry report

╭────────────────────────────── Chess Telemetry ───────────────────────────────╮
│ Analyzed games: 358 total, headline window = most recent 50                  │
│ Window span: 2024-08-22 → 2026-07-16 (693 days)                              │
│ ⚠ window spans 693 days (> 180) — early games may not reflect current        │
│ strength                                                                     │
╰──────────────────────────────────────────────────────────────────────────────╯
Study-focus scoring ignores moves played from already-lost positions (worse than
-3.0). ACPL below is over all moves, for comparability.
        Headline focus areas (recency-weighted, bootstrap confidence)         
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Dimension      ┃ Top weakness ┃ Confidence (#1 in resamples) ┃ Runner-up   ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Game phase     │ middlegame   │ 100%                         │ —           │
│ Tactical motif │ hung_piece   │ 77%                          │ other (18%) │
└────────────────┴──────────────┴──────────────────────────────┴─────────────┘
 Accuracy by time control (headline window) 
┏━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Speed     ┃ Games ┃ ACPL ┃ Blunders/game ┃
┡━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━┩
│ classical │    37 │   43 │           0.6 │
│ daily     │     7 │   35 │           0.6 │
│ rapid     │     5 │   60 │           1.0 │
│ bullet    │     1 │   80 │           1.0 │
└───────────┴───────┴──────┴───────────────┘
 Win-probability loss per move, by 
     phase (recency-weighted)      
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Phase      ┃ Avg winP loss/move ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ opening    │               2.12 │
│ middlegame │               4.52 │
│ endgame    │               1.90 │
└────────────┴────────────────────┘
            Checklist focus — which step of your process is failing            
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Checklist step                          ┃ Errors ┃ Share of win-prob damage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 5. What is hanging / en prise?          │     14 │                      30% │
│ Positional / uncategorized              │     11 │                      20% │
│ 5. Threats — double attacks (defending) │      4 │                      10% │
│ 6.3 Endgame technique                   │      5 │                       8% │
│ 5. Threats — double attacks (attacking) │      3 │                       8% │
│ 2. Is my king safe?                     │      3 │                       7% │
│ 5.2 Pins (defending)                    │      3 │                       4% │
│ 5. Win material / hanging (attacking)   │      3 │                       4% │
│ 5.1 Skewers (defending)                 │      2 │                       4% │
│ 5.1 Skewers (attacking)                 │      1 │                       3% │
│ 1. Is my opponent's king safe?          │      7 │                       2% │
│ 5.4 Discovered attacks (defending)      │      1 │                       1% │
└─────────────────────────────────────────┴────────┴──────────────────────────┘
         Tagged errors (mistakes/blunders in headline window)          
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Motif              ┃ Count ┃ Share of winP damage ┃ #1 in resamples ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ hung_piece         │    14 │                  30% │             77% │
│ other              │    11 │                  20% │             18% │
│ allowed_fork       │     4 │                  10% │              4% │
│ endgame_technique  │     5 │                   8% │              0% │
│ missed_fork        │     3 │                   8% │              1% │
│ allowed_mate       │     3 │                   7% │              0% │
│ allowed_pin        │     3 │                   4% │              0% │
│ missed_capture     │     3 │                   4% │              0% │
│ allowed_skewer     │     2 │                   4% │              0% │
│ missed_skewer      │     1 │                   3% │              0% │
│ missed_mate        │     7 │                   2% │              0% │
│ allowed_discovered │     1 │                   1% │              0% │
└────────────────────┴───────┴──────────────────────┴─────────────────┘
                Stability: rolling 30-game windows (step 10)                
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Window                  ┃ ACPL ┃ Blunders/game ┃ Top phase  ┃ Top motif  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ 2024-07-11 → 2025-03-05 │   47 │           0.7 │ middlegame │ hung_piece │
│ 2024-09-29 → 2025-07-03 │   46 │           0.6 │ middlegame │ hung_piece │
│ 2024-12-10 → 2025-08-26 │   45 │           0.7 │ middlegame │ hung_piece │
└─────────────────────────┴──────┴───────────────┴────────────┴────────────┘
  Phase focus stability: middlegame in 32/33 windows
  Motif focus stability: hung_piece in 31/33 windows

╭──────────────────────────── Study recommendation ────────────────────────────╮
│ • Middlegame decision-making is the leak: study annotated master games and   │
│ positional puzzle sets, not just tactics.                                    │
│ • Board vision: you leave or move pieces onto attacked squares. Enforce a    │
│ pre-move blunder check (checks, captures, threats).                          │
╰──────────────────────────────────────────────────────────────────────────────╯
```

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

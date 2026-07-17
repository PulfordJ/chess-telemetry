"""Build a drill puzzle set from the user's own blunders.

Each puzzle is the position immediately *before* a user mistake; the solution
is the engine's line from that position (cached from analysis). Output is a
multi-game PGN importable into a Lichess study or any PGN-based trainer.
"""

import io

import chess
import chess.pgn

from . import db

SOLUTION_PLIES = 6  # how much of the engine line to include as the solution


def _board_before(pgn: str, ply: int) -> tuple[chess.Board, str] | None:
    """Board just before the move at 1-indexed `ply`, plus the played SAN."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return None
    board = game.board()
    moves = list(game.mainline_moves())
    if ply < 1 or ply > len(moves):
        return None
    for mv in moves[: ply - 1]:
        board.push(mv)
    played_san = board.san(moves[ply - 1])
    return board, played_san


def _solution_line(conn, board: chess.Board, best_move: str | None) -> list[chess.Move]:
    row = db.get_eval_pv(conn, board.epd())
    ucis = (row["pv"].split() if row and row["pv"] else [])
    if not ucis and best_move:
        ucis = [best_move]
    line = []
    probe = board.copy(stack=False)
    for u in ucis[:SOLUTION_PLIES]:
        try:
            mv = chess.Move.from_uci(u)
        except ValueError:
            break
        if not probe.is_legal(mv):
            break
        line.append(mv)
        probe.push(mv)
    return line


def build_puzzles(
    conn, error_classes, motifs, speeds, limit,
    min_winp_loss=0.0, min_eval_before=None,
) -> list[chess.pgn.Game]:
    rows = db.error_moves(
        conn, error_classes, motifs, speeds, limit, min_winp_loss, min_eval_before
    )
    puzzles = []
    for r in rows:
        made = _board_before(r["pgn"], r["ply"])
        if made is None:
            continue
        board, played_san = made
        line = _solution_line(conn, board, r["best_move"])
        if not line:
            continue  # no usable solution — skip rather than emit a blank puzzle

        game = chess.pgn.Game()
        game.setup(board)
        game.headers["Event"] = f"Drill: {r['motif'] or r['error_class']}"
        game.headers["Site"] = (
            f"{r['platform']} {r['played_at'][:10]} vs {r['opponent']}"
        )
        game.headers["White"] = "Find the best move" if board.turn else r["opponent"]
        game.headers["Black"] = "Find the best move" if not board.turn else r["opponent"]
        game.headers["Result"] = "*"
        game.headers["Annotator"] = "chess-telemetry"
        to_move = "White" if board.turn else "Black"
        game.comment = (
            f"{to_move} to play. You played {played_san} "
            f"(-{r['cpl']}cp, {r['winp_loss']:.0f}% win prob). Find the engine's line."
        )
        node = game
        for mv in line:
            node = node.add_variation(mv)
        puzzles.append(game)
    return puzzles


def write_pgn(puzzles: list[chess.pgn.Game]) -> str:
    # A fresh exporter per game: StringExporter accumulates into one buffer, so
    # a shared instance would re-emit every earlier game on each accept().
    def one(g: chess.pgn.Game) -> str:
        return g.accept(
            chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        )

    return "\n\n".join(one(g) for g in puzzles)

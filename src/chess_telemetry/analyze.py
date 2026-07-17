"""Per-game analysis pipeline: replay the PGN, evaluate every position
(cache-first), derive per-move CPL / win-probability loss / phase / error
class, and tag motifs on the user's significant errors."""

import io

import chess
import chess.pgn

from . import db, motifs
from .engine import Engine, clamped_cp, move_loss, win_prob

MIN_PLIES = 8  # games shorter than this (aborts, instant resigns) carry no signal


def detect_phase(board: chess.Board) -> str:
    pieces = sum(
        len(board.pieces(pt, c))
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for c in (chess.WHITE, chess.BLACK)
    )
    if pieces <= 6:
        return "endgame"
    if board.fullmove_number <= 10:
        return "opening"
    return "middlegame"


def eval_position(conn, engine: Engine, board: chess.Board) -> dict:
    epd = board.epd()
    row = db.get_cached_eval(conn, epd, engine.name, engine.nodes)
    if row:
        return dict(row)
    result = engine.analyse(board)
    db.put_cached_eval(
        conn, epd, engine.name, engine.nodes,
        result["cp"], result["mate"], result["best_move"], result["pv"],
    )
    # Commit per eval so a hard kill mid-game loses at most one position.
    conn.commit()
    return result


def classify(cpl: int, cfg: dict) -> str:
    a = cfg["analysis"]
    if cpl >= a["blunder_cpl"]:
        return "blunder"
    if cpl >= a["mistake_cpl"]:
        return "mistake"
    if cpl >= a["inaccuracy_cpl"]:
        return "inaccuracy"
    return "ok"


def analyze_game(conn, engine: Engine, game_row, cfg: dict) -> bool:
    """Analyze one game; persist per-move rows. Returns False if skipped."""
    game = chess.pgn.read_game(io.StringIO(game_row["pgn"]))
    if game is None:
        db.save_move_rows(conn, game_row["id"], [], status=2)
        return False
    moves = list(game.mainline_moves())
    if len(moves) < MIN_PLIES:
        db.save_move_rows(conn, game_row["id"], [], status=2)
        return False

    board = game.board()
    user_is_white = game_row["color"] == "white"
    prev = eval_position(conn, engine, board)
    rows = []
    for ply, move in enumerate(moves, 1):
        mover_is_user = (board.turn == chess.WHITE) == user_is_white
        san = board.san(move)
        phase = detect_phase(board)
        board_before = board.copy(stack=False)
        board.push(move)
        cur = eval_position(conn, engine, board)

        # Mover's perspective: before = side-to-move score; after = negated,
        # since the opponent is to move in the resulting position.
        eval_before = clamped_cp(prev["cp"], prev["mate"])
        eval_after = -clamped_cp(cur["cp"], cur["mate"])
        cpl, winp_loss = move_loss(eval_before, eval_after)
        error_class = classify(cpl, cfg)

        motif = None
        if mover_is_user and error_class != "ok":
            motif = motifs.classify(
                prev, cur, board_before, move, phase, cpl,
                cfg["analysis"]["motif_min_cpl"],
            )

        rows.append({
            "game_id": game_row["id"],
            "ply": ply,
            "san": san,
            "uci": move.uci(),
            "mover": "user" if mover_is_user else "opponent",
            "phase": phase,
            "eval_before": eval_before,
            "eval_after": eval_after,
            "cpl": cpl,
            "winp_before": win_prob(eval_before),
            "winp_after": win_prob(eval_after),
            "winp_loss": winp_loss,
            "error_class": error_class,
            "motif": motif,
            "best_move": prev["best_move"],
        })
        prev = cur

    db.save_move_rows(conn, game_row["id"], rows)
    return True

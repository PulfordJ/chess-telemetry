"""Stockfish wrapper: fixed-node UCI analysis plus the eval math used everywhere.

Conventions:
- Raw evals are stored from the side-to-move's perspective.
- Exactly one of (cp, mate) is set per eval; mate=0 means side-to-move is checkmated.
- `clamped_cp` collapses everything to an integer in [-MATE_CLAMP, MATE_CLAMP].
"""

import math
import os

import chess
import chess.engine

MATE_CLAMP = 1000
WINP_K = 0.00368208  # Lichess's logistic constant for cp -> win probability


def clamped_cp(cp: int | None, mate: int | None) -> int:
    if mate is not None:
        return MATE_CLAMP if mate > 0 else -MATE_CLAMP
    return max(-MATE_CLAMP, min(MATE_CLAMP, cp))


def win_prob(cp: int) -> float:
    """Expected win percentage (0-100) for the side the cp score favors."""
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WINP_K * cp)) - 1.0)


def move_loss(eval_before: int, eval_after: int) -> tuple[int, float]:
    """(centipawn loss, win-probability loss) for a move, from the mover's POV.

    Both evals must already be clamped and in the mover's perspective.
    """
    cpl = max(0, min(MATE_CLAMP, eval_before - eval_after))
    winp_loss = max(0.0, win_prob(eval_before) - win_prob(eval_after))
    return cpl, winp_loss


class Engine:
    def __init__(self, nodes: int, threads: int = 4, hash_mb: int = 256):
        path = os.environ.get("STOCKFISH_PATH", "stockfish")
        self.engine = chess.engine.SimpleEngine.popen_uci(path)
        self.engine.configure({"Threads": threads, "Hash": hash_mb})
        self.nodes = nodes
        self.name = self.engine.id.get("name", "stockfish")

    def analyse(self, board: chess.Board) -> dict:
        """Evaluate a position. Handles terminal positions without engine calls."""
        if board.is_checkmate():
            return {"cp": None, "mate": 0, "best_move": None, "pv": ""}
        if board.is_game_over(claim_draw=False):
            return {"cp": 0, "mate": None, "best_move": None, "pv": ""}
        info = self.engine.analyse(board, chess.engine.Limit(nodes=self.nodes))
        score = info["score"].relative
        pv = info.get("pv", [])
        return {
            "cp": score.score() if not score.is_mate() else None,
            "mate": score.mate() if score.is_mate() else None,
            "best_move": pv[0].uci() if pv else None,
            "pv": " ".join(m.uci() for m in pv),
        }

    def close(self):
        self.engine.quit()


class CacheOnlyEngine:
    """Serves cached evals only; used to re-tag motifs without spawning
    Stockfish. Must report the same name/nodes the cache was written with."""

    def __init__(self, name: str, nodes: int):
        self.name = name
        self.nodes = nodes

    def analyse(self, board):
        raise RuntimeError(
            f"cache miss on {board.epd()} — retag needs a fully analyzed game"
        )

    def close(self):
        pass

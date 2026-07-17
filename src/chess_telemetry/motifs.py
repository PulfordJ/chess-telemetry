"""Geometric motif tagging for bad moves.

Given a bad user move, the engine's preferred line from before the move, and the
opponent's punishment line from after it, classify the error as an *allowed*
tactic (the punishment line executes it) or a *missed* one (the engine's line
would have executed it). Heuristic by design: clear-cut cases tag correctly,
everything else falls through to 'other' in the caller.
"""

import chess

VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}

MINOR_VALUE = 320  # threshold for "material that matters"


def see(board: chess.Board, move: chess.Move) -> int:
    """Static exchange evaluation of a capture, from the mover's perspective."""
    if board.is_en_passant(move):
        captured_value = VALUES[chess.PAWN]
    else:
        captured = board.piece_type_at(move.to_square)
        if captured is None:
            return 0
        captured_value = VALUES[captured]
    after = board.copy(stack=False)
    after.push(move)
    return captured_value - max(0, _see_square(after, move.to_square))


def _see_square(board: chess.Board, sq: chess.Square) -> int:
    """Value the side to move can win by initiating captures on `sq`."""
    victim = board.piece_type_at(sq)
    if victim is None:
        return 0
    attackers = board.attackers(board.turn, sq)
    for a in sorted(attackers, key=lambda s: VALUES[board.piece_type_at(s)]):
        move = chess.Move(a, sq)
        if board.piece_type_at(a) == chess.PAWN and chess.square_rank(sq) in (0, 7):
            move = chess.Move(a, sq, promotion=chess.QUEEN)
        if board.is_legal(move):
            after = board.copy(stack=False)
            after.push(move)
            return max(0, VALUES[victim] - _see_square(after, sq))
    return 0


def is_fork(board: chess.Board, square: chess.Square) -> bool:
    """Piece at `square` attacks >=2 enemy targets it could profitably win."""
    piece = board.piece_at(square)
    if piece is None:
        return False
    color, val = piece.color, VALUES[piece.piece_type]

    # A forker the enemy can favorably capture is not forking anything.
    enemy_attackers = board.attackers(not color, square)
    if enemy_attackers:
        min_attacker = min(
            VALUES[board.piece_type_at(a)] for a in enemy_attackers
        )
        if min_attacker < val or not board.attackers(color, square):
            return False

    targets = 0
    for t in board.attacks(square):
        tp = board.piece_at(t)
        if tp is None or tp.color == color:
            continue
        defended = bool(board.attackers(not color, t))
        if tp.piece_type == chess.KING or VALUES[tp.piece_type] > val or not defended:
            targets += 1
    return targets >= 2


def _behind(board: chess.Board, from_sq: chess.Square, through_sq: chess.Square):
    """First occupied square strictly beyond `through_sq` on the from->through ray."""
    if not chess.ray(from_sq, through_sq):
        return None
    df = chess.square_file(through_sq) - chess.square_file(from_sq)
    dr = chess.square_rank(through_sq) - chess.square_rank(from_sq)
    step_f, step_r = (df > 0) - (df < 0), (dr > 0) - (dr < 0)
    f, r = chess.square_file(through_sq) + step_f, chess.square_rank(through_sq) + step_r
    while 0 <= f <= 7 and 0 <= r <= 7:
        sq = chess.square(f, r)
        if board.piece_at(sq):
            return sq
        f, r = f + step_f, r + step_r
    return None


def ray_motifs(board: chess.Board, color: chess.Color) -> set[str]:
    """Pins and skewers currently exerted by `color`'s sliding pieces."""
    found: set[str] = set()
    sliders = (
        board.pieces(chess.BISHOP, color)
        | board.pieces(chess.ROOK, color)
        | board.pieces(chess.QUEEN, color)
    )
    for sq in sliders:
        for t in board.attacks(sq):
            tp = board.piece_at(t)
            if tp is None or tp.color == color:
                continue
            behind_sq = _behind(board, sq, t)
            if behind_sq is None:
                continue
            bp = board.piece_at(behind_sq)
            if bp.color == color:
                continue
            front_v, back_v = VALUES[tp.piece_type], VALUES[bp.piece_type]
            if max(front_v, back_v) < MINOR_VALUE:
                continue
            if bp.piece_type == chess.KING or back_v > front_v:
                found.add("pin")
            elif tp.piece_type == chess.KING or front_v > back_v:
                found.add("skewer")
    return found


def creates_discovered_attack(board: chess.Board, move: chess.Move) -> bool:
    """`move` uncovers a new attack on a valuable enemy piece from a piece that
    did not itself move."""
    color = board.turn
    after = board.copy(stack=False)
    after.push(move)
    for t in chess.SquareSet(after.occupied_co[not color]):
        tp = after.piece_at(t)
        if tp.piece_type != chess.KING and VALUES[tp.piece_type] < MINOR_VALUE:
            continue
        new_attackers = (
            after.attackers(color, t)
            - board.attackers(color, t)
            - chess.SquareSet([move.to_square])
        )
        if new_attackers:
            return True
    return False


def _parse_pv(pv: str | None) -> list[chess.Move]:
    if not pv:
        return []
    out = []
    for u in pv.split():
        try:
            out.append(chess.Move.from_uci(u))
        except ValueError:
            break
    return out


def classify(
    prev: dict,
    cur: dict,
    board_before: chess.Board,
    played: chess.Move,
    phase: str,
    cpl: int,
    threshold: int,
) -> str | None:
    """Full error classification, ordered to mirror the over-the-board
    checklist: king safety first, then material/tactics, then endgame
    technique. `prev`/`cur` are eval dicts (cp/mate/pv) from the side-to-move's
    perspective before and after the played move.

    Mate-based tags (king safety) fire regardless of `cpl`; tactical and
    endgame tags require `cpl >= threshold`, below which pattern detection is
    too noisy to trust.
    """
    prev_mate, cur_mate = prev["mate"], cur["mate"]

    # #1/#2 King safety. `cur` is the opponent's view after our move, so
    # cur_mate > 0 means we handed them a forced mate.
    already_lost = prev_mate is not None and prev_mate < 0
    if cur_mate is not None and cur_mate > 0 and not already_lost:
        return "allowed_mate"
    had_mate = prev_mate is not None and prev_mate > 0
    kept_mate = cur_mate is not None and cur_mate < 0
    if had_mate and not kept_mate:
        return "missed_mate"

    if cpl < threshold:
        return None

    tac = tag(board_before, played, _parse_pv(prev["pv"]), _parse_pv(cur["pv"]))
    if tac:
        return tac
    if phase == "endgame":
        return "endgame_technique"
    return "other"


def tag(
    board_before: chess.Board,
    played: chess.Move,
    best_pv: list[chess.Move],
    punish_pv: list[chess.Move],
) -> str | None:
    """Classify a bad move. `best_pv` is the engine line from before the move
    (what should have been played); `punish_pv` is the engine line after it
    (how the opponent refutes it). Allowed tactics take priority over missed
    ones: getting punished is the more actionable signal."""
    board_after = board_before.copy(stack=False)
    board_after.push(played)
    opp = board_after.turn  # opponent moves next

    if punish_pv:
        reply = punish_pv[0]
        if board_after.is_legal(reply):
            if board_after.is_capture(reply) and see(board_after, reply) >= 100:
                return "hung_piece"
            after_reply = board_after.copy(stack=False)
            after_reply.push(reply)
            if is_fork(after_reply, reply.to_square):
                return "allowed_fork"
            new_rays = ray_motifs(after_reply, opp) - ray_motifs(board_before, opp)
            if "skewer" in new_rays:
                return "allowed_skewer"
            if "pin" in new_rays:
                return "allowed_pin"
            if creates_discovered_attack(board_after, reply):
                return "allowed_discovered"

    if best_pv:
        best = best_pv[0]
        if best != played and board_before.is_legal(best):
            if board_before.is_capture(best) and see(board_before, best) >= 100:
                return "missed_capture"
            after_best = board_before.copy(stack=False)
            after_best.push(best)
            if is_fork(after_best, best.to_square):
                return "missed_fork"
            user = board_before.turn
            new_rays = ray_motifs(after_best, user) - ray_motifs(board_before, user)
            if "skewer" in new_rays:
                return "missed_skewer"
            if "pin" in new_rays:
                return "missed_pin"
            if creates_discovered_attack(board_before, best):
                return "missed_discovered"

    return None

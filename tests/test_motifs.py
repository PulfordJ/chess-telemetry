import chess

from chess_telemetry import motifs
from chess_telemetry.motifs import (
    creates_discovered_attack,
    is_fork,
    ray_motifs,
    see,
    tag,
)


def test_see_free_capture():
    board = chess.Board("4k3/p7/8/8/8/8/8/R3K3 w - - 0 1")
    assert see(board, chess.Move.from_uci("a1a7")) == 100


def test_see_defended_capture_loses_material():
    board = chess.Board("r3k3/p7/8/8/8/8/8/R3K3 w - - 0 1")
    assert see(board, chess.Move.from_uci("a1a7")) == 100 - 500


def test_knight_royal_fork():
    # White Nc7+ forks Ke8 and Ra8.
    board = chess.Board("r3k3/2N5/8/8/8/8/8/4K3 b - - 0 1")
    assert is_fork(board, chess.C7)


def test_attacked_undefended_forker_is_not_a_fork():
    # Same fork geometry, but a bishop on e5 attacks the undefended knight.
    board = chess.Board("r3k3/2N5/8/4b3/8/8/8/4K3 b - - 0 1")
    assert not is_fork(board, chess.C7)


def test_absolute_pin_detected():
    # Bb5 pins Nc6 against Ke8.
    board = chess.Board("4k3/8/2n5/1B6/8/8/8/4K3 w - - 0 1")
    assert "pin" in ray_motifs(board, chess.WHITE)


def test_skewer_detected():
    # Bb2 skewers Kf6 with Rh8 behind it.
    board = chess.Board("7r/8/5k2/8/8/8/1B6/4K3 b - - 0 1")
    assert "skewer" in ray_motifs(board, chess.WHITE)


def test_discovered_attack():
    # Rd4 moving off the d-file uncovers Rd1's attack on Qd8.
    board = chess.Board("3qk3/8/8/8/3R4/8/8/3RK3 w - - 0 1")
    assert creates_discovered_attack(board, chess.Move.from_uci("d4a4"))
    # Sliding along the d-file keeps the queen shielded (Rd6's own new attack
    # on d8 doesn't count — the mover is excluded).
    assert not creates_discovered_attack(board, chess.Move.from_uci("d4d6"))


def test_tag_hung_piece():
    # White plays Qd5?? and the punishment line takes it with the rook.
    board = chess.Board("3rk3/8/8/8/8/8/8/3QK3 w - - 0 1")
    motif = tag(
        board,
        chess.Move.from_uci("d1d5"),
        best_pv=[],
        punish_pv=[chess.Move.from_uci("d8d5")],
    )
    assert motif == "hung_piece"


def test_tag_missed_fork():
    # Nc7+ royal fork was available; white shuffled the king instead.
    board = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1")
    motif = tag(
        board,
        chess.Move.from_uci("e1d1"),
        best_pv=[chess.Move.from_uci("b5c7")],
        punish_pv=[chess.Move.from_uci("a8a7")],
    )
    assert motif == "missed_fork"


def test_classify_allowed_mate():
    # After our move the opponent (side to move) has mate in 2.
    prev = {"cp": 20, "mate": None, "pv": ""}
    cur = {"cp": None, "mate": 2, "pv": ""}
    board = chess.Board()
    motif = motifs.classify(prev, cur, board, chess.Move.from_uci("e2e4"),
                            "middlegame", 1000, 200)
    assert motif == "allowed_mate"


def test_classify_missed_mate():
    prev = {"cp": None, "mate": 3, "pv": ""}   # we had mate in 3
    cur = {"cp": 500, "mate": None, "pv": ""}  # now merely winning
    board = chess.Board()
    motif = motifs.classify(prev, cur, board, chess.Move.from_uci("e2e4"),
                            "middlegame", 500, 200)
    assert motif == "missed_mate"


def test_classify_already_lost_is_not_allowed_mate():
    # We were already being mated; continuing to lose is not a *new* king-safety
    # failure, so it should fall through to a normal tag, not allowed_mate.
    prev = {"cp": None, "mate": -1, "pv": ""}
    cur = {"cp": None, "mate": 1, "pv": ""}
    board = chess.Board()
    motif = motifs.classify(prev, cur, board, chess.Move.from_uci("e2e4"),
                            "middlegame", 0, 200)
    assert motif != "allowed_mate"


def test_classify_endgame_technique_fallback():
    # King-and-pawn ending, big loss, no tactic → endgame technique, not "other".
    board = chess.Board("8/8/8/p1p5/2P5/1P2k3/2K5/8 b - - 0 1")
    prev = {"cp": 0, "mate": None, "pv": ""}
    cur = {"cp": 400, "mate": None, "pv": ""}
    motif = motifs.classify(prev, cur, board, chess.Move.from_uci("e3e2"),
                            "endgame", 400, 200)
    assert motif == "endgame_technique"


def test_classify_below_threshold_is_untagged():
    board = chess.Board()
    prev = {"cp": 30, "mate": None, "pv": ""}
    cur = {"cp": -70, "mate": None, "pv": ""}
    assert motifs.classify(prev, cur, board, chess.Move.from_uci("e2e4"),
                           "middlegame", 100, 200) is None


def test_tag_returns_none_when_nothing_matches():
    board = chess.Board()
    motif = tag(
        board,
        chess.Move.from_uci("e2e4"),
        best_pv=[chess.Move.from_uci("d2d4")],
        punish_pv=[chess.Move.from_uci("e7e5")],
    )
    assert motif is None

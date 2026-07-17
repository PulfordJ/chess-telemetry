import chess

from chess_telemetry import analyze, db


class FakeEngine:
    """Engine stub: every position evaluates to a fixed cp for the side to move."""

    name = "fake"
    nodes = 1

    def __init__(self, cp=0):
        self.cp = cp

    def analyse(self, board):
        move = next(iter(board.legal_moves), None)
        return {
            "cp": self.cp,
            "mate": None,
            "best_move": move.uci() if move else None,
            "pv": move.uci() if move else "",
        }


PGN = (
    '[Event "test"]\n[White "u"]\n[Black "o"]\n[Result "*"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 *"
)


def _setup(tmp_path, pgn):
    conn = db.connect(tmp_path / "t.db")
    db.insert_game(conn, {
        "platform": "lichess", "platform_id": "abc",
        "played_at": "2025-06-01T00:00:00+00:00", "time_control": "600+0",
        "speed": "rapid", "rated": 1, "color": "white", "result": "draw",
        "user_rating": 1000, "opponent_rating": 1000, "opponent": "o", "pgn": pgn,
    })
    return conn, conn.execute("SELECT * FROM games").fetchone()


def test_analyze_game_end_to_end(tmp_path):
    conn, row = _setup(tmp_path, PGN)
    cfg = {"analysis": {"inaccuracy_cpl": 50, "mistake_cpl": 100,
                        "blunder_cpl": 300, "motif_min_cpl": 200}}
    assert analyze.analyze_game(conn, FakeEngine(), row, cfg)
    moves = conn.execute("SELECT * FROM moves ORDER BY ply").fetchall()
    assert len(moves) == 10
    assert [m["mover"] for m in moves[:2]] == ["user", "opponent"]
    # cp fixed at 0 for the side to move => 0 CPL, everything 'ok'
    assert all(m["cpl"] == 0 and m["error_class"] == "ok" for m in moves)
    assert all(m["phase"] == "opening" for m in moves)
    assert conn.execute("SELECT analyzed FROM games").fetchone()[0] == 1
    # positions are cached: 11 unique positions evaluated
    assert conn.execute("SELECT COUNT(*) FROM evals").fetchone()[0] == 11


def test_short_game_skipped(tmp_path):
    conn, row = _setup(tmp_path, '[Event "t"]\n\n1. e4 e5 *')
    cfg = {"analysis": {"inaccuracy_cpl": 50, "mistake_cpl": 100,
                        "blunder_cpl": 300, "motif_min_cpl": 200}}
    assert not analyze.analyze_game(conn, FakeEngine(), row, cfg)
    assert conn.execute("SELECT analyzed FROM games").fetchone()[0] == 2


def test_phase_detection():
    assert analyze.detect_phase(chess.Board()) == "opening"
    # King-and-pawn ending
    assert analyze.detect_phase(chess.Board("4k3/8/8/8/8/8/4P3/4K3 w - - 0 40")) == "endgame"
    # Full material at move 20
    b = chess.Board()
    b.fullmove_number = 20
    assert analyze.detect_phase(b) == "middlegame"

import io

import chess
import chess.pgn

from chess_telemetry import db, drills


def _seed(conn):
    # A short game where white plays the premature Qh5 on move 3 (ply 5).
    pgn = '[Event "t"]\n[White "u"]\n[Black "o"]\n[Result "*"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 *'
    db.insert_game(conn, {
        "platform": "lichess", "platform_id": "g1",
        "played_at": "2025-06-01T00:00:00+00:00", "time_control": "600+0",
        "speed": "rapid", "rated": 1, "color": "white", "result": "loss",
        "user_rating": 1000, "opponent_rating": 1000, "opponent": "o", "pgn": pgn,
    })
    gid = conn.execute("SELECT id FROM games").fetchone()[0]
    # Reconstruct the position before ply 5 (white to move) and cache a PV for it.
    board = chess.pgn.read_game(io.StringIO(pgn)).board()
    for mv in list(chess.pgn.read_game(io.StringIO(pgn)).mainline_moves())[:4]:
        board.push(mv)
    db.put_cached_eval(conn, board.epd(), "fake", 1, 30, None, "g1f3", "g1f3 g8f6 d2d3")
    db.save_move_rows(conn, gid, [{
        "game_id": gid, "ply": 5, "san": "Qh5", "uci": "d1h5", "mover": "user",
        "phase": "opening", "eval_before": 30, "eval_after": -400, "cpl": 430,
        "winp_before": 54.0, "winp_after": 8.0, "winp_loss": 46.0,
        "error_class": "blunder", "motif": "hung_piece", "best_move": "g1f3",
    }])
    return gid, board


def test_build_puzzle_from_blunder(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _gid, board_before = _seed(conn)
    puzzles = drills.build_puzzles(conn, ["blunder", "mistake"], None, None, 60)
    assert len(puzzles) == 1
    g = puzzles[0]
    assert g.headers["Event"] == "Drill: hung_piece"
    # Puzzle starts from the pre-blunder position...
    assert g.headers["FEN"] == board_before.fen()
    # ...and the solution mainline is the cached engine line (Nf3 first).
    line = list(g.mainline_moves())
    assert line[0] == chess.Move.from_uci("g1f3")
    assert "Qh5" in g.comment and "430cp" in g.comment


def test_filters(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn)
    assert drills.build_puzzles(conn, ["blunder"], ["allowed_fork"], None, 60) == []
    assert drills.build_puzzles(conn, ["blunder"], ["hung_piece"], ["blitz"], 60) == []
    assert len(drills.build_puzzles(conn, ["blunder"], ["hung_piece"], ["rapid"], 60)) == 1


def test_min_winp_loss_filter(tmp_path):
    # Seeded blunder cost 46% win prob; a 60% threshold must exclude it.
    conn = db.connect(tmp_path / "t.db")
    _seed(conn)
    assert len(drills.build_puzzles(conn, ["blunder"], None, None, 60, min_winp_loss=10)) == 1
    assert drills.build_puzzles(conn, ["blunder"], None, None, 60, min_winp_loss=60) == []


def test_roundtrip_pgn_parses(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn)
    text = drills.write_pgn(drills.build_puzzles(conn, ["blunder"], None, None, 60))
    reparsed = chess.pgn.read_game(io.StringIO(text))
    assert reparsed.headers["Event"] == "Drill: hung_piece"
    assert reparsed.board().turn == chess.WHITE  # white to move in the puzzle


def test_write_pgn_no_duplication(tmp_path):
    # Two puzzles must export as exactly two [Event] blocks — guards against the
    # shared-StringExporter bug that re-emitted every earlier game.
    conn = db.connect(tmp_path / "t.db")
    _seed(conn)
    pgn = '[Event "t"]\n[White "u"]\n[Black "o"]\n[Result "*"]\n\n1. d4 d5 2. Bf4 Nf6 3. Qd3 Nc6 *'
    db.insert_game(conn, {
        "platform": "lichess", "platform_id": "g2",
        "played_at": "2025-05-01T00:00:00+00:00", "time_control": "600+0",
        "speed": "rapid", "rated": 1, "color": "white", "result": "loss",
        "user_rating": 1000, "opponent_rating": 1000, "opponent": "o", "pgn": pgn,
    })
    import chess as _c
    gid = conn.execute("SELECT id FROM games WHERE platform_id='g2'").fetchone()[0]
    b = _c.pgn.read_game(io.StringIO(pgn)).board()
    for mv in list(_c.pgn.read_game(io.StringIO(pgn)).mainline_moves())[:4]:
        b.push(mv)
    db.put_cached_eval(conn, b.epd(), "fake", 1, 20, None, "g1f3", "g1f3 e7e6")
    db.save_move_rows(conn, gid, [{
        "game_id": gid, "ply": 5, "san": "Qd3", "uci": "d1d3", "mover": "user",
        "phase": "opening", "eval_before": 20, "eval_after": -180, "cpl": 200,
        "winp_before": 53.0, "winp_after": 20.0, "winp_loss": 33.0,
        "error_class": "blunder", "motif": "hung_piece", "best_move": "g1f3",
    }])
    text = drills.write_pgn(drills.build_puzzles(conn, ["blunder"], None, None, 60))
    assert text.count("[Event ") == 2

import chess
import chess.pgn

from chess_telemetry import openings

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4", "exd4"]


def make_pgn(san_moves):
    game = chess.pgn.Game()
    node = game
    board = game.board()
    for san in san_moves:
        move = board.parse_san(san)
        board.push(move)
        node = node.add_variation(move)
    return str(game)


def epd_after(san_moves):
    board = chess.Board()
    for san in san_moves:
        board.push_san(san)
    return board.epd()


def stub_lookup(table):
    """lookup(board) backed by an epd -> stats dict; adds derived 'total'."""
    def lookup(board):
        stats = table.get(board.epd())
        if stats is None:
            return None
        return {**stats, "total": stats["white"] + stats["draws"] + stats["black"]}
    return lookup


def masters(white, draws, black, name="Italian Game: Classical", eco="C53"):
    return {"white": white, "draws": draws, "black": black, "name": name, "eco": eco}


def test_expected_score():
    assert openings.expected_score(400, 400, 200, "white") == 0.6
    assert openings.expected_score(400, 400, 200, "black") == 0.4
    assert openings.expected_score(0, 0, 0, "white") == 0.5


def test_actual_score():
    assert openings.actual_score("win") == 1.0
    assert openings.actual_score("draw") == 0.5
    assert openings.actual_score("loss") == 0.0


def test_bucket_name():
    assert openings.bucket_name("Sicilian Defense: Najdorf Variation") == "Sicilian Defense"
    assert openings.bucket_name("King's Indian Attack") == "King's Indian Attack"


def test_game_record_anchors_at_depth():
    lookup = stub_lookup({epd_after(ITALIAN): masters(400, 400, 200)})
    rec = openings.game_record(
        make_pgn(ITALIAN), "white", "win", lookup,
        depth_plies=10, min_master_games=100,
    )
    assert rec == {
        "bucket": "Italian Game", "eco": "C53", "color": "white",
        "first": "e4", "sans": ITALIAN, "expected": 0.6, "actual": 1.0,
    }


def test_game_record_walks_back_when_masters_thin():
    table = {
        epd_after(ITALIAN): masters(5, 3, 2),          # below min at ply 10
        epd_after(ITALIAN[:8]): masters(300, 500, 200),  # anchor lands at ply 8
    }
    rec = openings.game_record(
        make_pgn(ITALIAN), "black", "loss", stub_lookup(table),
        depth_plies=10, min_master_games=100,
    )
    assert rec["bucket"] == "Italian Game"
    assert rec["expected"] == 0.45  # (200 + 250) / 1000 from Black's side
    assert rec["actual"] == 0.0


def test_game_record_unbucketed_when_always_thin():
    rec = openings.game_record(
        make_pgn(ITALIAN), "white", "win", stub_lookup({}),
        depth_plies=10, min_master_games=100,
    )
    assert rec is None


def test_game_record_coarse_bucket_when_out_of_book_early():
    # Out of theory by move 3; anchor walks all the way back to 1. e4 e5.
    weird = ["e4", "e5", "Qh5", "Nc6", "Qxe5+", "Nxe5"]
    table = {
        epd_after(weird[:2]): masters(6000, 3000, 2000, name="King's Pawn Game", eco="C20")
    }
    rec = openings.game_record(
        make_pgn(weird), "black", "win", stub_lookup(table),
        depth_plies=10, min_master_games=100,
    )
    assert rec["bucket"] == "King's Pawn Game"
    assert rec["eco"] == "C20"


def test_game_record_irregular_fallback_without_eco():
    # Nothing named anywhere; baseline comes from the starting position.
    start = chess.Board().epd()
    table = {start: masters(5500, 3000, 4000, name=None, eco=None)}
    rec = openings.game_record(
        make_pgn(["Na3", "e5", "h4", "d5"]), "white", "loss", stub_lookup(table),
        depth_plies=10, min_master_games=100,
    )
    assert rec["bucket"] == "Irregular (Na3)"
    assert rec["eco"] is None
    assert rec["expected"] == 0.56  # (5500 + 1500) / 12500
    assert rec["actual"] == 0.0


def test_game_record_moveless_pgn():
    assert openings.game_record(
        "*", "white", "win", stub_lookup({}), depth_plies=10, min_master_games=100
    ) is None


def test_game_record_short_game_anchors_at_end():
    short = ITALIAN[:6]
    lookup = stub_lookup({epd_after(short): masters(400, 400, 200)})
    rec = openings.game_record(
        make_pgn(short), "white", "draw", lookup,
        depth_plies=10, min_master_games=100,
    )
    assert rec["bucket"] == "Italian Game"
    assert rec["actual"] == 0.5


def test_game_record_transposition_same_bucket():
    # Different move orders into the same position share the anchor EPD.
    a = ["d4", "Nf6", "c4", "e6", "Nf3", "d5"]
    b = ["Nf3", "d5", "d4", "Nf6", "c4", "e6"]
    assert epd_after(a) == epd_after(b)
    lookup = stub_lookup(
        {epd_after(a): masters(400, 400, 200, name="Queen's Gambit Declined", eco="D30")}
    )
    kw = dict(depth_plies=6, min_master_games=100)
    rec_a = openings.game_record(make_pgn(a), "white", "win", lookup, **kw)
    rec_b = openings.game_record(make_pgn(b), "white", "win", lookup, **kw)
    assert rec_a["bucket"] == rec_b["bucket"] == "Queen's Gambit Declined"


def test_move_tree_aggregates_prefixes():
    recs = [
        {"sans": ["e4", "e5", "Nf3"], "actual": 1.0, "expected": 0.55},
        {"sans": ["e4", "e5", "Bc4"], "actual": 0.0, "expected": 0.55},
        {"sans": ["e4", "c5"], "actual": 1.0, "expected": 0.55},
        {"sans": ["d4", "d5"], "actual": 0.5, "expected": 0.55},
    ]
    root = openings.move_tree(recs, min_games=2)
    assert root["n"] == 4
    e4 = root["children"]["e4"]
    assert e4["n"] == 3
    assert abs(e4["actual"] - 2 / 3) < 1e-9
    e5 = e4["children"]["e5"]
    assert e5["n"] == 2 and e5["actual"] == 0.5
    assert abs(e5["delta"] - (0.5 - 0.55)) < 1e-9
    # Below min_games: 1.d4, 1...c5, and everything under 1...e5 are pruned.
    assert "d4" not in root["children"]
    assert "c5" not in e4["children"]
    assert e5["children"] == {}


def test_move_tree_empty():
    assert openings.move_tree([], min_games=2)["n"] == 0


def test_parse_line():
    assert openings.parse_line("1. e4 c5") == ["e4", "c5"]
    assert openings.parse_line("1.c4") == ["c4"]
    assert openings.parse_line("1...c5") == ["c5"]
    assert openings.parse_line("1. c4 e5 2. Nc3") == ["c4", "e5", "Nc3"]


def test_matches_repertoire_white():
    lines = [openings.parse_line("1. c4")]
    assert openings.matches_repertoire(["c4", "e5", "g3"], "white", lines)
    assert not openings.matches_repertoire(["e4", "e5"], "white", lines)
    assert openings.matches_repertoire(["e4"], "white", [])  # no filter


def test_matches_repertoire_black():
    lines = [openings.parse_line("1. e4 c5"), openings.parse_line("1. d4 d5")]
    assert openings.matches_repertoire(["e4", "c5", "Nf3"], "black", lines)
    assert not openings.matches_repertoire(["e4", "e6"], "black", lines)
    assert openings.matches_repertoire(["d4", "d5"], "black", lines)
    # Repertoire is silent on 1.Nf3 — the game is kept.
    assert openings.matches_repertoire(["Nf3", "d5"], "black", lines)


def test_matches_repertoire_deep_lines():
    lines = [openings.parse_line("1. c4 e5 2. Nc3")]
    assert openings.matches_repertoire(["c4", "e5", "Nc3", "Nf6"], "white", lines)
    assert not openings.matches_repertoire(["c4", "e5", "g3"], "white", lines)
    # Opponent deviates from the line at move 1 — repertoire is silent.
    assert openings.matches_repertoire(["c4", "Nf6", "g3"], "white", lines)


def test_notable_lines_significance_and_dedup():
    # 20 games of 1.e4: 10 wins in e5 lines (strong), 10 losses in c5 lines
    # (weak). "1.e4" overall is dead even — not notable; each child is.
    recs = (
        [{"sans": ["e4", "e5"], "actual": 1.0, "expected": 0.5}] * 10
        + [{"sans": ["e4", "c5"], "actual": 0.0, "expected": 0.5}] * 10
    )
    lines = openings.notable_lines(openings.move_tree(recs, min_games=5))
    assert [(l["sans"], l["n"]) for l in lines] == [
        (["e4", "e5"], 10), (["e4", "c5"], 10),
    ]

    # All wins: "1.e4" itself is notable and its children repeat the same
    # signal, so only the shallowest line is reported.
    recs = [{"sans": ["e4", "e5"], "actual": 1.0, "expected": 0.5}] * 10
    lines = openings.notable_lines(openings.move_tree(recs, min_games=5))
    assert [l["sans"] for l in lines] == [["e4"]]

    # A significant reversal below a significant ancestor is still reported.
    recs = (
        [{"sans": ["e4", "e5"], "actual": 1.0, "expected": 0.5}] * 30
        + [{"sans": ["e4", "c5"], "actual": 0.0, "expected": 0.5}] * 6
    )
    lines = openings.notable_lines(openings.move_tree(recs, min_games=5))
    assert [l["sans"] for l in lines] == [["e4"], ["e4", "c5"]]


def rec(color, bucket, actual, expected):
    return {"color": color, "bucket": bucket, "eco": "C50",
            "actual": actual, "expected": expected}


def test_aggregate_means_and_delta():
    agg = openings.aggregate([
        rec("white", "Italian Game", 1.0, 0.6),
        rec("white", "Italian Game", 0.0, 0.5),
        rec("black", "Sicilian Defense", 0.5, 0.45),
    ])
    it = agg[("white", "Italian Game")]
    assert it["n"] == 2
    assert it["actual"] == 0.5
    assert abs(it["delta"] - (0.5 - 0.55)) < 1e-9
    assert agg[("black", "Sicilian Defense")]["n"] == 1


def bucket(n, delta):
    return {"n": n, "actual": 0.5 + delta, "expected": 0.5, "delta": delta, "eco": "C50"}


def test_edges_matches_opposite_colors_only():
    user = {("white", "Italian Game"): bucket(10, 0.15)}
    opp_same_color = {("white", "Italian Game"): bucket(10, -0.10)}
    assert openings.edges(user, opp_same_color, min_games=5, shrink_k=5) == []

    opp = {("black", "Italian Game"): bucket(10, -0.10)}
    rows = openings.edges(user, opp, min_games=5, shrink_k=5)
    assert len(rows) == 1
    assert abs(rows[0]["edge"] - 0.25) < 1e-9
    assert rows[0]["strict"] is True


def test_edges_min_games_both_sides():
    user = {("white", "Italian Game"): bucket(10, 0.15)}
    opp = {("black", "Italian Game"): bucket(3, -0.10)}
    assert openings.edges(user, opp, min_games=5, shrink_k=5) == []


def test_edges_shrinkage_reorders_small_samples():
    user = {
        ("white", "Fluke Opening"): bucket(2, 0.4),
        ("white", "Solid Opening"): bucket(20, 0.2),
    }
    opp = {
        ("black", "Fluke Opening"): bucket(2, 0.0),
        ("black", "Solid Opening"): bucket(20, 0.0),
    }
    rows = openings.edges(user, opp, min_games=2, shrink_k=5)
    assert [r["bucket"] for r in rows] == ["Solid Opening", "Fluke Opening"]

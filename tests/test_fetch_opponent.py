import pytest

from chess_telemetry import db
from chess_telemetry.fetch import chesscom, lichess

LICHESS_GAME = {
    "id": "abc123",
    "variant": "standard",
    "speed": "blitz",
    "rated": True,
    "createdAt": 1735689600000,
    "winner": "white",
    "players": {
        "white": {"user": {"name": "Rival"}, "rating": 1500},
        "black": {"user": {"name": "SomeoneElse"}, "rating": 1480},
    },
    "clock": {"initial": 300, "increment": 0},
    "pgn": "1. e4 e5 *",
}

CHESSCOM_GAME = {
    "rules": "chess",
    "uuid": "uuid-1",
    "end_time": 1735689600,
    "time_control": "600",
    "time_class": "rapid",
    "rated": True,
    "white": {"username": "Rival", "result": "checkmated", "rating": 900},
    "black": {"username": "SomeoneElse", "result": "win", "rating": 950},
    "pgn": "1. d4 d5 *",
}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def as_opponent(parsed, username):
    return {**parsed, "username": username}


def test_lichess_parse_is_fetched_username_pov():
    won = lichess._parse(LICHESS_GAME, "rival")
    assert won["color"] == "white"
    assert won["result"] == "win"
    lost = lichess._parse(LICHESS_GAME, "someoneelse")
    assert lost["color"] == "black"
    assert lost["result"] == "loss"


def test_chesscom_parse_is_fetched_username_pov():
    lost = chesscom._parse(CHESSCOM_GAME, "rival")
    assert lost["color"] == "white"
    assert lost["result"] == "loss"
    assert lost["user_rating"] == 900


def test_insert_opponent_game_separate_from_games(conn):
    parsed = lichess._parse(LICHESS_GAME, "rival")
    assert db.insert_opponent_game(conn, as_opponent(parsed, "rival")) is True
    # Re-insert is ignored; the user's games table is untouched.
    assert db.insert_opponent_game(conn, as_opponent(parsed, "rival")) is False
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    rows = db.opponent_game_rows(conn, "lichess", "Rival")
    assert len(rows) == 1
    assert rows[0]["result"] == "win"
    assert rows[0]["rating"] == 1500


def test_same_platform_id_allowed_for_two_opponents(conn):
    a = lichess._parse(LICHESS_GAME, "rival")
    b = lichess._parse(LICHESS_GAME, "someoneelse")
    assert db.insert_opponent_game(conn, as_opponent(a, "rival")) is True
    assert db.insert_opponent_game(conn, as_opponent(b, "someoneelse")) is True
    assert db.opponent_game_count(conn, "lichess", "rival") == 1
    assert db.opponent_game_count(conn, "lichess", "someoneelse") == 1

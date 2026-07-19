import chess
import pytest

from chess_telemetry import db, explorer


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        assert self.status_code == 200

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        return self.responses.pop(0)


MASTERS_JSON = {
    "white": 400, "draws": 400, "black": 200,
    "opening": {"eco": "C53", "name": "Italian Game: Classical"},
}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(explorer, "REQUEST_DELAY", 0)
    monkeypatch.setattr(explorer, "RATE_LIMIT_SLEEP", 0)


def test_lookup_caches_position(conn):
    client = FakeClient([FakeResponse(200, MASTERS_JSON)])
    board = chess.Board()
    first = explorer.masters_lookup(conn, client, board)
    assert first == {
        "white": 400, "draws": 400, "black": 200, "total": 1000,
        "eco": "C53", "name": "Italian Game: Classical",
    }
    second = explorer.masters_lookup(conn, client, board)
    assert second == first
    assert client.calls == 1


def test_empty_result_cached_as_none(conn):
    client = FakeClient([FakeResponse(200, {"white": 0, "draws": 0, "black": 0})])
    board = chess.Board()
    assert explorer.masters_lookup(conn, client, board) is None
    assert explorer.masters_lookup(conn, client, board) is None
    assert client.calls == 1


def test_429_retries_once(conn):
    client = FakeClient([FakeResponse(429, {}), FakeResponse(200, MASTERS_JSON)])
    result = explorer.masters_lookup(conn, client, chess.Board())
    assert result["total"] == 1000
    assert client.calls == 2

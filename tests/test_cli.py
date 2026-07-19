import sys

import pytest

from chess_telemetry import cli
from chess_telemetry.cli import account_names


def test_account_names_single_string():
    assert account_names("PulfordJ") == ["PulfordJ"]


def test_account_names_list():
    assert account_names(["main", "alt"]) == ["main", "alt"]


def test_account_names_strips_and_drops_blanks():
    assert account_names([" main ", "", "  "]) == ["main"]


def test_account_names_empty():
    assert account_names("") == []
    assert account_names(None) == []
    assert account_names([]) == []


def test_suggest_requires_opponent(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chess-telemetry", "suggest", "--platform", "lichess"])
    with pytest.raises(SystemExit):
        cli.main()


def test_suggest_rejects_unknown_platform(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["chess-telemetry", "suggest", "--opponent", "bob", "--platform", "fics"],
    )
    with pytest.raises(SystemExit):
        cli.main()


def test_suggest_empty_db_asks_for_fetch(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[accounts]\n")
    monkeypatch.setattr(
        sys, "argv",
        ["chess-telemetry", "--config", str(cfg),
         "suggest", "--opponent", "bob", "--platform", "lichess"],
    )
    cli.main()
    assert "fetch" in capsys.readouterr().out

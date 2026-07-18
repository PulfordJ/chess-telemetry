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

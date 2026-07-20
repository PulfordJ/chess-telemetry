import argparse

import pytest

from chess_telemetry.suggest import filter_window, since_date


def rows(*dates):
    """Games as newest-first rows, matching db loader ordering."""
    return [{"played_at": f"{d}T12:00:00+00:00"} for d in sorted(dates, reverse=True)]


def test_since_keeps_games_on_or_after_date():
    r = rows("2026-05-10", "2026-05-01", "2026-04-30")
    kept = filter_window(r, since="2026-05-01", last=None)
    assert [g["played_at"][:10] for g in kept] == ["2026-05-10", "2026-05-01"]


def test_last_keeps_most_recent_n():
    r = rows("2026-05-10", "2026-05-01", "2026-04-30")
    kept = filter_window(r, since=None, last=2)
    assert [g["played_at"][:10] for g in kept] == ["2026-05-10", "2026-05-01"]


def test_since_applies_before_last():
    r = rows("2026-05-10", "2026-05-05", "2026-05-01", "2026-04-30")
    kept = filter_window(r, since="2026-05-01", last=2)
    # since drops 2026-04-30, then last=2 keeps the two newest of what remains.
    assert [g["played_at"][:10] for g in kept] == ["2026-05-10", "2026-05-05"]


def test_no_filters_is_identity():
    r = rows("2026-05-10", "2026-04-30")
    assert filter_window(r, since=None, last=None) == r


def test_last_larger_than_rows_is_safe():
    r = rows("2026-05-10")
    assert filter_window(r, since=None, last=99) == r


def test_since_date_accepts_valid_date():
    assert since_date("2026-05-01") == "2026-05-01"


def test_since_date_normalizes_zero_padding():
    # Canonical form keeps lexical comparison against ISO timestamps correct.
    assert since_date("2026-5-1") == "2026-05-01"


@pytest.mark.parametrize("bad", ["2026-13-01", "05-01-2026", "yesterday", "2026-5-1x"])
def test_since_date_rejects_bad_input(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        since_date(bad)

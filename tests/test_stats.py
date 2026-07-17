import random

from chess_telemetry import stats


def mk_move(phase="middlegame", cpl=0, winp_loss=0.0, error_class="ok", motif=None,
            eval_before=0):
    return {
        "phase": phase,
        "cpl": cpl,
        "winp_loss": winp_loss,
        "error_class": error_class,
        "motif": motif,
        "eval_before": eval_before,
    }


def mk_game(played_at, moves, speed="rapid"):
    return {"game": {"played_at": played_at, "speed": speed}, "moves": moves}


def test_recency_weights():
    w = stats.recency_weights(30, half_life=25)
    assert w[0] == 1.0
    assert abs(w[25] - 0.5) < 1e-9
    assert w[1] > w[2]


def test_phase_scores_normalized_by_exposure():
    # Many cheap middlegame moves vs few expensive endgame moves:
    # per-move normalization must rank endgame on top.
    g = mk_game(
        "2025-01-01T00:00:00+00:00",
        [mk_move("middlegame", winp_loss=1.0)] * 20
        + [mk_move("endgame", winp_loss=10.0)] * 4,
    )
    scores = stats.phase_scores([g])
    assert scores["endgame"] > scores["middlegame"]


def test_min_eval_excludes_already_lost_moves():
    # A big "blunder" made at -800 must not outrank a real leak at equality.
    g = mk_game(
        "2025-01-01T00:00:00+00:00",
        [
            mk_move("middlegame", winp_loss=3.0, motif="hung_piece", eval_before=0),
            mk_move("endgame", winp_loss=40.0, motif="allowed_mate", eval_before=-800),
        ],
    )
    unfiltered = stats.motif_scores([g])
    assert unfiltered["shares"]["allowed_mate"] > unfiltered["shares"]["hung_piece"]

    filtered = stats.motif_scores([g], None, min_eval=-300)
    assert "allowed_mate" not in filtered["shares"]
    assert filtered["shares"]["hung_piece"] == 1.0

    # Phase scoring drops it too.
    assert stats.phase_scores([g], None, min_eval=-300)["endgame"] == 0.0


def test_eligible_boundary():
    assert stats.eligible(mk_move(eval_before=-300), -300)      # exactly at cutoff
    assert not stats.eligible(mk_move(eval_before=-301), -300)
    assert stats.eligible(mk_move(eval_before=-9000), None)     # filter disabled


def test_motif_scores_shares_sum_to_one():
    g = mk_game(
        "2025-01-01T00:00:00+00:00",
        [
            mk_move(winp_loss=30.0, error_class="blunder", motif="hung_piece"),
            mk_move(winp_loss=10.0, error_class="mistake", motif="allowed_fork"),
        ],
    )
    m = stats.motif_scores([g])
    assert abs(sum(m["shares"].values()) - 1.0) < 1e-9
    assert m["counts"]["hung_piece"] == 1
    assert m["shares"]["hung_piece"] > m["shares"]["allowed_fork"]


def test_bootstrap_clear_signal_is_confident():
    games = [
        mk_game(
            f"2025-01-{d:02d}T00:00:00+00:00",
            [mk_move("endgame", winp_loss=20.0), mk_move("middlegame", winp_loss=1.0)],
        )
        for d in range(1, 21)
    ]
    weights = stats.recency_weights(len(games), 25)
    conf = stats.bootstrap_phase_top1(games, weights, draws=200, rng=random.Random(1))
    assert conf["endgame"] > 0.95


def test_rolling_windows_and_stability():
    games = [
        mk_game(f"2025-01-{d:02d}T00:00:00+00:00", [mk_move("endgame", cpl=100, winp_loss=5.0)])
        for d in range(10, 0, -1)  # newest first
    ]
    windows = stats.rolling_windows(games, window=4, step=3)
    assert len(windows) == 3
    # chronological order and correct spans
    assert windows[0]["from"] < windows[-1]["from"]
    assert all(w["acpl"] == 100 for w in windows)
    assert stats.stability_summary(windows, "top_phase") == "endgame in 3/3 windows"


def test_window_span_and_staleness():
    games = [
        mk_game("2025-06-01T00:00:00+00:00", [mk_move()]),
        mk_game("2025-01-01T00:00:00+00:00", [mk_move()]),
    ]
    assert stats.window_span_days(games) == 151
    assert stats.days_since_last_game(games) > 300  # relative to mid-2026

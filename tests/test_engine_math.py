from chess_telemetry.engine import MATE_CLAMP, clamped_cp, move_loss, win_prob


def test_clamp_passthrough_and_limits():
    assert clamped_cp(50, None) == 50
    assert clamped_cp(-120, None) == -120
    assert clamped_cp(5000, None) == MATE_CLAMP
    assert clamped_cp(-5000, None) == -MATE_CLAMP


def test_mate_scores():
    assert clamped_cp(None, 3) == MATE_CLAMP       # side to move mates
    assert clamped_cp(None, -2) == -MATE_CLAMP     # side to move gets mated
    assert clamped_cp(None, 0) == -MATE_CLAMP      # side to move IS mated


def test_win_prob():
    assert win_prob(0) == 50.0
    assert win_prob(1000) > 90.0
    assert abs(win_prob(300) + win_prob(-300) - 100.0) < 1e-9


def test_move_loss_basic():
    cpl, wl = move_loss(100, 100)
    assert cpl == 0 and wl == 0.0
    cpl, _ = move_loss(-50, -200)
    assert cpl == 150


def test_move_loss_never_negative_and_clamped():
    cpl, wl = move_loss(-100, 50)  # move improved the eval
    assert cpl == 0 and wl == 0.0
    cpl, _ = move_loss(MATE_CLAMP, -MATE_CLAMP)  # mate blundered into mated
    assert cpl == MATE_CLAMP

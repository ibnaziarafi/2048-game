import random

from app.game import legal_moves
from app.learner import INITIAL_WEIGHTS, features, hint, update


def test_features_are_bounded_and_expected_eight_slots():
    board = [[2, 4, 8, 16], [0, 0, 0, 0], [2, 0, 2, 0], [0, 0, 0, 0]]
    values = features(board)
    assert len(values) == 8
    assert all(0 <= value <= 1 for value in values)


def test_td_update_is_finite_and_changes_the_shared_weights():
    before = [[2, 2, 4, 4], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    after = [[4, 8, 2, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    weights, delta, shaped_reward = update(INITIAL_WEIGHTS, 0, before, after, 12, False)
    assert all(value != old for value, old in zip(weights, INITIAL_WEIGHTS))
    assert all(value == value for value in weights)
    assert -2 <= delta <= 2 and shaped_reward > 0


def test_hint_is_legal_and_does_not_consume_randomness(monkeypatch):
    board = [[2, 2, 4, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    state_before = [row[:] for row in board]
    monkeypatch.setattr(random, "choice", lambda *_args: (_ for _ in ()).throw(AssertionError("hint used RNG")))
    monkeypatch.setattr(random, "random", lambda: (_ for _ in ()).throw(AssertionError("hint used RNG")))
    direction = hint(board, INITIAL_WEIGHTS)
    assert direction in legal_moves(board)
    assert board == state_before


def test_terminal_hint_is_null():
    board = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    assert hint(board, INITIAL_WEIGHTS) is None

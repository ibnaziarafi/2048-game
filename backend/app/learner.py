"""Pure linear TD learner and expected-spawn hint policy."""

from __future__ import annotations

import math
from typing import Sequence

from app.game import DIRECTIONS, legal_moves, slide

ALGORITHM = "linear-td-v1"
FEATURE_ORDER = (
    "bias", "empty_fraction", "maximum_exponent", "maximum_in_corner",
    "smoothness", "monotonicity", "equal_adjacent_fraction", "legal_move_fraction",
)
INITIAL_WEIGHTS = (0.0, 2.0, 1.0, 1.0, 0.5, 1.0, 0.5, 1.0)
GAMMA = 0.99


def features(board: Sequence[Sequence[int]]) -> tuple[float, ...]:
    exponents = [[min(math.log2(v), 16) if v else 0.0 for v in row] for row in board]
    occupied = [(r, c, exponents[r][c]) for r in range(4) for c in range(4) if board[r][c]]
    maximum = max((v for _, _, v in occupied), default=0.0)
    corners = {(0, 0), (0, 3), (3, 0), (3, 3)}
    corner = float(bool(occupied) and any((r, c) in corners and e == maximum for r, c, e in occupied))
    adjacent: list[tuple[float, float]] = []
    for r in range(4):
        for c in range(4):
            if c < 3:
                adjacent.append((exponents[r][c], exponents[r][c + 1]))
            if r < 3:
                adjacent.append((exponents[r][c], exponents[r + 1][c]))
    smoothness = 1 - sum(abs(a - b) for a, b in adjacent) / (24 * 16)
    imbalance = 0.0
    lines = [*exponents, *[[exponents[r][c] for r in range(4)] for c in range(4)]]
    for line in lines:
        inc = sum(max(0.0, line[i + 1] - line[i]) for i in range(3))
        dec = sum(max(0.0, line[i] - line[i + 1]) for i in range(3))
        imbalance += min(inc, dec)
    monotonicity = 1 - imbalance / (8 * 3 * 16)
    equals = sum(1 for a, b in adjacent if a and a == b)
    result = (
        1.0,
        sum(v == 0 for row in board for v in row) / 16,
        maximum / 16,
        corner,
        smoothness,
        monotonicity,
        equals / 24,
        len(legal_moves(board)) / 4,
    )
    if any(not math.isfinite(x) or x < 0 or x > 1 for x in result):
        raise ValueError("invalid feature value")
    return result


def value(board: Sequence[Sequence[int]], weights: Sequence[float]) -> float:
    if not legal_moves(board):
        return 0.0
    estimate = sum(w * x for w, x in zip(weights, features(board), strict=True))
    if not math.isfinite(estimate):
        raise ValueError("non-finite model value")
    return max(-10.0, min(10.0, estimate))


def reward(score_gained: int, terminal: bool) -> float:
    return min(math.log2(1 + score_gained) / 16, 1) - (1 if terminal else 0)


def update(weights: Sequence[float], training_updates: int, before, after, score_gained: int, terminal: bool):
    """Return (new weights, delta, shaped reward), rejecting non-finite arithmetic."""
    phi = features(before)
    old_value = value(before, weights)
    next_value = 0.0 if terminal else value(after, weights)
    shaped = reward(score_gained, terminal)
    target = shaped + (0 if terminal else GAMMA * next_value)
    delta = max(-2.0, min(2.0, target - old_value))
    alpha = 0.01 / math.sqrt(1 + training_updates / 100000)
    denominator = 1 + sum(x * x for x in phi)
    new = tuple(max(-10.0, min(10.0, w + alpha * delta * x / denominator)) for w, x in zip(weights, phi, strict=True))
    if not all(math.isfinite(x) for x in (*new, delta, shaped, target)):
        raise ValueError("non-finite TD update")
    return new, delta, shaped


def hint(board: Sequence[Sequence[int]], weights: Sequence[float]) -> str | None:
    best_direction = None
    best_value = -math.inf
    for direction in DIRECTIONS:
        moved, gain = slide(board, direction)
        if moved == board:
            continue
        empties = [(r, c) for r in range(4) for c in range(4) if moved[r][c] == 0]
        if not empties:
            raise AssertionError("a changed 2048 slide must leave a spawn cell")
        expected = 0.0
        for r, c in empties:
            for tile, tile_probability in ((2, 0.9), (4, 0.1)):
                candidate = [row[:] for row in moved]
                candidate[r][c] = tile
                terminal = not legal_moves(candidate)
                expected += tile_probability / len(empties) * (
                    reward(gain, terminal) + (0 if terminal else GAMMA * value(candidate, weights))
                )
        if expected > best_value:
            best_direction, best_value = direction, expected
    return best_direction

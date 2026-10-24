#!/usr/bin/env python3
"""Read-only paired-seed pilot for a saved linear-td-v1 weight snapshot."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.game import legal_moves, slide
from app.learner import INITIAL_WEIGHTS, hint
from app.store import Store


def spawn(board, rng):
    empty = [(r, c) for r in range(4) for c in range(4) if board[r][c] == 0]
    if empty:
        r, c = rng.choice(empty)
        board[r][c] = 2 if rng.random() < 0.9 else 4


def play(weights, seed, cap):
    rng = random.Random(seed)
    board = [[0] * 4 for _ in range(4)]
    spawn(board, rng)
    spawn(board, rng)
    score = moves = 0
    started = time.perf_counter()
    while moves < cap:
        direction = hint(board, weights)
        if direction is None:
            break
        after, gain = slide(board, direction)
        if after == board:
            raise RuntimeError("policy returned an illegal direction")
        board = after
        score += gain
        spawn(board, rng)
        moves += 1
    terminal = not legal_moves(board)
    return {"seed": seed, "score": score, "maximum_tile": max(max(row) for row in board),
            "moves": moves, "terminal": terminal, "truncated": not terminal and moves >= cap,
            "elapsed_seconds": round(time.perf_counter() - started, 6)}


def summarize(rows):
    return {"games": len(rows), "mean_score": round(statistics.mean(x["score"] for x in rows), 2),
            "median_score": statistics.median(x["score"] for x in rows),
            "mean_maximum_tile": round(statistics.mean(x["maximum_tile"] for x in rows), 2),
            "terminal_games": sum(x["terminal"] for x in rows), "truncated_games": sum(x["truncated"] for x in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="existing local SQLite database; no schema creation or writes")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--move-cap", type=int, default=500)
    args = parser.parse_args()
    if args.seeds < 1 or args.seeds > 100 or args.move_cap < 1 or args.move_cap > 10000:
        parser.error("--seeds must be 1..100 and --move-cap must be 1..10000")
    db_path = args.db_path or str(Path(__file__).resolve().parents[1] / "data" / "2048.sqlite3")
    snapshot = Store(db_path=db_path, readonly=True).snapshot()
    if snapshot["algorithm"] != "linear-td-v1":
        parser.error("unsupported model algorithm")
    seeds = list(range(args.seeds))
    learned = [play(snapshot["weights"], seed, args.move_cap) for seed in seeds]
    prior = [play(INITIAL_WEIGHTS, seed, args.move_cap) for seed in seeds]
    print(json.dumps({"algorithm": snapshot["algorithm"], "model_version": snapshot["model_version"],
                      "training_samples": snapshot["training_samples"], "seeds": seeds, "move_cap": args.move_cap,
                      "current_model": {"runs": learned, "summary": summarize(learned)},
                      "seeded_prior": {"runs": prior, "summary": summarize(prior)},
                      "interpretation": "Paired seed pilot only; differing policies can encounter different boards and this does not establish player strength."},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

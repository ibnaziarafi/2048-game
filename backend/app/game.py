import random
from typing import List, Tuple, Dict, Any

DIRECTIONS = ("left", "right", "up", "down")


def _process_row(row: List[int]) -> Tuple[List[int], int]:
    values = [x for x in row if x]
    result: list[int] = []
    score = 0
    index = 0
    while index < len(values):
        if index + 1 < len(values) and values[index] == values[index + 1]:
            merged = values[index] * 2
            result.append(merged)
            score += merged
            index += 2
        else:
            result.append(values[index])
            index += 1
    return result + [0] * (4 - len(result)), score


def slide(board: List[List[int]], direction: str) -> Tuple[List[List[int]], int]:
    """Calculate a deterministic slide without mutating the supplied board."""
    if direction not in DIRECTIONS:
        raise ValueError(f"Invalid direction: {direction}")
    work = [row[:] for row in board]
    if direction in ("up", "down"):
        work = [[board[r][c] for r in range(4)] for c in range(4)]
    if direction in ("right", "down"):
        work = [row[::-1] for row in work]
    output, score = [], 0
    for row in work:
        merged, points = _process_row(row)
        output.append(merged)
        score += points
    if direction in ("right", "down"):
        output = [row[::-1] for row in output]
    if direction in ("up", "down"):
        output = [[output[r][c] for r in range(4)] for c in range(4)]
    return output, score


def legal_moves(board: List[List[int]]) -> list[str]:
    return [direction for direction in DIRECTIONS if slide(board, direction)[0] != board]


class Game:
    """
    Simple 2048 Game Engine based on a 4x4 2D Python list.
    0 represents an empty cell.
    """

    def __init__(self, board: List[List[int]] | None = None, score: int = 0):
        if board is not None:
            self.board = [row[:] for row in board]
            self.score = score
        else:
            self.board = [[0] * 4 for _ in range(4)]
            self.score = 0
            # Add two initial random tiles for a new game
            self.add_random_tile()
            self.add_random_tile()

        self.game_over = self.is_game_over()

    def add_random_tile(self) -> bool:
        """
        Adds a random tile (2 with 90% probability, 4 with 10%)
        to a random empty cell. Returns True if tile added, False if board full.
        """
        empty_cells = [
            (r, c)
            for r in range(4)
            for c in range(4)
            if self.board[r][c] == 0
        ]
        if not empty_cells:
            return False

        r, c = random.choice(empty_cells)
        self.board[r][c] = 2 if random.random() < 0.9 else 4
        return True

    def _process_row(self, row: List[int]) -> Tuple[List[int], int]:
        """
        Processes a single row for a LEFT move:
        1. Remove zeroes
        2. Merge adjacent equal tiles (each tile merges at most once)
        3. Pad zeroes to length 4
        Returns (processed_row, score_gained).
        """
        return _process_row(row)

    @staticmethod
    def _transpose(matrix: List[List[int]]) -> List[List[int]]:
        """Transposes a 4x4 matrix (swaps rows and columns)."""
        return [[matrix[r][c] for r in range(4)] for c in range(4)]

    @staticmethod
    def _reverse_rows(matrix: List[List[int]]) -> List[List[int]]:
        """Reverses each row in a matrix."""
        return [row[::-1] for row in matrix]

    def _execute_move(self, direction: str) -> Tuple[List[List[int]], int]:
        """
        Calculates the new board state and score gained for a direction
        without mutating self.board directly.
        """
        return slide(self.board, direction.lower())

    def move(self, direction: str) -> bool:
        """
        Executes a move in the given direction.
        If valid (board changes):
          - updates self.board and self.score
          - adds a random tile
          - updates self.game_over status
          - returns True
        If invalid (board unchanged):
          - state remains unchanged
          - returns False
        """
        new_board, score_gained = self._execute_move(direction)

        # Check if the move changed the board state
        if new_board != self.board:
            self.board = new_board
            self.score += score_gained
            self.add_random_tile()
            self.game_over = self.is_game_over()
            return True

        return False

    def move_left(self) -> bool:
        return self.move("left")

    def move_right(self) -> bool:
        return self.move("right")

    def move_up(self) -> bool:
        return self.move("up")

    def move_down(self) -> bool:
        return self.move("down")

    def can_move(self) -> bool:
        """
        Checks if any move is possible.
        True if there is at least one empty cell or any adjacent matching numbers.
        """
        # 1. Any empty cell?
        for r in range(4):
            for c in range(4):
                if self.board[r][c] == 0:
                    return True

        # 2. Any horizontal match?
        for r in range(4):
            for c in range(3):
                if self.board[r][c] == self.board[r][c + 1]:
                    return True

        # 3. Any vertical match?
        for r in range(3):
            for c in range(4):
                if self.board[r][c] == self.board[r + 1][c]:
                    return True

        return False

    def is_game_over(self) -> bool:
        """Returns True if no valid moves remain."""
        return not self.can_move()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "board": self.board,
            "score": self.score,
            "game_over": self.game_over,
        }


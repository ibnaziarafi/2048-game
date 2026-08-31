import random
from typing import List, Tuple, Dict, Any


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
        # Step 1: Remove zeroes
        non_zeros = [x for x in row if x != 0]

        # Step 2: Merge adjacent equal tiles
        merged = []
        score_gained = 0
        skip = False

        for i in range(len(non_zeros)):
            if skip:
                skip = False
                continue

            if i + 1 < len(non_zeros) and non_zeros[i] == non_zeros[i + 1]:
                val = non_zeros[i] * 2
                merged.append(val)
                score_gained += val
                skip = True
            else:
                merged.append(non_zeros[i])

        # Step 3: Add zeroes back to maintain 4 elements
        while len(merged) < 4:
            merged.append(0)

        return merged, score_gained

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
        direction = direction.lower()
        working_board = [row[:] for row in self.board]
        total_score_gained = 0

        if direction == "left":
            processed_board = []
            for row in working_board:
                new_row, pts = self._process_row(row)
                processed_board.append(new_row)
                total_score_gained += pts
            return processed_board, total_score_gained

        elif direction == "right":
            reversed_board = self._reverse_rows(working_board)
            processed_board = []
            for row in reversed_board:
                new_row, pts = self._process_row(row)
                processed_board.append(new_row)
                total_score_gained += pts
            return self._reverse_rows(processed_board), total_score_gained

        elif direction == "up":
            transposed = self._transpose(working_board)
            processed_board = []
            for row in transposed:
                new_row, pts = self._process_row(row)
                processed_board.append(new_row)
                total_score_gained += pts
            return self._transpose(processed_board), total_score_gained

        elif direction == "down":
            transposed = self._transpose(working_board)
            reversed_board = self._reverse_rows(transposed)
            processed_board = []
            for row in reversed_board:
                new_row, pts = self._process_row(row)
                processed_board.append(new_row)
                total_score_gained += pts
            unreversed = self._reverse_rows(processed_board)
            return self._transpose(unreversed), total_score_gained

        else:
            raise ValueError(f"Invalid direction: {direction}")

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

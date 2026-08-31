import pytest
from app.game import Game


def test_initial_game():
    """Test initial game state with 2 random tiles."""
    game = Game()
    non_zero_count = sum(
        1 for r in range(4) for c in range(4) if game.board[r][c] != 0
    )
    assert non_zero_count == 2
    assert game.score == 0
    assert game.game_over is False


def test_row_processing_compress_and_merge():
    """Test standard row processing: [2, 0, 2, 0] -> [4, 0, 0, 0]."""
    game = Game(board=[[0] * 4 for _ in range(4)], score=0)
    row = [2, 0, 2, 0]
    processed_row, pts = game._process_row(row)
    assert processed_row == [4, 0, 0, 0]
    assert pts == 4


def test_multiple_merges_no_double_merge():
    """
    Test that [2, 2, 2, 2] merges into [4, 4, 0, 0] and NOT [8, 0, 0, 0].
    Also tests score calculation (4 + 4 = 8).
    """
    game = Game(board=[[0] * 4 for _ in range(4)], score=0)
    row = [2, 2, 2, 2]
    processed_row, pts = game._process_row(row)
    assert processed_row == [4, 4, 0, 0]
    assert pts == 8


def test_different_values_no_merge():
    """Test [2, 4, 8, 16] does not merge."""
    game = Game(board=[[0] * 4 for _ in range(4)], score=0)
    row = [2, 4, 8, 16]
    processed_row, pts = game._process_row(row)
    assert processed_row == [2, 4, 8, 16]
    assert pts == 0


def test_score_increase():
    """Test score increases correctly for [2, 2, 4, 4] -> [4, 8, 0, 0] (score gained = 4 + 8 = 12)."""
    game = Game(
        board=[
            [2, 2, 4, 4],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ],
        score=0,
    )
    row = game.board[0]
    processed_row, pts = game._process_row(row)
    assert processed_row == [4, 8, 0, 0]
    assert pts == 12


def test_move_left():
    """Test full board move left with random tile generation."""
    board = [
        [2, 0, 2, 0],
        [0, 0, 0, 0],
        [0, 2, 0, 0],
        [0, 0, 0, 4],
    ]
    game = Game(board=board, score=0)
    moved = game.move_left()

    assert moved is True
    assert game.score == 4
    # First row should start with 4
    assert game.board[0][0] == 4


def test_move_right():
    """Test move right direction."""
    board = [
        [2, 0, 2, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]
    game = Game(board=board, score=0)
    moved = game.move_right()

    assert moved is True
    assert game.score == 4
    # Merged 4 should land at index 3
    assert game.board[0][3] == 4


def test_move_up():
    """Test move up direction."""
    board = [
        [2, 0, 0, 0],
        [0, 0, 0, 0],
        [2, 0, 0, 0],
        [0, 0, 0, 0],
    ]
    game = Game(board=board, score=0)
    moved = game.move_up()

    assert moved is True
    assert game.score == 4
    assert game.board[0][0] == 4


def test_move_down():
    """Test move down direction."""
    board = [
        [2, 0, 0, 0],
        [0, 0, 0, 0],
        [2, 0, 0, 0],
        [0, 0, 0, 0],
    ]
    game = Game(board=board, score=0)
    moved = game.move_down()

    assert moved is True
    assert game.score == 4
    assert game.board[3][0] == 4


def test_invalid_move_no_state_change():
    """
    If a move does not change the board:
    - board remains unchanged (except no random tile added)
    - score remains unchanged
    - returns False
    """
    board = [
        [2, 4, 8, 16],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]
    game = Game(board=board, score=10)
    moved = game.move_left()

    assert moved is False
    assert game.score == 10
    assert game.board[0] == [2, 4, 8, 16]


def test_game_over_full_board_no_merges():
    """Test game over detection when board is full and no merges are possible."""
    board = [
        [2, 4, 2, 4],
        [4, 2, 4, 2],
        [2, 4, 2, 4],
        [4, 2, 4, 2],
    ]
    game = Game(board=board, score=100)

    assert game.can_move() is False
    assert game.is_game_over() is True


def test_available_move_full_board_with_adjacent_match():
    """Test full board where at least one merge is possible."""
    board = [
        [2, 4, 2, 4],
        [4, 2, 4, 2],
        [2, 4, 2, 2],  # Matching 2s at (2,2) and (2,3)
        [4, 2, 4, 2],
    ]
    game = Game(board=board, score=100)

    assert game.can_move() is True
    assert game.is_game_over() is False

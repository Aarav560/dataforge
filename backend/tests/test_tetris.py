import numpy as np

from app.synthetic import tetris as T


def test_rotations_and_shapes():
    assert len(T.ROTATIONS["O"]) == 1
    assert len(T.ROTATIONS["I"]) == 2
    assert len(T.ROTATIONS["T"]) == 4
    assert len(T.SHAPES) == 19  # the 19 fixed tetrominoes


def test_place_and_clear_line():
    board = np.zeros((T.ROWS, T.COLS), dtype=np.uint8)
    board[-1, :6] = 1
    new, lines = T.place(board, "I", 0, 6)
    assert lines == 1
    assert not new.any()


def test_place_rejects_overflow():
    board = np.ones((T.ROWS, T.COLS), dtype=np.uint8)
    assert T.place(board, "O", 0, 0) is None


def test_features():
    board = np.zeros((T.ROWS, T.COLS), dtype=np.uint8)
    board[-1, 0] = 1
    board[-3, 0] = 1  # one hole at row -2
    height, holes, bump = T.features(board)
    assert (height, holes, bump) == (3, 1, 3)


def test_heuristic_beats_random_and_is_deterministic():
    h = list(T.play_game("heuristic", seed=4, max_steps=200))
    r = list(T.play_game("random", seed=4, max_steps=200))
    assert sum(s.lines for s in h) > sum(s.lines for s in r)
    again = list(T.play_game("heuristic", seed=4, max_steps=200))
    assert [(s.piece, s.rotation, s.column) for s in h] == [(s.piece, s.rotation, s.column) for s in again]

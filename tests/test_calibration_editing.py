import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.calibration.editing import (
    History, MIN_SIZE, handle_points, move_rect, resize_rect, scale_rect, snap_move, snap_resize, snap_targets,
)

RECT = [0.1, 0.2, 0.4, 0.3]


def test_handle_points():
    p = handle_points(RECT)
    assert p['tl'] == (0.1, 0.2) and p['br'] == pytest.approx((0.5, 0.5)) and p['r'] == pytest.approx((0.5, 0.35))


@pytest.mark.parametrize('handle, point, expected', [
    ('br', (0.6, 0.7), [0.1, 0.2, 0.5, 0.5]),
    ('tl', (0.0, 0.1), [0.0, 0.1, 0.5, 0.4]),
    ('r', (0.7, 0.99), [0.1, 0.2, 0.6, 0.3]),      # an edge handle only moves its edge
    ('t', (0.99, 0.0), [0.1, 0.0, 0.4, 0.5]),
    ('l', (0.9, 0.5), [0.5 - MIN_SIZE, 0.2, MIN_SIZE, 0.3]),   # can't pass the opposite edge
])
def test_resize_rect(handle, point, expected):
    assert resize_rect(RECT, handle, point) == pytest.approx(expected)


def test_move_and_scale():
    assert move_rect(RECT, 0.1, -0.1) == pytest.approx([0.2, 0.1, 0.4, 0.3])
    assert scale_rect(RECT, 2) == pytest.approx([-0.1, 0.05, 0.8, 0.6])   # about the centre


def test_snap_move_to_screen_centre_and_other_frames():
    targets = snap_targets([[0.6, 0.6, 0.2, 0.2]])
    rect, guides = snap_move([0.097, 0.3, 0.4, 0.1], targets, (0.01, 0.01))
    assert rect[0] == pytest.approx(0.1)            # right edge 0.497 -> screen centre 0.5
    assert guides == [('x', 0.5)]
    rect, guides = snap_move([0.1, 0.495, 0.2, 0.1], targets, (0.01, 0.01))
    assert rect[1] == pytest.approx(0.5) and ('y', 0.5) in guides
    rect, guides = snap_move([0.2, 0.2, 0.1, 0.1], targets, (0.01, 0.01))
    assert guides == [] and rect == [0.2, 0.2, 0.1, 0.1]


def test_snap_resize_only_moves_dragged_edges():
    rect, guides = snap_resize([0.103, 0.2, 0.894, 0.3], "r", snap_targets([]), (0.01, 0.01))   # right edge at 0.997
    assert rect == pytest.approx([0.103, 0.2, 0.897, 0.3])   # right edge -> 1.0, left untouched
    assert guides == [('x', 1.0)]


def test_history_undo_redo_and_grouping():
    h = History()
    config = {'n': 0}
    h.checkpoint(config)
    config = {'n': 1}
    h.checkpoint(config, group='arrow')
    config = {'n': 2}
    h.checkpoint(config, group='arrow')        # same group: no new step
    config = {'n': 3}
    config = h.undo(config)
    assert config == {'n': 1}
    config = h.undo(config)
    assert config == {'n': 0}
    assert h.undo(config) is None
    config = h.redo(config)
    assert config == {'n': 1}
    h.checkpoint(config)                        # a new change clears redo
    assert not h.can_redo

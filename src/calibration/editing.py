"""
Pure layout-editing helpers for the calibration UI: moving and resizing
rects in normalized screen space, snapping, and undo history.
"""

import copy


MIN_SIZE = 0.02
# Rect handles: corners and edge midpoints. A handle moves the edges named
# by its letters (t/b = top/bottom, l/r = left/right).
RECT_HANDLES = ('tl', 't', 'tr', 'r', 'br', 'b', 'bl', 'l')


def handle_points(rect):
    """Positions of the rect's handles, in normalized screen space."""
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    return {'tl': (x, y), 't': (cx, y), 'tr': (x + w, y), 'r': (x + w, cy),
            'br': (x + w, y + h), 'b': (cx, y + h), 'bl': (x, y + h), 'l': (x, cy)}


def _edges(rect):
    x, y, w, h = rect
    return x, y, x + w, y + h


def _rect(x0, y0, x1, y1):
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def resize_rect(rect, handle, point):
    """The rect with the handle's edges dragged to point; the opposite edges stay put."""
    x0, y0, x1, y1 = _edges(rect)
    px, py = point
    if 'l' in handle:
        x0 = min(px, x1 - MIN_SIZE)
    if 'r' in handle:
        x1 = max(px, x0 + MIN_SIZE)
    if 't' in handle:
        y0 = min(py, y1 - MIN_SIZE)
    if 'b' in handle:
        y1 = max(py, y0 + MIN_SIZE)
    return _rect(x0, y0, x1, y1)


def move_rect(rect, dx, dy):
    x, y, w, h = rect
    return [round(x + dx, 4), round(y + dy, 4), w, h]


def scale_rect(rect, factor):
    """Grow or shrink the rect about its centre."""
    x, y, w, h = rect
    nw, nh = max(MIN_SIZE, w * factor), max(MIN_SIZE, h * factor)
    return [round(x + (w - nw) / 2, 4), round(y + (h - nh) / 2, 4), round(nw, 4), round(nh, 4)]


# -- snapping ------------------------------------------------------------------

def snap_targets(other_rects):
    """Lines worth snapping to: the screen's edges and centre, and other frames' edges."""
    xs, ys = {0.0, 0.5, 1.0}, {0.0, 0.5, 1.0}
    for x, y, w, h in other_rects:
        xs.update((x, x + w))
        ys.update((y, y + h))
    return sorted(xs), sorted(ys)


def _nearest(values, targets, threshold):
    """Best (offset, target) moving any of values onto a target within threshold, or None."""
    best = None
    for v in values:
        for t in targets:
            d = t - v
            if abs(d) <= threshold and (best is None or abs(d) < abs(best[0])):
                best = (d, t)
    return best


def snap_move(rect, targets, threshold):
    """Shift a moved rect so an edge (or its centre) lines up with a nearby target. Returns (rect, guides)."""
    xs, ys = targets
    x, y, w, h = rect
    guides = []
    sx = _nearest((x, x + w / 2, x + w), xs, threshold[0])
    if sx:
        x += sx[0]
        guides.append(('x', sx[1]))
    sy = _nearest((y, y + h / 2, y + h), ys, threshold[1])
    if sy:
        y += sy[0]
        guides.append(('y', sy[1]))
    return [round(x, 4), round(y, 4), w, h], guides


def snap_resize(rect, handle, targets, threshold):
    """Snap the edges a handle moves to nearby targets. Returns (rect, guides)."""
    xs, ys = targets
    x0, y0, x1, y1 = _edges(rect)
    guides = []

    def snap(value, axis_targets, thr, axis):
        hit = _nearest((value,), axis_targets, thr)
        if hit:
            guides.append((axis, hit[1]))
            return hit[1]
        return value

    if 'l' in handle:
        x0 = min(snap(x0, xs, threshold[0], 'x'), x1 - MIN_SIZE)
    if 'r' in handle:
        x1 = max(snap(x1, xs, threshold[0], 'x'), x0 + MIN_SIZE)
    if 't' in handle:
        y0 = min(snap(y0, ys, threshold[1], 'y'), y1 - MIN_SIZE)
    if 'b' in handle:
        y1 = max(snap(y1, ys, threshold[1], 'y'), y0 + MIN_SIZE)
    return _rect(x0, y0, x1, y1), guides


# -- undo ------------------------------------------------------------------------

class History:
    """Undo/redo of whole-config snapshots."""

    def __init__(self, limit=200):
        self.limit = limit
        self._undo = []
        self._redo = []
        self._last_group = None

    def checkpoint(self, config, group=None):
        """
        Remember config before a change. Consecutive changes in the same
        group (e.g. holding an arrow key) share one undo step.
        """
        if group is not None and group == self._last_group:
            return
        self._last_group = group
        self._undo.append(copy.deepcopy(config))
        del self._undo[:-self.limit]
        self._redo.clear()

    def end_group(self):
        self._last_group = None

    def undo(self, current):
        if not self._undo:
            return None
        self._last_group = None
        self._redo.append(copy.deepcopy(current))
        return self._undo.pop()

    def redo(self, current):
        if not self._redo:
            return None
        self._last_group = None
        self._undo.append(copy.deepcopy(current))
        return self._redo.pop()

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

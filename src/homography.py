import numpy as np
import cv2


def compute_homography(source_rect, dest_quad):
    """
    Compute homography matrix from source rectangle to destination quad.

    Args:
        source_rect: (width, height) of source media
        dest_quad: dict with 'tl', 'tr', 'br', 'bl' keys, each [x, y]

    Returns:
        4x4 homography matrix for use in shader/GL transform
    """
    src_points = np.float32([
        [0, 0],
        [source_rect[0], 0],
        [source_rect[0], source_rect[1]],
        [0, source_rect[1]]
    ])

    dst_points = np.float32([
        dest_quad['tl'],
        dest_quad['tr'],
        dest_quad['br'],
        dest_quad['bl']
    ])

    H = cv2.getPerspectiveTransform(src_points, dst_points)

    # Embed the 3x3 homography into the 4x4 matrix so that, for a vertex
    # shader computing `transform * vec4(x, y, 0, 1)`, the GPU's hardware
    # perspective divide (by the resulting w) reproduces H's projective
    # divide. Rows/cols 0,1 carry H's x/y terms, row/col 2 (z) passes
    # through untouched, and row/col 3 carries H's translation/w terms.
    H_4x4 = np.eye(4, dtype=np.float32)
    rows_cols = [0, 1, 3]
    H_4x4[np.ix_(rows_cols, rows_cols)] = H

    return H_4x4


def homography_to_corners(homography, source_w, source_h):
    """
    Extract destination corners from a homography matrix.
    Inverse of compute_homography — useful for validation.
    """
    src_corners = np.array([
        [0, 0, 1],
        [source_w, 0, 1],
        [source_w, source_h, 1],
        [0, source_h, 1]
    ], dtype=np.float32).T

    rows_cols = [0, 1, 3]
    H = homography[np.ix_(rows_cols, rows_cols)]
    dst = H @ src_corners
    dst = dst / dst[2, :]

    return dst[:2, :].T


def nudge_corner(quad, corner_name, dx, dy):
    """Nudge a corner by (dx, dy) and return updated quad."""
    updated = quad.copy()
    updated[corner_name][0] += dx
    updated[corner_name][1] += dy
    return updated


CORNER_ORDER = ('tl', 'tr', 'br', 'bl')
UNIT_SQUARE = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])


def corners_to_array(corners):
    """Corner dict ({'tl': [x, y], ...}) -> 4x2 float32 array in TL, TR, BR, BL order."""
    return np.float32([corners[k] for k in CORNER_ORDER])


def array_to_corners(points):
    """4x2 array in TL, TR, BR, BL order -> corner dict with plain-float lists."""
    return {k: [float(points[i][0]), float(points[i][1])] for i, k in enumerate(CORNER_ORDER)}


def apply_homography(H, points):
    """Apply a 3x3 homography to an Nx2 array of points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.asarray(H, dtype=np.float64)).reshape(-1, 2)


def screen_homography(screen_corners):
    """
    3x3 homography mapping normalized screen space (unit square, y down)
    onto the screen's corners in canvas pixels.
    """
    return cv2.getPerspectiveTransform(UNIT_SQUARE, corners_to_array(screen_corners))


def rect_to_canvas_corners(rect, S):
    """
    Map a normalized screen-space rect [x, y, w, h] through the screen
    homography S to a corner dict in canvas pixels. Because the screen is a
    flat surface, a rectangle on it stays a rectangle physically -- S alone
    carries all of the keystone correction.
    """
    x, y, w, h = rect
    rect_pts = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    return array_to_corners(apply_homography(S, rect_pts))


def uv_homography(corners):
    """
    3x3 homography mapping canvas pixels to texture UV for a quad, so that
    each corner maps to its unit-square UV (TL=(0,0) ... BL=(0,1)). The
    fragment shader applies this per pixel, which gives an exact perspective
    warp instead of the affine per-triangle interpolation you get from
    per-vertex UVs.
    """
    return cv2.getPerspectiveTransform(corners_to_array(corners), UNIT_SQUARE)


def quad_footprint(corners):
    """Approximate on-canvas pixel size (width, height) of a quad: its longest opposing edges."""
    p = corners_to_array(corners)
    width = max(np.linalg.norm(p[1] - p[0]), np.linalg.norm(p[2] - p[3]))
    height = max(np.linalg.norm(p[3] - p[0]), np.linalg.norm(p[2] - p[1]))
    return int(round(width)), int(round(height))

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

    H_4x4 = np.eye(4, dtype=np.float32)
    H_4x4[:2, :3] = H

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

    H = homography[:2, :3]
    dst = H @ src_corners
    dst = dst / dst[1, :]

    return dst.T


def nudge_corner(quad, corner_name, dx, dy):
    """Nudge a corner by (dx, dy) and return updated quad."""
    updated = quad.copy()
    updated[corner_name][0] += dx
    updated[corner_name][1] += dy
    return updated

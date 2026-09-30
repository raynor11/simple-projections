"""
Find the projector screen with a camera.

1. Project a chessboard whose inner-corner positions in projector pixels
   are known, find it in the camera image, and compute the camera ->
   projector homography. The screen is flat, so one homography maps any
   point on it exactly.
2. Project full white and full black. The white screen surface lights up;
   its black border absorbs the light and leaves a dark ring around it.
   The bright four-sided region with no holes in it is the screen.
3. Map the screen's corners through the homography into projector pixels.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .homography import apply_homography, array_to_corners


PATTERN = (9, 6)            # inner corners (columns, rows) of the projected chessboard
BOARD_FRACTION = 0.7        # the chessboard spans this share of the canvas
SETTLE_SECONDS = 1.5        # let the camera's exposure/white balance settle after each pattern
MIN_SCREEN_AREA = 0.03      # of the camera image
EDGE_MARGIN_PX = 6          # projector px: a screen corner this close to the canvas edge is suspicious
EXPECTED_ASPECT = 16 / 9


class DetectionError(RuntimeError):
    pass


@dataclass
class DetectionResult:
    corners: dict                      # screen corners in projector (canvas) pixels
    camera_quad: np.ndarray            # screen corners in the camera image
    warnings: list = field(default_factory=list)


# -- patterns ----------------------------------------------------------------

def chessboard_pattern(width, height, pattern=PATTERN, fraction=BOARD_FRACTION):
    """
    A white canvas with a centered chessboard. Returns (RGBA image, inner
    corner positions in canvas pixels, row-major from the top-left).
    """
    cols, rows = pattern
    square = int(min(width * fraction / (cols + 1), height * fraction / (rows + 1)))
    board_w, board_h = square * (cols + 1), square * (rows + 1)
    x0, y0 = (width - board_w) // 2, (height - board_h) // 2

    img = np.full((height, width, 4), 255, np.uint8)
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                img[y0 + r * square:y0 + (r + 1) * square, x0 + c * square:x0 + (c + 1) * square, :3] = 0
    corners = np.float32([[x0 + (c + 1) * square, y0 + (r + 1) * square]
                          for r in range(rows) for c in range(cols)])
    return img, corners


def solid_pattern(width, height, value):
    img = np.full((height, width, 4), value, np.uint8)
    img[..., 3] = 255
    return img


def outline_pattern(width, height, corners, color=(0, 255, 0)):
    """Black canvas with the detected screen outline, for the user to confirm."""
    img = np.zeros((height, width, 4), np.uint8)
    img[..., 3] = 255
    pts = np.int32([corners[k] for k in ('tl', 'tr', 'br', 'bl')])
    cv2.polylines(img, [pts], True, color + (255,), 4, cv2.LINE_AA)
    for p in pts:
        cv2.circle(img, tuple(int(v) for v in p), 12, color + (255,), 3, cv2.LINE_AA)
    return img


# -- analysis (pure functions) -----------------------------------------------

def to_gray(img):
    if img.ndim == 2:
        return img
    code = cv2.COLOR_BGRA2GRAY if img.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(img, code)


def order_corners(points):
    """4 points in any order -> TL, TR, BR, BL."""
    pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
    s, d = pts.sum(axis=1), pts[:, 1] - pts[:, 0]
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])


def find_projector_homography(cam_img, board_px, pattern=PATTERN):
    """Camera -> projector homography from a photo of the projected chessboard."""
    gray = to_gray(cam_img)
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    found, cam_pts = cv2.findChessboardCornersSB(gray, pattern, flags=flags)
    if not found:
        raise DetectionError("Couldn't find the projected chessboard in the camera image. "
                             "Is the camera pointed at the screen and in focus?")
    cam_pts = cam_pts.reshape(-1, 2)
    # The detector may number the corners from either end of the board. The
    # camera is roughly upright relative to the projector, so the first
    # corner should be the top-left one.
    if cam_pts[0].sum() > cam_pts[-1].sum():
        cam_pts = cam_pts[::-1]
    H, _ = cv2.findHomography(cam_pts, board_px, cv2.RANSAC, 3.0)
    if H is None:
        raise DetectionError("Couldn't compute the camera-to-projector mapping")
    return H


def _refine_quad(contour, quad):
    """Sub-pixel corners: fit a line to the contour points along each side and intersect neighbours."""
    pts = contour.reshape(-1, 2).astype(np.float64)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        ab = b - a
        length = np.linalg.norm(ab)
        t = ((pts - a) @ ab) / (length ** 2)
        rel = pts - a
        dist = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / length
        # Middle 80% of each side, away from rounded/blurred corners.
        side = pts[(t > 0.1) & (t < 0.9) & (dist < max(3.0, 0.02 * length))]
        if len(side) < 5:
            return quad
        vx, vy, x0, y0 = cv2.fitLine(side.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    refined = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        denom = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(denom) < 1e-9:
            return quad
        s = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / denom
        refined.append(p1 + s * d1)
    return np.array(refined)


def find_screen_quad(white_img, black_img):
    """
    The screen's corners in the camera image (TL, TR, BR, BL), from photos
    taken while projecting full white and full black.
    """
    lit = cv2.subtract(to_gray(white_img), to_gray(black_img))
    lit = cv2.GaussianBlur(lit, (5, 5), 0)
    _, mask = cv2.threshold(lit, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        raise DetectionError("Nothing lit up when projecting white. Is the projector on?")
    image_area = mask.shape[0] * mask.shape[1]

    best, best_area = None, 0
    for i, contour in enumerate(contours):
        if hierarchy[0][i][3] != -1:
            continue   # a hole, not a lit region
        area = cv2.contourArea(contour)
        if area < MIN_SCREEN_AREA * image_area or area <= best_area:
            continue
        # The lit wall around a screen is a ring with the screen's border as
        # a big hole in it; the screen itself is solid.
        child = hierarchy[0][i][2]
        holes = 0.0
        while child != -1:
            holes += cv2.contourArea(contours[child])
            child = hierarchy[0][child][0]
        if holes > 0.05 * area:
            continue
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        best, best_area = (contour, order_corners(approx)), area

    if best is None:
        raise DetectionError("Couldn't find a four-sided screen with a dark border in the camera image")
    contour, quad = best
    return order_corners(_refine_quad(contour, quad))


def detect_screen(white_img, black_img, board_img, board_px, canvas_size, pattern=PATTERN):
    """Screen corners in projector pixels, plus warnings about anything that looks off."""
    H = find_projector_homography(board_img, board_px, pattern)
    cam_quad = find_screen_quad(white_img, black_img)
    proj = order_corners(apply_homography(H, cam_quad))

    warnings = []
    width, height = canvas_size
    if (proj[:, 0].min() < EDGE_MARGIN_PX or proj[:, 1].min() < EDGE_MARGIN_PX
            or proj[:, 0].max() > width - EDGE_MARGIN_PX or proj[:, 1].max() > height - EDGE_MARGIN_PX):
        warnings.append("The detected screen touches the edge of the projected image. The "
                        "projection may not cover the whole screen, so its border wasn't seen.")
    top, bottom = np.linalg.norm(proj[1] - proj[0]), np.linalg.norm(proj[2] - proj[3])
    left, right = np.linalg.norm(proj[3] - proj[0]), np.linalg.norm(proj[2] - proj[1])
    aspect = (top + bottom) / (left + right)
    if abs(aspect / EXPECTED_ASPECT - 1) > 0.15:
        warnings.append(f"The detected screen's aspect ratio is {aspect:.2f}, not the expected "
                        f"{EXPECTED_ASPECT:.2f} (16:9). Check the preview carefully.")
    return DetectionResult(corners=array_to_corners(proj), camera_quad=cam_quad, warnings=warnings)


# -- running it on real hardware ---------------------------------------------

class CameraGrabber:
    """Keeps the newest camera frame while patterns are on screen."""

    def __init__(self, device, width=1920, height=1080):
        from .sources.v4l2 import open_capture
        self.cap = open_capture(device, width, height)
        if self.cap is None:
            raise DetectionError(f"Couldn't open the camera {device!r}. "
                                 "Stop the projection-mapper service first so it isn't in use.")

    def set_auto_exposure(self, on):
        # V4L2 UVC: 3 = auto, 1 = manual (holds the current exposure).
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if on else 1)

    def grab_after(self, seconds, pump):
        """Keep reading (so the buffer stays fresh) for `seconds`, then average a few frames."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pump()
            self.cap.read()
        frames = []
        for _ in range(3):
            ok, frame = self.cap.read()
            if ok:
                frames.append(frame.astype(np.float32))
        if not frames:
            raise DetectionError("The camera stopped delivering frames")
        return np.mean(frames, axis=0).astype(np.uint8)

    def close(self):
        self.cap.release()


def capture_and_detect(show, pump, device, canvas_size, camera_size=(1920, 1080), save_dir='captures'):
    """
    Project the patterns with show(rgba), photograph each, and detect the
    screen. pump() keeps the window responsive while waiting. The photos are
    saved to save_dir for troubleshooting.
    """
    width, height = canvas_size
    board, board_px = chessboard_pattern(width, height)
    camera = CameraGrabber(device, *camera_size)
    try:
        # Let auto-exposure settle on the brightest pattern, then hold it so
        # all three photos are directly comparable.
        show(solid_pattern(width, height, 255))
        camera.grab_after(SETTLE_SECONDS * 2, pump)
        camera.set_auto_exposure(False)
        white = camera.grab_after(SETTLE_SECONDS, pump)
        show(solid_pattern(width, height, 0))
        black = camera.grab_after(SETTLE_SECONDS, pump)
        show(board)
        board_img = camera.grab_after(SETTLE_SECONDS, pump)
    finally:
        camera.set_auto_exposure(True)
        camera.close()

    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)
    for name, img in (('white', white), ('black', black), ('board', board_img)):
        cv2.imwrite(str(save_path / f"detect_{name}.png"), img)

    return detect_screen(white, black, board_img, board_px, canvas_size)

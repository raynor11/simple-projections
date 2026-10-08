"""
Find the projector screen with a camera.

1. Project a chessboard whose inner-corner positions in projector pixels
   are known, find it in the camera image, and compute the camera ->
   projector homography. The screen is flat, so one homography maps any
   point on it exactly.
2. Project full white, warp the photo into projector pixels with that
   homography, and scan outwards from inside the screen to where the bright
   screen meets its dark border. A line fitted to each side gives the corners.
   (This webcam has no exposure lock, so the white and black photos aren't
   comparable; only brightness differences within the white photo are used.)
3. Wide-angle webcams bow straight lines, so a lens model is fitted that
   makes the chessboard and the screen's edges straight, and the corners
   are recomputed with it.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .homography import apply_homography, array_to_corners


PATTERN = (9, 6)            # inner corners (columns, rows) of the projected chessboard
BOARD_FRACTION = 0.7        # the chessboard spans this share of the canvas
BOARD_WHITE_LEVELS = (255, 150, 90)   # chessboard brightness to try, for an overexposing camera
SETTLE_SECONDS = 1.5        # let the camera's exposure/white balance settle after each pattern
EDGE_MARGIN_PX = 6          # projector px: a screen corner this close to the canvas edge is suspicious
EXPECTED_ASPECT = 16 / 9
# Finding the screen's edges (projector pixels unless noted):
SCAN_MARGIN_PX = 200        # also look this far past the canvas edge
SCAN_START = 0.3            # start scanning this far in from each side (inside the screen)
SCAN_SPAN = (0.2, 0.8)      # scan lines cover this middle part of each side, away from corners
SCAN_LINES = 80             # scan lines per side
SCAN_REF_SAMPLES = 40       # samples at the start of a scan that give the screen's brightness
BORDER_DARKNESS = 0.5       # the border starts where brightness drops below this share
BORDER_SEARCH_PX = 40       # look this far into the border for its darkest level
LINE_TOLERANCE_PX = 2.0
MIN_SCREEN_BRIGHTNESS = 40  # 8-bit: the middle of the screen in the white photo
EDGE_SPAN = (0.05, 0.95)    # second pass: scan this much of each side of the screen
LENS_TRIM = 0.1             # ignore the worst 10% of edge points when straightening (stuck-on objects)
LENS_MIN_GAIN = 0.8         # use a lens correction only if it cuts the misfit by at least 20%
MIN_EDGE_POINTS = 10


class DetectionError(RuntimeError):
    pass


@dataclass
class DetectionResult:
    corners: dict                      # screen corners in projector (canvas) pixels
    camera_quad: np.ndarray            # screen corners in the camera image
    warnings: list = field(default_factory=list)


# -- patterns ----------------------------------------------------------------

def chessboard_pattern(width, height, pattern=PATTERN, fraction=BOARD_FRACTION, white=255):
    """
    A white (or, with `white` below 255, grey) canvas with a centered
    chessboard. Returns (RGBA image, inner corner positions in canvas pixels,
    row-major from the top-left).
    """
    cols, rows = pattern
    square = int(min(width * fraction / (cols + 1), height * fraction / (rows + 1)))
    board_w, board_h = square * (cols + 1), square * (rows + 1)
    x0, y0 = (width - board_w) // 2, (height - board_h) // 2

    img = np.full((height, width, 4), 255, np.uint8)
    img[..., :3] = white
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


def _outer_square_brightness(gray, cam_pts, pattern, first):
    """
    Brightness at the centre of the board's corner square beyond the first
    (or last) inner corner, sampled in the camera image.
    """
    cols, _ = pattern
    pts = cam_pts if first else cam_pts[::-1]
    corner, along_row, along_col = pts[0], pts[1], pts[cols]
    centre = corner + 0.5 * ((corner - along_row) + (corner - along_col))
    r = max(2, int(0.15 * np.linalg.norm(corner - along_row)))
    x, y = int(round(centre[0])), int(round(centre[1]))
    patch = gray[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    return float(patch.mean()) if patch.size else 0.0


def find_board_corners(cam_img, pattern=PATTERN):
    """The projected chessboard's inner corners in the camera image, numbered from its top-left."""
    gray = to_gray(cam_img)
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    found, cam_pts = cv2.findChessboardCornersSB(gray, pattern, flags=flags)
    if not found:
        raise DetectionError("Couldn't find the projected chessboard in the camera image. "
                             "Is the camera pointed at the screen and in focus?")
    cam_pts = cam_pts.reshape(-1, 2).astype(np.float64)
    # The detector may number the corners from either end of the board, and
    # the camera can be mounted at any angle (even on its side), so tell the
    # ends apart by the board itself: its top-left square is black and its
    # bottom-right square white.
    if _outer_square_brightness(gray, cam_pts, pattern, first=True) > \
            _outer_square_brightness(gray, cam_pts, pattern, first=False):
        cam_pts = cam_pts[::-1]
    return cam_pts


def find_projector_homography(cam_img, board_px, pattern=PATTERN):
    """Camera -> projector homography from a photo of the projected chessboard."""
    H, _ = cv2.findHomography(find_board_corners(cam_img, pattern), board_px, cv2.RANSAC, 3.0)
    if H is None:
        raise DetectionError("Couldn't compute the camera-to-projector mapping")
    return H


def _crossing(profile, start_ref=SCAN_REF_SAMPLES):
    """
    Index (sub-pixel) where a brightness profile, starting inside the screen,
    first falls to halfway between the screen and the border's darkness.
    None if it never gets dark (no border on this line).
    """
    ref = float(np.median(profile[:start_ref]))
    if ref <= 0:
        return None
    dark = np.flatnonzero(profile < BORDER_DARKNESS * ref)
    dark = dark[dark >= start_ref // 2]
    if dark.size == 0:
        return None
    first = dark[0]
    # The border's own level: the darkest point just past where it starts.
    floor = float(profile[first:first + BORDER_SEARCH_PX].min())
    half = (ref + floor) / 2
    i = first
    while i > 0 and profile[i - 1] < half:
        i -= 1
    if i == 0:
        return None
    a, b = float(profile[i - 1]), float(profile[i])
    return i - 1 + (a - half) / (a - b) if a != b else float(i)


def _robust_line(points):
    """Fit u = m*v + c through (v, u) points, dropping outliers (e.g. something stuck on the screen)."""
    pts = np.asarray(points, np.float64)
    keep = np.ones(len(pts), bool)
    for _ in range(3):
        m, c = np.polyfit(pts[keep, 0], pts[keep, 1], 1)
        resid = np.abs(pts[:, 1] - (m * pts[:, 0] + c))
        mad = np.median(resid[keep])
        keep = resid <= max(LINE_TOLERANCE_PX, 4 * mad)
        if keep.sum() < MIN_EDGE_POINTS:
            break
    return m, c, int(keep.sum())


def _warp_to_canvas(white_img, H, canvas_size):
    """The white photo in projector pixels (plus a margin all round), blurred a little."""
    width, height = canvas_size
    m = SCAN_MARGIN_PX
    shift = np.array([[1, 0, m], [0, 1, m], [0, 0, 1]], np.float64)
    gray = to_gray(white_img).astype(np.float32)
    flat = cv2.warpPerspective(gray, shift @ H, (width + 2 * m, height + 2 * m), flags=cv2.INTER_LINEAR)
    flat = cv2.GaussianBlur(flat, (5, 5), 0)
    centre = flat[m + height // 3:m + 2 * height // 3, m + width // 3:m + 2 * width // 3]
    if centre.size == 0 or float(np.median(centre)) < MIN_SCREEN_BRIGHTNESS:
        raise DetectionError("The screen didn't light up when projecting white. Is the projector "
                             "on, and is the camera pointed at the screen?")
    return flat


def _scan_sides(flat, canvas_size, corners=None):
    """
    Points (x, y in projector pixels) on the inner edge of the screen's border,
    per side. Without `corners`, scan lines cover the middle of each side of
    the canvas; with an estimate of the corners, they cover nearly the whole
    length of each side of the screen.
    """
    width, height = canvas_size
    m = SCAN_MARGIN_PX

    def scan(fixed, start, stop, vertical):
        """Profile from `start` towards `stop` along a row (or a column if vertical)."""
        fixed, start = int(round(fixed)), int(round(start))
        if not -m <= fixed < (height if not vertical else width) + m:
            return None
        step = 1 if stop > start else -1
        idx = np.arange(start, stop, step) + m
        line = flat[idx, fixed + m] if vertical else flat[fixed + m, idx]
        hit = _crossing(line)
        return None if hit is None else start + step * hit

    if corners is None:
        rows = np.linspace(SCAN_SPAN[0] * height, SCAN_SPAN[1] * height, SCAN_LINES)
        cols = np.linspace(SCAN_SPAN[0] * width, SCAN_SPAN[1] * width, SCAN_LINES)
        x0, x1 = SCAN_START * width, (1 - SCAN_START) * width
        y0, y1 = SCAN_START * height, (1 - SCAN_START) * height
        plan = {'left': [(y, x0) for y in rows], 'right': [(y, x1) for y in rows],
                'top': [(x, y0) for x in cols], 'bottom': [(x, y1) for x in cols]}
    else:
        tl, tr, br, bl = np.asarray(corners, np.float64)
        t = np.linspace(EDGE_SPAN[0], EDGE_SPAN[1], SCAN_LINES)
        inset_x = SCAN_START * (tr[0] - tl[0] + br[0] - bl[0]) / 2
        inset_y = SCAN_START * (bl[1] - tl[1] + br[1] - tr[1]) / 2

        def along(a, b, axis):
            return a[axis] + t * (b[axis] - a[axis]), a[1 - axis] + t * (b[1 - axis] - a[1 - axis])

        ly, lx = along(tl, bl, 1)
        ry, rx = along(tr, br, 1)
        tx, ty = along(tl, tr, 0)
        bx, by = along(bl, br, 0)
        plan = {'left': list(zip(ly, lx + inset_x)), 'right': list(zip(ry, rx - inset_x)),
                'top': list(zip(tx, ty + inset_y)), 'bottom': list(zip(bx, by - inset_y))}

    stops = {'left': -m, 'right': width + m, 'top': -m, 'bottom': height + m}
    edges = {}
    for side, lines in plan.items():
        vertical = side in ('top', 'bottom')
        points = []
        for fixed, start in lines:
            hit = scan(fixed, start, stops[side], vertical)
            if hit is not None:
                points.append((fixed, hit) if vertical else (hit, fixed))
        if len(points) < MIN_EDGE_POINTS:
            raise DetectionError(f"Couldn't find the screen's {side} border in the camera image. "
                                 "Is the whole screen and its black border in the camera's view?")
        edges[side] = np.float64(points)
    return edges


def _corners_from_edges(edges):
    """Fit a straight line to each side's points and intersect neighbours: TL, TR, BR, BL."""
    fits = {}
    for side, pts in edges.items():
        if side in ('left', 'right'):
            fits[side] = _robust_line(pts[:, ::-1])[:2]    # x = a*y + b
        else:
            fits[side] = _robust_line(pts)[:2]             # y = c*x + d

    def corner(vertical_side, horizontal_side):
        a, b = fits[vertical_side]
        c, d = fits[horizontal_side]
        y = (c * b + d) / (1 - a * c)
        return [a * y + b, y]

    return np.float64([corner('left', 'top'), corner('right', 'top'),
                       corner('right', 'bottom'), corner('left', 'bottom')])


def find_screen_in_canvas(white_img, H, canvas_size):
    """
    The screen's corners in projector pixels (TL, TR, BR, BL).

    The white photo is warped into projector pixels with the camera ->
    projector homography H. Then, from inside the screen, many lines are
    scanned outwards on each side to where the bright screen meets its dark
    border, and a straight line is fitted to each side. This only needs the
    screen to be brighter than its border in the white photo, so it doesn't
    care about the camera's auto-exposure, room light or a bright wall.
    """
    return _corners_from_edges(_scan_sides(_warp_to_canvas(white_img, H, canvas_size), canvas_size))


# -- lens distortion ----------------------------------------------------------

@dataclass
class LensModel:
    """
    Radial lens distortion (k1, k2) about an optical centre offset (dx, dy)
    from the image centre. Wide-angle webcams bow straight lines; without
    this, a homography fitted on the chessboard drifts towards the screen's
    far edges. The identity model changes nothing.
    """
    image_size: tuple
    k1: float = 0.0
    k2: float = 0.0
    dx: float = 0.0
    dy: float = 0.0

    def _camera_matrix(self):
        w, h = self.image_size
        f = float(max(w, h))
        return np.array([[f, 0, w / 2 + self.dx], [0, f, h / 2 + self.dy], [0, 0, 1]], np.float64)

    def undistort(self, points):
        pts = np.asarray(points, np.float64).reshape(-1, 1, 2)
        if self.k1 == 0 and self.k2 == 0:
            return pts.reshape(-1, 2)
        K = self._camera_matrix()
        f, centre = K[0, 0], K[:2, 2]
        distorted = (pts.reshape(-1, 2) - centre) / f
        # Invert r_d = r_u * (1 + k1 r_u^2 + k2 r_u^4) by fixed-point iteration.
        undistorted = distorted.copy()
        for _ in range(50):
            r2 = (undistorted ** 2).sum(axis=1, keepdims=True)
            undistorted = distorted / (1 + self.k1 * r2 + self.k2 * r2 ** 2)
        return undistorted * f + centre


def _side_residuals(points):
    """Distances of points from their best-fit straight line, worst LENS_TRIM dropped."""
    c = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - c)
    r = np.sort(np.abs((points - c) @ vt[1]))
    return r[:max(2, int(len(r) * (1 - LENS_TRIM)))]


def _lens_cost(lens, board_cam, board_px, edges_cam):
    """How far the board misses a homography plus how bent the screen's edges are, in projector px^2."""
    board_u = lens.undistort(board_cam)
    H, _ = cv2.findHomography(board_u, board_px, 0)
    if H is None:
        return np.inf
    board_err = np.linalg.norm(apply_homography(H, board_u) - board_px, axis=1)
    edge_err = np.concatenate([_side_residuals(apply_homography(H, lens.undistort(p)))
                               for p in edges_cam.values()])
    return float(np.mean(board_err ** 2) + np.mean(edge_err ** 2))


def fit_lens(board_cam, board_px, edges_cam, image_size):
    """
    The lens model that makes the chessboard fit a homography and the screen's
    edges straight, by coordinate descent (no scipy on the Pi). Falls back to
    no correction unless it clearly helps.
    """
    names = ('k1', 'k2', 'dx', 'dy')
    scale = {'k1': 1.0, 'k2': 1.0, 'dx': 0.2 * max(image_size), 'dy': 0.2 * max(image_size)}
    limits = {'k1': 1.5, 'k2': 3.0, 'dx': 0.25 * max(image_size), 'dy': 0.25 * max(image_size)}
    best = LensModel(image_size)
    base = best_cost = _lens_cost(best, board_cam, board_px, edges_cam)
    for step in (0.2, 0.05, 0.01, 0.002):
        improved = True
        while improved:
            improved = False
            for name in names:
                for sign in (1, -1):
                    value = getattr(best, name) + sign * step * scale[name]
                    if abs(value) > limits[name]:
                        continue
                    trial = LensModel(image_size, **{n: getattr(best, n) for n in names})
                    setattr(trial, name, value)
                    cost = _lens_cost(trial, board_cam, board_px, edges_cam)
                    if cost < best_cost:
                        best, best_cost, improved = trial, cost, True
    if best_cost > LENS_MIN_GAIN * base:
        return LensModel(image_size)
    return best


def detect_screen(white_img, black_img, board_img, board_px, canvas_size, pattern=PATTERN):
    """
    Screen corners in projector pixels, plus warnings about anything that looks off.

    A first pass (homography only) gives rough corners. Then the border is
    scanned along nearly the full length of each side, those points are taken
    back into the camera image, and a lens model is fitted so that the
    chessboard and the screen's straight edges agree. The corners come from
    the corrected edges. (black_img is kept for troubleshooting only; this
    webcam can't lock its exposure, so it isn't comparable with the white one.)
    """
    board_cam = find_board_corners(board_img, pattern)
    H0, _ = cv2.findHomography(board_cam, board_px, cv2.RANSAC, 3.0)
    if H0 is None:
        raise DetectionError("Couldn't compute the camera-to-projector mapping")
    flat = _warp_to_canvas(white_img, H0, canvas_size)
    rough = _corners_from_edges(_scan_sides(flat, canvas_size))
    try:
        edges = _scan_sides(flat, canvas_size, rough)
    except DetectionError:
        edges = _scan_sides(flat, canvas_size)
    to_cam = np.linalg.inv(H0)
    edges_cam = {side: apply_homography(to_cam, pts) for side, pts in edges.items()}

    h, w = white_img.shape[:2]
    lens = fit_lens(board_cam, board_px, edges_cam, (w, h))
    H, _ = cv2.findHomography(lens.undistort(board_cam), board_px, 0)
    proj = _corners_from_edges({side: apply_homography(H, lens.undistort(pts))
                                for side, pts in edges_cam.items()})
    cam_quad = apply_homography(to_cam, proj)

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
        # The webcam can't lock its exposure, and an overexposed board blooms
        # until the black squares stop touching; then a dimmer board works.
        board_img, tries = None, []
        for level in BOARD_WHITE_LEVELS:
            show(chessboard_pattern(width, height, white=level)[0])
            board_img = camera.grab_after(SETTLE_SECONDS, pump)
            try:
                find_board_corners(board_img)
                break
            except DetectionError:
                tries.append((level, board_img))
    finally:
        camera.set_auto_exposure(True)
        camera.close()

    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)
    for name, img in (('white', white), ('black', black), ('board', board_img)):
        cv2.imwrite(str(save_path / f"detect_{name}.png"), img)
    for level, img in tries:
        cv2.imwrite(str(save_path / f"detect_board_failed_{level}.png"), img)
    if len(tries) > 0 and len(tries) < len(BOARD_WHITE_LEVELS):
        print(f"Found the chessboard after dimming it to {BOARD_WHITE_LEVELS[len(tries)]}/255")

    return detect_screen(white, black, board_img, board_px, canvas_size)

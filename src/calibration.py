import datetime
import time

import numpy as np
import pygame

from .config_io import load_config, save_config, migrate_config
from .homography import CORNER_ORDER, screen_homography
from .playback import screen_corners, frame_canvas_corners
from .rules import effective_frame


CAST_TYPES = ('chromecast', 'airplay')
MOVE_STEP, MOVE_STEP_BIG = 0.005, 0.05     # normalized screen units
PIXEL_STEP, PIXEL_STEP_BIG = 1, 10         # canvas pixels (screen corners, legacy frames)
MIN_SIZE = 0.02

# Preview states: which frames count as active, and the time of day, for evaluating rules.
PREVIEW_STATES = ('idle', 'casting', 'night')
PREVIEW_TIMES = {'idle': (12, 0), 'casting': (12, 0), 'night': (2, 0)}

FRAME_COLOR = (90, 90, 90)
SELECTED_COLOR = (255, 255, 255)
SCREEN_COLOR = (0, 180, 255)
CORNER_COLORS = {'tl': (0, 255, 0), 'tr': (255, 0, 0), 'br': (0, 0, 255), 'bl': (255, 255, 0)}

HELP = [
    "Tab: next frame   n: new   d: delete   s: save   l: reload   Esc/q: quit",
    "Arrows: move (Shift = faster)   Ctrl/Alt+Arrows: resize   p: edit portrait layout",
    "r: preview idle/casting/night rules   e: edit screen corners   a: auto-detect screen",
]


class CalibrationUI:
    """
    Interactive layout editor. Frames are moved/resized in normalized screen
    space; in screen mode the screen's corners are nudged in canvas pixels.
    Draws on a canvas-sized surface that's scaled to the window.
    """

    def __init__(self, canvas_width, canvas_height, config, config_path=None, detect=None):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.config = migrate_config(config)
        self.config_path = config_path
        self.detect = detect            # callable(show, pump) -> DetectionResult, for 'a'
        self.current_frame_idx = 0
        self.current_corner = 'tl'
        self.screen_mode = False
        self.portrait = False
        self.preview = 'idle'
        self.running = True
        self.modified = False
        self.canvas = pygame.Surface((canvas_width, canvas_height))
        self.font = None
        self._window = None

    # -- state helpers ---------------------------------------------------------

    def frames(self):
        return self.config.setdefault('frames', [])

    def get_current_frame(self):
        frames = self.frames()
        if 0 <= self.current_frame_idx < len(frames):
            return frames[self.current_frame_idx]
        return None

    def screen(self):
        screen = self.config.setdefault('screen', {})
        if 'corners' not in screen:
            screen['corners'] = {k: list(v) for k, v in screen_corners(self.config).items()}
        return screen

    def preview_active_ids(self):
        if self.preview != 'casting':
            return set()
        return {f['id'] for f in self.frames() if (f.get('source') or {}).get('type') in CAST_TYPES}

    def preview_time(self):
        hour, minute = PREVIEW_TIMES[self.preview]
        return datetime.datetime.now().replace(hour=hour, minute=minute)

    def editable(self, frame, create=False):
        """
        Where the arrow keys' rect edits go, as (dict, description): the rule
        matching the current preview state -- so each state's layout can be
        set -- else the frame itself. With create=True, a matching rule that
        doesn't move the frame yet gets a copy of the frame's rect to edit.
        """
        _, index = effective_frame(frame, self.preview_active_ids(), self.preview_time())
        if index is None or 'rect' not in frame:
            return frame, "base"
        overrides = frame['rules'][index]['set']
        if overrides.get('hidden'):
            return frame, "base"     # the frame isn't shown in this state
        if 'rect' not in overrides and 'rect_portrait' not in overrides and create:
            overrides['rect'] = list(frame['rect'])
            if 'rect_portrait' in frame:
                overrides['rect_portrait'] = list(frame['rect_portrait'])
        return overrides, f"rule {index + 1}"

    # -- drawing ---------------------------------------------------------------

    def render_grid(self, window):
        if self.font is None:
            self.font = pygame.font.Font(None, max(18, self.canvas_height // 45))
        surface = self.canvas
        surface.fill((20, 20, 20))
        pygame.draw.line(surface, (40, 40, 40), (0, self.canvas_height // 2),
                         (self.canvas_width, self.canvas_height // 2), 1)
        pygame.draw.line(surface, (40, 40, 40), (self.canvas_width // 2, 0),
                         (self.canvas_width // 2, self.canvas_height), 1)

        screen = screen_corners(self.config)
        S = screen_homography(screen)
        self._draw_quad(surface, screen, SCREEN_COLOR, 3 if self.screen_mode else 1)
        if self.screen_mode:
            self._draw_corner_handles(surface, screen)

        active, now = self.preview_active_ids(), self.preview_time()
        for i, frame in enumerate(self.frames()):
            selected = i == self.current_frame_idx and not self.screen_mode
            cfg, _ = effective_frame(frame, active, now)
            hidden = cfg.get('hidden') or ((cfg.get('source') or {}).get('type') in CAST_TYPES
                                          and frame['id'] not in active)
            portrait = selected and self.portrait
            corners = frame_canvas_corners(cfg, S, portrait)
            color = SELECTED_COLOR if selected else FRAME_COLOR
            self._draw_quad(surface, corners, color, 3 if selected else 1, dashed=hidden)
            label = frame.get('label') or frame['id']
            if hidden:
                label += " (hidden)"
            if portrait and cfg.get('rect_portrait'):
                label += " [portrait]"
            text = self.font.render(label, True, color)
            tl = corners['tl']
            surface.blit(text, (tl[0] + 6, tl[1] + 4))
            if selected and 'rect' not in frame:
                self._draw_corner_handles(surface, corners)

        self._draw_hud(surface)
        self._window = window
        pygame.transform.smoothscale(surface, window.get_size(), window)

    def _draw_quad(self, surface, corners, color, width, dashed=False):
        pts = [corners[k] for k in CORNER_ORDER]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if not dashed:
                pygame.draw.line(surface, color, a, b, width)
                continue
            a, b = np.array(a, float), np.array(b, float)
            length = np.linalg.norm(b - a)
            for t in np.arange(0, length, 24):
                p1 = a + (b - a) * (t / length)
                p2 = a + (b - a) * (min(length, t + 12) / length)
                pygame.draw.line(surface, color, p1, p2, width)

    def _draw_corner_handles(self, surface, corners):
        for key, color in CORNER_COLORS.items():
            pos = tuple(int(v) for v in corners[key])
            pygame.draw.circle(surface, (255, 255, 255) if key == self.current_corner else color, pos, 8)

    def _draw_hud(self, surface):
        frame = self.get_current_frame()
        if self.screen_mode:
            status = f"SCREEN corners: {self.current_corner.upper()}   (e: back to frames)"
        elif frame:
            _, target = self.editable(frame) if 'rect' in frame else (None, 'corners')
            status = (f"Frame: {frame.get('label') or frame['id']}   editing: {target}"
                      f"{' portrait' if self.portrait else ''}")
        else:
            status = "No frames (n: add one)"
        lines = [f"{status}   preview: {self.preview}{'   *unsaved*' if self.modified else ''}"] + HELP
        y = self.canvas_height - (len(lines) + 1) * self.font.get_linesize()
        backing = pygame.Surface((self.canvas_width, self.canvas_height - y + 8), pygame.SRCALPHA)
        backing.fill((0, 0, 0, 170))
        surface.blit(backing, (0, y - 8))
        for line in lines:
            surface.blit(self.font.render(line, True, (200, 200, 200)), (12, y))
            y += self.font.get_linesize()

    # -- input -----------------------------------------------------------------

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.handle_key(event.key, event.mod)

    def handle_key(self, key, mod=0):
        shift = bool(mod & pygame.KMOD_SHIFT)
        resize = bool(mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT | pygame.KMOD_META))
        if key in (pygame.K_ESCAPE, pygame.K_q):
            self.running = False
        elif key == pygame.K_TAB:
            self._cycle_frame(shift)
        elif key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
            self.current_corner = CORNER_ORDER[key - pygame.K_1]
        elif key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            self._arrow(key, shift, resize)
        elif key == pygame.K_e:
            self.screen_mode = not self.screen_mode
            self.current_corner = 'tl'
        elif key == pygame.K_p:
            self.portrait = not self.portrait
        elif key == pygame.K_r:
            self.preview = PREVIEW_STATES[(PREVIEW_STATES.index(self.preview) + 1) % len(PREVIEW_STATES)]
        elif key == pygame.K_a:
            self._auto_detect()
        elif key == pygame.K_n:
            self._add_frame()
        elif key == pygame.K_d:
            self._delete_frame()
        elif key == pygame.K_s:
            self._save_config()
        elif key == pygame.K_l:
            self._load_config()

    def _cycle_frame(self, reverse=False):
        frames = self.frames()
        if not frames:
            return
        step = -1 if reverse else 1
        self.current_frame_idx = (self.current_frame_idx + step) % len(frames)
        self.current_corner = 'tl'

    def _arrow(self, key, shift, resize):
        dx = {pygame.K_LEFT: -1, pygame.K_RIGHT: 1}.get(key, 0)
        dy = {pygame.K_UP: -1, pygame.K_DOWN: 1}.get(key, 0)

        if self.screen_mode:
            step = PIXEL_STEP_BIG if shift else PIXEL_STEP
            corner = self.screen()['corners'][self.current_corner]
            corner[0] += dx * step
            corner[1] += dy * step
            self.modified = True
            return

        frame = self.get_current_frame()
        if not frame:
            return
        if 'rect' not in frame:
            # Legacy frame: nudge its corners in canvas pixels.
            step = PIXEL_STEP_BIG if shift else PIXEL_STEP
            frame['corners'][self.current_corner][0] += dx * step
            frame['corners'][self.current_corner][1] += dy * step
            self.modified = True
            return

        target, _ = self.editable(frame, create=True)
        key_name = 'rect_portrait' if self.portrait else 'rect'
        if key_name not in target:
            # First edit of this layout: start from the one currently shown.
            target[key_name] = list(target.get('rect') or frame.get('rect_portrait' if self.portrait
                                                                    else 'rect') or frame['rect'])
        rect = target[key_name]
        step = MOVE_STEP_BIG if shift else MOVE_STEP
        if resize:
            rect[2] = round(max(MIN_SIZE, rect[2] + dx * step), 4)
            rect[3] = round(max(MIN_SIZE, rect[3] - dy * step), 4)   # Up grows
        else:
            rect[0] = round(rect[0] + dx * step, 4)
            rect[1] = round(rect[1] + dy * step, 4)
        self.modified = True

    def _add_frame(self):
        frames = self.frames()
        ids = {f['id'] for f in frames}
        n = len(frames) + 1
        while f"frame_{n}" in ids:
            n += 1
        frames.append({"id": f"frame_{n}", "label": f"Frame {n}", "rect": [0.3, 0.3, 0.4, 0.4],
                       "source": {"type": "text", "text": f"Frame {n}"}})
        self.current_frame_idx = len(frames) - 1
        self.screen_mode = False
        self.modified = True

    def _delete_frame(self):
        frames = self.frames()
        if not frames or self.screen_mode:
            return
        frames.pop(self.current_frame_idx)
        self.current_frame_idx = max(0, min(self.current_frame_idx, len(frames) - 1))
        self.modified = True

    def _auto_detect(self):
        if self.detect is None:
            print("Auto-detect needs a camera: set detection.camera in the config")
            return
        from .screen_detect import DetectionError

        def show(rgba):
            h, w = rgba.shape[:2]
            image = pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA')
            pygame.transform.smoothscale(image.convert(), self._window.get_size(), self._window)
            pygame.display.flip()

        print("Detecting the screen...")
        try:
            result = self.detect(show, pygame.event.pump)
        except DetectionError as e:
            print(f"Screen detection failed: {e}")
            return
        for warning in result.warnings:
            print(f"Warning: {warning}")
        self.screen()['corners'] = {k: [round(v, 1) for v in xy] for k, xy in result.corners.items()}
        self.screen_mode = True
        self.modified = True
        print("Screen detected. Check the outline, nudge corners if needed, then press s to save.")

    def _save_config(self):
        try:
            if self.config_path:
                save_config(self.config, self.config_path)
            else:
                save_config(self.config)
            self.modified = False
            print("Config saved!")
        except Exception as e:
            print(f"Error saving config: {e}")

    def _load_config(self):
        try:
            config = load_config(self.config_path) if self.config_path else load_config()
            self.config = migrate_config(config)
            self.current_frame_idx = 0
            self.current_corner = 'tl'
            self.modified = False
            print("Config reloaded!")
        except Exception as e:
            print(f"Error loading config: {e}")

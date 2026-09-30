import time

import numpy as np

from .homography import (
    screen_homography, rect_to_canvas_corners, corners_to_array, array_to_corners, quad_footprint,
)
from .renderer import Layer
from .sources import create_source, source_config


FADE_SECONDS = 0.25
SLIDE_SECONDS = 0.25


def full_canvas_corners(width, height):
    return {'tl': [0, 0], 'tr': [width, 0], 'br': [width, height], 'bl': [0, height]}


class FrameState:
    """A frame's live source plus its animated on-canvas position and opacity."""

    def __init__(self, cfg, source):
        self.cfg = cfg
        self.source = source
        self.alpha = 0.0
        self.corners = None          # currently displayed corners (4x2 array)
        self.target = None           # corners we're sliding toward
        self.slide_from = None
        self.slide_start = 0.0

    def set_target(self, target, now, animate=True):
        if self.target is not None and np.allclose(target, self.target):
            return
        if self.corners is None or not animate:
            self.corners = target.copy()
            self.slide_from = None
        else:
            self.slide_from = self.corners.copy()
            self.slide_start = now
        self.target = target
        w, h = quad_footprint(array_to_corners(target))
        if self.source:
            self.source.set_target_size(w, h)

    def step(self, now, dt, show):
        if self.slide_from is not None:
            t = min(1.0, (now - self.slide_start) / SLIDE_SECONDS)
            t = t * t * (3 - 2 * t)  # smoothstep
            self.corners = self.slide_from + (self.target - self.slide_from) * t
            if t >= 1.0:
                self.slide_from = None
        rate = dt / FADE_SECONDS
        self.alpha = min(1.0, self.alpha + rate) if show else max(0.0, self.alpha - rate)


class Playback:
    """Owns the frames' sources and turns the config into layers each tick."""

    def __init__(self, config, renderer):
        self.renderer = renderer
        self.config = config
        self.frames = {}   # id -> FrameState, in config order
        self._last_tick = None

    # -- setup ---------------------------------------------------------------

    def start(self):
        for frame_cfg in self.config.get('frames', []):
            self._add_frame(frame_cfg)

    def _context(self):
        return {'location': self.config.get('location')}

    def _make_source(self, src_cfg, frame_id):
        if not src_cfg:
            return None
        try:
            source = create_source(src_cfg, self._context())
            source.start()
            return source
        except Exception as e:
            print(f"Error starting source for frame {frame_id}: {e}")
            return None

    def _add_frame(self, frame_cfg):
        frame_id = frame_cfg['id']
        source = self._make_source(source_config(frame_cfg), frame_id)
        # Frames that are showing from the start appear immediately, not faded in.
        state = FrameState(frame_cfg, source)
        state.alpha = 1.0 if source is not None and source.visible else 0.0
        self.frames[frame_id] = state

    def close(self):
        for state in self.frames.values():
            if state.source:
                state.source.close()
        self.frames.clear()

    # -- per tick ------------------------------------------------------------

    def screen_matrix(self):
        screen = self.config.get('screen') or {}
        corners = screen.get('corners') or full_canvas_corners(self.renderer.canvas_width,
                                                                 self.renderer.canvas_height)
        return screen_homography(corners)

    def frame_corners(self, cfg, source, S):
        """Canvas-pixel corners for a frame, honouring rect_portrait for portrait content."""
        if 'rect' in cfg:
            rect = cfg['rect']
            if source is not None and source.orientation == 'portrait' and cfg.get('rect_portrait'):
                rect = cfg['rect_portrait']
            return corners_to_array(rect_to_canvas_corners(rect, S))
        return corners_to_array(cfg['corners'])

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last_tick is None else min(0.25, now - self._last_tick)
        self._last_tick = now

        S = self.screen_matrix()
        layers = []
        for frame_id, state in self.frames.items():
            if state.source is None:
                continue
            cfg = state.cfg
            show = state.source.visible and not cfg.get('hidden', False)
            state.set_target(self.frame_corners(cfg, state.source, S), now, animate=state.alpha > 0)
            state.step(now, dt, show)
            if state.alpha <= 0.0:
                continue
            layers.append(Layer(key=frame_id, corners=array_to_corners(state.corners),
                                source=state.source, alpha=state.alpha, crop=state.source.crop))
        self.renderer.render(layers)

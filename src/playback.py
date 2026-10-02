import datetime
import json
import threading
import time

import numpy as np

from .homography import (
    CORNER_ORDER, apply_homography, screen_homography, rect_to_canvas_corners, corners_to_array, array_to_corners, quad_footprint,
)
from .renderer import Layer
from .rules import effective_frame
from .sources import create_source, source_config


DIM_CHECK_SECONDS = 60
FADE_SECONDS = 0.25
SLIDE_SECONDS = 0.25


def full_canvas_corners(width, height):
    return {'tl': [0, 0], 'tr': [width, 0], 'br': [width, height], 'bl': [0, height]}


def screen_corners(config):
    """The screen's corners in canvas pixels; the whole canvas until a screen is detected."""
    screen = config.get('screen') or {}
    canvas = config['canvas']
    return screen.get('corners') or full_canvas_corners(canvas['width'], canvas['height'])


def frame_canvas_corners(cfg, S, portrait=False):
    """
    A frame's corners in canvas pixels. A rect (or rect_portrait) or a
    free-form quad in normalized screen space goes through the screen
    homography S; legacy corners are already canvas pixels.
    """
    if 'quad' in cfg:
        points = apply_homography(S, [cfg['quad'][k] for k in CORNER_ORDER])
        return array_to_corners(points)
    if 'rect' in cfg:
        rect = cfg['rect']
        if portrait and cfg.get('rect_portrait'):
            rect = cfg['rect_portrait']
        return rect_to_canvas_corners(rect, S)
    return cfg['corners']


def _cfg_key(cfg):
    return json.dumps(cfg, sort_keys=True)


class FrameState:
    """
    A frame's live sources plus its animated on-canvas position and opacity.

    base_source is the frame's own source and always stays running (its
    active state drives other frames' rules). A rule that swaps the source
    gets a separate override source, alive only while that rule matches.
    """

    def __init__(self, cfg, base_source):
        self.cfg = cfg
        self.base_source = base_source
        self.override_source = None
        self.override_key = None
        self.alpha = 0.0
        self.corners = None          # currently displayed corners (4x2 array)
        self.target = None           # corners we're sliding toward
        self.slide_from = None
        self.slide_start = 0.0

    def close(self):
        for source in (self.base_source, self.override_source):
            if source:
                source.close()
        self.override_source = self.override_key = None

    def set_override(self, key, source):
        if self.override_source:
            self.override_source.close()
        self.override_key, self.override_source = key, source
        if source and self.target is not None:
            source.set_target_size(*quad_footprint(array_to_corners(self.target)))

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
        size = quad_footprint(array_to_corners(target))
        for source in (self.base_source, self.override_source):
            if source:
                source.set_target_size(*size)

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

    def __init__(self, config, renderer, clock=datetime.datetime.now):
        self.renderer = renderer
        self.config = config
        self.clock = clock
        self.frames = {}   # id -> FrameState, in config order
        self._last_tick = None
        self.dim = 1.0
        self._dim_stop = threading.Event()

    # -- setup ---------------------------------------------------------------

    def start(self):
        for frame_cfg in self.config.get('frames', []):
            self._add_frame(frame_cfg)
        threading.Thread(target=self._dim_loop, name='SoftwareDim', daemon=True).start()

    def _dim_loop(self):
        """
        Keep self.dim matching projector.brightness.software_dim for the
        time of day. Runs off the render thread: resolving the location can
        involve a network lookup.
        """
        from .location import resolve_location
        from .projector.brightness import dim_at, local_now
        location, location_cfg = None, object()
        while not self._dim_stop.is_set():
            brightness = (self.config.get('projector') or {}).get('brightness')
            if brightness and brightness.get('software_dim'):
                if self.config.get('location') != location_cfg:
                    location_cfg = self.config.get('location')
                    location = resolve_location(location_cfg)
                if location is not None:
                    self.dim = dim_at(local_now(location), location, brightness)
            else:
                self.dim = 1.0
            self._dim_stop.wait(DIM_CHECK_SECONDS)

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
        self.frames[frame_id] = FrameState(frame_cfg, source)

    def close(self):
        self._dim_stop.set()
        for state in self.frames.values():
            state.close()
        self.frames.clear()

    def apply_config(self, new_config):
        """
        Switch to an edited config without restarting: frames are matched by
        id, geometry and rules change in place, and a source is only
        recreated when it can't take the change itself (e.g. a different
        device) -- so cameras and capture cards aren't reopened needlessly.
        """
        location_changed = new_config.get('location') != self.config.get('location')
        self.config = new_config
        old_frames, self.frames = self.frames, {}
        for frame_cfg in new_config.get('frames', []):
            frame_id = frame_cfg['id']
            state = old_frames.pop(frame_id, None)
            if state is None:
                self._add_frame(frame_cfg)
                continue
            new_src, old_src = source_config(frame_cfg), source_config(state.cfg)
            needs_new = location_changed and (new_src or {}).get('type') == 'weather'
            if new_src != old_src or needs_new:
                keep = (not needs_new and state.base_source is not None and new_src is not None
                        and old_src is not None and new_src.get('type') == old_src.get('type')
                        and state.base_source.update(new_src))
                if not keep:
                    if state.base_source:
                        state.base_source.close()
                    state.base_source = self._make_source(new_src, frame_id)
                    if state.base_source and state.target is not None:
                        state.base_source.set_target_size(*quad_footprint(array_to_corners(state.target)))
                state.set_override(None, None)
            state.cfg = frame_cfg
            self.frames[frame_id] = state
        for state in old_frames.values():
            state.close()

    # -- per tick ------------------------------------------------------------

    def screen_matrix(self):
        return screen_homography(screen_corners(self.config))

    def frame_corners(self, cfg, source, S):
        portrait = source is not None and source.orientation == 'portrait'
        return corners_to_array(frame_canvas_corners(cfg, S, portrait))

    def active_ids(self):
        return {fid for fid, state in self.frames.items()
                if state.base_source is not None and state.base_source.active}

    def _shown_source(self, state, cfg):
        """The source to display for the frame's effective config (the base one unless a rule swapped it)."""
        src_cfg = source_config(cfg)
        if src_cfg is None or src_cfg == source_config(state.cfg):
            if state.override_source:
                state.set_override(None, None)
            return state.base_source
        key = _cfg_key(src_cfg)
        if key != state.override_key:
            state.set_override(key, self._make_source(src_cfg, state.cfg['id']))
        if state.override_source:
            state.override_source.poll(time.monotonic())
        return state.override_source

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last_tick is None else min(0.25, now - self._last_tick)
        first_tick = self._last_tick is None
        self._last_tick = now

        for state in self.frames.values():
            if state.base_source:
                state.base_source.poll(now)
        active = self.active_ids()
        wall_clock = self.clock()

        S = self.screen_matrix()
        layers = []
        for frame_id, state in self.frames.items():
            cfg, _ = effective_frame(state.cfg, active, wall_clock)
            source = self._shown_source(state, cfg)
            if source is None:
                continue
            show = source.visible and not cfg.get('hidden', False)
            if first_tick and show:
                state.alpha = 1.0   # frames showing at startup appear immediately
            state.set_target(self.frame_corners(cfg, source, S), now, animate=state.alpha > 0)
            state.step(now, dt, show)
            if state.alpha <= 0.0:
                continue
            layers.append(Layer(key=f"{frame_id}:{id(source)}", corners=array_to_corners(state.corners),
                                source=source, alpha=state.alpha, crop=source.crop))
        self.renderer.render(layers, dim=self.dim)

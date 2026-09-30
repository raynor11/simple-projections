import copy
import datetime
import time

import cv2
import numpy as np
import pygame

from ..config_io import load_config, save_config, migrate_config
from ..homography import CORNER_ORDER, apply_homography, screen_homography
from ..playback import screen_corners, frame_canvas_corners
from ..rules import effective_frame
from . import widgets as w
from .editing import (
    History, handle_points, move_rect, resize_rect, scale_rect, snap_move, snap_resize, snap_targets,
)


CAST_TYPES = ('chromecast', 'airplay')
MOVE_STEP, MOVE_STEP_BIG = 0.005, 0.05     # normalized screen units
PIXEL_STEP, PIXEL_STEP_BIG = 1, 10         # canvas pixels (screen corners, legacy frames)
MIN_SIZE = 0.02
SNAP_PIXELS = 10                           # snap when within this many window pixels
WHEEL_SCALE = 1.04
MESSAGE_SECONDS = 4.0
QUIT_CONFIRM_SECONDS = 5.0

# Preview states: which frames count as active, and the time of day, for evaluating rules.
PREVIEW_STATES = ('idle', 'casting', 'night')
PREVIEW_TIMES = {'idle': (12, 0), 'casting': (12, 0), 'night': (2, 0)}

MOUSE_HINTS = [
    "Click a frame to select it", "Drag it to move", "Drag its handles to resize",
    "Scroll to scale it", "Hold Alt while dragging to turn off snapping",
]
KEY_HINTS = [
    "Tab: next frame", "Arrows: nudge (Shift = faster)", "Ctrl/Alt+Arrows: resize", "1-4: pick corner",
]
# For the terminal when calibration starts.
HELP = [
    "Mouse: " + ", ".join(MOUSE_HINTS).lower().capitalize(),
    "Keys: " + "   ".join(KEY_HINTS),
    "Toolbar buttons show their shortcut keys (S save, Ctrl+Z undo, N new, D delete, E screen corners, "
    "P portrait, R preview, A auto-detect, +/- text size, H help, Q quit)",
]

CURSORS = {
    'tl': 'SYSTEM_CURSOR_SIZENWSE', 'br': 'SYSTEM_CURSOR_SIZENWSE',
    'tr': 'SYSTEM_CURSOR_SIZENESW', 'bl': 'SYSTEM_CURSOR_SIZENESW',
    't': 'SYSTEM_CURSOR_SIZENS', 'b': 'SYSTEM_CURSOR_SIZENS',
    'l': 'SYSTEM_CURSOR_SIZEWE', 'r': 'SYSTEM_CURSOR_SIZEWE',
    'move': 'SYSTEM_CURSOR_SIZEALL', 'button': 'SYSTEM_CURSOR_HAND', None: 'SYSTEM_CURSOR_ARROW',
}


class CalibrationUI:
    """
    Layout editor, usable with the mouse, the keyboard, or both.

    Frames are moved/resized in normalized screen space; in screen mode the
    screen's corners are moved in canvas pixels. Outlines are drawn in
    canvas coordinates mapped to the window (stretched exactly as playback
    stretches the canvas, so they line up with the projection); text,
    handles and buttons are drawn at the window's own resolution.
    """

    def __init__(self, canvas_width, canvas_height, config, config_path=None, detect=None):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.config = migrate_config(config)
        self.config_path = config_path
        self.detect = detect            # callable(show, pump) -> DetectionResult
        self.current_frame_idx = 0
        self.current_corner = 'tl'
        self.screen_mode = False
        self.portrait = False
        self.preview = 'idle'
        self.show_help = True
        self.ui_scale = 1.0
        self.running = True
        self.modified = False
        self.history = History()
        self.fonts = w.Fonts()
        self.buttons = []
        self.hover = None               # ('button', id) | ('handle', key) | ('frame', index)
        self.drag = None
        self.guides = []
        self._message = None
        self._message_until = 0.0
        self._quit_armed_until = 0.0
        self._cursor = None
        self._window = None
        self._scale = (1.0, 1.0)
        self._top = 0                   # toolbar bottom, window px
        self._bottom = 0                # help panel top, window px

    # -- state helpers -------------------------------------------------------------

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
        Where rect edits go, as (dict, description): the rule matching the
        current preview state -- so each state's layout can be set -- else
        the frame itself. With create=True, a matching rule that doesn't
        move the frame yet gets a copy of the frame's rect to edit.
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

    def _rect_key(self):
        return 'rect_portrait' if self.portrait else 'rect'

    def _shown(self, frame, selected=False):
        """(effective cfg, canvas corners, hidden) for a frame in the current preview state."""
        active = self.preview_active_ids()
        cfg, _ = effective_frame(frame, active, self.preview_time())
        hidden = bool(cfg.get('hidden')) or ((cfg.get('source') or {}).get('type') in CAST_TYPES
                                             and frame['id'] not in active)
        corners = frame_canvas_corners(cfg, self._S(), selected and self.portrait)
        return cfg, corners, hidden

    def _shown_rect(self, frame):
        cfg, _, _ = self._shown(frame)
        if self.portrait and cfg.get('rect_portrait'):
            return list(cfg['rect_portrait'])
        return list(cfg['rect'])

    def _rect_target(self, frame):
        """The (dict, key) holding the rect that edits change, created from what's shown if needed."""
        shown = self._shown_rect(frame)
        target, _ = self.editable(frame, create=True)
        key = self._rect_key()
        if key not in target:
            target[key] = shown
        return target, key

    # -- changes, undo, messages ---------------------------------------------------

    def _change(self, group=None):
        """Call before modifying the config: records an undo step (grouped, e.g. while holding a key)."""
        self.history.checkpoint(self.config, group)
        self.modified = True

    def _say(self, text):
        print(text)
        self._message, self._message_until = text, time.monotonic() + MESSAGE_SECONDS

    def undo(self):
        previous = self.history.undo(self.config)
        if previous is None:
            self._say("Nothing to undo")
            return
        self.config, self.modified = previous, True
        self._clamp_selection()
        self._say("Undone")

    def redo(self):
        following = self.history.redo(self.config)
        if following is None:
            self._say("Nothing to redo")
            return
        self.config, self.modified = following, True
        self._clamp_selection()
        self._say("Redone")

    def _clamp_selection(self):
        self.current_frame_idx = max(0, min(self.current_frame_idx, len(self.frames()) - 1))

    # -- coordinates ---------------------------------------------------------------

    def _S(self):
        return screen_homography(screen_corners(self.config))

    def canvas_to_window(self, p):
        return (p[0] * self._scale[0], p[1] * self._scale[1])

    def window_to_canvas(self, p):
        return (p[0] / self._scale[0], p[1] / self._scale[1])

    def screen_to_canvas(self, points):
        return apply_homography(self._S(), points)

    def canvas_to_screen(self, points):
        return apply_homography(np.linalg.inv(self._S()), points)

    def _window_to_screen(self, pos):
        return tuple(self.canvas_to_screen([self.window_to_canvas(pos)])[0])

    def _visible_pos(self, pos, inset):
        """Keep a handle inside the part of the window not covered by the toolbar or help panel."""
        win_w, _ = self._window.get_size()
        return (int(min(max(pos[0], inset), win_w - inset)),
                int(min(max(pos[1], self._top + inset), self._bottom - inset)))

    def _handles(self):
        """{key: window position} of the draggable handles right now, and whether they're numbered corners."""
        inset = self._handle_radius() + 4
        if self.screen_mode:
            corners = screen_corners(self.config)
            return {k: self._visible_pos(self.canvas_to_window(corners[k]), inset) for k in CORNER_ORDER}, True
        frame = self.get_current_frame()
        if frame is None:
            return {}, False
        if 'rect' not in frame:
            return {k: self._visible_pos(self.canvas_to_window(frame['corners'][k]), inset)
                    for k in CORNER_ORDER}, True
        points = handle_points(self._shown_rect(frame))
        canvas = self.screen_to_canvas(list(points.values()))
        return {k: self._visible_pos(self.canvas_to_window(p), inset) for k, p in zip(points, canvas)}, False

    def _handle_radius(self):
        return int(max(9, 12 * self.ui_scale))

    # -- drawing -------------------------------------------------------------------

    def _base_size(self):
        _, win_h = self._window.get_size()
        return max(14, min(26, win_h // 42)) * self.ui_scale

    def _make_buttons(self):
        frame = self.get_current_frame()
        return [
            w.Button('save', 'Save', 'S', enabled=True),
            w.Button('undo', 'Undo', 'Ctrl+Z', enabled=self.history.can_undo),
            w.Button('redo', 'Redo', 'Ctrl+Y', enabled=self.history.can_redo),
            w.Button('add', 'Add frame', 'N'),
            w.Button('delete', 'Delete frame', 'D', enabled=frame is not None and not self.screen_mode),
            w.Button('screen', 'Screen corners', 'E', on=self.screen_mode),
            w.Button('portrait', 'Portrait layout', 'P', on=self.portrait),
            w.Button('preview', f'Preview: {self.preview}', 'R'),
            w.Button('detect', 'Auto-detect screen', 'A', enabled=self.detect is not None),
            w.Button('smaller', 'Smaller text', '-'),
            w.Button('bigger', 'Bigger text', '+'),
            w.Button('help', 'Help', 'H', on=self.show_help),
            w.Button('quit', 'Quit', 'Q'),
        ]

    def render_grid(self, window):
        self._window = window
        win_w, win_h = window.get_size()
        self._scale = (win_w / self.canvas_width, win_h / self.canvas_height)
        base = self._base_size()
        font = self.fonts.get(base)

        # Toolbar and help panel sizes first: handles are kept clear of them.
        self.buttons = self._make_buttons()
        gap = max(4, int(base * 0.3))
        self._top = w.layout_buttons(self.buttons, font, win_w, gap, gap)
        panel_lines = self._panel_lines(font, win_w - 2 * gap)
        self._bottom = win_h - (len(panel_lines) * font.get_linesize() + 2 * gap)

        window.fill(w.BACKGROUND)
        pygame.draw.line(window, w.GRID, (0, win_h // 2), (win_w, win_h // 2), 1)
        pygame.draw.line(window, w.GRID, (win_w // 2, 0), (win_w // 2, win_h), 1)

        # The screen outline.
        screen = screen_corners(self.config)
        screen_pts = [self.canvas_to_window(screen[k]) for k in CORNER_ORDER]
        w.polyline(window, screen_pts, w.SCREEN, 3 if self.screen_mode else 2)
        # Labels are nudged down when they'd overlap (e.g. frames sharing a rect).
        placed = []
        offset = 2 * self._handle_radius() + 8     # clear of a corner handle
        sx, sy = self._visible_pos(screen_pts[0], 6)
        self._label(window, self.fonts.get(base * 0.85), "Screen", (sx + offset, sy), w.SCREEN, placed)

        # Frames, in drawing order (later ones on top).
        label_font = self.fonts.get(base * 0.9)
        for i, frame in enumerate(self.frames()):
            selected = i == self.current_frame_idx and not self.screen_mode
            hovered = self.hover == ('frame', i) and not selected
            cfg, corners, hidden = self._shown(frame, selected)
            pts = [self.canvas_to_window(corners[k]) for k in CORNER_ORDER]
            if selected:
                w.fill_polygon(window, pts, (255, 255, 255, 38))
            color = w.SELECTED if selected else w.FRAME_HOVER if hovered else w.FRAME
            w.polyline(window, pts, color, 3 if selected else 2, dashed=hidden)
            label = frame.get('label') or frame['id']
            kind = (cfg.get('source') or {}).get('type')
            if kind:
                label += f"  ({kind})"
            if hidden:
                label += "  hidden"
            if selected and self.portrait and cfg.get('rect_portrait'):
                label += "  portrait"
            self._label(window, label_font, label, (pts[0][0] + offset, pts[0][1] + 6),
                        w.SELECTED if selected else w.TEXT, placed)

        for axis, value in self.guides:
            ends = [(value, 0), (value, 1)] if axis == 'x' else [(0, value), (1, value)]
            w.polyline(window, [self.canvas_to_window(p) for p in self.screen_to_canvas(ends)],
                       w.GUIDE, 2, closed=False, dashed=True)

        self._draw_toolbar(window, font)
        self._draw_panel(window, font, panel_lines, gap)
        self._draw_handles(window, base)   # last, so panels never hide them

    @staticmethod
    def _label(window, font, text, pos, color, placed, pad=4):
        width, height = font.size(text)
        rect = pygame.Rect(int(pos[0]), int(pos[1]), width + 2 * pad, height + 2 * pad)
        while any(rect.colliderect(other) for other in placed):
            rect.y += rect.height + 2
        placed.append(rect)
        w.text_with_backing(window, font, text, rect.topleft, color=color, pad=pad)

    def _draw_toolbar(self, window, font):
        bar = pygame.Surface((window.get_width(), self._top), pygame.SRCALPHA)
        bar.fill(w.PANEL)
        window.blit(bar, (0, 0))
        for button in self.buttons:
            w.draw_button(window, button, font, self.hover == ('button', button.id))

    def _status(self):
        frame = self.get_current_frame()
        if self.screen_mode:
            status = f"Editing the screen corners (selected: {CORNER_ORDER.index(self.current_corner) + 1})"
        elif frame:
            _, target = self.editable(frame) if 'rect' in frame else (None, 'corners')
            status = (f"Selected: {frame.get('label') or frame['id']}  ·  editing {target}"
                      f"{' (portrait layout)' if self.portrait else ''}")
        else:
            status = "No frames yet: click Add frame (N)"
        status += f"  ·  previewing: {self.preview}"
        if self.modified:
            status += "  ·  unsaved changes"
        return status

    def _panel_lines(self, font, width):
        lines = [(self._status(), w.SELECTED)]
        if self._message and time.monotonic() < self._message_until:
            lines.append((self._message, w.YELLOW))
        if self.show_help:
            lines += [(line, w.TEXT_DIM) for line in self._wrap(MOUSE_HINTS + KEY_HINTS, font, width)]
        return lines

    @staticmethod
    def _wrap(items, font, width, sep="     "):
        """Pack items into lines that fit width, never splitting an item."""
        lines, line = [], ""
        for item in items:
            candidate = item if not line else line + sep + item
            if line and font.size(candidate)[0] > width:
                lines.append(line)
                line = item
            else:
                line = candidate
        if line:
            lines.append(line)
        return lines

    def _draw_panel(self, window, font, lines, gap):
        win_w, win_h = window.get_size()
        panel = pygame.Surface((win_w, win_h - self._bottom), pygame.SRCALPHA)
        panel.fill(w.PANEL)
        window.blit(panel, (0, self._bottom))
        y = self._bottom + gap
        for text, color in lines:
            window.blit(font.render(text, True, color), (gap, y))
            y += font.get_linesize()

    def _draw_handles(self, window, base):
        handles, numbered = self._handles()
        active = self.drag['handle'] if self.drag and self.drag.get('handle') else None
        radius = self._handle_radius()
        number_font = self.fonts.get(max(16, radius * 1.9))
        for key, pos in handles.items():
            hot = key == active or self.hover == ('handle', key)
            if numbered:
                w.corner_handle(window, pos, CORNER_ORDER.index(key) + 1, number_font, radius,
                                active=hot or key == self.current_corner)
            else:
                w.square_handle(window, pos, max(5, radius - 2), active=hot)

    # -- input -----------------------------------------------------------------------

    def handle_events(self):
        for event in pygame.event.get():
            self.handle_event(event)

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self._request_quit()
        elif event.type == pygame.KEYDOWN:
            self.handle_key(event.key, event.mod)
        elif self._window is None:
            return
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._mouse_down(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self.drag:
                self._drag_to(event.pos, snapping=not (pygame.key.get_mods() & pygame.KMOD_ALT))
            else:
                self._update_hover(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.drag, self.guides = None, []
            self.history.end_group()
        elif event.type == pygame.MOUSEWHEEL:
            self._wheel(event.y)

    def _hit(self, pos):
        for button in self.buttons:
            if button.rect and button.rect.collidepoint(pos):
                return ('button', button.id)
        if pos[1] >= self._bottom:
            return None     # the help panel
        handles, _ = self._handles()
        reach = self._handle_radius() * 1.8
        for key, hpos in handles.items():
            if (pos[0] - hpos[0]) ** 2 + (pos[1] - hpos[1]) ** 2 <= reach ** 2:
                return ('handle', key)
        if not self.screen_mode:
            for i in reversed(range(len(self.frames()))):
                _, corners, _ = self._shown(self.frames()[i], i == self.current_frame_idx)
                poly = np.float32([self.canvas_to_window(corners[k]) for k in CORNER_ORDER])
                if cv2.pointPolygonTest(poly, (float(pos[0]), float(pos[1])), False) >= 0:
                    return ('frame', i)
        return None

    def _update_hover(self, pos):
        self.hover = self._hit(pos)
        kind = self.hover[0] if self.hover else None
        if kind == 'handle':
            numbered = self.screen_mode or 'rect' not in (self.get_current_frame() or {})
            cursor = 'move' if numbered else self.hover[1]
        elif kind == 'frame':
            cursor = 'move'
        elif kind == 'button':
            cursor = 'button'
        else:
            cursor = None
        if cursor != self._cursor:
            self._cursor = cursor
            try:
                pygame.mouse.set_cursor(getattr(pygame, CURSORS[cursor]))
            except (pygame.error, AttributeError, TypeError):
                pass   # e.g. no display or an older pygame

    def _mouse_down(self, pos):
        hit = self._hit(pos)
        if hit is None:
            return
        kind, value = hit
        if kind == 'button':
            self.press(value)
            return
        snapshot = copy.deepcopy(self.config)
        if kind == 'handle':
            numbered = self.screen_mode or 'rect' not in (self.get_current_frame() or {})
            if numbered:
                self.current_corner = value
            self.drag = {'handle': value, 'snapshot': snapshot, 'changed': False}
            frame = self.get_current_frame()
            if not self.screen_mode and frame and 'rect' in frame:
                self.drag['rect'] = self._shown_rect(frame)
            return
        # A frame: select it and start moving it.
        self.current_frame_idx = value
        self.current_corner = 'tl'
        frame = self.frames()[value]
        self.drag = {'move': True, 'snapshot': snapshot, 'changed': False,
                     'start': self._window_to_screen(pos), 'start_canvas': self.window_to_canvas(pos)}
        if 'rect' in frame:
            self.drag['rect'] = self._shown_rect(frame)
        else:
            self.drag['corners'] = copy.deepcopy(frame['corners'])

    def _drag_to(self, pos, snapping=True):
        drag = self.drag
        if not drag['changed']:
            self.history.checkpoint(drag['snapshot'])
            drag['changed'] = True
            self.modified = True
        self.guides = []
        canvas = self.window_to_canvas(pos)
        frame = self.get_current_frame()

        if self.screen_mode:
            self.screen()['corners'][drag['handle']] = [round(canvas[0], 1), round(canvas[1], 1)]
            return
        if frame is None:
            return
        if 'rect' not in frame:
            if 'handle' in drag:
                frame['corners'][drag['handle']] = [round(canvas[0], 1), round(canvas[1], 1)]
            else:
                dx = canvas[0] - drag['start_canvas'][0]
                dy = canvas[1] - drag['start_canvas'][1]
                frame['corners'] = {k: [round(x + dx, 1), round(y + dy, 1)]
                                    for k, (x, y) in drag['corners'].items()}
            return

        target, key = self._rect_target(frame)
        point = self._window_to_screen(pos)
        threshold = self._snap_threshold()
        targets = snap_targets(self._other_rects())
        if 'handle' in drag:
            rect = resize_rect(drag['rect'], drag['handle'], point)
            if snapping:
                rect, self.guides = snap_resize(rect, drag['handle'], targets, threshold)
        else:
            rect = move_rect(drag['rect'], point[0] - drag['start'][0], point[1] - drag['start'][1])
            if snapping:
                rect, self.guides = snap_move(rect, targets, threshold)
        target[key] = rect

    def _snap_threshold(self):
        """SNAP_PIXELS in normalized screen units, per axis."""
        corners = screen_corners(self.config)
        tl, tr, bl = (np.array(self.canvas_to_window(corners[k])) for k in ('tl', 'tr', 'bl'))
        return (SNAP_PIXELS / max(1.0, np.linalg.norm(tr - tl)),
                SNAP_PIXELS / max(1.0, np.linalg.norm(bl - tl)))

    def _other_rects(self):
        rects = []
        for i, frame in enumerate(self.frames()):
            if i == self.current_frame_idx or 'rect' not in frame:
                continue
            cfg, _, hidden = self._shown(frame)
            if not hidden:
                rects.append(cfg['rect'])
        return rects

    def _wheel(self, steps):
        frame = self.get_current_frame()
        if self.screen_mode or frame is None or 'rect' not in frame or steps == 0:
            return
        self._change(group=('wheel', self.current_frame_idx))
        target, key = self._rect_target(frame)
        target[key] = scale_rect(target[key], WHEEL_SCALE ** steps)

    # -- actions (shared by keys and toolbar buttons) ---------------------------------

    def press(self, action):
        """Run a toolbar action by id."""
        handler = {
            'save': self._save_config, 'undo': self.undo, 'redo': self.redo,
            'add': self._add_frame, 'delete': self._delete_frame, 'screen': self._toggle_screen_mode,
            'portrait': self._toggle_portrait, 'preview': self._next_preview, 'detect': self._auto_detect,
            'smaller': lambda: self._resize_ui(-1), 'bigger': lambda: self._resize_ui(1),
            'help': self._toggle_help, 'quit': self._request_quit,
        }[action]
        self.history.end_group()
        handler()

    def handle_key(self, key, mod=0):
        shift = bool(mod & pygame.KMOD_SHIFT)
        command = bool(mod & (pygame.KMOD_CTRL | pygame.KMOD_META))
        resize = bool(mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT | pygame.KMOD_META))
        if key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            self._arrow(key, shift, resize)
            return
        self.history.end_group()
        if command and key == pygame.K_z:
            self.redo() if shift else self.undo()
        elif command and key == pygame.K_y:
            self.redo()
        elif key in (pygame.K_ESCAPE, pygame.K_q):
            self._request_quit()
        elif key == pygame.K_TAB:
            self._cycle_frame(shift)
        elif key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
            self.current_corner = CORNER_ORDER[key - pygame.K_1]
        elif key == pygame.K_e:
            self._toggle_screen_mode()
        elif key == pygame.K_p:
            self._toggle_portrait()
        elif key == pygame.K_h:
            self._toggle_help()
        elif key == pygame.K_r:
            self._next_preview()
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
        elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self._resize_ui(1)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self._resize_ui(-1)

    def _toggle_screen_mode(self):
        self.screen_mode = not self.screen_mode
        self.current_corner = 'tl'
        self._say("Editing the screen corners: drag the numbered handles" if self.screen_mode
                  else "Editing frames")

    def _toggle_portrait(self):
        self.portrait = not self.portrait
        self._say("Editing the portrait layout (used when cast content is portrait)" if self.portrait
                  else "Editing the landscape layout")

    def _toggle_help(self):
        self.show_help = not self.show_help

    def _next_preview(self):
        self.preview = PREVIEW_STATES[(PREVIEW_STATES.index(self.preview) + 1) % len(PREVIEW_STATES)]
        self._say(f"Previewing the {self.preview} layout; edits apply to it")

    def _resize_ui(self, direction):
        self.ui_scale = min(2.0, max(0.75, self.ui_scale + 0.125 * direction))

    def _request_quit(self):
        if self.modified and time.monotonic() >= self._quit_armed_until:
            self._quit_armed_until = time.monotonic() + QUIT_CONFIRM_SECONDS
            self._say("Unsaved changes! Press Q again (or click Quit) to discard them, or S to save.")
            return
        self.running = False

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
            self._change(group=('screen', self.current_corner))
            step = PIXEL_STEP_BIG if shift else PIXEL_STEP
            corner = self.screen()['corners'][self.current_corner]
            corner[0] += dx * step
            corner[1] += dy * step
            return

        frame = self.get_current_frame()
        if not frame:
            return
        self._change(group=('frame', self.current_frame_idx, resize))
        if 'rect' not in frame:
            # Legacy frame: nudge its corners in canvas pixels.
            step = PIXEL_STEP_BIG if shift else PIXEL_STEP
            frame['corners'][self.current_corner][0] += dx * step
            frame['corners'][self.current_corner][1] += dy * step
            return

        target, key_name = self._rect_target(frame)
        rect = target[key_name]
        step = MOVE_STEP_BIG if shift else MOVE_STEP
        if resize:
            rect[2] = round(max(MIN_SIZE, rect[2] + dx * step), 4)
            rect[3] = round(max(MIN_SIZE, rect[3] - dy * step), 4)   # Up grows
        else:
            rect[0] = round(rect[0] + dx * step, 4)
            rect[1] = round(rect[1] + dy * step, 4)

    def _add_frame(self):
        self._change()
        frames = self.frames()
        ids = {f['id'] for f in frames}
        n = len(frames) + 1
        while f"frame_{n}" in ids:
            n += 1
        frames.append({"id": f"frame_{n}", "label": f"Frame {n}", "rect": [0.3, 0.3, 0.4, 0.4],
                       "source": {"type": "text", "text": f"Frame {n}"}})
        self.current_frame_idx = len(frames) - 1
        self.screen_mode = False
        self._say(f"Added Frame {n} (a text frame: set its source in the config)")

    def _delete_frame(self):
        frames = self.frames()
        if not frames or self.screen_mode:
            return
        self._change()
        removed = frames.pop(self.current_frame_idx)
        self._clamp_selection()
        self._say(f"Deleted {removed.get('label') or removed['id']} (Ctrl+Z to undo)")

    def _auto_detect(self):
        if self.detect is None:
            self._say("Auto-detect needs a camera: set detection.camera in the config")
            return
        from ..screen_detect import DetectionError

        def show(rgba):
            window = pygame.display.get_surface() or self._window
            h, w_ = rgba.shape[:2]
            image = pygame.image.frombuffer(rgba.tobytes(), (w_, h), 'RGBA')
            pygame.transform.smoothscale(image.convert(), window.get_size(), window)
            pygame.display.flip()

        self._say("Detecting the screen...")
        try:
            result = self.detect(show, pygame.event.pump)
        except DetectionError as e:
            self._say(f"Screen detection failed: {e}")
            return
        for warning in result.warnings:
            print(f"Warning: {warning}")
        self._change()
        self.screen()['corners'] = {k: [round(v, 1) for v in xy] for k, xy in result.corners.items()}
        self.screen_mode = True
        self._say("Screen detected" + (f" ({len(result.warnings)} warning(s) in the terminal)"
                                       if result.warnings else "")
                  + ". Check the outline, drag corners if needed, then Save.")

    def _save_config(self):
        try:
            if self.config_path:
                save_config(self.config, self.config_path)
            else:
                save_config(self.config)
            self.modified = False
            self._say("Saved")
        except Exception as e:
            self._say(f"Error saving config: {e}")

    def _load_config(self):
        try:
            config = load_config(self.config_path) if self.config_path else load_config()
            self._change()
            self.config = migrate_config(config)
            self.current_frame_idx = 0
            self.current_corner = 'tl'
            self.modified = False
            self._say("Reloaded the config from disk (Ctrl+Z to go back)")
        except Exception as e:
            self._say(f"Error loading config: {e}")

import pygame
from .config_io import load_config, save_config, new_frame
from .homography import nudge_corner


class CalibrationUI:
    """Interactive calibration mode for adjusting frame corners."""

    def __init__(self, canvas_width, canvas_height, config, config_path=None):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.config = config
        self.config_path = config_path
        self.current_frame_idx = 0
        self.current_corner = 'tl'
        self.running = True
        self.modified = False

    def get_current_frame(self):
        """Get currently selected frame."""
        frames = self.config.get('frames', [])
        if 0 <= self.current_frame_idx < len(frames):
            return frames[self.current_frame_idx]
        return None

    def render_grid(self, surface):
        """Draw calibration grid on surface."""
        pygame.draw.rect(surface, (20, 20, 20), surface.get_rect())

        frame = self.get_current_frame()
        if not frame:
            return

        corners = frame['corners']
        quad_points = [
            corners['tl'],
            corners['tr'],
            corners['br'],
            corners['bl'],
            corners['tl']
        ]

        for i in range(4):
            p1 = quad_points[i]
            p2 = quad_points[i + 1]
            pygame.draw.line(surface, (100, 100, 100), p1, p2, 2)

        colors = {'tl': (0, 255, 0), 'tr': (255, 0, 0), 'br': (0, 0, 255), 'bl': (255, 255, 0)}
        for corner, color in colors.items():
            pos = tuple(corners[corner])
            size = 8
            if corner == self.current_corner:
                pygame.draw.circle(surface, (255, 255, 255), pos, size)
            else:
                pygame.draw.circle(surface, color, pos, size)

        pygame.draw.line(surface, (50, 50, 50), (0, self.canvas_height // 2), (self.canvas_width, self.canvas_height // 2), 1)
        pygame.draw.line(surface, (50, 50, 50), (self.canvas_width // 2, 0), (self.canvas_width // 2, self.canvas_height), 1)

    def handle_events(self):
        """Handle keyboard input."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                    self.running = False
                elif event.key == pygame.K_TAB:
                    self._cycle_frame(event.mod & pygame.KMOD_SHIFT)
                elif event.key in [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4]:
                    self._select_corner(event.key)
                elif event.key in [pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT]:
                    self._nudge_corner(event.key, event.mod & pygame.KMOD_SHIFT)
                elif event.key == pygame.K_n:
                    self._add_frame()
                elif event.key == pygame.K_d:
                    self._delete_frame()
                elif event.key == pygame.K_s:
                    self._save_config()
                elif event.key == pygame.K_l:
                    self._load_config()

    def _cycle_frame(self, reverse=False):
        """Cycle to next/previous frame."""
        frames = self.config.get('frames', [])
        if not frames:
            return

        if reverse:
            self.current_frame_idx = (self.current_frame_idx - 1) % len(frames)
        else:
            self.current_frame_idx = (self.current_frame_idx + 1) % len(frames)

        self.current_corner = 'tl'

    def _select_corner(self, key):
        """Select corner by key 1-4."""
        corners_map = {pygame.K_1: 'tl', pygame.K_2: 'tr', pygame.K_3: 'br', pygame.K_4: 'bl'}
        self.current_corner = corners_map.get(key, 'tl')

    def _nudge_corner(self, key, shift=False):
        """Move selected corner."""
        frame = self.get_current_frame()
        if not frame:
            return

        delta = 10 if shift else 1
        dx, dy = 0, 0

        if key == pygame.K_UP:
            dy = -delta
        elif key == pygame.K_DOWN:
            dy = delta
        elif key == pygame.K_LEFT:
            dx = -delta
        elif key == pygame.K_RIGHT:
            dx = delta

        frame['corners'][self.current_corner][0] += dx
        frame['corners'][self.current_corner][1] += dy
        self.modified = True

    def _add_frame(self):
        """Add new frame."""
        frames = self.config.get('frames', [])
        new_id = f"frame_{len(frames) + 1}"
        new_f = new_frame(new_id, f"Frame {len(frames) + 1}", "media/sample.mp4",
                          self.canvas_width, self.canvas_height)
        frames.append(new_f)
        self.current_frame_idx = len(frames) - 1
        self.modified = True

    def _delete_frame(self):
        """Delete current frame."""
        frames = self.config.get('frames', [])
        if not frames:
            return

        frames.pop(self.current_frame_idx)
        if frames:
            self.current_frame_idx = min(self.current_frame_idx, len(frames) - 1)
        self.modified = True

    def _save_config(self):
        """Save config to disk."""
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
        """Reload config from disk."""
        try:
            self.config = load_config(self.config_path) if self.config_path else load_config()
            self.current_frame_idx = 0
            self.current_corner = 'tl'
            self.modified = False
            print("Config reloaded!")
        except Exception as e:
            print(f"Error loading config: {e}")

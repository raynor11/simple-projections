import random
import threading
import time
from functools import lru_cache
from pathlib import Path

import cv2

from .base import FrameRing, Source, ThreadedSource
from .file import SOFTWARE, decode_size, open_video


@lru_cache(maxsize=None)
def clip_info(path):
    """(frame rate, (width, height), duration in seconds), from the file's header."""
    cap = cv2.VideoCapture(str(path))
    fps = min(max(cap.get(cv2.CAP_PROP_FPS) or 30, 1), 60)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, size, frames / fps if frames > 0 else 0.0


def repeat_range(clip):
    """A clip's "repeat" as (min, max): a number, [min, max], or 1 when left out."""
    repeat = clip.get('repeat', 1)
    if isinstance(repeat, list):
        return int(repeat[0]), int(repeat[1])
    return int(repeat), int(repeat)


def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


class ClipPlayer(ThreadedSource):
    """
    One play of one video, no looping. The decoder opens and decodes the
    first frame as soon as the player starts, then waits for go(): so the
    next clip is ready the moment it's due and the cut has no gap. After the
    last frame it holds that frame.
    """

    pixel_format = 'BGR'

    def __init__(self, path, target_size, decoders):
        super().__init__({'type': 'file', 'path': path})
        self.target_size = target_size
        self._decoders = decoders    # path -> decoder that worked, shared across plays
        self._go = threading.Event()
        self._at = None
        self.started_at = None       # when the first frame went on screen (monotonic)
        self.duration = clip_info(path)[2]

    def go(self, at=None):
        """Start playing at monotonic time `at` (or as soon as ready)."""
        self._at = at
        self._go.set()

    def _run(self):
        path = self.cfg['path']
        fps, native, _ = clip_info(path)
        interval = 1.0 / fps
        size = decode_size(native, self.target_size)
        cap, decoder = open_video(path, size, native, only=self._decoders.get(path))
        if not cap.isOpened():
            print(f"Failed to open video: {path}")
            return
        if path not in self._decoders:
            self._decoders[path] = decoder
            print(f"Video {Path(path).name}: {decoder} decoding"
                  + (f", at {size[0]}x{size[1]}" if size else ""))
        ring, shrink_ring = FrameRing(), FrameRing()

        def read():
            ok, frame = cap.read(ring.next())
            if not ok:
                return None
            frame = ring.filled(frame)
            if decoder == SOFTWARE and size and frame.shape[1::-1] != size:
                dst = shrink_ring.next()
                if dst is None or dst.shape[1::-1] != size:
                    dst = None
                return shrink_ring.filled(cv2.resize(frame, size, dst=dst, interpolation=cv2.INTER_AREA))
            return self._shrink_for_target(frame, shrink_ring)

        try:
            frame = read()
            if frame is None:
                print(f"Video produced no frames: {path}")
                return
            while not self._go.wait(0.1):
                if self._stop.is_set():
                    return
            if self._at is not None and self._stop.wait(max(0.0, self._at - time.monotonic())):
                return
            self._publish(frame)
            self.started_at = next_time = time.monotonic()
            while not self._stop.is_set():
                frame = read()
                if frame is None:
                    return       # the last frame stays up until the next clip covers it
                next_time += interval
                delay = next_time - time.monotonic()
                if delay > 0:
                    if self._stop.wait(delay):
                        return
                else:
                    next_time = time.monotonic()   # can't keep up; don't catch up in a burst
                self._publish(frame)
        finally:
            cap.release()


class SequenceSource(Source):
    """
    Plays named clips one after another: each plays a random number of
    times within its "repeat" range, then moves on to its "next" clip. Every
    change, including a clip repeating, fades the incoming play in over the
    end of the outgoing one, so the seams don't jump.

    Draws as up to two layers (see layers()), blended on the GPU. While its
    frame is hidden (e.g. while casting) for more than PAUSE_AFTER_SECONDS
    the decoders are released; it starts over from "start" when shown.
    """

    PAUSE_AFTER_SECONDS = 2.0
    SIZE_WAIT_SECONDS = 1.0     # the frame's on-screen size arrives just after start()

    def __init__(self, cfg, rng=None):
        super().__init__(cfg)
        self._rng = rng or random.Random()
        self._decoders = {}
        self._current = None     # (clip name, player)
        self._next = None
        self._remaining = 0      # plays of the current clip still to come after this one
        self._shown = True
        self._hidden_since = None
        self._started = None
        self._now = 0.0

    @property
    def fade(self):
        return max(0.0, float(self.cfg.get('fade_seconds', 0.4)))

    def _new_player(self, name):
        player = ClipPlayer(self.cfg['clips'][name]['path'], self.target_size, self._decoders)
        player.start()
        return player

    def start(self):
        self._started = None

    def close(self):
        self._stop_players()

    def update(self, cfg):
        return cfg == self.cfg

    def _stop_players(self):
        for item in (self._current, self._next):
            if item:
                item[1].close()
        self._current = self._next = None

    def _plays(self, name):
        return self._rng.randint(*repeat_range(self.cfg['clips'][name]))

    def poll(self, now):
        self._now = now
        if self._started is None:
            self._started = now
        if not self._shown:
            return
        if self._current is None:
            if self.target_size is None and now - self._started < self.SIZE_WAIT_SECONDS:
                return
            name = self.cfg['start']
            self._remaining = self._plays(name) - 1
            self._current = (name, self._new_player(name))
            self._current[1].go()
            return
        name, player = self._current
        if player.started_at is None:
            return
        if self._next is None:
            if self._remaining > 0:
                next_name = name
            else:
                next_name = self.cfg['clips'][name]['next']
            self._next = (next_name, self._new_player(next_name))
            self._next[1].go(player.started_at + max(0.0, player.duration - self.fade))
        elif self._next_alpha(now) >= 1.0:
            player.close()
            next_name = self._next[0]
            if next_name == name and self._remaining > 0:
                self._remaining -= 1
            else:
                self._remaining = self._plays(next_name) - 1
            self._current, self._next = self._next, None

    def _next_alpha(self, now):
        started = self._next[1].started_at if self._next else None
        if started is None:
            return 0.0
        if self.fade <= 0:
            return 1.0
        return smoothstep((now - started) / self.fade)

    def layers(self):
        """[(source, alpha), ...] to draw, bottom first."""
        out = []
        if self._current:
            out.append((self._current[1], 1.0))
        alpha = self._next_alpha(self._now)
        if alpha > 0:
            out.append((self._next[1], alpha))
        return out

    def set_shown(self, shown):
        if shown:
            self._hidden_since = None
            self._shown = True
        elif self._hidden_since is None:
            self._hidden_since = self._now
        elif self._shown and self._now - self._hidden_since >= self.PAUSE_AFTER_SECONDS:
            self._shown = False
            self._stop_players()
            print("Sequence: paused while hidden")

    def set_target_size(self, width, height):
        super().set_target_size(width, height)
        for item in (self._current, self._next):
            if item:
                item[1].target_size = self.target_size

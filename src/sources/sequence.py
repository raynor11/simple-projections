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
    first frame as soon as the player starts, then waits for go(), so the
    clip starts on time. At the end it releases its decoder (sets
    `finished`) and holds the last frame.

    `software` forces FFmpeg decoding. The Pi 4's codec can't run two
    hardware decodes at once (it runs out of memory and can hang the
    firmware), so a sequence opens the next clip in software while the
    current one is on the hardware decoder, and the other way round.
    """

    pixel_format = 'BGR'

    def __init__(self, path, target_size, decoders, software=False):
        super().__init__({'type': 'file', 'path': path})
        self.target_size = target_size
        self.software = software
        self._decoders = decoders    # path -> hardware decoder that worked, shared across plays
        self._go = threading.Event()
        self._at = None
        self.decoder = SOFTWARE if software else None   # known once opened
        self.duration = clip_info(path)[2]
        self.started_at = None       # when the first frame went on screen (monotonic)
        self.finished = False        # played to the end (or failed) and released the decoder

    @property
    def hardware(self):
        """Whether this player holds (or may take) the hardware decoder."""
        return self.decoder != SOFTWARE and not self.finished

    def go(self, at=None):
        """Start playing at monotonic time `at` (or as soon as it's ready)."""
        self._at = at
        self._go.set()

    def _run(self):
        path = self.cfg['path']
        fps, native, _ = clip_info(path)
        interval = 1.0 / fps
        size = decode_size(native, self.target_size)
        only = SOFTWARE if self.software else self._decoders.get(path)
        cap, decoder = open_video(path, size, native, only=only)
        self.decoder = decoder
        if not cap.isOpened():
            print(f"Failed to open video: {path}")
            cap.release()
            self.finished = True
            return
        if decoder != SOFTWARE and path not in self._decoders:
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
            self.finished = True


class SequenceSource(Source):
    """
    Plays named clips one after another: each plays a random number of
    times within its "repeat" range, then moves on to its "next" clip.

    A clip with a "reverse" file (itself played backwards) bounces: its
    repeats alternate forwards and backwards and cut straight over, since
    the frames match. "end_reversed" makes it always finish on a backwards
    play (an even number of plays), for when its first frame leads into the
    next clip better than its last. Any other change fades the incoming
    clip in over the last fade_seconds of the outgoing one.

    The next clip is opened ahead of time on the other kind of decoder
    (see ClipPlayer), so it's ready the moment it's due. Draws as up to two
    layers (see layers()), blended on the GPU. While its frame is hidden
    (e.g. while casting) for more than PAUSE_AFTER_SECONDS the decoders are
    released; it starts over from "start" when shown.
    """

    PAUSE_AFTER_SECONDS = 2.0
    SIZE_WAIT_SECONDS = 1.0     # the frame's on-screen size arrives just after start()

    def __init__(self, cfg, rng=None):
        super().__init__(cfg)
        self._rng = rng or random.Random()
        self._decoders = {}
        self._current = None     # (clip name, reversed, player)
        self._next = None
        self._next_fades = False
        self._remaining = 0      # plays of the current clip still to come after this one
        self._shown = True
        self._hidden_since = None
        self._started = None
        self._now = 0.0

    @property
    def fade(self):
        return max(0.0, float(self.cfg.get('fade_seconds', 0.4)))

    def _new_player(self, name, reversed_, software):
        clip = self.cfg['clips'][name]
        player = ClipPlayer(clip['reverse'] if reversed_ else clip['path'], self.target_size,
                            self._decoders, software=software)
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
                item[2].close()
        self._current = self._next = None

    def _plays(self, name):
        clip = self.cfg['clips'][name]
        lo, hi = repeat_range(clip)
        plays = self._rng.randint(lo, hi)
        if clip.get('end_reversed') and clip.get('reverse') and plays % 2:
            plays = plays + 1 if plays < hi else max(2, plays - 1)
        return plays

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
            self._current = (name, False, self._new_player(name, False, False))
            self._current[2].go()
            return
        name, reversed_, player = self._current
        if player.started_at is None and not player.finished:
            return
        if self._next is None:
            clip = self.cfg['clips'][name]
            if self._remaining > 0:
                next_name = name
                bounce = bool(clip.get('reverse'))
                next_reversed = not reversed_ if bounce else False
            else:
                next_name, bounce, next_reversed = clip['next'], False, False
            self._next_fades = not bounce
            # Never two hardware decodes at once: if this one may be on the
            # hardware decoder, the next is opened in software.
            nxt = self._new_player(next_name, next_reversed, software=player.hardware)
            self._next = (next_name, next_reversed, nxt)
            if player.started_at is None:    # the current one failed: go straight on
                nxt.go()
            else:
                overlap = self.fade if self._next_fades else 0.0
                nxt.go(player.started_at + max(0.0, player.duration - overlap))
        elif self._next_alpha(now) >= 1.0 or (self._next[2].finished and self._next[2].started_at is None):
            player.close()
            next_name = self._next[0]
            if next_name == name and self._remaining > 0:
                self._remaining -= 1
            else:
                self._remaining = self._plays(next_name) - 1
            self._current, self._next = self._next, None

    def _next_alpha(self, now):
        started = self._next[2].started_at if self._next else None
        if started is None:
            return 0.0
        if self.fade <= 0 or not self._next_fades:
            return 1.0
        return smoothstep((now - started) / self.fade)

    def layers(self):
        """[(source, alpha), ...] to draw, bottom first."""
        out = []
        if self._current:
            out.append((self._current[2], 1.0))
        alpha = self._next_alpha(self._now)
        if alpha > 0:
            out.append((self._next[2], alpha))
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
                item[2].target_size = self.target_size

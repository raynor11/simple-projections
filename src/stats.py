"""
Playback performance stats: frame times, how much work each tick did, CPU,
memory and (on a Raspberry Pi) temperature and throttling.
"""

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

import numpy as np


THERMAL_ZONE = '/sys/class/thermal/thermal_zone0/temp'


def rss_mb():
    """Current resident memory of this process in MB (peak RSS where current isn't available)."""
    try:
        with open('/proc/self/statm') as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE') / 1e6
    except OSError:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1e6 if sys.platform == 'darwin' else peak / 1e3   # bytes on macOS, KB on Linux


def cpu_temperature():
    try:
        with open(THERMAL_ZONE) as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        return None


def throttled_flags():
    """The Pi firmware's throttle/undervoltage bit field (0 is healthy), or None off a Pi."""
    try:
        out = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
        return int(out.stdout.strip().split('=')[1], 16)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None


@dataclass
class Window:
    """Counters for one reporting interval."""
    started: float
    cpu_started: float
    tick_ms: list = field(default_factory=list)
    upload_ms: float = 0.0
    draw_ms: float = 0.0
    renders: int = 0
    ticks: int = 0
    upload_bytes: int = 0
    uploads: int = 0
    dropped: int = 0


class Stats:
    """
    Collects per-tick numbers and summarises them every `interval` seconds.
    `clock` and `cpu_clock` are injectable for tests.
    """

    def __init__(self, interval=10.0, clock=time.monotonic, cpu_clock=time.process_time):
        self.interval = interval
        self.clock = clock
        self.cpu_clock = cpu_clock
        self.window = Window(clock(), cpu_clock())
        self.last = None   # the most recent summary dict

    def record_tick(self, tick_ms, rendered):
        w = self.window
        w.ticks += 1
        w.tick_ms.append(tick_ms)
        if rendered:
            w.renders += 1

    def record_render(self, upload_ms, draw_ms, upload_bytes, uploads, dropped):
        w = self.window
        w.upload_ms += upload_ms
        w.draw_ms += draw_ms
        w.upload_bytes += upload_bytes
        w.uploads += uploads
        w.dropped += dropped

    def maybe_report(self):
        """A summary dict once per interval (and starts a new window), else None."""
        now = self.clock()
        elapsed = now - self.window.started
        if elapsed < self.interval:
            return None
        w = self.window
        cpu = self.cpu_clock() - w.cpu_started
        ticks = np.array(w.tick_ms) if w.tick_ms else np.zeros(1)
        renders = max(1, w.renders)
        summary = {
            'fps': w.renders / elapsed,
            'ticks_per_s': w.ticks / elapsed,
            'tick_ms_avg': float(ticks.mean()),
            'tick_ms_p95': float(np.percentile(ticks, 95)),
            'upload_ms_per_render': w.upload_ms / renders,
            'draw_ms_per_render': w.draw_ms / renders,
            'upload_mb_s': w.upload_bytes / elapsed / 1e6,
            'uploads_per_s': w.uploads / elapsed,
            'dropped_per_s': w.dropped / elapsed,
            'cpu_percent': 100 * cpu / elapsed,     # of one core; >100 means several threads busy
            'rss_mb': rss_mb(),
            'temp_c': cpu_temperature(),
            'throttled': throttled_flags(),
        }
        self.last = summary
        self.window = Window(now, self.cpu_clock())
        return summary


def format_summary(s):
    parts = [
        f"FPS {s['fps']:.1f}",
        f"tick {s['tick_ms_avg']:.1f}/{s['tick_ms_p95']:.1f}ms (avg/p95)",
        f"upload {s['upload_ms_per_render']:.1f}ms + draw {s['draw_ms_per_render']:.1f}ms per frame",
        f"{s['upload_mb_s']:.0f}MB/s uploaded",
        f"{s['dropped_per_s']:.1f} dropped/s",
        f"CPU {s['cpu_percent']:.0f}%",
        f"RSS {s['rss_mb']:.0f}MB",
    ]
    if s['temp_c'] is not None:
        parts.append(f"{s['temp_c']:.0f}°C")
    if s['throttled'] is not None:
        parts.append("throttled=0x%x" % s['throttled'] + ("" if s['throttled'] == 0 else " (!)"))
    return " | ".join(parts)

import shutil
import subprocess
import sys
import time


RESTART_SECONDS = 5.0


class AudioLoop:
    """
    Plays a capture card's USB audio out through the Pi's HDMI (to the
    projector's speakers) with gst-launch, delayed to line up with the video
    (which lags by the capture -> decode -> render path).
    """

    def __init__(self, device, sink='projector', delay_ms=120):
        self.device = device
        self.sink = sink
        self.delay_ms = delay_ms
        self._proc = None
        self._last_start = 0.0
        self._warned = False

    def command(self):
        delay_ns = int(self.delay_ms) * 1_000_000
        return [
            'gst-launch-1.0', '-q',
            'alsasrc', f'device={self.device}', '!',
            'audioconvert', '!', 'audioresample', '!',
            'queue', 'max-size-buffers=0', 'max-size-bytes=0',
            f'max-size-time={delay_ns + 1_000_000_000}', f'min-threshold-time={delay_ns}', '!',
            'alsasink', f'device={self.sink}',
        ]

    def _available(self):
        if not sys.platform.startswith('linux') or shutil.which('gst-launch-1.0') is None:
            if not self._warned:
                print("Cast audio disabled: needs Linux with gst-launch-1.0")
                self._warned = True
            return False
        return True

    def ensure_running(self):
        """Start the loop, or restart it if it died (rate-limited)."""
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self._available() or time.monotonic() - self._last_start < RESTART_SECONDS:
            return
        if self._proc is not None:
            print(f"Cast audio loop exited ({self._proc.returncode}); restarting")
        self._last_start = time.monotonic()
        self._proc = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL)

    def stop(self):
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._last_start = 0.0

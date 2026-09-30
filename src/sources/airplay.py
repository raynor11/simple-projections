import time
from pathlib import Path

from .cast import ActiveDebouncer, OnDemandCaptureSource, DEFAULT_GRACE_SECONDS


TCP_ESTABLISHED = '01'
PROC_TCP_FILES = ('/proc/net/tcp', '/proc/net/tcp6')
CHECK_SECONDS = 0.5


def count_established(proc_net_tcp_text, port):
    """
    Count ESTABLISHED connections whose local port is `port`, from the
    contents of /proc/net/tcp or /proc/net/tcp6.
    """
    count = 0
    for line in proc_net_tcp_text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        local, state = fields[1], fields[3]
        if state == TCP_ESTABLISHED and int(local.rsplit(':', 1)[1], 16) == port:
            count += 1
    return count


def airplay_connected(port):
    total = 0
    for path in PROC_TCP_FILES:
        try:
            total += count_established(Path(path).read_text(), port)
        except OSError:
            pass
    return total > 0


class AirPlaySource(OnDemandCaptureSource):
    """
    AirPlay mirroring, received by UxPlay (a separate service) and written
    into a v4l2loopback device.

    A session is running while a phone holds a TCP connection to UxPlay's
    port. That's checked directly because mirroring stops sending frames
    while the phone's screen is static, so "no recent frames" doesn't mean
    the session ended.
    """

    DEVICE_KEYS = OnDemandCaptureSource.DEVICE_KEYS + ('port', 'grace_seconds')
    # v4l2loopback only accepts readers while UxPlay is writing, so retry
    # opening quickly once a session starts.
    RECONNECT_SECONDS = 0.5

    def __init__(self, cfg):
        cfg = dict(cfg)
        cfg.setdefault('fourcc', None)   # take the loopback's own format
        super().__init__(cfg)
        self.port = int(cfg.get('port', 7000))
        self.debouncer = ActiveDebouncer(cfg.get('grace_seconds', DEFAULT_GRACE_SECONDS))
        self._last_check = 0.0

    def _session_active(self):
        now = time.monotonic()
        if now - self._last_check >= CHECK_SECONDS:
            self._last_check = now
            self.debouncer.set(airplay_connected(self.port))
        return self.debouncer.value

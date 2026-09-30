"""
Wall-power loss handling.

The UPS's outlet is on a light switch. When the switch is turned off the
UPS goes on battery; after a short debounce (so flicking the switch, or a
brief sag, does nothing) the projector is told to turn off, and once it
has finished (or a timeout passes) the Pi shuts down. NUT then tells the
UPS to cut its output, and to turn it back on when wall power returns --
which is what boots the Pi again.
"""

import subprocess


NORMAL, DEBOUNCE, POWERING_OFF, SHUTDOWN = 'normal', 'debounce', 'powering_off', 'shutdown'
RESEND_OFF_SECONDS = 15

# Actions for the daemon to carry out.
PROJECTOR_OFF, PROJECTOR_ON, SHUTDOWN_HOST = 'projector_off', 'projector_on', 'shutdown'


def parse_ups_status(text):
    """`upsc <ups> ups.status` output, e.g. 'OB DISCHRG' -> {'OB', 'DISCHRG'}."""
    return set(text.split())


def read_ups_status(ups='apc@localhost'):
    """The UPS's status flags, or None if NUT can't be reached."""
    try:
        out = subprocess.run(['upsc', ups, 'ups.status'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return parse_ups_status(out.stdout)


def on_battery(flags):
    """True/False, or None if unknown. Unknown never starts a shutdown."""
    if flags is None:
        return None
    return 'OB' in flags


def request_shutdown():
    """
    Have NUT's upsmon do a forced shutdown: it powers the Pi off cleanly
    and, in the final shutdown stage, tells the UPS to cut and later
    restore its output.
    """
    subprocess.run(['upsmon', '-c', 'fsd'], check=True)


class PowerSequencer:
    """Pure state machine: feed it the UPS and projector state, get actions back."""

    def __init__(self, debounce_s=10, projector_off_timeout_s=90):
        self.debounce_s = debounce_s
        self.projector_off_timeout_s = projector_off_timeout_s
        self.state = NORMAL
        self._since = None
        self._last_off_sent = None

    def step(self, now, battery, projector_power=None):
        """
        battery: True (on battery), False (wall power) or None (unknown).
        projector_power: the projector's reported state ('off', 'on', ...) or None.
        """
        if self.state == SHUTDOWN:
            return []
        if self.state == NORMAL:
            if battery:
                print(f"UPS on battery; turning off in {self.debounce_s}s unless power returns")
                self.state, self._since = DEBOUNCE, now
            return []
        if not battery and battery is not None:
            was_off = self.state == POWERING_OFF
            print("Wall power restored" + ("; turning the projector back on" if was_off else ""))
            self.state = NORMAL
            return [PROJECTOR_ON] if was_off else []
        if self.state == DEBOUNCE:
            if now - self._since >= self.debounce_s:
                print("Power still off; turning off the projector")
                self.state, self._since, self._last_off_sent = POWERING_OFF, now, now
                return [PROJECTOR_OFF]
            return []
        # POWERING_OFF
        if projector_power == 'off':
            print("Projector is off; shutting down")
            self.state = SHUTDOWN
            return [SHUTDOWN_HOST]
        if now - self._since >= self.projector_off_timeout_s:
            print("Projector didn't confirm it's off in time; shutting down anyway")
            self.state = SHUTDOWN
            return [SHUTDOWN_HOST]
        if projector_power == 'on' and now - self._last_off_sent >= RESEND_OFF_SECONDS:
            self._last_off_sent = now
            return [PROJECTOR_OFF]   # it ignored the first request
        return []

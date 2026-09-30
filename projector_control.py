#!/usr/bin/env python3
"""
Projector power and brightness control over RS-232, driven by the UPS.

- At boot (once the UPS reports wall power) turns the projector on.
- Sets the projector's light source mode by time of day (sunrise/sunset).
- When the light switch cuts the UPS's wall power: waits out a debounce,
  turns the projector off, then shuts the Pi down through NUT.

Runs as its own service (separate from the renderer) so a crash or
restart of the display can never block the power-loss shutdown.

Test commands against the real projector:
  projector_control.py --test status
  projector_control.py --test on | off
  projector_control.py --test light-source            (read the current mode)
  projector_control.py --test light-source eco        (set a mode)
  projector_control.py --test raw "07 14 00 05 00 34 00 00 11 00 5E"
  projector_control.py --test ups
"""

import argparse
import sys
import time

from src.config_io import ConfigWatcher, load_config, migrate_config, validate_config
from src.projector.brightness import local_now, mode_at
from src.projector.power import (
    NORMAL, POWERING_OFF, PROJECTOR_OFF, PROJECTOR_ON, SHUTDOWN_HOST,
    PowerSequencer, on_battery, read_ups_status, request_shutdown,
)
from src.projector.viewsonic import ProjectorError, from_config


POLL_SECONDS = 2
BOOT_WAIT_FOR_UPS_SECONDS = 30     # if NUT never answers, power the projector on anyway
REASSERT_SECONDS = 30 * 60         # re-send the light source mode in case the remote changed it
FAILED = object()


class Controller:
    def __init__(self, config, clock=time.monotonic, ups_reader=read_ups_status, shutdown=request_shutdown):
        self.clock = clock
        self.ups_reader = ups_reader
        self.shutdown = shutdown
        self.started = clock()
        self.booted = False
        self.mode_sent = None
        self.mode_sent_at = 0.0
        self._last_error = None
        self.projector = None
        self.apply_config(config)

    def apply_config(self, config):
        cfg = config['projector']
        old = getattr(self, 'cfg', None)
        self.config, self.cfg = config, cfg
        self.location = config.get('location')
        serial_keys = ('serial', 'baud', 'light_source_opcode', 'light_source_modes')
        if self.projector is None or any(cfg.get(k) != old.get(k) for k in serial_keys):
            if self.projector:
                self.projector.close()
            self.projector = from_config(cfg)
        if not hasattr(self, 'sequencer'):
            self.sequencer = PowerSequencer(cfg.get('power_loss_debounce_s', 10),
                                            cfg.get('projector_off_timeout_s', 90))
        else:
            self.sequencer.debounce_s = cfg.get('power_loss_debounce_s', 10)
            self.sequencer.projector_off_timeout_s = cfg.get('projector_off_timeout_s', 90)
        self.mode_sent = None   # re-apply brightness with the new settings

    def _log_error(self, what, error):
        message = f"{what}: {error}"
        if message != self._last_error:
            print(message)
            self._last_error = message

    def _projector(self, action, what):
        """Run a projector command; returns its result, or FAILED (after logging) if it errored."""
        try:
            result = action()
            self._last_error = None
            return result
        except ProjectorError as e:
            self._log_error(what, e)
            return FAILED

    def step(self):
        now = self.clock()
        battery = on_battery(self.ups_reader(self.cfg.get('ups', 'apc@localhost')))

        if not self.booted and self.sequencer.state == NORMAL:
            waited_long_enough = now - self.started > BOOT_WAIT_FOR_UPS_SECONDS
            if battery is False or (battery is None and waited_long_enough):
                if battery is None:
                    print("UPS status unavailable; powering the projector on anyway")
                if self.cfg.get('power_on_at_boot', True):
                    print("Turning the projector on")
                    self._projector(self.projector.power_on, "Couldn't turn the projector on")
                self.booted = True

        projector_power = None
        if self.sequencer.state == POWERING_OFF:
            projector_power = self._projector(self.projector.power_state, "Couldn't read projector power")
            if projector_power is FAILED:
                projector_power = None

        for action in self.sequencer.step(now, battery, projector_power):
            if action == PROJECTOR_OFF:
                self._projector(self.projector.power_off, "Couldn't turn the projector off")
            elif action == PROJECTOR_ON:
                self._projector(self.projector.power_on, "Couldn't turn the projector on")
                self.mode_sent = None
            elif action == SHUTDOWN_HOST:
                self.shutdown()

        if self.booted and self.sequencer.state == NORMAL:
            self._apply_brightness(now)

    def _apply_brightness(self, now):
        if not self.location or 'brightness' not in self.cfg:
            return
        mode = mode_at(local_now(self.location), self.location, self.cfg['brightness'])
        if mode == self.mode_sent and now - self.mode_sent_at < REASSERT_SECONDS:
            return
        # The projector ignores mode changes while it's off or warming up.
        if self._projector(self.projector.power_state, "Couldn't read projector power") != 'on':
            return
        if self._projector(lambda: self.projector.set_light_source(mode),
                           "Couldn't set the light source mode") is FAILED:
            return
        if mode != self.mode_sent:
            print(f"Light source mode: {mode}")
        self.mode_sent, self.mode_sent_at = mode, now

    def run(self, watcher=None):
        print("Projector control running")
        while True:
            if watcher:
                new_config = watcher.check(self.clock())
                if new_config is not None and 'projector' in new_config:
                    self.apply_config(new_config)
            self.step()
            time.sleep(POLL_SECONDS)


def run_test(config, args):
    projector = from_config(config['projector'])
    command, rest = args[0], args[1:]
    try:
        if command == 'status':
            print(f"Power: {projector.power_state()}")
        elif command == 'on':
            projector.power_on()
            print("Sent power on")
        elif command == 'off':
            projector.power_off()
            print("Sent power off")
        elif command == 'light-source' and rest:
            projector.set_light_source(rest[0])
            print(f"Set light source mode: {rest[0]}")
        elif command == 'light-source':
            print(f"Light source mode: {projector.light_source()}")
        elif command == 'raw' and rest:
            response = projector.transact(bytes.fromhex(' '.join(rest)))
            print(f"Response: {response.hex(' ')}")
        elif command == 'ups':
            print(f"UPS status: {read_ups_status(config['projector'].get('ups', 'apc@localhost'))}")
        else:
            print(__doc__)
            return 1
    except ProjectorError as e:
        print(f"Error: {e}")
        return 1
    finally:
        projector.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Projector power/brightness control")
    parser.add_argument("--config", default="config/frames.json")
    parser.add_argument("--test", nargs='+', metavar="CMD", help="Send one command and exit (see --help)")
    args = parser.parse_args()

    config = migrate_config(load_config(args.config))
    validate_config(config)
    if 'projector' not in config:
        print('No "projector" section in the config')
        return 1
    if args.test:
        return run_test(config, args.test)
    Controller(config).run(ConfigWatcher(args.config))


if __name__ == "__main__":
    sys.exit(main())

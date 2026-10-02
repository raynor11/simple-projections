#!/usr/bin/env python3
"""
Projector power and brightness control over RS-232, driven by the UPS.

- At boot (once the UPS reports wall power) turns the projector on, and
  once it has warmed up selects the Pi's input, 16:9 and the volume.
- Sets the projector's light source mode by time of day (sunrise/sunset).
- When the light switch cuts the UPS's wall power: waits out a debounce,
  turns the projector off, then shuts the Pi down through NUT.

Runs as its own service (separate from the renderer) so a crash or
restart of the display can never block the power-loss shutdown.

Test commands against the real projector:
  projector_control.py --test status
  projector_control.py --test on | off
  projector_control.py --test light-source            (read the current mode)
  projector_control.py --test light-source eco        (set a mode: normal, eco, dynamic_black)
  projector_control.py --test input hdmi1             (or hdmi2)
  projector_control.py --test aspect 16:9
  projector_control.py --test volume 5                (0-10)
  projector_control.py --test blank on | off          (AV mute)
  projector_control.py --test hours                   (light source hours)
  projector_control.py --test raw "07 14 00 05 00 34 00 00 11 00 5E"
  projector_control.py --test ups
"""

import argparse
import sys
import time

from src.config_io import ConfigWatcher, load_config, migrate_config, validate_config
from src.location import resolve_location
from src.projector.brightness import local_now, mode_at
from src.projector.power import (
    NORMAL, POWERING_OFF, PROJECTOR_OFF, PROJECTOR_ON, SHUTDOWN_HOST,
    PowerSequencer, on_battery, read_ups_status, request_shutdown,
)
from src.projector.viewsonic import ProjectorError, from_config


POLL_SECONDS = 2
BOOT_WAIT_FOR_UPS_SECONDS = 30     # if NUT never answers, power the projector on anyway
REASSERT_SECONDS = 30 * 60         # re-send the light source mode in case the remote changed it
LOCATION_RETRY_SECONDS = 10 * 60   # how often to retry an automatic location lookup that failed
HOURS_LOG_SECONDS = 24 * 3600      # log the light source hours once a day
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
        self.needs_setup = True        # input/aspect/volume, once the projector is on
        self._hours_logged_at = None
        self._last_error = None
        self.projector = None
        self.apply_config(config)

    def apply_config(self, config):
        cfg = config['projector']
        old = getattr(self, 'cfg', None)
        self.config, self.cfg = config, cfg
        self.location_cfg = config.get('location')
        self.location = None
        self._location_tried_at = None
        serial_keys = ('serial', 'baud')
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
        self.mode_sent = None   # re-apply brightness and setup with the new settings
        self.needs_setup = True

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
                self.needs_setup = True
            elif action == SHUTDOWN_HOST:
                self.shutdown()

        if self.booted and self.sequencer.state == NORMAL:
            self._maintain(now)

    def _maintain(self, now):
        """Setup, brightness and the hours log -- talking to the projector only when one is due."""
        mode = self._desired_mode(now)
        mode_due = mode is not None and (mode != self.mode_sent or now - self.mode_sent_at >= REASSERT_SECONDS)
        hours_due = self._hours_logged_at is None or now - self._hours_logged_at >= HOURS_LOG_SECONDS
        if not (self.needs_setup or mode_due or hours_due):
            return
        # The projector ignores most commands until it has finished warming up.
        if self._projector(self.projector.power_state, "Couldn't read projector power") != 'on':
            return
        if self.needs_setup:
            self._setup()
        if mode_due:
            self._send_mode(mode, now)
        if hours_due:
            hours = self._projector(self.projector.light_source_hours, "Couldn't read the light source hours")
            if hours is not FAILED:
                print(f"Light source hours: {hours}")
                self._hours_logged_at = now

    def _setup(self):
        """Put the projector in a known state: the Pi's input, 16:9, and the configured volume."""
        steps = [(lambda: self.projector.set_input(self.cfg.get('input', 'hdmi1')), "select the input"),
                 (lambda: self.projector.set_aspect(self.cfg.get('aspect', '16:9')), "set the aspect ratio")]
        if 'volume' in self.cfg:
            steps.append((lambda: self.projector.set_volume(self.cfg['volume']), "set the volume"))
        ok = True
        for action, what in steps:
            ok &= self._projector(action, f"Couldn't {what}") is not FAILED
        if ok:
            print(f"Projector set up: input {self.cfg.get('input', 'hdmi1')}, "
                  f"aspect {self.cfg.get('aspect', '16:9')}")
            self.needs_setup = False

    def _desired_mode(self, now):
        """The light source mode for right now, or None (no brightness schedule, or location unknown yet)."""
        if 'brightness' not in self.cfg:
            return None
        if self.location is None:
            # Looked up (from the IP address when not configured) only now and
            # then, since a failed lookup can block for a few seconds.
            if self._location_tried_at is not None and now - self._location_tried_at < LOCATION_RETRY_SECONDS:
                return None
            self._location_tried_at = now
            self.location = resolve_location(self.location_cfg)
            if self.location is None:
                return None
        return mode_at(local_now(self.location), self.location, self.cfg['brightness'])

    def _send_mode(self, mode, now):
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
        elif command == 'input' and rest:
            projector.set_input(rest[0])
            print(f"Selected input: {rest[0]}")
        elif command == 'aspect' and rest:
            projector.set_aspect(rest[0])
            print(f"Set aspect ratio: {rest[0]}")
        elif command == 'volume' and rest:
            projector.set_volume(int(rest[0]))
            print(f"Set volume: {rest[0]}")
        elif command == 'blank' and rest:
            projector.blank(rest[0] == 'on')
            print(f"Blank (AV mute): {rest[0]}")
        elif command == 'hours':
            print(f"Light source hours: {projector.light_source_hours()}")
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

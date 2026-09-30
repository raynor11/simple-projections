"""
ViewSonic projector RS-232 control.

Packet format, from ViewSonic's "Projector RS-232/LAN Control Protocol":

  write:    06 14 00 04 00 34 <hi> <lo> <value>  <checksum>
  read:     07 14 00 05 00 34 00 00 <hi> <lo>   <checksum>
  ack:      03 14 00 00 00 14
  reply:    05 14 00 <len> 00 <len bytes of payload> <checksum>

The checksum is the low byte of the sum of every byte after the first.
e.g. power on = 06 14 00 04 00 34 11 00 00 5D.

The opcode for the light source mode differs between models (0x11 0x0C
in the generic protocol document, 0x11 0x10 on the PX701-4K/PX7xx-4K
series), so it and the mode values are configurable. Check them against
the LS740-4K's own RS-232 table with `projector_control.py --test`.
"""

import time


# Power on and off are separate commands (not one command with a value):
# on = 34 11 00 00, off = 34 11 01 00. Status is read from 11 00.
POWER = (0x11, 0x00)
POWER_OFF = (0x11, 0x01)
POWER_STATES = {0: 'off', 1: 'on', 2: 'warming', 3: 'cooling'}
DEFAULT_LIGHT_SOURCE_OPCODE = (0x11, 0x10)
DEFAULT_LIGHT_SOURCE_MODES = {'normal': 0, 'eco': 1, 'dynamic_eco': 2, 'supereco': 3}
ACK = bytes([0x03, 0x14, 0x00, 0x00, 0x00, 0x14])


class ProjectorError(RuntimeError):
    pass


def checksum(body):
    return sum(body[1:]) & 0xFF


def write_packet(opcode, value):
    hi, lo = opcode
    body = [0x06, 0x14, 0x00, 0x04, 0x00, 0x34, hi, lo, value]
    return bytes(body + [checksum(body)])


def read_packet(opcode):
    hi, lo = opcode
    body = [0x07, 0x14, 0x00, 0x05, 0x00, 0x34, 0x00, 0x00, hi, lo]
    return bytes(body + [checksum(body)])


def parse_reply(packet):
    """Value from a read reply (05 14 00 <len> 00 <payload> <cs>); the payload's value is little-endian after 2 bytes."""
    if len(packet) < 6 or packet[0] != 0x05 or packet[1] != 0x14:
        raise ProjectorError(f"Unexpected reply: {packet.hex(' ')}")
    length = packet[3]
    if len(packet) != 5 + length + 1:
        raise ProjectorError(f"Truncated reply: {packet.hex(' ')}")
    if checksum(packet[:-1]) != packet[-1]:
        raise ProjectorError(f"Bad reply checksum: {packet.hex(' ')}")
    payload = packet[5:5 + length]
    return int.from_bytes(payload[2:], 'little')


class ViewSonicProjector:
    def __init__(self, port, baud=19200, light_source_opcode=DEFAULT_LIGHT_SOURCE_OPCODE,
                 light_source_modes=None, timeout=1.0, retries=3, serial_factory=None):
        self.port = port
        self.baud = baud
        self.light_source_opcode = tuple(light_source_opcode)
        self.light_source_modes = dict(light_source_modes or DEFAULT_LIGHT_SOURCE_MODES)
        self.timeout = timeout
        self.retries = retries
        self._serial_factory = serial_factory
        self._serial = None

    # -- transport -------------------------------------------------------------

    def _open(self):
        if self._serial is None:
            if self._serial_factory:
                self._serial = self._serial_factory()
            else:
                import serial
                self._serial = serial.Serial(self.port, self.baud, timeout=self.timeout)
        return self._serial

    def close(self):
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def _read_response(self, ser):
        header = ser.read(5)
        if len(header) < 5:
            raise ProjectorError("No response from the projector (is it plugged in, and is "
                                 "RS-232 control in standby enabled?)")
        rest = ser.read(header[3] + 1)
        return header + rest

    def transact(self, packet):
        """Send a packet, return the projector's response. Retries and reopens the port on failure."""
        last_error = None
        for attempt in range(self.retries):
            try:
                ser = self._open()
                ser.reset_input_buffer()
                ser.write(packet)
                return self._read_response(ser)
            except Exception as e:
                last_error = e
                self.close()
                time.sleep(0.3 * (attempt + 1))
        raise ProjectorError(str(last_error))

    def write(self, opcode, value):
        response = self.transact(write_packet(opcode, value))
        if response != ACK:
            raise ProjectorError(f"Projector rejected command: {response.hex(' ')}")

    def read(self, opcode):
        return parse_reply(self.transact(read_packet(opcode)))

    # -- commands --------------------------------------------------------------

    def power_on(self):
        self.write(POWER, 0x00)

    def power_off(self):
        self.write(POWER_OFF, 0x00)

    def power_state(self):
        """'off', 'on', 'warming' or 'cooling'."""
        value = self.read(POWER)
        return POWER_STATES.get(value, f'unknown({value})')

    def set_light_source(self, mode):
        if mode not in self.light_source_modes:
            raise ProjectorError(f"Unknown light source mode {mode!r}; "
                                 f"known: {', '.join(self.light_source_modes)}")
        self.write(self.light_source_opcode, self.light_source_modes[mode])

    def light_source(self):
        value = self.read(self.light_source_opcode)
        for name, v in self.light_source_modes.items():
            if v == value:
                return name
        return f'unknown({value})'


def from_config(cfg):
    opcode = cfg.get('light_source_opcode', list(DEFAULT_LIGHT_SOURCE_OPCODE))
    return ViewSonicProjector(cfg['serial'], cfg.get('baud', 19200), opcode, cfg.get('light_source_modes'))

"""
ViewSonic projector RS-232 control, using the LS740-4K command table
(user guide LSD500-4K_LS740-4K_UG_ENG, "RS-232 Protocol", pp. 57-69).

Serial: 115200 baud (default; the OSD also offers 9600), 8 data bits, no
parity, 1 stop bit, no flow control (UG p.57).

Packet format:

  write:    06 14 00 04 00 34 <hi> <lo> <value>  <checksum>
  read:     07 14 00 05 00 34 00 00 <hi> <lo>   <checksum>
  ack:      03 14 00 00 00 14
  reply:    05 14 00 <len> 00 <len bytes of payload> <checksum>

The checksum is the low byte of the sum of every byte after the first.
e.g. power on = 06 14 00 04 00 34 11 00 00 5D.
"""

import time


# Opcodes (hi, lo) from the LS740-4K command table, with UG page numbers.
# Power on and off are separate commands: on = 34 11 00 00, off = 34 11 01 00;
# status is read from 11 00 (p.58).
POWER = (0x11, 0x00)
POWER_OFF = (0x11, 0x01)
POWER_STATES = {0: 'off', 1: 'on', 2: 'warming', 3: 'cooling'}
LIGHT_SOURCE = (0x11, 0x10)                       # p.59
# Values written to set a mode...
LIGHT_SOURCE_MODES = {'normal': 0x00, 'eco': 0x01, 'dynamic_black': 0x09}
# ...and what a status read returns, which isn't the same for Normal. 0x00
# means the OSD-only "Light Source Power 50%-100%" setting is in use (p.59).
LIGHT_SOURCE_READBACK = {0x0B: 'normal', 0x01: 'eco', 0x09: 'dynamic_black', 0x00: 'custom_power'}
SOURCE_INPUT = (0x13, 0x01)                       # p.63
INPUTS = {'hdmi1': 0x03, 'hdmi2': 0x07}
ASPECT_RATIO = (0x12, 0x04)                       # p.60
ASPECTS = {'auto': 0x00, '4:3': 0x02, '16:9': 0x03, 'native': 0x09, '21:9': 0x0B}
BLANK = (0x12, 0x09)                              # AV mute, p.61
VOLUME_SET = (0x13, 0x2A)                         # p.64 (value 0-10)
LIGHT_SOURCE_HOURS = (0x15, 0x01)                 # p.68
DEFAULT_BAUD = 115200
ACK = bytes([0x03, 0x14, 0x00, 0x00, 0x00, 0x14])
REPLY_STARTS = (0x00, 0x03, 0x05)   # 00 14: the projector rejected the command
MAX_NOTICE_BYTES = 256


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


_checksum_warned = set()


def parse_reply(packet):
    """Value from a read reply (05 14 00 <len> 00 <payload> <cs>); the payload's value is little-endian after 2 bytes."""
    if len(packet) < 6 or packet[0] != 0x05 or packet[1] != 0x14:
        raise ProjectorError(f"Unexpected reply: {packet.hex(' ')}")
    length = packet[3]
    if len(packet) != 5 + length + 1:
        raise ProjectorError(f"Truncated reply: {packet.hex(' ')}")
    if checksum(packet[:-1]) != packet[-1]:
        # Not fatal: the LS740-4K manual's own example reply for Normal light
        # source mode (05 14 00 03 00 00 00 0B 27, UG p.59) doesn't match the
        # checksum rule either, so the firmware may do the same. A short
        # serial link rarely corrupts bytes; note it once and use the value.
        if packet.hex() not in _checksum_warned:
            _checksum_warned.add(packet.hex())
            print(f"Projector reply checksum mismatch (using it anyway): {packet.hex(' ')}")
    payload = packet[5:5 + length]
    return int.from_bytes(payload[2:], 'little')


class ViewSonicProjector:
    def __init__(self, port, baud=DEFAULT_BAUD, timeout=1.0, retries=3, serial_factory=None):
        self.port = port
        self.baud = baud
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
        # The projector also sends unsolicited ASCII notices (e.g. "\nINFO1\r" while
        # it warms up), which can arrive before a reply: skip to the reply's
        # start (03 14 for an ACK, 05 14 for a read, 00 14 for a rejection) and
        # discard the rest.
        skipped = bytearray()
        prev = None
        while True:
            b = ser.read(1)
            if not b:
                raise ProjectorError("No response from the projector (is it plugged in, and is "
                                     "RS-232 control in standby enabled?)"
                                     + (f"; got only {bytes(skipped)!r}" if skipped else ""))
            if prev is not None and prev in REPLY_STARTS and b[0] == 0x14:
                break
            if prev is not None:
                skipped.append(prev)
            prev = b[0]
            if len(skipped) > MAX_NOTICE_BYTES:
                raise ProjectorError(f"Unexpected data from the projector: {bytes(skipped)!r}")
        header = bytes([prev, 0x14]) + ser.read(3)
        if len(header) < 5:
            raise ProjectorError(f"Truncated reply: {header.hex(' ')}")
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
        if mode not in LIGHT_SOURCE_MODES:
            raise ProjectorError(f"Unknown light source mode {mode!r}; "
                                 f"known: {', '.join(LIGHT_SOURCE_MODES)}")
        self.write(LIGHT_SOURCE, LIGHT_SOURCE_MODES[mode])

    def light_source(self):
        value = self.read(LIGHT_SOURCE)
        return LIGHT_SOURCE_READBACK.get(value, f'unknown({value})')

    def set_input(self, name):
        if name not in INPUTS:
            raise ProjectorError(f"Unknown input {name!r}; known: {', '.join(INPUTS)}")
        self.write(SOURCE_INPUT, INPUTS[name])

    def set_aspect(self, name):
        if name not in ASPECTS:
            raise ProjectorError(f"Unknown aspect ratio {name!r}; known: {', '.join(ASPECTS)}")
        self.write(ASPECT_RATIO, ASPECTS[name])

    def blank(self, on):
        """AV mute: hide the image (and sound) without turning the light source off."""
        self.write(BLANK, 0x01 if on else 0x00)

    def set_volume(self, level):
        if not 0 <= int(level) <= 10:
            raise ProjectorError("Volume must be 0-10")
        self.write(VOLUME_SET, int(level))

    def light_source_hours(self):
        return self.read(LIGHT_SOURCE_HOURS)


def from_config(cfg):
    return ViewSonicProjector(cfg['serial'], cfg.get('baud', DEFAULT_BAUD))

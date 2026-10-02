import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.projector.viewsonic import (
    ACK, POWER, POWER_OFF, ProjectorError, ViewSonicProjector, parse_reply, read_packet, write_packet,
)


def hexbytes(s):
    return bytes.fromhex(s)


# Byte sequences quoted from ViewSonic's projector RS-232 protocol documents.
def test_packets_match_documented_commands():
    assert write_packet(POWER, 0x00) == hexbytes("06 14 00 04 00 34 11 00 00 5D")   # power on
    assert write_packet(POWER_OFF, 0x00) == hexbytes("06 14 00 04 00 34 11 01 00 5E")  # power off
    assert read_packet(POWER) == hexbytes("07 14 00 05 00 34 00 00 11 00 5E")       # power status
    assert write_packet((0x11, 0x10), 0x03) == hexbytes("06 14 00 04 00 34 11 10 03 70")  # PX7xx SuperEco
    assert write_packet((0x11, 0x0C), 0x01) == hexbytes("06 14 00 04 00 34 11 0C 01 6A")  # generic Eco
    assert read_packet((0x11, 0x10)) == hexbytes("07 14 00 05 00 34 00 00 11 10 6E")


def test_parse_reply():
    assert parse_reply(hexbytes("05 14 00 03 00 00 00 01 18")) == 1        # power on
    assert parse_reply(hexbytes("05 14 00 03 00 00 00 03 1A")) == 3        # cooling
    assert parse_reply(hexbytes("05 14 00 04 00 00 00 32 00 4A")) == 50    # 2-byte value
    # The manual's Normal-mode reply has a checksum that doesn't follow its own
    # rule (0x27, should be 0x22); replies like it are accepted with a warning.
    assert parse_reply(hexbytes("05 14 00 03 00 00 00 0B 27")) == 0x0B
    with pytest.raises(ProjectorError):
        parse_reply(hexbytes("05 14 00 05 00 00 00 01"))                    # truncated


class FakeSerial:
    def __init__(self, responses):
        self.responses = list(responses)
        self.written = []
        self.buffer = b''

    def reset_input_buffer(self):
        self.buffer = b''

    def write(self, data):
        self.written.append(bytes(data))
        self.buffer = self.responses.pop(0) if self.responses else b''

    def read(self, n):
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def close(self):
        pass


def projector(responses):
    fake = FakeSerial(responses)
    return ViewSonicProjector('fake', serial_factory=lambda: fake, retries=1), fake


def test_power_state_and_commands():
    p, fake = projector([hexbytes("05 14 00 03 00 00 00 02 19"), ACK])
    assert p.power_state() == 'warming'
    p.power_off()
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 11 01 00 5E")


def test_light_source_modes():
    # Writes and read-backs from the LS740-4K command table (UG p.59).
    p, fake = projector([ACK, hexbytes("05 14 00 03 00 00 00 0B 27"), ACK, ACK,
                         hexbytes("05 14 00 03 00 00 00 00 17")])
    p.set_light_source('normal')
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 11 10 00 6D")
    assert p.light_source() == 'normal'                       # reads back as 0x0B, not 0x00
    assert fake.written[-1] == hexbytes("07 14 00 05 00 34 00 00 11 10 6E")
    p.set_light_source('eco')
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 11 10 01 6E")
    p.set_light_source('dynamic_black')
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 11 10 09 76")
    assert p.light_source() == 'custom_power'                 # OSD Light Source Power 50-100%
    with pytest.raises(ProjectorError):
        p.set_light_source('supereco')                        # not on this model


def test_input_aspect_volume_blank_hours():
    p, fake = projector([ACK, ACK, ACK, ACK, ACK, hexbytes("05 14 00 06 00 00 00 B8 0B 00 00 DD")])
    p.set_input('hdmi1')
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 13 01 03 63")      # UG p.63
    p.set_aspect('16:9')
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 12 04 03 65")      # UG p.60
    p.set_volume(5)
    assert fake.written[-1][:9] == hexbytes("06 14 00 04 00 34 13 2A 05")     # UG p.64 (NN = volume)
    p.blank(True)
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 12 09 01 68")      # UG p.61
    p.blank(False)
    assert fake.written[-1] == hexbytes("06 14 00 04 00 34 12 09 00 67")
    assert p.light_source_hours() == 3000                                     # UG p.68 example reply
    assert fake.written[-1] == hexbytes("07 14 00 05 00 34 00 00 15 01 63")


def test_bad_arguments_rejected():
    p, _ = projector([])
    for call in (lambda: p.set_input('vga'), lambda: p.set_aspect('5:4'), lambda: p.set_volume(11)):
        with pytest.raises(ProjectorError):
            call()


def test_default_baud_is_115200():
    from src.projector.viewsonic import from_config
    assert from_config({'serial': 'x'}).baud == 115200


def test_no_response_raises():
    p, _ = projector([])
    with pytest.raises(ProjectorError, match="No response"):
        p.power_state()


def test_rejected_command_raises():
    p, _ = projector([hexbytes("00 14 00 00 00 14")])
    with pytest.raises(ProjectorError, match="rejected"):
        p.power_on()

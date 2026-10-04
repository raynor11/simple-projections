import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.airplay import count_established


# Port 7000 = 0x1B58. States: 01 ESTABLISHED, 0A LISTEN, 06 TIME_WAIT.
PROC_NET_TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000:1B58 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1 0000 100 0 0 10 0
   1: 0A00000F:1B58 1400000A:C35A 01 00000000:00000000 00:00000000 00000000  1000        0 2 1 0000 20 4 30 10 -1
   2: 0A00000F:0016 1400000A:D001 01 00000000:00000000 00:00000000 00000000     0        0 3 1 0000 20 4 30 10 -1
   3: 0A00000F:1B58 1400000A:C35B 06 00000000:00000000 00:00000000 00000000     0        0 0 3 0000 0 0 0 0 0
"""

PROC_NET_TCP6 = """\
  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0000000000000000FFFF00000F00000A:1B58 0000000000000000FFFF00001400000A:C400 01 00000000:00000000 00:00000000 00000000  1000        0 5 1 0000 20 4 30 10 -1
"""


def test_counts_only_established_on_port():
    assert count_established(PROC_NET_TCP, 7000) == 1
    assert count_established(PROC_NET_TCP, 22) == 1
    assert count_established(PROC_NET_TCP, 8009) == 0


def test_tcp6_format():
    assert count_established(PROC_NET_TCP6, 7000) == 1


def test_header_only():
    assert count_established(PROC_NET_TCP.splitlines()[0], 7000) == 0


def fake_querycap(monkeypatch, device_caps=None, open_error=False):
    import struct
    from src.sources import airplay

    def fake_open(path, flags):
        if open_error:
            raise FileNotFoundError(path)
        return 99

    def fake_ioctl(fd, request, arg):
        if device_caps is None:
            raise OSError("not a V4L2 device")
        buf = bytearray(104)
        struct.pack_into('<I', buf, 88, device_caps)
        return bytes(buf)

    monkeypatch.setattr(airplay.os, 'open', fake_open)
    monkeypatch.setattr(airplay.os, 'close', lambda fd: None)
    monkeypatch.setattr(airplay.fcntl, 'ioctl', fake_ioctl)


def test_loopback_without_writer_is_not_ready(monkeypatch):
    from src.sources.airplay import has_video_capture
    fake_querycap(monkeypatch, device_caps=0x05200002)   # output only, as UxPlay leaves it idle
    assert not has_video_capture('/dev/video10')


def test_loopback_with_writer_is_ready(monkeypatch):
    from src.sources.airplay import has_video_capture
    fake_querycap(monkeypatch, device_caps=0x05200001)
    assert has_video_capture('/dev/video10')


def test_missing_device_is_not_ready_and_unknown_is_tried(monkeypatch):
    from src.sources.airplay import has_video_capture
    fake_querycap(monkeypatch, open_error=True)
    assert not has_video_capture('/dev/video10')
    fake_querycap(monkeypatch, device_caps=None)
    assert has_video_capture('/dev/video10')

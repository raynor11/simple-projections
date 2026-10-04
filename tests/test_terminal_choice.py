import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


class FakeStdin:
    def __init__(self, line, tty=True):
        self.line, self.tty = line, tty

    def isatty(self):
        return self.tty

    def readline(self):
        return self.line


def answer(monkeypatch, line, ready=True, tty=True):
    monkeypatch.setattr(main.sys, 'stdin', FakeStdin(line, tty))
    monkeypatch.setattr(main.select, 'select', lambda r, w, x, t: (r if ready else [], [], []))
    return main.terminal_choice()


def test_enter_or_s_saves(monkeypatch):
    assert answer(monkeypatch, '\n') == 'save'
    assert answer(monkeypatch, 's\n') == 'save'
    assert answer(monkeypatch, 'YES\n') == 'save'


def test_q_discards(monkeypatch):
    assert answer(monkeypatch, 'q\n') == 'discard'
    assert answer(monkeypatch, 'n\n') == 'discard'


def test_nothing_typed_or_no_terminal(monkeypatch):
    assert answer(monkeypatch, '', ready=False) is None
    assert answer(monkeypatch, 's\n', tty=False) is None
    assert answer(monkeypatch, 'maybe\n') is None

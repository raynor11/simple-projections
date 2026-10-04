"""
Drive the calibration UI from the terminal it was started in (e.g. over SSH),
so no keyboard is needed on the Pi. Keystrokes are decoded and posted as
pygame KEYDOWN events, so they go through the UI's normal key handling.

Terminals often don't pass modifier+arrow combinations through (macOS uses
Ctrl+arrows for Spaces; Terminal scrolls on Shift+Up/Down), so two toggles
stand in for the modifiers: M switches the arrows between moving and
resizing, B between small and big steps.
"""
import os
import select
import sys

import pygame


ARROWS = {'A': pygame.K_UP, 'B': pygame.K_DOWN, 'C': pygame.K_RIGHT, 'D': pygame.K_LEFT}
# xterm modifier parameter (ESC [ 1 ; <n> A): n - 1 is a bitmask of shift=1, alt=2, ctrl=4.
XTERM_MODS = ((1, pygame.KMOD_SHIFT), (2, pygame.KMOD_ALT), (4, pygame.KMOD_CTRL))
CONTROL_CHARS = {
    '\t': (pygame.K_TAB, 0),
    '\x1a': (pygame.K_z, pygame.KMOD_CTRL),    # Ctrl+Z: undo
    '\x19': (pygame.K_y, pygame.KMOD_CTRL),    # Ctrl+Y: redo
    '\x03': (pygame.K_q, 0),                   # Ctrl+C: quit (asks again if unsaved)
}
TOGGLE_RESIZE = 'toggle-resize'
TOGGLE_BIG = 'toggle-big'
TOGGLES = {'m': TOGGLE_RESIZE, 'b': TOGGLE_BIG}

HELP = [
    "Keys typed in this terminal also work (no keyboard needed on the Pi):",
    "  Tab / Shift+Tab select a frame, arrows move it, M switches the arrows to resizing,",
    "  B switches to big steps, Ctrl+Z / Ctrl+Y undo / redo, S save, Q quit;",
    "  the other letter keys work as listed above.",
]


def _xterm_mod(param):
    try:
        bits = int(param) - 1
    except ValueError:
        return 0
    return sum(flag for bit, flag in XTERM_MODS if bits & bit)


def parse(text):
    """
    Decode terminal input into a list of (key, mod) tuples and toggle names.
    Returns (decoded, leftover): leftover is an incomplete escape sequence to
    prepend to the next read.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\x1b':
            rest = text[i + 1:]
            if not rest:
                out.append((pygame.K_ESCAPE, 0))     # a lone Esc
                i += 1
                continue
            alt = 0
            if rest[0] == '\x1b':                    # ESC ESC [ A: Option+arrow in macOS Terminal
                alt, rest, i = pygame.KMOD_ALT, rest[1:], i + 1
                if not rest:
                    return out, text[i - 1:]
            if rest[0] in '[O':
                # CSI/SS3: parameters, then a final letter (or ~).
                j = 1
                while j < len(rest) and not (rest[j].isalpha() or rest[j] == '~'):
                    j += 1
                if j == len(rest):
                    return out, text[i:]            # incomplete; wait for more bytes
                params, final = rest[1:j], rest[j]
                if final in ARROWS:
                    mod = _xterm_mod(params.split(';')[-1]) if ';' in params else 0
                    out.append((ARROWS[final], mod | alt))
                elif final == 'Z':
                    out.append((pygame.K_TAB, pygame.KMOD_SHIFT))
                i += 1 + j + 1
                continue
            out.append((pygame.K_ESCAPE, 0))        # Esc followed by something else
            i += 1
            continue
        if ch in CONTROL_CHARS:
            out.append(CONTROL_CHARS[ch])
        elif ch.lower() in TOGGLES and ch.isalpha():
            out.append(TOGGLES[ch.lower()])
        elif ch.isalnum() and ch.isascii() or ch in '+=-':
            # pygame's key codes for letters, digits and + = - are their ASCII codes.
            mod = pygame.KMOD_SHIFT if ch.isupper() else 0
            out.append((ord(ch.lower()), mod))
        i += 1
    return out, ''


class TerminalKeys:
    """
    While active, puts the terminal into character-at-a-time mode with signals
    off (so Ctrl+Z means undo, not suspend) and restores it on exit.
    """

    def __init__(self, ui, stream=None):
        self.ui = ui
        self.stream = stream if stream is not None else sys.stdin
        self.resize = False
        self.big = False
        self._fd = None
        self._saved = None
        self._pending = ''

    def __enter__(self):
        try:
            if not self.stream.isatty():
                return self
            import termios
            fd = self.stream.fileno()
            self._saved = termios.tcgetattr(fd)
            attrs = termios.tcgetattr(fd)
            attrs[0] &= ~(termios.IXON | termios.ICRNL)                       # iflag
            attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)   # lflag
            attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            self._fd = fd
            for line in HELP:
                print(line)
        except (OSError, ValueError, ImportError):
            self._fd = None
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSANOW, self._saved)
            self._fd = None

    def _read(self):
        chunks = []
        while select.select([self._fd], [], [], 0)[0]:
            data = os.read(self._fd, 1024)
            if not data:
                break
            chunks.append(data)
        return b''.join(chunks).decode('utf-8', 'ignore')

    def pump(self):
        """Read whatever was typed and post it to pygame as key presses."""
        if self._fd is None:
            return
        text = self._pending + self._read()
        if not text:
            return
        decoded, self._pending = parse(text)
        for item in decoded:
            if item == TOGGLE_RESIZE:
                self.resize = not self.resize
                self.ui._say("Arrows now resize the frame (M to move)" if self.resize
                             else "Arrows now move the frame (M to resize)")
                self.ui.dirty = True
                continue
            if item == TOGGLE_BIG:
                self.big = not self.big
                self.ui._say("Big steps (B for small)" if self.big else "Small steps (B for big)")
                self.ui.dirty = True
                continue
            key, mod = item
            if key in ARROWS.values():
                if self.resize:
                    mod |= pygame.KMOD_CTRL
                if self.big:
                    mod |= pygame.KMOD_SHIFT
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                                                 unicode='', scancode=0))

"""How long the user has actually been away from the keyboard and mouse.

Windows already tracks this for the whole desktop, so asking it beats running
input listeners of our own: GetLastInputInfo sees every keystroke and mouse
movement, including ones inside a fullscreen game where a hook may not fire,
and it needs no third-party library.

The catch is that it counts *synthetic* input too, so a keepalive tap looks
exactly like the user returning to their desk - the app would tap once, decide
someone was back, and never tap again. Every keystroke this app sends is
therefore timestamped as it goes out (`note_injection`) and filtered back out
of the reading, which keeps the idle clock running across our own taps.
"""

import ctypes
import threading
from collections import deque
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount.argtypes = ()
kernel32.GetTickCount.restype = wintypes.DWORD

# Tick counts are 32 bit and wrap roughly every 49 days, so every comparison
# below goes through 32 bit unsigned arithmetic rather than plain subtraction.
_MASK = 0xFFFFFFFF

_INJECTIONS = deque(maxlen=32)
_INJECTION_LOCK = threading.Lock()

#: Slack around a recorded send, covering GetTickCount's ~15ms resolution and
#: the time input takes to reach the desktop's last-input bookkeeping. Too
#: small and our own taps read as the user returning, which defeats the whole
#: idea; too large and real input landing within a blink of a tap is mistaken
#: for ours. Genuine input is not synchronised to our taps, so the cost of the
#: latter is noticing the user a fraction of a second late.
_INJECTION_SLACK_MS = 150


def tick() -> int:
    return kernel32.GetTickCount()


def note_injection(started: int, finished: int) -> None:
    """Record that this app sent input between two tick counts."""
    with _INJECTION_LOCK:
        _INJECTIONS.append((started, finished))


def _ticks_between(earlier: int, later: int) -> int:
    return (later - earlier) & _MASK


def _tick_within(start: int, value: int, end: int) -> bool:
    return _ticks_between(start, value) <= _ticks_between(start, end)


def input_was_ours(value: int) -> bool:
    """Did this app synthesise the input that landed at this tick?"""
    with _INJECTION_LOCK:
        recent = list(_INJECTIONS)
    return any(_tick_within(start - _INJECTION_SLACK_MS, value, end + _INJECTION_SLACK_MS)
               for start, end in recent)


def last_input_tick() -> int:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return tick()
    return info.dwTime


def seconds_since_tick(value: int) -> float:
    return _ticks_between(value, tick()) / 1000.0


class IdleWatcher:
    """Seconds since the *user* last touched the keyboard or mouse.

    Poll it regularly - it can only notice genuine input while it is looking,
    and each tap this app sends overwrites the system's last-input timestamp.
    """

    def __init__(self):
        self._last_user_tick = last_input_tick()

    def seconds(self) -> float:
        reported = last_input_tick()
        if reported != self._last_user_tick and not input_was_ours(reported):
            self._last_user_tick = reported
        return seconds_since_tick(self._last_user_tick)

    def reset(self) -> None:
        """Treat the user as having just been active."""
        self._last_user_tick = tick()

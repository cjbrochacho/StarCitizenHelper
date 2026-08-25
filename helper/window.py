"""Finding the game's window and briefly bringing it to the front.

Used by the keepalive's snap-focus mode: a game that is not the active window
never sees an injected keystroke, because injected input goes to whatever has
focus. Snapping the game forward for the tap and handing focus straight back
is the only way to reach it from another app without elevation.
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (_ENUM_PROC, wintypes.LPARAM)

GA_ROOT = 2
ASFW_ANY = 0xFFFFFFFF

#: SetForegroundWindow is asynchronous - the switch lands a moment after the
#: call returns, and a fullscreen game takes longer than a plain window. Give
#: it this long to settle before deciding it failed.
_SETTLE_SECONDS = 0.35


def top_level(hwnd):
    """The owning top level window, which is what GetForegroundWindow reports."""
    if not hwnd:
        return 0
    return user32.GetAncestor(hwnd, GA_ROOT) or hwnd


def foreground_hwnd():
    return user32.GetForegroundWindow()


def window_for_pid(pid):
    """The first visible top level window owned by a process, or 0."""
    if not pid:
        return 0
    found = []

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
            return False  # stop enumerating
        return True

    user32.EnumWindows(_ENUM_PROC(visit), 0)
    return found[0] if found else 0


def is_foreground(hwnd):
    if not hwnd:
        return False
    return top_level(hwnd) == top_level(user32.GetForegroundWindow())


def force_foreground(hwnd):
    """Bring a window to the front, working around the focus-stealing rules.

    Windows refuses SetForegroundWindow from a process that does not own the
    current foreground window, so we briefly attach to that window's input
    thread - the standard workaround - and detach again straight after.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    current = user32.GetForegroundWindow()
    if top_level(current) == top_level(hwnd):
        return True

    user32.AllowSetForegroundWindow(ASFW_ANY)
    this_thread = kernel32.GetCurrentThreadId()
    other_thread = user32.GetWindowThreadProcessId(current, None) if current else 0
    attached = bool(other_thread and other_thread != this_thread
                    and user32.AttachThreadInput(this_thread, other_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, other_thread, False)

    deadline = time.monotonic() + _SETTLE_SECONDS
    while time.monotonic() < deadline:
        if is_foreground(hwnd):
            return True
        time.sleep(0.02)
    return is_foreground(hwnd)

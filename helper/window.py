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


# ── Taskbar identity and icons ────────────────────────────────────────────
#
# Two things decide what the taskbar button shows, and Tk sets neither.
# Windows groups buttons by AppUserModelID: without an explicit one, the app
# inherits the interpreter's identity and its icon. And the button is drawn
# from the window's *large* icon, where Tk's iconbitmap only sets the small
# one - which is why the title bar can look right while the taskbar does not.

shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50

user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.LoadImageW.restype = wintypes.HANDLE
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)


def set_app_id(app_id):
    """Give the process its own taskbar identity, not the interpreter's.

    Must be called before the first window exists.
    """
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        return False


def apply_window_icon(hwnd, ico_path):
    """Attach both icon sizes to a window so the taskbar picks them up."""
    hwnd = top_level(hwnd)
    if not hwnd:
        return False
    applied = False
    for which, cx, cy in ((ICON_SMALL, SM_CXSMICON, SM_CYSMICON),
                          (ICON_BIG, SM_CXICON, SM_CYICON)):
        handle = user32.LoadImageW(None, str(ico_path), IMAGE_ICON,
                                   user32.GetSystemMetrics(cx),
                                   user32.GetSystemMetrics(cy), LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, which, handle)
            applied = True
    return applied

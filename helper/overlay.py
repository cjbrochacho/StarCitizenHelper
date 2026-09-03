"""A floating window that mirrors the header HUD over whatever is beneath it.

Not an in-game overlay in the RTSS/Discord/Steam sense - those inject into
the game process and register a Vulkan implicit layer, which is exactly what
helper.fps's out-of-process PresentMon design exists to avoid (a layer that
fails to initialise can stop the game from starting at all, per its own
docstring). This is an ordinary second top-level window that Windows' own
compositor happens to draw on top of the game - nothing is loaded into Star
Citizen, and a crash or close here can never touch it.

Locked, it is click-through and cannot be dragged - Windows routes clicks to
whatever is behind a WS_EX_TRANSPARENT window before the app ever sees them,
so there is no way to also make it draggable while locked. Unlocking removes
that style for exactly as long as it takes to drag it somewhere, then it goes
back on.
"""

from __future__ import annotations

import tkinter as tk

from . import theme
from .hud import HudGraph
from .window import set_overlay_styles, top_level

#: Tk's -transparentcolor keys out exactly one colour, not real per-pixel
#: alpha - picked to be a shade the graph itself never draws.
KEY_COLOR = "#010203"
KEY_COLOR_RGB = (0x01, 0x02, 0x03)

#: Distance from the screen edge, or from the game window's corner, for the
#: default position before the user has ever dragged it anywhere.
MARGIN = 20


class OverlayWindow(tk.Toplevel):
    """A borderless, always-on-top copy of the header HUD, plus its server line."""

    def __init__(self, parent: tk.Misc, position=None, anchor_rect=None,
                locked: bool = True, opacity_percent: int = 90,
                on_dragged=None) -> None:
        """position, if given, is a (x, y) screen point from a previous drag
        - takes priority over anchor_rect. anchor_rect, if given, is the game
        window's (left, top, right, bottom) in real screen pixels; without
        either, top-right of the primary monitor is the best guess left.
        on_dragged(x, y), if given, is called once when a drag finishes, to
        persist the new position.
        """
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=KEY_COLOR)
        self.attributes("-transparentcolor", KEY_COLOR)

        self.hud = HudGraph(self)
        self.hud.configure(bg=KEY_COLOR)
        self.hud.pack()
        self.server_label = tk.Label(self, text="", bg=KEY_COLOR, fg=theme.MUTED,
                                     font=("Consolas", 8), anchor="e", justify="right")
        self.server_label.pack(fill="x", pady=(2, 0))

        self._on_dragged = on_dragged
        self._locked = None
        self._alpha = round(max(20, min(100, opacity_percent)) * 255 / 100)
        self._drag_origin = None
        # Not resolved yet: GetAncestor(GA_ROOT) on winfo_id() answers itself
        # unchanged until the window is actually mapped and Windows finishes
        # reparenting it into its real top-level frame - resolving this now
        # would freeze in the wrong (pre-reparenting) handle. Filled in by
        # _apply_initial_lock, at the same deferred point as everything else
        # that needs the window to be fully realised first.
        self._hwnd = None

        self.update_idletasks()               # winfo_reqwidth needs real geometry
        if position:
            x, y = position
        elif anchor_rect:
            _, top, right, _ = anchor_rect
            x, y = right - self.winfo_reqwidth() - MARGIN, top + MARGIN
        else:
            x, y = self.winfo_screenwidth() - self.winfo_reqwidth() - MARGIN, MARGIN
        self.geometry("+%d+%d" % (x, y))

        # Deferred: -transparentcolor's real Windows-side setup happens when
        # this window is actually mapped, which is after __init__ returns -
        # calling set_locked() here, synchronously, races Tk's own default
        # SetLayeredWindowAttributes call and reliably loses to it, silently
        # putting the opacity back to fully opaque. A short delay - rather
        # than after_idle, whose ordering against Tk's own internal mapping
        # idle callback isn't guaranteed - reliably lands after mapping, so
        # this one applies last and wins.
        self._pending_lock = locked
        self.after(50, self._apply_initial_lock)

    def _apply_initial_lock(self) -> None:
        self._hwnd = top_level(self.winfo_id())
        self.set_locked(self._pending_lock)

    # -- public -------------------------------------------------------------

    def update(self, fps_stats, net_stats) -> None:
        self.hud.update(fps_stats, net_stats)
        if net_stats.server:
            region = ("  •  " + net_stats.region) if net_stats.region not in ("", "unknown") else ""
            shard = ("  •  " + net_stats.shard) if net_stats.shard else ""
            self.server_label.config(text=net_stats.server + shard + region)
        else:
            self.server_label.config(text="server unknown - not in a match")

    def set_locked(self, locked: bool) -> None:
        if locked == self._locked:
            return
        self._locked = locked
        set_overlay_styles(self._hwnd, locked, KEY_COLOR_RGB, self._alpha)
        if locked:
            self.unbind("<ButtonPress-1>")
            self.unbind("<B1-Motion>")
            self.unbind("<ButtonRelease-1>")
        else:
            self.bind("<ButtonPress-1>", self._drag_start)
            self.bind("<B1-Motion>", self._drag_move)
            self.bind("<ButtonRelease-1>", self._drag_end)

    def set_opacity(self, percent: int) -> None:
        self._alpha = round(max(20, min(100, percent)) * 255 / 100)
        set_overlay_styles(self._hwnd, bool(self._locked), KEY_COLOR_RGB, self._alpha)

    # -- dragging, only reachable while unlocked -----------------------------

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event) -> None:
        if self._drag_origin is None:
            return
        offset_x, offset_y = self._drag_origin
        self.geometry("+%d+%d" % (event.x_root - offset_x, event.y_root - offset_y))

    def _drag_end(self, _event) -> None:
        self._drag_origin = None
        if self._on_dragged:
            self._on_dragged(self.winfo_x(), self.winfo_y())

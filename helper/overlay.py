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
#: default position before the user has ever dragged it anywhere. A 96dpi
#: measurement like the rest of the layout: the coordinates it is applied to
#: are real screen pixels, so it has to be scaled to the display or the gap
#: comes out proportionally smaller the higher the DPI - 20px against a 5760
#: wide screen rather than the 40 it describes.
#:
#: One per axis, because the resting place was set by eye rather than derived:
#: from an even 20/20 it wanted to sit a little lower and a little closer to
#: the right edge, which pulls the two apart. Still in design units, so the
#: nudge stays in proportion on a display that is not the one it was judged on.
MARGIN_X = 15
MARGIN_Y = 25


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

        # Both packed with fill="x" so they always end up the same final
        # width as each other - HudGraph's own <Configure> handler reflows
        # its layout to match, which is exactly the mechanism the header
        # already relies on (there, an explicit configure(width=460) plus
        # fill="x" against a wider container). Without this, the canvas
        # (which asks for nothing beyond its default) can end up narrower
        # than the server line below it, if that line's own text runs long -
        # the visible symptom being the readout not lining up with the graph
        # under it, because they were never actually the same width.
        self.hud = HudGraph(self)
        self.hud.configure(bg=KEY_COLOR)
        self.hud.pack(fill="x")
        # Scaled for the same reason everything else here is: the font below
        # follows the display's DPI, so a gap written in 96dpi units has to
        # follow it too or the two drift apart.
        self._scale = self.winfo_fpixels("1i") / 96.0
        self.server_label = tk.Label(self, text="", bg=KEY_COLOR, fg=theme.MUTED,
                                     font=("Consolas", 8), anchor="e", justify="right")
        self.server_label.pack(fill="x", pady=(round(2 * self._scale), 0))

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
        margin_x = round(MARGIN_X * self._scale)
        margin_y = round(MARGIN_Y * self._scale)
        if position:
            x, y = position
        elif anchor_rect:
            _, top, right, _ = anchor_rect
            x, y = right - self.winfo_reqwidth() - margin_x, top + margin_y
        else:
            x, y = self.winfo_screenwidth() - self.winfo_reqwidth() - margin_x, margin_y
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
        # Bound on the content widgets, not just self: Tk delivers a click to
        # whichever widget is directly under the pointer, and the canvas and
        # label between them cover this window's entire visible area - a
        # binding on self alone would only ever fire for the sliver (if any)
        # neither one is drawn over, which is why dragging from "anywhere on
        # the overlay" didn't actually work anywhere in practice.
        targets = (self, self.hud, self.server_label)
        if locked:
            for widget in targets:
                widget.unbind("<ButtonPress-1>")
                widget.unbind("<B1-Motion>")
                widget.unbind("<ButtonRelease-1>")
        else:
            for widget in targets:
                widget.bind("<ButtonPress-1>", self._drag_start)
                widget.bind("<B1-Motion>", self._drag_move)
                widget.bind("<ButtonRelease-1>", self._drag_end)

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

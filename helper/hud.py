"""The header HUD: one minute of FPS and latency on a single graph.

Both series share the canvas but scale independently - frame rate against its
own ceiling, latency against its own - because they have nothing to do with
each other numerically. The readout on the right names each with its colour.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import fps as fps_module
from .fps import STATUS_NO_RTSS, STATUS_OK, WINDOW_SECONDS, Stats
from .net import STATUS_NO_TARGET, NetStats
from .net import STATUS_OK as NET_OK
from .theme import ACCENT, BG, FPS_LOW, GRID, LAT, MUTED, WARN

MIN_PLOT_WIDTH = 220
READOUT_WIDTH = 122
HEIGHT = 76
PADDING = 6

_FPS_STEPS = (30, 60, 90, 120, 144, 165, 240, 360)
_MS_STEPS = (20, 40, 60, 100, 150, 200, 300, 500)


class HudGraph(tk.Canvas):
    """FPS and latency sparklines with a compact numeric readout.

    Canvas items are created once and then moved, never rebuilt - at ten
    redraws a second, recreating them would churn hundreds of objects per
    second for nothing.
    """

    def __init__(self, parent: tk.Misc, on_start_rtss: Callable[[], None] | None = None) -> None:
        super().__init__(parent, width=MIN_PLOT_WIDTH + READOUT_WIDTH, height=HEIGHT,
                         bg=BG, highlightthickness=0, bd=0)
        self._on_start_rtss = on_start_rtss
        self._fps_top = 120.0
        self._ms_top = 60.0
        self._width = MIN_PLOT_WIDTH + READOUT_WIDTH
        self._rtss_hint = False
        self.bind("<Button-1>", self._clicked)
        self.bind("<Configure>", self._resized)

        # plot: two sparklines plus a faint FPS 1%-low reference
        self._fps_low = self.create_line(0, 0, 0, 0, fill=FPS_LOW, dash=(1, 5), width=1)
        self._fps_line = self.create_line(0, 0, 0, 0, fill=ACCENT, width=1)
        self._ping_line = self.create_line(0, 0, 0, 0, fill=LAT, width=1)

        # readout: two headline numbers, each with a tiny sub-line
        self._fps_tag = self.create_text(0, 0, text="FPS", fill=MUTED, anchor="w",
                                         font=("Segoe UI", 7))
        self._fps_val = self.create_text(0, 0, text="--", fill=ACCENT, anchor="e",
                                         font=("Consolas", 14))
        self._fps_sub = self.create_text(0, 0, text="", fill=MUTED, anchor="e",
                                         font=("Consolas", 8))
        self._ping_tag = self.create_text(0, 0, text="PING", fill=MUTED, anchor="w",
                                          font=("Segoe UI", 7))
        self._ping_val = self.create_text(0, 0, text="--", fill=LAT, anchor="e",
                                          font=("Consolas", 14))
        self._ping_sub = self.create_text(0, 0, text="", fill=MUTED, anchor="e",
                                          font=("Consolas", 8))

        self._message = self.create_text(0, HEIGHT // 2, text="", fill=MUTED, anchor="c",
                                         font=("Segoe UI", 8))
        self._layout()

    # -- geometry ---------------------------------------------------------

    @property
    def _plot_width(self) -> int:
        return max(MIN_PLOT_WIDTH, self._width - READOUT_WIDTH)

    def _resized(self, event: tk.Event) -> None:
        self._width = event.width
        self._layout()

    def _layout(self) -> None:
        left = self._plot_width + PADDING
        right = self._width - PADDING
        self.coords(self._fps_tag, left, 11)
        self.coords(self._fps_val, right, 15)
        self.coords(self._fps_sub, right, 29)
        self.coords(self._ping_tag, left, 47)
        self.coords(self._ping_val, right, 51)
        self.coords(self._ping_sub, right, 65)
        self.coords(self._message, self._plot_width // 2, HEIGHT // 2)

    # -- interaction ------------------------------------------------------

    def _clicked(self, _event: tk.Event) -> None:
        if self._on_start_rtss is not None and self._rtss_hint:
            self._on_start_rtss()

    # -- public -----------------------------------------------------------

    def update(self, fps_stats: Stats, net_stats: NetStats) -> None:
        self._draw_fps(fps_stats)
        self._draw_ping(net_stats)
        self._draw_message(fps_stats, net_stats)

    # -- fps --------------------------------------------------------------

    def _draw_fps(self, stats: Stats) -> None:
        if stats.status == STATUS_OK and stats.history:
            self._fps_top = _ease(self._fps_top, stats.history, _FPS_STEPS, 30.0)
            self._plot(self._fps_line, stats.history, self._fps_top)
            low_y = self._value_y(stats.low_1, self._fps_top) if stats.low_1 > 0 else None
            if low_y is not None:
                self.coords(self._fps_low, PADDING, low_y, self._plot_width - PADDING, low_y)
                self.itemconfig(self._fps_low, state="normal")
            else:
                self.itemconfig(self._fps_low, state="hidden")
            self.itemconfig(self._fps_val, text=f"{stats.fps:.2f}")
            self.itemconfig(self._fps_sub, text=f"{stats.average:.2f} avg  {stats.frame_time_ms:.2f}ms")
        else:
            self._hide(self._fps_line, self._fps_low)
            self.itemconfig(self._fps_val, text="--")
            self.itemconfig(self._fps_sub, text="")

    # -- ping -------------------------------------------------------------

    def _draw_ping(self, stats: NetStats) -> None:
        if stats.status == NET_OK and stats.history:
            self._ms_top = _ease(self._ms_top, stats.history, _MS_STEPS, 20.0)
            self._plot(self._ping_line, stats.history, self._ms_top)
            self.itemconfig(self._ping_val, text=f"{stats.ping_ms:.2f}")
            loss = f"  {stats.loss_pct:.0f}%loss" if stats.loss_pct >= 1 else ""
            self.itemconfig(self._ping_sub, text=f"{stats.jitter:.2f} jit{loss}")
        else:
            self._hide(self._ping_line)
            self.itemconfig(self._ping_val, text="--")
            self.itemconfig(self._ping_sub, text="")

    # -- shared plot ------------------------------------------------------

    def _value_y(self, value: float, top: float) -> float:
        floor, ceiling = HEIGHT - PADDING, PADDING
        return floor - (floor - ceiling) * min(value / top, 1.0)

    def _plot(self, line_item: int, history, top: float) -> None:
        left, right = PADDING, self._plot_width - PADDING
        span = right - left
        # One point per pixel column keeps the redraw cost flat regardless of
        # how many samples the minute holds.
        columns: dict[int, list[float]] = {}
        for age, value in history:
            pos = max(0.0, min(1.0, 1.0 - age / WINDOW_SECONDS))
            columns.setdefault(int(pos * span), []).append(value)

        points: list[float] = []
        for column in sorted(columns):
            values = columns[column]
            points.extend((left + column, self._value_y(sum(values) / len(values), top)))

        if len(points) < 4:
            self.itemconfig(line_item, state="hidden")
            return
        self.itemconfig(line_item, state="normal")
        self.coords(line_item, *points)

    def _hide(self, *items: int) -> None:
        for item in items:
            self.itemconfig(item, state="hidden")

    # -- centre message ---------------------------------------------------

    def _draw_message(self, fps_stats: Stats, net_stats: NetStats) -> None:
        have_fps = fps_stats.status == STATUS_OK and fps_stats.history
        have_ping = net_stats.status == NET_OK and net_stats.history
        if have_fps or have_ping:
            self._rtss_hint = False
            self.itemconfig(self._message, text="")
            return

        # Nothing to plot yet - explain the more actionable of the two gaps.
        if fps_stats.status == STATUS_NO_RTSS:
            if fps_module.rtss_executable():
                self._rtss_hint = True
                self.itemconfig(self._message, text="click to start RivaTuner for FPS", fill=WARN)
                return
            self.itemconfig(self._message, text="RivaTuner not installed - FPS unavailable", fill=MUTED)
        elif net_stats.status == STATUS_NO_TARGET:
            self.itemconfig(self._message, text="waiting for the game", fill=MUTED)
        else:
            self.itemconfig(self._message, text="waiting for the game", fill=MUTED)
        self._rtss_hint = False


def _ease(current: float, history, steps, floor_value: float) -> float:
    """Slide `current` toward a ceiling that brackets the data's peak."""
    peak = max((value for _, value in history), default=floor_value)
    target = next((step for step in steps if step >= peak * 1.1), float(steps[-1]))
    return max(current + (target - current) * 0.25, floor_value)


FRAME_GRAPH_HEIGHT = 150
_MS_GRID = (8.3, 16.7, 33.3, 50.0)          # 120, 60, 30 and 20 fps


class FrameTimeGraph(tk.Canvas):
    """Frame times, the way benchmarking tools draw them.

    A line in milliseconds rather than frames per second, because a frame rate
    is an average over a window and a stutter is one frame: at 120 fps a single
    50 ms frame barely moves the average but is plainly visible here. Lower is
    better, and spikes are the thing to look at.

    Where several frames share a pixel column the worst of them is drawn, not
    the mean. Averaging is what hides the spike you came to find.
    """

    def __init__(self, parent, background=BG, width=760):
        super().__init__(parent, width=width, height=FRAME_GRAPH_HEIGHT, bg=background,
                         highlightthickness=0, bd=0)
        self._width = width
        self._top = 20.0
        self.bind("<Configure>", self._resized)

        self._grid = [self.create_line(0, 0, 0, 0, fill=GRID, dash=(1, 5)) for _ in _MS_GRID]
        self._grid_text = [self.create_text(0, 0, text="", fill=MUTED, anchor="w",
                                            font=("Consolas", 7)) for _ in _MS_GRID]
        self._band = self.create_line(0, 0, 0, 0, fill=FPS_LOW, width=1)
        self._worst = self.create_line(0, 0, 0, 0, fill=ACCENT, width=1)
        self._low_line = self.create_line(0, 0, 0, 0, fill=LAT, dash=(4, 3), width=1)
        self._low_text = self.create_text(0, 0, text="", fill=LAT, anchor="e",
                                          font=("Segoe UI", 7))
        self._message = self.create_text(0, 0, text="", fill=MUTED, anchor="c",
                                         font=("Segoe UI", 8))
        self._layout()

    def _resized(self, event):
        self._width = event.width
        self._layout()

    def _layout(self):
        self.coords(self._message, self._width // 2, FRAME_GRAPH_HEIGHT // 2)
        self.coords(self._low_text, self._width - PADDING, PADDING + 6)

    def _y(self, ms):
        floor, ceiling = FRAME_GRAPH_HEIGHT - PADDING, PADDING
        return floor - (floor - ceiling) * min(ms / self._top, 1.0)

    def update_frames(self, stats):
        frames = stats.frame_times
        if not frames:
            self.itemconfig(self._message,
                            text="waiting for frame data" if stats.status != STATUS_OK
                            else "no frames yet")
            for item in (self._band, self._worst, self._low_line, *self._grid,
                         *self._grid_text):
                self.itemconfig(item, state="hidden")
            self.itemconfig(self._low_text, text="")
            return

        self.itemconfig(self._message, text="")
        # Scale to the data with a little headroom rather than snapping up to
        # the next gridline, which would squash the trace into the bottom of
        # the panel whenever the worst frame sat just above a mark.
        target = max(frames) * 1.2
        self._top = max(self._top + (target - self._top) * 0.3, 12.0)

        left, right = PADDING, self._width - PADDING
        span = max(1, right - left)
        columns = {}
        for index, ms in enumerate(frames):
            slot = int(index * span / len(frames))
            low, high = columns.get(slot, (ms, ms))
            columns[slot] = (min(low, ms), max(high, ms))

        worst, best = [], []
        for slot in sorted(columns):
            low, high = columns[slot]
            worst.extend((left + slot, self._y(high)))
            best.extend((left + slot, self._y(low)))

        if len(worst) < 4:
            return
        for item in (self._band, self._worst, *self._grid, *self._grid_text):
            self.itemconfig(item, state="normal")
        self.coords(self._worst, *worst)
        self.coords(self._band, *best)

        for line, label, mark in zip(self._grid, self._grid_text, _MS_GRID):
            if mark > self._top:
                self.itemconfig(line, state="hidden")
                self.itemconfig(label, state="hidden")
                continue
            y = self._y(mark)
            self.coords(line, left, y, right, y)
            self.coords(label, left + 2, y - 6)
            self.itemconfig(label, text=f"{mark:g}ms")

        if stats.low_1 > 0:
            ms = 1000.0 / stats.low_1
            y = self._y(ms)
            self.coords(self._low_line, left, y, right, y)
            self.itemconfig(self._low_line, state="normal")
            self.itemconfig(self._low_text, text=f"1% low  {ms:.1f}ms")
        else:
            self.itemconfig(self._low_line, state="hidden")
            self.itemconfig(self._low_text, text="")

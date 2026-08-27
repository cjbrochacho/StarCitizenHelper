"""The header HUD: one minute of FPS and latency on a single graph.

Both series share the canvas but scale independently - frame rate against its
own ceiling, latency against its own - because they have nothing to do with
each other numerically. The readout on the right names each with its colour.
"""

from __future__ import annotations

import tkinter as tk

from .fps import (STATUS_NO_ACCESS, STATUS_NO_SOURCE, STATUS_OK, WINDOW_SECONDS,
                  Stats)
from .net import NetStats
from .net import STATUS_OK as NET_OK
from .theme import ACCENT, BG, FPS_LOW, GRID, LAT, MUTED, WARN

#: Frame time bars sit behind the lines, so they read as texture rather
#: than as a third thing competing for attention.
FRAME_BAR = "#24404f"

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

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, width=MIN_PLOT_WIDTH + READOUT_WIDTH, height=HEIGHT,
                         bg=BG, highlightthickness=0, bd=0)
        self._fps_top = 120.0
        self._ms_top = 60.0
        self._width = MIN_PLOT_WIDTH + READOUT_WIDTH
        self.bind("<Configure>", self._resized)

        # plot: two sparklines plus a faint FPS 1%-low reference
        # Drawn first so it sits behind both lines. One item, not one per bar:
        # the path runs along the baseline and spikes up for each column, which
        # is cheap enough to redraw ten times a second.
        self._bars = self.create_line(0, 0, 0, 0, fill=FRAME_BAR, width=1)
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

    # -- public -----------------------------------------------------------

    def update(self, fps_stats: Stats, net_stats: NetStats) -> None:
        self._draw_frame_bars(fps_stats)
        self._draw_fps(fps_stats)
        self._draw_ping(net_stats)
        self._draw_message(fps_stats, net_stats)

    # -- fps --------------------------------------------------------------

    def _draw_frame_bars(self, stats: Stats) -> None:
        """Worst frame time per pixel column, as bars off the bottom.

        The frame rate line is an average over each second, so a single slow
        frame barely dents it. These come from every frame the game presented,
        and the tallest bar in a column is the worst frame in it - averaging
        them would hide the stutter that makes them worth drawing.
        """
        frames = stats.frame_times
        if not frames:
            self.itemconfig(self._bars, state="hidden")
            return

        left, right = PADDING, self._plot_width - PADDING
        span = max(1, right - left)
        floor = HEIGHT - PADDING
        # Scaled so the worst frame in view reaches a little over half height,
        # leaving the lines above it readable.
        worst = max(ms for _, ms in frames) or 1.0
        reach = (HEIGHT - 2 * PADDING) * 0.55

        # Placed by age, on the same axis as the lines, so a spike sits under
        # the moment it happened rather than being spread across the window.
        columns: dict[int, float] = {}
        for age, ms in frames:
            position = max(0.0, min(1.0, 1.0 - age / WINDOW_SECONDS))
            slot = int(position * span)
            if ms > columns.get(slot, 0.0):
                columns[slot] = ms

        points: list[float] = []
        for slot in sorted(columns):
            x = left + slot
            top = floor - reach * (columns[slot] / worst)
            points.extend((x, floor, x, top, x, floor))

        if len(points) < 6:
            self.itemconfig(self._bars, state="hidden")
            return
        self.itemconfig(self._bars, state="normal")
        self.coords(self._bars, *points)

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
            self._hide(self._fps_line, self._fps_low, self._bars)
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
            self.itemconfig(self._message, text="")
            return

        # Nothing to plot yet - explain the more actionable of the two gaps.
        # Both of these are settled on the Performance tab, which is where the
        # long version of the explanation lives.
        if fps_stats.status == STATUS_NO_ACCESS:
            self.itemconfig(self._message, text="not allowed to measure frames", fill=WARN)
        elif fps_stats.status == STATUS_NO_SOURCE:
            self.itemconfig(self._message, text="frame capture unavailable", fill=MUTED)
        else:
            self.itemconfig(self._message, text="waiting for the game", fill=MUTED)


def _ease(current: float, history, steps, floor_value: float) -> float:
    """Slide `current` toward a ceiling that brackets the data's peak."""
    peak = max((value for _, value in history), default=floor_value)
    target = next((step for step in steps if step >= peak * 1.1), float(steps[-1]))
    return max(current + (target - current) * 0.25, floor_value)

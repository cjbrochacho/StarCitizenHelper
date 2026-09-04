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
from .theme import ACCENT, BG, FPS_REF, GRID, LAT, LAT_REF, MUTED, WARN

MIN_PLOT_WIDTH = 220
READOUT_WIDTH = 122
HEIGHT = 76
PADDING = 6

#: Each series owns a horizontal band rather than the whole plot. The two
#: ceilings were always eased independently, but drawing both into one box
#: meant a step in either one swung its line across the full height - and a
#: line moving the width of the chart reads as the chart rescaling, for both
#: series at once. Separate bands make the independence visible: neither line
#: can enter the other's space, whatever its scale does.
BAND_GAP = 5
#: Frame rate takes the larger share; it is the series people read first.
FPS_BAND_SHARE = 0.58

#: Where each dashed reference line sits, in that series' own units.
#:
#: Fixed marks, not derived ones. The frame rate line used to track the 1% low,
#: which moved with the data - so it answered "how far below your own average
#: are your worst frames", a question you have to already understand to read.
#: A constant answers the one asked at a glance instead: am I above the number
#: I care about, or below it. Both are drawn against their series' own eased
#: ceiling, so each lands correctly whatever scale its band is currently on.
FPS_REFERENCE = 60.0
PING_REFERENCE = 50.0

#: Line weights, in the same 96dpi units as every other measurement here. They
#: were plain pixels, which meant a hairline stayed one physical pixel however
#: dense the display got - while the text beside it, being in points, thickened
#: with the DPI. Same colour, wildly different weight, and the graph read as
#: washed out next to its own readout.
LINE_WIDTH = 1
#: The frame rate is the headline series - the number people actually watch -
#: so its line is drawn heavier than the rest rather than a different colour.
FPS_LINE_WIDTH = 1.5

_FPS_STEPS = (30, 60, 90, 120, 144, 165, 240, 360)
_MS_STEPS = (20, 40, 60, 100, 150, 200, 300, 500)


def _floor_for(reference: float, steps: tuple[int, ...]) -> float:
    """The lowest ceiling that still leaves `reference` inside the plot.

    Strictly above the reference, not equal to it. _value_y clamps at
    min(value / top, 1.0), so a ceiling of exactly the reference puts its line
    on the band's top edge, where it reads as the border rather than as a
    threshold - and it has to stay legible precisely when the data never
    reaches it, which is the case it exists to show. One step above leaves it
    inside with the series visibly below.
    """
    return next((float(s) for s in steps if s > reference), float(steps[-1]))


#: Ceilings never ease below these, so both reference lines are always drawn
#: somewhere readable - 60fps against a 90 ceiling, 50ms against 60.
_FPS_FLOOR = _floor_for(FPS_REFERENCE, _FPS_STEPS)
_MS_FLOOR = _floor_for(PING_REFERENCE, _MS_STEPS)


class HudGraph(tk.Canvas):
    """FPS and latency sparklines with a compact numeric readout.

    Canvas items are created once and then moved, never rebuilt - at ten
    redraws a second, recreating them would churn hundreds of objects per
    second for nothing.
    """

    def __init__(self, parent: tk.Misc) -> None:
        # Every constant in this module is a 96dpi design measurement, but the
        # fonts below are in points and Tk renders those against the display's
        # real DPI. Once the app became DPI-aware those two stopped agreeing:
        # on a 200% display the text doubled while the canvas it is positioned
        # inside did not, so the readout lines overlapped each other and ran
        # past the edge. Scaling the geometry by the same factor the fonts got
        # keeps the design proportions whatever the display does.
        self._s = s = parent.winfo_fpixels("1i") / 96.0
        self._pad = round(PADDING * s)
        self._gap = round(BAND_GAP * s)
        self._height = round(HEIGHT * s)
        self._readout_w = round(READOUT_WIDTH * s)
        self._min_plot = round(MIN_PLOT_WIDTH * s)

        super().__init__(parent, width=self._min_plot + self._readout_w,
                         height=self._height, bg=BG, highlightthickness=0, bd=0)
        self._fps_top = 120.0
        self._ms_top = 60.0
        self._width = self._min_plot + self._readout_w
        self.bind("<Configure>", self._resized)

        # plot: two sparklines, each with a faint dashed reference beneath it
        # The band divider goes down first of all, so everything else sits on
        # top of it. It is the only cue that the two halves are separate
        # scales rather than one plot.
        lw = max(1, round(LINE_WIDTH * s))
        self._divider = self.create_line(0, 0, 0, 0, fill=GRID, width=lw)
        # The dash pattern is a measurement too - left unscaled it turns into a
        # near-solid line as the weight around it grows.
        dashes = (max(1, round(1 * s)), max(1, round(5 * s)))
        self._fps_ref = self.create_line(0, 0, 0, 0, fill=FPS_REF, width=lw, dash=dashes)
        self._ping_ref = self.create_line(0, 0, 0, 0, fill=LAT_REF, width=lw, dash=dashes)
        self._fps_line = self.create_line(0, 0, 0, 0, fill=ACCENT,
                                          width=max(1, round(FPS_LINE_WIDTH * s)))
        self._ping_line = self.create_line(0, 0, 0, 0, fill=LAT, width=lw)

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

        self._message = self.create_text(0, self._height // 2, text="", fill=MUTED,
                                         anchor="c", font=("Segoe UI", 8))
        self._layout()

    # -- geometry ---------------------------------------------------------

    @property
    def _plot_width(self) -> int:
        return self._layout_width - self._readout_w

    @property
    def _layout_width(self) -> int:
        """The width everything is positioned against.

        Both the plot and the readout must come from *one* number. They used
        to disagree: the plot clamped at MIN_PLOT_WIDTH while the readout was
        placed against the raw canvas width, so once the canvas fell below
        MIN_PLOT_WIDTH + READOUT_WIDTH the two were laid out to different
        scales and drew straight through each other - values landing at
        negative x, labels past the right edge. Clamping here instead means a
        canvas too narrow to hold the layout simply clips it at the edge,
        which is legible; overlapping is not.
        """
        return max(self._width, self._min_plot + self._readout_w)

    def _resized(self, event: tk.Event) -> None:
        self._width = event.width
        self._layout()

    @property
    def _fps_band(self) -> tuple[float, float]:
        """(floor, ceiling) for frame rate - the upper band, growing upward."""
        ceiling = float(self._pad)
        floor = self._pad + (self._height - 2 * self._pad - self._gap) * FPS_BAND_SHARE
        return floor, ceiling

    @property
    def _ping_band(self) -> tuple[float, float]:
        """(floor, ceiling) for latency - the lower band, also growing upward.

        Both bands grow up from their own floor, which keeps the reading the
        header was designed around: frame rate rides high when it is good,
        latency sits low when it is good, so healthy still shows as two lines
        hugging opposite edges - now without either being able to reach the
        other's half.
        """
        return float(self._height - self._pad), self._fps_band[0] + self._gap

    def _layout(self) -> None:
        s = self._s
        left = self._plot_width + self._pad
        right = self._layout_width - self._pad
        split = self._fps_band[0] + self._gap / 2
        self.coords(self._divider, self._pad, split, self._plot_width - self._pad, split)
        # Baselines of the two readout blocks, in the same 96dpi units as the
        # rest of this module and scaled alongside it.
        self.coords(self._fps_tag, left, 11 * s)
        self.coords(self._fps_val, right, 15 * s)
        self.coords(self._fps_sub, right, 29 * s)
        self.coords(self._ping_tag, left, 47 * s)
        self.coords(self._ping_val, right, 51 * s)
        self.coords(self._ping_sub, right, 65 * s)
        self.coords(self._message, self._plot_width // 2, self._height // 2)

    # -- public -----------------------------------------------------------

    def update(self, fps_stats: Stats, net_stats: NetStats) -> None:
        self._draw_fps(fps_stats)
        self._draw_ping(net_stats)
        self._draw_message(fps_stats, net_stats)

    # -- fps --------------------------------------------------------------

    def _draw_fps(self, stats: Stats) -> None:
        """Draw the history whenever there is history; the plot is not a
        health indicator.

        This used to require the capture to be reporting *right now*, so any
        pause - a streaming hitch, a capture restart, the game briefly not
        presenting - blanked the whole minute even though every sample of it
        was still held. The window came back intact a moment later, which is
        the tell: the data was never gone, only hidden. A measured minute stays
        worth looking at while frames are momentarily not arriving.

        The live readout is the one thing that does go quiet, because a number
        labelled as current when nothing is arriving would be a lie.
        """
        if stats.history:
            band = self._fps_band
            self._fps_top = _ease(self._fps_top, stats.history, _FPS_STEPS, _FPS_FLOOR)
            self._plot(self._fps_line, stats.history, self._fps_top, band)
            mark = self._value_y(FPS_REFERENCE, self._fps_top, band)
            self.coords(self._fps_ref, self._pad, mark, self._plot_width - self._pad, mark)
            self.itemconfig(self._fps_ref, state="normal")
        else:
            self._hide(self._fps_line, self._fps_ref)

        live = stats.status == STATUS_OK
        self.itemconfig(self._fps_val, text=f"{stats.fps:.2f}" if live else "--",
                        fill=ACCENT if live else MUTED)
        self.itemconfig(
            self._fps_sub,
            text=(f"{stats.average:.2f} avg  {stats.frame_time_ms:.2f}ms" if live
                  else ("no frames arriving" if stats.history else "")))

    # -- ping -------------------------------------------------------------

    def _draw_ping(self, stats: NetStats) -> None:
        if stats.status == NET_OK and stats.history:
            band = self._ping_band
            self._ms_top = _ease(self._ms_top, stats.history, _MS_STEPS, _MS_FLOOR)
            self._plot(self._ping_line, stats.history, self._ms_top, band)
            mark = self._value_y(PING_REFERENCE, self._ms_top, band)
            self.coords(self._ping_ref, self._pad, mark, self._plot_width - self._pad, mark)
            self.itemconfig(self._ping_ref, state="normal")
            self.itemconfig(self._ping_val, text=f"{stats.ping_ms:.2f}")
            loss = f"  {stats.loss_pct:.0f}%loss" if stats.loss_pct >= 1 else ""
            self.itemconfig(self._ping_sub, text=f"{stats.jitter:.2f} jit{loss}")
        else:
            self._hide(self._ping_line, self._ping_ref)
            self.itemconfig(self._ping_val, text="--")
            self.itemconfig(self._ping_sub, text="")

    # -- shared plot ------------------------------------------------------

    def _value_y(self, value: float, top: float, band: tuple[float, float]) -> float:
        """Project a value onto its own band. `top` scales it, `band` places it.

        Passing both means a series can only ever be drawn where it belongs -
        there is no shared floor left for one scale to move under the other.
        """
        floor, ceiling = band
        return floor - (floor - ceiling) * min(value / top, 1.0)

    def _plot(self, line_item: int, history, top: float,
              band: tuple[float, float]) -> None:
        left, right = self._pad, self._plot_width - self._pad
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
            points.extend((left + column,
                           self._value_y(sum(values) / len(values), top, band)))

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
        # Having something drawn is what settles this, not whether the source
        # is reporting this instant - otherwise the centre text says "waiting
        # for the game" over a plot of the last minute of it.
        have_fps = bool(fps_stats.history)
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

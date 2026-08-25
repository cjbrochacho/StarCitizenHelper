"""The app's radar mark, drawn beside the title.

Drawn rather than loaded so it always appears: the icon file is a build
artefact of the installer, and a fresh clone will not have one yet. The shapes
match helper/shortcut.py, which renders the same mark into the .ico.
"""

import tkinter as tk

from .theme import ACCENT, BG


class BrandMark(tk.Canvas):
    """The radar glyph at whatever size the header wants."""

    def __init__(self, parent, size=46, background=BG, colour=ACCENT):
        super().__init__(parent, width=size, height=size, bg=background,
                         highlightthickness=0, bd=0)
        centre = size / 2
        outer = size * 0.40
        inner = size * 0.22
        blip = size * 0.055
        width = max(1, round(size * 0.055))

        self.create_oval(centre - outer, centre - outer, centre + outer, centre + outer,
                         outline=colour, width=width)
        self.create_oval(centre - inner, centre - inner, centre + inner, centre + inner,
                         outline=colour, width=max(1, width - 1))
        # sweep arm, out towards the upper right
        reach = size * 0.40
        self.create_line(centre, centre, centre + reach * 0.7071, centre - reach * 0.7071,
                         fill=colour, width=width)
        self.create_oval(centre - blip, centre - blip, centre + blip, centre + blip,
                         fill=colour, outline=colour)

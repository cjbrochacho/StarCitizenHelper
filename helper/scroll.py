"""A tab that scrolls when the window is shorter than its contents.

Tk has no scrollable frame. The idiom is a Canvas with a Frame drawn onto it
as a window item, a scrollbar driving the canvas, and two bindings keeping
them honest: the canvas resize sets the frame's width, and the frame resize
sets the scroll region. That is what ScrollFrame is, plus two things the
idiom leaves out.

The scrollbar hides itself when everything fits. Showing and hiding it
changes the canvas width, which could change the frame's height and flip
the decision back - so the frame's height must not depend on its width.
Every label in these tabs wraps at a fixed pixel width for exactly that
reason; leave them that way.

The mouse wheel is routed once, for the whole window, rather than bound per
frame. The usual recipe binds on <Enter> and unbinds on <Leave>, and has a
hole: moving from the canvas onto one of its own children fires <Leave> on
the canvas, and Tk bindings cannot tell that leave from a real one. A single
handler that walks up from whatever is under the pointer has no state to get
wrong. It also knows to stand aside for widgets that scroll themselves - a
Text, a Listbox, the opacity Scale - whose class binding has already acted by
the time the handler runs, since Tk runs bindings widget, class, toplevel,
then all.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

#: Widgets whose own class binding consumes the wheel. Scrolling the tab
#: underneath them as well would move two things per notch.
_SELF_SCROLLING = frozenset({
    "Text", "Listbox", "Treeview", "Scale", "Spinbox",
    "TSpinbox", "TCombobox", "TScrollbar", "Scrollbar",
})

#: Pixels per wheel notch. With yscrollincrement=1 a "unit" is a pixel.
_NOTCH = 40


class ScrollFrame(tk.Frame):
    """Put widgets in `.inner`; the rest takes care of itself."""

    def __init__(self, parent, bg: str, **kwargs) -> None:
        super().__init__(parent, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0,
                                yscrollincrement=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.bar.grid_remove()
        self._bar_shown = False

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.inner.bind("<Configure>", lambda event: self._sync())

    def _on_canvas_resize(self, event) -> None:
        # The frame is as wide as the canvas, always; only its height is its own.
        self.canvas.itemconfigure(self._window, width=event.width)
        self._sync()

    def _sync(self) -> None:
        height = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        need = height > self.canvas.winfo_height()
        if need == self._bar_shown:
            return                              # nothing changed; do not touch the layout
        self._bar_shown = need
        if need:
            self.bar.grid()
        else:
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)         # nothing may stay scrolled out of sight

    def scroll(self, notches: int) -> None:
        if self._bar_shown:
            self.canvas.yview_scroll(-notches * _NOTCH, "units")


def install_wheel_routing(root: tk.Misc) -> None:
    """One wheel handler for the whole window. Call once, after building the UI."""

    def route(event):
        widget = event.widget
        while widget is not None:
            if isinstance(widget, ScrollFrame):
                widget.scroll(int(event.delta / 120))
                return
            try:
                if widget.winfo_class() in _SELF_SCROLLING:
                    return                      # already scrolled by its class binding
            except tk.TclError:
                return
            widget = getattr(widget, "master", None)

    root.bind_all("<MouseWheel>", route)


def style_scrollbars(style: ttk.Style) -> None:
    """The clam scrollbar, in the app's colours. Both orientations."""
    style.configure("TScrollbar",
                    background="#2e435a", troughcolor="#0f1721",
                    bordercolor="#101722", lightcolor="#2e435a", darkcolor="#2e435a",
                    arrowcolor="#91a7bd", gripcount=0, relief="flat")
    style.map("TScrollbar",
              background=[("active", "#466f91"), ("pressed", "#466f91")],
              arrowcolor=[("disabled", "#253448")])

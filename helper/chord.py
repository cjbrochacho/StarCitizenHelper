"""One key chord, as the user presses it.

For "what is bound to this key?" - and, later, for setting a binding - the
app needs to hear a chord the way the game would: the modifiers held, then
the key that finished it. The keyboard library's hook delivers every press
and release on every key; this keeps the modifiers that are down and ends
on the first key that is not one.

The hook is not suppressing. The keys still go wherever they were going -
the focused window, the app's own hotkeys - which is the right call for a
question, and something the caller has to know for an answer.

Callbacks are invoked on the keyboard library's own thread. A Tk caller
wraps them in after(0, ...); nothing here touches Tk.
"""

from __future__ import annotations

import threading

#: What counts as holding, rather than pressing. The library's Windows
#: names: a left modifier is the bare name, a right one is prefixed, and
#: AltGr has a name of its own.
MODIFIER_NAMES = frozenset({
    "alt", "left alt", "right alt", "alt gr",
    "ctrl", "left ctrl", "right ctrl",
    "shift", "left shift", "right shift",
    "windows", "left windows", "right windows",
})

CANCEL_KEY = "esc"


class ChordRecorder:
    """Listens once. `on_done(modifiers, key, is_keypad)` or `on_cancel()`."""

    def __init__(self, on_done, on_cancel, hook=None, unhook=None) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._hook = hook
        self._unhook = unhook
        self._handle = None
        self._held: list[str] = []
        self._finished = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._hook is None:
            import keyboard                      # here, so the module imports without it
            self._hook, self._unhook = keyboard.hook, keyboard.unhook
        self._handle = self._hook(self._on_event)

    def cancel(self) -> None:
        """Stop listening. Safe to call more than once, or after a result."""
        if self._finish():
            self._on_cancel()

    def _finish(self) -> bool:
        """Unhook and mark done; True only for the first caller."""
        with self._lock:
            if self._finished:
                return False
            self._finished = True
        if self._handle is not None and self._unhook is not None:
            try:
                self._unhook(self._handle)
            except (ValueError, KeyError):
                pass                             # already gone
            self._handle = None
        return True

    def _on_event(self, event) -> None:
        if self._finished:
            return
        name = (getattr(event, "name", "") or "").lower()
        kind = getattr(event, "event_type", "")
        if not name:
            return
        if name in MODIFIER_NAMES:
            if kind == "down":
                if name not in self._held:       # auto-repeat sends the same press again
                    self._held.append(name)
            elif kind == "up" and name in self._held:
                self._held.remove(name)
            return
        if kind != "down":
            return
        if name == CANCEL_KEY:
            self.cancel()
            return
        if self._finish():
            self._on_done(list(self._held), name, bool(getattr(event, "is_keypad", False)))

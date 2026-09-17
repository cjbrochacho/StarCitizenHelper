"""What the chord recorder must not get wrong:

    python test_chord.py

No framework and no dependencies, like the rest of this project. The
keyboard library is never hooked: a fake hook hands the recorder whatever
key events the test invents.

The case it exists for: "what is bound to Alt+K?" is answered by pressing
Alt+K, and the answer is only as good as the recorder's idea of which keys
were held when K went down - through auto-repeat, a modifier let go early,
and a second key pressed after it has already stopped listening.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper.chord import ChordRecorder

PASSED = 0
FAILED = 0


def check(what, fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print("  [PASS] %s" % what)
    except AssertionError as exc:
        FAILED += 1
        print("  [FAIL] %s\n         %s" % (what, exc))


class Fake:
    """A hook that remembers what is installed, and a way to press keys."""

    def __init__(self):
        self.installed = []
        self.done = []
        self.cancelled = 0
        self.recorder = ChordRecorder(
            on_done=lambda mods, key, keypad: self.done.append((mods, key, keypad)),
            on_cancel=lambda: setattr(self, "cancelled", self.cancelled + 1),
            hook=self._hook, unhook=self._unhook)

    def _hook(self, callback):
        self.installed.append(callback)
        return callback

    def _unhook(self, handle):
        self.installed.remove(handle)

    def press(self, name, kind="down", keypad=False):
        for callback in list(self.installed):
            callback(SimpleNamespace(name=name, event_type=kind, is_keypad=keypad))


print("\n1. a chord")


def _plain():
    f = Fake(); f.recorder.start()
    assert len(f.installed) == 1
    f.press("alt"); f.press("k")
    assert f.done == [(["alt"], "k", False)], f.done
    assert f.installed == [], "still listening after the answer"
    assert f.cancelled == 0


def _key_first():
    f = Fake(); f.recorder.start()
    f.press("k")
    assert f.done == [([], "k", False)]


def _two_modifiers():
    f = Fake(); f.recorder.start()
    f.press("ctrl"); f.press("shift"); f.press("f")
    assert f.done == [(["ctrl", "shift"], "f", False)], f.done


def _released_early():
    f = Fake(); f.recorder.start()
    f.press("alt"); f.press("ctrl"); f.press("alt", "up"); f.press("k")
    assert f.done == [(["ctrl"], "k", False)], f.done


def _auto_repeat():
    f = Fake(); f.recorder.start()
    f.press("alt"); f.press("alt"); f.press("alt"); f.press("k")
    assert f.done == [(["alt"], "k", False)], f.done


def _keypad():
    f = Fake(); f.recorder.start()
    f.press("1", keypad=True)
    assert f.done == [([], "1", True)]


def _right_side():
    f = Fake(); f.recorder.start()
    f.press("right alt"); f.press("K")
    assert f.done == [(["right alt"], "k", False)], f.done


check("modifiers held, then the key", _plain)
check("a key on its own", _key_first)
check("two modifiers, in the order pressed", _two_modifiers)
check("a modifier let go before the key is not part of it", _released_early)
check("auto-repeat of a held modifier counts once", _auto_repeat)
check("the keypad flag travels with the key", _keypad)
check("right-hand modifiers keep their name; the key is lower-cased", _right_side)


print("\n2. stopping")


def _escape():
    f = Fake(); f.recorder.start()
    f.press("shift"); f.press("esc")
    assert f.cancelled == 1 and f.done == []
    assert f.installed == []


def _after_done():
    f = Fake(); f.recorder.start()
    f.press("k"); f.press("j"); f.press("esc")
    assert f.done == [([], "k", False)] and f.cancelled == 0, "kept listening after the answer"


def _cancel_twice():
    f = Fake(); f.recorder.start()
    f.recorder.cancel(); f.recorder.cancel()
    assert f.cancelled == 1 and f.installed == []


def _cancel_after_done():
    f = Fake(); f.recorder.start()
    f.press("k"); f.recorder.cancel()
    assert f.cancelled == 0, "a cancel after the answer should be a no-op"


def _releases_ignored():
    f = Fake(); f.recorder.start()
    f.press("k", "up")
    assert f.done == [] and len(f.installed) == 1, "a key going up is not a press"
    f.press("k")
    assert f.done == [([], "k", False)]


check("Escape cancels, once, and stops listening", _escape)
check("nothing after the answer is heard", _after_done)
check("cancel twice is one cancel", _cancel_twice)
check("cancel after an answer does nothing", _cancel_after_done)
check("a key release on its own is nothing", _releases_ignored)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "CHORD VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)

"""What the window-position helpers must not get wrong:

    python test_window.py

No framework and no dependencies, like the rest of this project. Nothing
here opens a window: the two functions under test are the ones that decide
whether a saved position is worth using, and they take numbers, not widgets.

The case it exists for: a window remembered on a second monitor that has
since been unplugged, or one whose coordinates were mangled in settings.json
by hand. Either must fall back to the default size rather than open where
nobody can reach it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper.window import parse_geometry, rect_is_visible

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


print("\n1. reading what Tk wrote")


def _parse():
    cases = [
        ("1225x950+100+50", (1225, 950, 100, 50)),
        ("900x700+-1200+80", (900, 700, -1200, 80)),      # a monitor left of the primary
        ("  980x760+0+0 ", (980, 760, 0, 0)),
        ("900x700-10-10", None),                            # right-edge form: Tk never writes it
        ("900x700", None),
        ("junk", None),
        ("", None),
        (None, None),
    ]
    for text, expected in cases:
        got = parse_geometry(text)
        assert got == expected, "%r -> %r, expected %r" % (text, got, expected)


check("the +X+Y form, negatives included; nothing else", _parse)


print("\n2. asking the desktop whether a place exists")


def _visible():
    assert rect_is_visible(0, 0, 100, 40), "the primary monitor's corner is always there"


def _not_visible():
    assert not rect_is_visible(-100000, -100000, -99000, -99960), "nothing is that far away"


check("the primary monitor's corner is visible", _visible)
check("a rectangle far off every monitor is not", _not_visible)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "WINDOW VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)

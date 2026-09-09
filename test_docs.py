"""What the README must not get wrong:

    python test_docs.py

No framework and no dependencies, like the rest of this project.

Documentation drifts because nothing fails when it does. The overlay landed
with six settings and the README gained none of them; `perf_capture_enabled`
would have been the eighth undocumented key, and `scan_toggle` has been
printed with a default the app has not used in some time. None of that was
caught, because a stale paragraph runs perfectly well.

The same went for the project layout: helper/overlay.py shipped a whole
feature and never appeared in the tree, and neither test file was listed.

So the two blocks in the README that describe the app are read here as if
they were code - the settings block against DEFAULTS, the layout tree
against the files that actually exist. Add a setting or a module without
documenting it and this fails, which is the only thing that reliably keeps
the two together.

It reads the sources as text rather than importing the app: importing
StarCitizenHelper builds a Tk window and starts threads, which is not
something a test should do to a machine.
"""

import ast
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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


def _module_dict(name):
    """A literal assigned at module level in StarCitizenHelper.py."""
    source = io.open(ROOT / "StarCitizenHelper.py", encoding="utf-8").read()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise AssertionError("%s is not a module-level literal any more" % name)


def _module_string(name):
    """A string assigned at module level in StarCitizenHelper.py."""
    source = io.open(ROOT / "StarCitizenHelper.py", encoding="utf-8").read()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise AssertionError("%s is not a module-level literal any more" % name)


def _readme_release():
    """The version in the banner at the top of the README."""
    readme = io.open(ROOT / "README.md", encoding="utf-8").read()
    match = re.search(r"Latest release: v([0-9]+[.][0-9]+[.][0-9]+)", readme)
    assert match, "the README no longer states a latest release"
    return match.group(1)


def _readme_layout():
    """The tree under the Project layout heading, as the names it lists."""
    readme = io.open(ROOT / "README.md", encoding="utf-8").read()
    match = re.search(r"## Project layout.*?```(.*?)```", readme, re.S)
    assert match, "the README has no tree under the Project layout heading"
    names = re.findall(r"[A-Za-z0-9_.-]+", match.group(1))
    return {name for name in names if name.endswith(".py")}


def _readme_settings():
    """The JSON block under '## Settings file', parsed."""
    readme = io.open(ROOT / "README.md", encoding="utf-8").read()
    match = re.search(r"## Settings file.*?```json\n(.*?)```", readme, re.S)
    assert match, "the README has no JSON block under '## Settings file'"
    try:
        return json.loads(match.group(1))
    except ValueError as exc:
        raise AssertionError("that block is not valid JSON: %s" % exc)


#: Documented on purpose without being in DEFAULTS: written by the app rather
#: than configured, so a reader needs to know what they are without ever
#: needing to set one. Anything else present in one and not the other is drift.
GENERATED_KEYS = frozenset({"telemetry_client_id"})

#: Keys whose value in the README is illustrative rather than the default -
#: `macros` ships empty, but an empty list would document nothing.
ILLUSTRATIVE = frozenset({"macros"})


print("\n1. every setting the app has is written down")


def _no_undocumented():
    missing = sorted(set(_module_dict("DEFAULTS")) - set(_readme_settings()))
    assert not missing, (
        "settings the app reads that the README never mentions: %s\n"
        "         add them to the JSON block under '## Settings file'"
        % ", ".join(missing))


def _nothing_invented():
    extra = sorted(set(_readme_settings()) - set(_module_dict("DEFAULTS")) - GENERATED_KEYS)
    assert not extra, (
        "the README documents settings the app does not have: %s\n"
        "         they were removed or renamed - fix or drop them"
        % ", ".join(extra))


def _defaults_match():
    defaults, documented = _module_dict("DEFAULTS"), _readme_settings()
    wrong = ["%s (app %r, README %r)" % (k, defaults[k], documented[k])
             for k in sorted(set(defaults) & set(documented))
             if k not in ILLUSTRATIVE and defaults[k] != documented[k]]
    assert not wrong, "the README prints a default the app does not use: %s" % "; ".join(wrong)


check("no setting is missing from the README", _no_undocumented)
check("the README invents none", _nothing_invented)
check("and the values it prints are the real defaults", _defaults_match)


print("")
print("2. the release number says what is actually running")


def _version_matches_readme():
    """A zip install has no tags, so __version__ is the only version it has.

    current_revision() returns it verbatim there - meaning an install seven
    commits past the tag still called itself v3.0.0, because nothing failed
    when the number and the release drifted apart.
    """
    source, banner = _module_string("__version__"), _readme_release()
    assert source == banner, (
        "__version__ is %s but the README announces v%s - a release bumps both"
        % (source, banner))


check("__version__ agrees with the README banner", _version_matches_readme)


print("")
print("3. every module is in the project layout")


def _modules_on_disk():
    found = {path.name for path in ROOT.glob("*.py")}
    return found | {path.name for path in (ROOT / "helper").glob("*.py")}


def _no_unlisted_modules():
    """A module nobody has read about may as well not be there.

    helper/overlay.py shipped a whole feature and never appeared in the
    tree; so did both test files. Same fault as an undocumented setting,
    and the same fix.
    """
    missing = sorted(_modules_on_disk() - _readme_layout())
    assert not missing, (
        "modules the project layout never lists: %s" % ", ".join(missing))


def _no_phantom_modules():
    gone = sorted(_readme_layout() - _modules_on_disk())
    assert not gone, (
        "the layout lists modules that are no longer there: %s" % ", ".join(gone))


check("no module is missing from the layout", _no_unlisted_modules)
check("and none of the listed ones are gone", _no_phantom_modules)


print("\n4. the block stays machine-readable, so this test keeps working")


def _boolean_keys_are_real():
    defaults = _module_dict("DEFAULTS")
    booleans = _module_dict("BOOLEAN_KEYS")
    unknown = sorted(set(booleans) - set(defaults))
    assert not unknown, "BOOLEAN_KEYS names settings that do not exist: %s" % ", ".join(unknown)
    wrong = sorted(k for k in booleans if not isinstance(defaults[k], bool))
    assert not wrong, "BOOLEAN_KEYS names non-booleans: %s" % ", ".join(wrong)


def _every_boolean_is_listed():
    """A boolean left out of BOOLEAN_KEYS round-trips as the string "false",
    which bool() reads as true - the bug the list exists to prevent."""
    defaults = _module_dict("DEFAULTS")
    booleans = set(_module_dict("BOOLEAN_KEYS"))
    missed = sorted(k for k, v in defaults.items() if isinstance(v, bool) and k not in booleans)
    assert not missed, (
        "boolean settings missing from BOOLEAN_KEYS: %s\n"
        "         each would be saved as a string and read back as true"
        % ", ".join(missed))


check("BOOLEAN_KEYS names only real booleans", _boolean_keys_are_real)
check("and every boolean setting is in it", _every_boolean_is_listed)


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "DOCS VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)

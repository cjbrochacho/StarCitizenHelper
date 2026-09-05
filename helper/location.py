"""Where in the 'verse the player is, read out of the game's own log.

Star Citizen never states a position outright, but it leaks one three ways,
and they disagree about how much they know:

    Location[Stanton4_NewBabbage]                        the place, named
    objectcontainers/pu/loc/flagship/stanton/orison/...  the place, implied
    objectcontainers/pu/loc/mod/pyro/station/...         only the system

A reading is a *path* down a fixed ladder - system, body, site, detail - not a
single label, so the same data answers "how does Stanton run" and "how does
the Green Circle in Orison run" without being collected twice:

    stanton . crusader . orison . greencircle

Levels are named rather than merely positional, because the sources disagree
about depth: a container path names the site and skips the body, while a
Location line names the body and skips the district. Filling levels by name
and building the path from whichever are known keeps `stanton.crusader.orison`
from colliding with `stanton.orison.green_circle` at the same rollup.

Nothing guesses past what the line said. RR_ARC_L1 names ArcCorp and gets a
body; RR_P3_LEO says "planet three" without saying of what, and gets none.
Half a location recorded honestly is worth more than a whole one invented -
and `source` says whether the game stated the place or we inferred it from
what happened to be loading.

Nothing from these lines is kept but the levels below. That matters: the
highest-quality source is a line that also carries the player's handle -

    <RequestLocationInventory> Player[...] requested inventory for Location[...]

- so the pattern lifts the one field it wants, and the rest of the line is
never held anywhere it could be sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

# --- vocabulary -----------------------------------------------------------
#
# Hand-maintained, and it will rot: CIG ships systems. Every table here is
# consulted with a default of "unknown" rather than a guess, so a new system
# shows up as a gap in the data instead of being quietly filed under Stanton.

SYSTEMS = ("stanton", "pyro", "nyx", "castra")

#: Stanton's planets by index, as they appear in Stanton1..Stanton4.
_STANTON_BODIES = {"1": "hurston", "2": "crusader", "3": "arccorp", "4": "microtech"}

#: The same four in the three-letter form rest stops use.
_BODY_CODES = {"hur": "hurston", "cru": "crusader", "arc": "arccorp",
               "mic": "microtech"}

#: Which body a well-known site sits on. Container paths name the site and
#: skip the body, so without this the same city would roll up under two
#: different parents depending on which line happened to be read.
_SITE_BODIES = {
    "orison": ("stanton", "crusader"),
    "lorville": ("stanton", "hurston"),
    "area18": ("stanton", "arccorp"),
    "new_babbage": ("stanton", "microtech"),
    "grim_hex": ("stanton", "yela"),
    "levski": ("nyx", "delamar"),
}

#: Sites whose names carry no system at all, but which exist in one place only.
_LANDMARKS = {"grimhex": "grim_hex", "levski": "levski"}

#: The same place under the name the streaming paths use. Without these, one
#: city arrives as two rows - stanton.arccorp.area18 from a Location line and
#: stanton.unsaid.a18 from a container - and neither total is the real one.
_SITE_ALIASES = {
    "a18": "area18",
    "newbab": "new_babbage",
    "newbabbage": "new_babbage",
    "levski_v2": "levski",
    "orison_sp": "orison",
    "lorville_sp": "lorville",
}

#: The ladder. Position carries meaning, so level 1 is always a system.
LEVELS = ("system", "body", "site", "detail")

#: Stands in for a rung the log never named, so the rungs below it keep their
#: position. Querying for it is how you find what the parser could not place.
UNSAID = "unsaid"

SOURCE_NAMED = "named"              # the game said where you were
SOURCE_STREAMED = "streamed"        # inferred from what was loading around you
SOURCE_JURISDICTION = "jurisdiction"
SOURCE_NONE = "none"

KIND_CITY = "city"
KIND_STATION = "station"
KIND_REST_STOP = "rest_stop"
KIND_JUMP_POINT = "jump_point"
KIND_OUTPOST = "outpost"
KIND_ASTEROID_BASE = "asteroid_base"
KIND_UNDERGROUND = "underground"
KIND_CAVE = "cave"
KIND_HUB = "hub"
KIND_UNKNOWN = "unknown"

#: Container folders that describe a kind of place rather than a place.
_CONTAINER_KINDS = {
    "asteroid_base": KIND_ASTEROID_BASE, "outpost": KIND_OUTPOST,
    "station": KIND_STATION, "cave": KIND_CAVE, "ugf": KIND_UNDERGROUND,
    "fob": KIND_OUTPOST,
}


@dataclass(frozen=True)
class Place:
    """One reading. An empty level means "the log did not say", never "none"."""

    system: str = ""
    body: str = ""
    site: str = ""
    detail: str = ""
    kind: str = KIND_UNKNOWN
    source: str = SOURCE_NONE

    @property
    def path(self) -> tuple[str, ...]:
        """The ladder, in order, as a rollup key.

        A gap in the middle is filled with UNSAID rather than closed up.
        Closing it would slide the next level into the empty slot - a rest
        stop whose system is unknown would land in the system position and
        turn up in a "which systems are slow" rollup as though it were one.
        Trailing gaps are simply where the reading ran out.
        """
        rungs = [self.system, self.body, self.site, self.detail]
        while rungs and not rungs[-1]:
            rungs.pop()
        return tuple(rung or UNSAID for rung in rungs)

    @property
    def dotted(self) -> str:
        """stanton.crusader.orison.greencircle - one column, every altitude."""
        return ".".join(self.path)

    @property
    def depth(self) -> int:
        return len(self.path)

    @property
    def known(self) -> bool:
        return self.source != SOURCE_NONE and bool(self.system or self.site)

    def at(self, levels: int) -> str:
        """The path truncated to a bird's-eye level: at(1) is the system."""
        return ".".join(self.path[:levels])

    def as_fields(self) -> dict:
        """Exactly the keys that may be sent, in the order the schema lists."""
        return {"system": self.system, "body": self.body, "site": self.site,
                "detail": self.detail, "path": self.dotted, "depth": self.depth,
                "kind": self.kind, "source": self.source}


UNKNOWN = Place()


def _resolve(place: Place) -> Place:
    """Settle a site on its canonical name, then fill in the body above it.

    Only ever adds or normalises a level; it never overrules something the log
    actually said about a level it did name.
    """
    site = _SITE_ALIASES.get(place.site, place.site)
    known = _SITE_BODIES.get(site) if not place.body else None
    if site == place.site and not known:
        return place
    system, body = known if known else (place.system, place.body)
    return Place(system=place.system or system, body=place.body or body,
                 site=site, detail=place.detail, kind=place.kind,
                 source=place.source)


# --- reading Location[...] ------------------------------------------------

_RE_LOCATION = re.compile(rb"Location\[([A-Za-z0-9_\-]{2,70})\]")

#: RR_JP_StantonPyro - a jump point, named for the two ends.
_RE_JUMP = re.compile(r"^rr_jp_([a-z]+?)(stanton|pyro|nyx|castra)$")
#: RR_ARC_L1 / RR_MIC_LEO - a rest stop that names its body.
_RE_REST_NAMED = re.compile(r"^rr_(hur|cru|arc|mic)_([a-z0-9]+)$")
#: RR_P3_LEO - a rest stop at "planet three" of a system it does not name.
_RE_REST_INDEX = re.compile(r"^rr_p(\d+)_([a-z0-9]+)$")
#: Stanton4_NewBabbage, Stanton3b_ArcCorp_Area048, Pyro4_Outpost_...
_RE_BODY_PLACE = re.compile(r"^(stanton|pyro|nyx|castra)(\d+)([a-z]?)_(.+)$")
#: Nyx_Levski - a system and a site, no body index.
_RE_SYSTEM_PLACE = re.compile(r"^(stanton|pyro|nyx|castra)_(.+)$")
#: Outpost_PAF_Stanton2b_Lamina_1
_RE_OUTPOST = re.compile(r"^outpost_[a-z]+_(stanton|pyro|nyx|castra)(\d+)([a-z]?)_(.+)$")


def _tidy(name: str) -> str:
    """A name as a stable, lowercase token.

    CIG writes these in three styles at once - NewBabbage, Area048,
    col_m_trdpst - so word boundaries are restored before lowercasing. Without
    that step new_babbage and newbabbage become two rows for one city.
    """
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return re.sub(r"_+", "_", name)


def _kind_of(name: str) -> str:
    """Guess a kind from the token, conservatively."""
    if "monorail" in name or "transport_hub" in name or "hub" in name:
        return KIND_HUB
    if "outpost" in name or "trdpst" in name:
        return KIND_OUTPOST
    if "asteroid" in name:
        return KIND_ASTEROID_BASE
    return KIND_UNKNOWN


def _cased(raw: str, match: re.Match, group: int) -> str:
    """The original-case text of a group matched against the lowered token.

    Matching is done lowercased so the patterns stay readable, but names need
    their capitals back for _tidy to find the boundary in NewBabbage.
    Lowercasing ASCII preserves length, so the spans still line up.
    """
    return raw[match.start(group):match.end(group)]


def _split_site(name: str, body: str) -> tuple[str, str]:
    """Separate a site from any detail below it.

    Stanton3b_ArcCorp_Area048 repeats the body before the district, so the
    repeat is dropped rather than becoming part of the site's name. The two
    spellings never match directly - the table says "arccorp" while the token
    tidies to "arc_corp" - so the comparison walks the token a word at a time
    and stops when the accumulated words spell the body.
    """
    token = _tidy(name)
    base = body.split("_")[0] if body else ""      # a moon letter is not part of it
    if base:
        parts = token.split("_")
        joined = ""
        for index, part in enumerate(parts):
            joined += part
            if not base.startswith(joined):
                break
            if joined == base:
                token = "_".join(parts[index + 1:]) or token
                break
    return token, ""


def _body_of(system: str, index: str, moon: str) -> str:
    """Stanton names its planets; nothing else here does, so nothing else guesses.

    A moon letter is kept on the body rather than given a level of its own -
    it is a distinct place, but prefix rollup on "crusader" still gathers
    Crusader and its moons together.
    """
    if system != "stanton":
        return f"{system}_{index}" if index else ""
    body = _STANTON_BODIES.get(index, "")
    if not body:
        return ""
    return f"{body}_{moon}" if moon else body


def parse_location_token(token: str) -> Place:
    """Turn the contents of Location[...] into a Place.

    Every branch is a shape actually seen in a log. Anything unrecognised is
    returned as unknown rather than forced into the closest-looking rule.
    """
    raw = token.strip()
    low = raw.lower()

    if low in _LANDMARKS:
        site = _LANDMARKS[low]
        system, body = _SITE_BODIES[site]
        return Place(system=system, body=body, site=site, kind=KIND_STATION,
                     source=SOURCE_NAMED)

    match = _RE_JUMP.match(low)
    if match:
        near, far = match.group(1), match.group(2)
        if near not in SYSTEMS:
            return UNKNOWN
        # A jump point hangs in the system, not on a planet, so it takes the
        # body rung rather than leaving one unsaid above it: it is the same
        # altitude as a planet, and rolls up beside one.
        return Place(system=near, body=_tidy(f"jp_{near}_{far}"),
                     kind=KIND_JUMP_POINT, source=SOURCE_NAMED)

    match = _RE_REST_NAMED.match(low)
    if match:
        # The four named bodies are Stanton's, so the system follows.
        return Place(system="stanton", body=_BODY_CODES[match.group(1)],
                     site=_tidy(f"rest_stop_{match.group(2)}"),
                     kind=KIND_REST_STOP, source=SOURCE_NAMED)

    match = _RE_REST_INDEX.match(low)
    if match:
        # "Planet three" of an unnamed system. Which planet is knowable only
        # from context this parser deliberately does not have, so the body
        # level stays empty and the reading sits one rung shallower.
        return Place(site=_tidy(f"rest_stop_p{match.group(1)}_{match.group(2)}"),
                     kind=KIND_REST_STOP, source=SOURCE_NAMED)

    match = _RE_OUTPOST.match(low)
    if match:
        system, index, moon = match.group(1), match.group(2), match.group(3)
        body = _body_of(system, index, moon)
        site, detail = _split_site(_cased(raw, match, 4), body)
        return _resolve(Place(system=system, body=body, site=site, detail=detail,
                              kind=KIND_OUTPOST, source=SOURCE_NAMED))

    match = _RE_BODY_PLACE.match(low)
    if match:
        system, index, moon = match.group(1), match.group(2), match.group(3)
        body = _body_of(system, index, moon)
        site, detail = _split_site(_cased(raw, match, 4), body)
        kind = _kind_of(site)
        if kind == KIND_UNKNOWN and not moon:
            kind = KIND_CITY            # Stanton4_NewBabbage and friends
        return _resolve(Place(system=system, body=body, site=site, detail=detail,
                              kind=kind, source=SOURCE_NAMED))

    match = _RE_SYSTEM_PLACE.match(low)
    if match:
        site = _tidy(_cased(raw, match, 2))
        return _resolve(Place(system=match.group(1), site=site,
                              kind=_kind_of(site), source=SOURCE_NAMED))

    return UNKNOWN


# --- reading object container paths ---------------------------------------

_RE_CONTAINER = re.compile(rb"objectcontainers/pu/loc/([a-z0-9_\-/]{3,90})", re.I)


def parse_container_path(path: str) -> Place:
    """A place implied by whatever the game is streaming in around you.

    Noisier than a Location line and constantly firing, but it is the only
    signal present while simply flying about - and it reaches a rung deeper,
    naming the district inside a city. "common" is shared furniture that
    exists everywhere and says nothing about where you are.
    """
    parts = [p for p in path.lower().split("/") if p]
    if len(parts) < 2:
        return UNKNOWN
    group, second = parts[0], parts[1]

    if second == "common":
        return UNKNOWN

    # flagship/stanton/orison/greencircle - the landing zones, named outright,
    # one level deeper than any Location line manages.
    if group == "flagship" and second in SYSTEMS and len(parts) >= 3:
        site = _tidy(parts[2])
        detail = _tidy(parts[3]) if len(parts) > 3 else ""
        if detail.startswith(site + "_"):
            detail = detail[len(site) + 1:]
        return _resolve(Place(system=second, site=site, detail=detail,
                              kind=KIND_CITY, source=SOURCE_STREAMED))

    # mod/pyro/asteroid_base/... - the system, and sometimes a kind.
    if second in SYSTEMS:
        kind = _CONTAINER_KINDS.get(parts[2], KIND_UNKNOWN) if len(parts) > 2 else KIND_UNKNOWN
        return Place(system=second, kind=kind, source=SOURCE_STREAMED)

    return UNKNOWN


# --- following the log ----------------------------------------------------

class LocationReader:
    """Follows Game.log and keeps the best current idea of where you are.

    Reads only what has been appended since last time, the way the shard
    reader does, and resets when the log is replaced by a new launch.

    A place is held until the log contradicts it. That is the whole rule, and
    it matters because of what the log actually contains: in a three-hour
    session it named a location outright twice, and of 215 streaming paths
    only 56% resolved to anywhere. The rest were hangars, habs, ship interiors
    and station modules - the same assets wherever you are, which is exactly
    why they cannot say where you are.

    This used to expire after five minutes without a fresh reading, which
    meant walking indoors and staying there was indistinguishable from
    leaving: the client learned you were at Orison, streamed nothing but
    modular interiors while you sat in your ship, and then discarded Orison on
    a timer. Four batches in five went up with no location at all.

    So depth is no longer the only ranking. A reading that contradicts the one
    held - a different system, a different body - replaces it however shallow
    it is, which is what makes travel register. A reading that merely restates
    less of the same place is redundant and ignored, and one that says nothing
    is not evidence of anything.
    """

    def __init__(self) -> None:
        self._offset = 0
        self._place = UNKNOWN

    def reset(self) -> None:
        self._offset = 0
        self._place = UNKNOWN

    def read(self, path: Path, now: float = 0.0) -> Place:
        """Consume new bytes and return the best current reading.

        `now` is no longer read - nothing expires on a clock any more - but it
        stays in the signature because the collector passes its monotonic time
        to every reader it drives, and they should keep looking alike.
        """
        try:
            size = path.stat().st_size
            if size < self._offset:
                self.reset()                    # a new launch, a new log
            with path.open("rb") as handle:
                handle.seek(self._offset)
                fresh = handle.read()
                self._offset = handle.tell()
        except OSError:
            return self._place

        for token in _RE_LOCATION.findall(fresh):
            self._offer(parse_location_token(token.decode("ascii", "replace")))
        for raw in _RE_CONTAINER.findall(fresh):
            self._offer(parse_container_path(raw.decode("ascii", "replace")))

        return self._place

    def _offer(self, place: Place) -> None:
        """Take the reading if it beats or contradicts the one being held."""
        if not place.known:
            return                      # a hangar is not a place; say nothing
        if not self._place.known:
            self._place = place
            return
        deeper = place.depth > self._place.depth
        firmer = (place.depth == self._place.depth
                  and place.source == SOURCE_NAMED
                  and self._place.source != SOURCE_NAMED)
        if deeper or firmer or self._contradicts(place):
            self._place = self._carry_system(place)

    def _carry_system(self, place: Place) -> Place:
        """Keep the system when the new reading does not name one.

        The game names plenty of places without saying which system they are
        in: RR_P3_LEO is "the rest stop in low orbit of planet three", and the
        parser cannot know whose planet three without context it does not
        have. The reader does have it - it is holding the last place - so the
        system carries over and the rest stop lands under Pyro instead of
        under nothing. On live data that was 458 batches filed as
        unsaid.unsaid.rest_stop_p3_leo and friends.

        Only the system. The body is not carried: RR_P3_LEO is at planet
        three, and the place being held might be planet four, so inheriting
        the body would file it confidently in the wrong orbit. An empty rung
        is honest; a wrong one is not.

        Crossing between systems goes through a jump point, which the game
        does name - jp_stanton_pyro - so the system is refreshed on the way
        rather than carried past its expiry.
        """
        if place.system or not self._place.system:
            return place
        return replace(place, system=self._place.system)

    def _contradicts(self, place: Place) -> bool:
        """Whether a reading rules out the place being held.

        Only rungs both readings name are compared, so a bare "stanton" while
        holding stanton.crusader.orison is agreement with less detail, not a
        move to the system. "pyro" disagrees at the first rung, and a jump
        across the 'verse registers even though it says less.
        """
        return any(new and held and new != held for new, held in (
            (place.system, self._place.system), (place.body, self._place.body),
            (place.site, self._place.site), (place.detail, self._place.detail)))

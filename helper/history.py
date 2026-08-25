"""Which shard you were on, and when.

Star Citizen writes a `<Join PU>` line with a timestamp every time it puts you
on a shard, and keeps the previous session's log in `logbackups`. That is
enough to reconstruct where you have been - including sessions from before
this tool existed, and, more to the point, the one that ended in a crash.

Only the most recent handful is wanted, so logs are read newest first and
reading stops as soon as there are enough - the game keeps months of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_JOIN = re.compile(
    rb"<(?P<stamp>\d{4}-\d\d-\d\dT[\d:.]+Z)>\s*\[Notice\]\s*<Join PU>\s*"
    rb"address\[(?P<ip>[\d.]+)\]\s*port\[(?P<port>\d+)\]\s*shard\[(?P<shard>[a-z0-9_]+)\]")
_STAMP = re.compile(rb"<(\d{4}-\d\d-\d\dT[\d:.]+Z)>")

#: Enough of the tail to be sure of catching the final timestamp.
_TAIL_BYTES = 64 * 1024

# A shard name is pub_<region><n><az>_<build>_<number>, e.g.
# pub_use1b_12326004_120. The build changes with every patch - the same server
# appeared as pub_use1b_12269732_120 before the last one - so it is no part of
# the server's identity and is left out of the readable name.
_SHARD = re.compile(r"^(?P<access>[a-z]+)_(?P<region>[a-z]+)(?P<zone>\d+[a-z]?)"
                    r"_(?P<build>\d+)_(?P<number>\d+)$")

#: Longest first, so "apse" is not mistaken for "aps".
_REGIONS = [
    ("apse", "Asia-Pacific SE"), ("apne", "Asia-Pacific NE"),
    ("aps", "Asia-Pacific S"), ("ape", "Asia-Pacific E"), ("ap", "Asia-Pacific"),
    ("use", "US-East"), ("usw", "US-West"), ("usc", "US-Central"), ("us", "US"),
    ("euw", "EU-West"), ("euc", "EU-Central"), ("eun", "EU-North"), ("eu", "Europe"),
    ("aus", "Australia"),
]


def region_of(shard: str) -> str:
    """Just the region, e.g. US-East."""
    match = _SHARD.match(shard)
    body = match["region"] if match else (shard.split("_", 1)[-1])
    for prefix, name in _REGIONS:
        if body.startswith(prefix):
            return name
    return "unknown"


def server_name(shard: str) -> str:
    """The shard as something readable, e.g. "US-East 1B  #120".

    Star Citizen gives its servers no names of their own, so this is built from
    the parts of the shard id that identify one: region, availability zone and
    instance number. The same shard reaches the same address every time, so
    this names the machine your ship is parked on.
    """
    match = _SHARD.match(shard)
    if not match:
        return shard
    zone = match["zone"].upper()
    number = match["number"].lstrip("0") or "0"
    return f"{region_of(shard)} {zone}  #{number}"


def _parse_stamp(raw: bytes) -> datetime:
    """The log writes UTC; show it in local time, which is what you remember."""
    text = raw.decode("ascii").replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone()


@dataclass
class Session:
    shard: str
    server: str
    joined: datetime
    ended: datetime
    ongoing: bool = False

    @property
    def region(self) -> str:
        return region_of(self.shard)

    @property
    def name(self) -> str:
        return server_name(self.shard)

    @property
    def seconds(self) -> float:
        return max(0.0, (self.ended - self.joined).total_seconds())

    @property
    def duration(self) -> str:
        total = int(self.seconds)
        hours, minutes = divmod(total // 60, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m"
        return f"{total}s"


def _last_stamp(handle, size: int) -> datetime | None:
    """The final timestamp, read from the tail rather than the whole file."""
    handle.seek(max(0, size - _TAIL_BYTES))
    found = _STAMP.findall(handle.read())
    return _parse_stamp(found[-1]) if found else None


def sessions_in(path: Path, live: bool = False) -> list[Session]:
    """Every shard this log records, each running until the next one starts."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            body = handle.read()
            joins = list(_JOIN.finditer(body))
            if not joins:
                return []
            finished = _last_stamp(handle, size)
    except OSError:
        return []

    out: list[Session] = []
    for index, match in enumerate(joins):
        joined = _parse_stamp(match["stamp"])
        if index + 1 < len(joins):
            ended = _parse_stamp(joins[index + 1]["stamp"])
            ongoing = False
        else:
            ended = finished or joined
            ongoing = live
        out.append(Session(match["shard"].decode(),
                           f"{match['ip'].decode()}:{match['port'].decode()}",
                           joined, ended, ongoing))
    return out


DEFAULT_LIMIT = 10


def log_files(live_dir: Path) -> list[tuple[Path, bool]]:
    """Every log worth reading, newest first. The flag marks the live one."""
    found: list[tuple[Path, bool]] = []
    current = live_dir / "Game.log"
    if current.exists():
        found.append((current, True))

    others = list(live_dir.glob("Game(*).log"))
    backups = live_dir / "logbackups"
    if backups.is_dir():
        others += backups.glob("*.log")
    try:
        others.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        others.sort(reverse=True)
    return found + [(p, False) for p in others]


def collect(live_dir, limit: int = DEFAULT_LIMIT) -> list[Session]:
    """The last few shards you were on, newest first.

    Reads newest logs first and stops once there are enough, rather than
    working through months of backups to throw nearly all of it away.
    """
    found: list[Session] = []
    for path, live in log_files(Path(live_dir)):
        found.extend(sessions_in(path, live=live))
        if len(found) >= limit:
            break
    found.sort(key=lambda item: item.joined, reverse=True)
    return found[:limit]

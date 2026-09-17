"""Pull the game's default bindings and English strings out of Data.p4k.

    py -3 data\\keybinds\\extract_defaults.py [--live <LIVE folder>] [--out <folder>]

A maintainer's tool, run once per game patch; the app never runs it. It
needs Python 3.14 or newer for `compression.zstd` and nothing else.

Data.p4k is a ZIP64 archive with two CIG twists: a custom extra field in
every central-directory entry that makes the standard library's zipfile
refuse the whole archive, and compression method 100, which is Zstandard.
Some entries are encrypted as well; the two this wants are not, so the
whole job is: read the central directory by hand, find the two names, seek
to their data, and hand the bytes to zstd. About 2.6 MB read from a 157 GB
file; a second or so.

It writes defaultProfile.xml (the binding defaults, ~185 KB of XML) and
global.ini (every UI string, ~10 MB) into --out, which defaults to a
`_extracted` folder beside this script (git-ignored). build_defaults.py
turns those into the two small files that actually ship.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

WANTED = {
    "Data\\Libs\\Config\\defaultProfile.xml": "defaultProfile.xml",
    "Data\\Localization\\english\\global.ini": "global.ini",
}

ZSTD_METHOD = 100
ZIP64_EXTRA = 0x0001


def find_live() -> Path | None:
    """The LIVE folder, the way the app finds it - from the launcher's log."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from helper.net import find_game_log
    except ImportError:
        return None
    log = find_game_log()
    return log.parent if log else None


def central_directory(handle):
    """Yield (name, method, flags, csize, usize, local_header_offset) for every entry."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(max(0, size - 65536 - 22))
    tail = handle.read()
    locator = tail.rfind(b"PK\x06\x07")
    if locator < 0:
        raise SystemExit("no ZIP64 end-of-central-directory locator: is this Data.p4k?")
    eocd64 = struct.unpack("<Q", tail[locator + 8:locator + 16])[0]
    handle.seek(eocd64)
    record = handle.read(56)
    if record[:4] != b"PK\x06\x06":
        raise SystemExit("ZIP64 end record not where the locator said")
    entries, cd_size, cd_offset = struct.unpack("<QQQ", record[32:56])
    handle.seek(cd_offset)
    data = handle.read(cd_size)
    pos = 0
    for _ in range(entries):
        if data[pos:pos + 4] != b"PK\x01\x02":
            raise SystemExit("central directory lost its shape at byte %d" % pos)
        flags, method = struct.unpack("<HH", data[pos + 8:pos + 12])
        csize, usize = struct.unpack("<II", data[pos + 20:pos + 28])
        nlen, elen, clen = struct.unpack("<HHH", data[pos + 28:pos + 34])
        offset = struct.unpack("<I", data[pos + 42:pos + 46])[0]
        name = data[pos + 46:pos + 46 + nlen]
        extra = data[pos + 46 + nlen:pos + 46 + nlen + elen]
        # Sizes and offset past 4 GB live in the ZIP64 extra, in this order,
        # each present only if its 32-bit field is the 0xFFFFFFFF marker.
        q = 0
        while q + 4 <= len(extra):
            eid, esz = struct.unpack("<HH", extra[q:q + 4])
            body = extra[q + 4:q + 4 + esz]
            if eid == ZIP64_EXTRA:
                r = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
                if offset == 0xFFFFFFFF:
                    offset = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
            q += 4 + esz
        yield name, method, flags, csize, usize, offset
        pos += 46 + nlen + elen + clen


def extract(p4k: Path, out: Path) -> None:
    try:
        from compression import zstd
    except ImportError:
        raise SystemExit("compression.zstd needs Python 3.14 or newer (this is %s)."
                         % sys.version.split()[0])
    out.mkdir(parents=True, exist_ok=True)
    wanted = {name.encode("utf-8"): target for name, target in WANTED.items()}
    with open(p4k, "rb") as handle:
        found = {}
        for name, method, flags, csize, usize, offset in central_directory(handle):
            if name in wanted:
                found[name] = (method, flags, csize, usize, offset)
                if len(found) == len(wanted):
                    break
        for name, target in wanted.items():
            if name not in found:
                raise SystemExit("not in the archive: %s" % name.decode())
            method, flags, csize, usize, offset = found[name]
            if flags & 1:
                raise SystemExit("%s is encrypted in this build; this tool cannot read it." % target)
            if method != ZSTD_METHOD:
                raise SystemExit("%s uses compression method %d, not zstd (%d)." % (target, method, ZSTD_METHOD))
            handle.seek(offset)
            local = handle.read(30)
            if local[:4] != b"PK\x03\x04":
                raise SystemExit("local header for %s is not where the directory said" % target)
            nlen, elen = struct.unpack("<HH", local[26:30])
            handle.seek(offset + 30 + nlen + elen)
            raw = handle.read(csize)
            data = zstd.decompress(raw)
            if len(data) != usize:
                raise SystemExit("%s: got %d bytes, expected %d" % (target, len(data), usize))
            (out / target).write_bytes(data)
            print("%-20s %10s bytes -> %s" % (target, format(len(data), ","), out / target))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", type=Path, help="the game's LIVE folder (default: found from the launcher log)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "_extracted")
    args = parser.parse_args(argv)
    live = args.live or find_live()
    if live is None:
        raise SystemExit("Star Citizen not found; pass --live <path to LIVE>.")
    p4k = live / "Data.p4k"
    if not p4k.is_file():
        raise SystemExit("no Data.p4k in %s" % live)
    extract(p4k, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Turn a session into telemetry batches, and put them on disk.

Nothing here talks to a network. Batches are written locally so they can be
opened and read before anybody is asked to send one - the format has to earn
trust as a file first.

Three ideas do most of the work.

**Allowlist, never filter.** A field reaches a payload because its name is in
one of the tuples below and a builder put it there. There is no path that
copies a dict through, so a future log format cannot introduce a field nobody
decided to send. The game log carries the player's handle, account id and a
persistent numeric id; none of them can arrive here by accident.

**Hoist what repeats.** Location, shard, build and machine barely change
within a minute, so they are written once per batch as context and a new batch
starts when any of them does. Measured on real payloads that is a 5x saving,
larger than any choice of wire format.

**Per second, summarised per batch.** Rows carry what a single second can say
honestly - a frame rate, a mean and a worst frame. A 1% low over sixty frames
is one frame, which is not a percentile, so the percentile figures are
computed once per batch over every frame in it.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .gamecfg import graphics_settings, upscaler
from .location import LocationReader, UNKNOWN as NOWHERE

SCHEMA = 1

#: One row per second; a batch closes at this many, or sooner if the context
#: changes. Sixty keeps HTTP framing down to a third of what ten would cost.
BATCH_SECONDS = 60
SAMPLE_SECONDS = 1.0

#: Keep a fortnight, and never more than this on disk.
KEEP_DAYS = 14
MAX_BYTES = 32 * 1024 * 1024

# --- the allowlist --------------------------------------------------------
#
# These names are the schema. Nothing else is ever written.

PROFILE_FIELDS = ("machine_id", "cpu", "cpu_mhz_nominal", "cores", "gpu",
                  "ram_mb", "screen", "os_build")

VIDEO_FIELDS = ("upscaler", "dlss_support")

#: Graphics settings are allowlisted in gamecfg, where the file is read. This
#: is the second gate: whatever arrives must still look like a setting - a
#: plain name and a scalar value - before it can be written.
_RE_SETTING = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")

CONTEXT_FIELDS = ("system", "body", "site", "detail", "path", "depth", "kind",
                  "source", "region", "instance", "build", "frame_source")

#: Positional, so a stray field cannot ride along in a row.
ROW_FIELDS = ("dt", "fps", "frame_ms", "worst_ms", "cpu_mhz", "gpu_mhz",
              "ping_ms")

SUMMARY_FIELDS = ("frames", "fps_avg", "low_1", "min_fps", "swing_ms",
                  "swing_pct", "stutter_pct", "ping_avg", "jitter_ms",
                  "loss_pct")

_RE_BUILD = re.compile(rb"Game Version Identifier:\s*([0-9a-f\-]{8,40})")


def game_build(log_path: Path | None) -> str:
    """The build the client is running, so patches can be compared.

    Written near the top of the log, so only the head is read.
    """
    if not log_path:
        return ""
    try:
        with log_path.open("rb") as handle:
            found = _RE_BUILD.search(handle.read(64 * 1024))
    except OSError:
        return ""
    return found.group(1).decode("ascii", "replace") if found else ""


# --- one second -----------------------------------------------------------

@dataclass
class Second:
    """What a single second can say for itself."""

    dt: int = 0
    fps: float = 0.0
    frame_ms: float = 0.0
    worst_ms: float = 0.0
    cpu_mhz: int = 0
    gpu_mhz: int = 0
    ping_ms: float = 0.0
    #: Every frame drawn in this second, kept for the batch percentiles and
    #: never written out - a per-second 1% low is one frame, not a percentile.
    frames: list[float] = field(default_factory=list)

    def as_row(self) -> list:
        return [round(getattr(self, name), 2) if isinstance(getattr(self, name), float)
                else getattr(self, name) for name in ROW_FIELDS]


def _percentiles(frames: list[float]) -> dict:
    """The consistency figures, over every frame in the batch."""
    if len(frames) < 2:
        return {name: 0.0 for name in SUMMARY_FIELDS[:7]}
    ordered = sorted(frames, reverse=True)
    worst_count = max(1, len(ordered) // 100)
    worst_mean = sum(ordered[:worst_count]) / worst_count
    mean = sum(frames) / len(frames)
    gaps = [abs(b - a) for a, b in zip(frames, frames[1:])]
    swing = sum(gaps) / len(gaps) if gaps else 0.0
    middle = sorted(frames)[len(frames) // 2]
    return {
        "frames": len(frames),
        "fps_avg": round(1000.0 / mean, 2) if mean else 0.0,
        "low_1": round(1000.0 / worst_mean, 2) if worst_mean else 0.0,
        "min_fps": round(1000.0 / ordered[0], 2) if ordered[0] else 0.0,
        "swing_ms": round(swing, 3),
        "swing_pct": round(100.0 * swing / mean, 2) if mean else 0.0,
        "stutter_pct": round(100.0 * sum(1 for ms in frames if ms > 2 * middle)
                             / len(frames), 2),
    }


# --- building a batch -----------------------------------------------------

def build_context(place, net_stats, build: str, frame_source: str) -> dict:
    """The context block, assembled a named field at a time."""
    fields = place.as_fields() if place else NOWHERE.as_fields()
    context = {name: fields.get(name, "") for name in CONTEXT_FIELDS}
    context["region"] = getattr(net_stats, "region", "") or ""
    context["instance"] = _instance_of(getattr(net_stats, "shard", ""))
    context["build"] = build
    context["frame_source"] = frame_source
    return context


_RE_INSTANCE = re.compile(r"_(\d+)$")


def _instance_of(shard: str) -> int:
    """The server number out of a shard name, or 0.

    The build number in the middle of a shard id changes every patch, so it is
    no part of the server's identity and is not kept.
    """
    found = _RE_INSTANCE.search(shard or "")
    return int(found.group(1)) if found else 0


def build_batch(client: str, session: str, t0: int, context: dict,
                seconds: list[Second]) -> dict:
    """One batch: context once, a row per second, percentiles over the lot."""
    frames: list[float] = []
    for second in seconds:
        frames.extend(second.frames)
    summary = _percentiles(frames)
    pings = [s.ping_ms for s in seconds if s.ping_ms > 0]
    summary["ping_avg"] = round(sum(pings) / len(pings), 2) if pings else 0.0
    summary["jitter_ms"] = 0.0
    summary["loss_pct"] = 0.0
    return {
        "type": "batch",
        "schema": SCHEMA,
        "client": client,
        "session": session,
        "t0": t0,
        "ctx": {name: context.get(name, "") for name in CONTEXT_FIELDS},
        "sum": {name: summary.get(name, 0.0) for name in SUMMARY_FIELDS},
        "rows": [s.as_row() for s in seconds],
    }


def safe_settings(settings: dict) -> dict:
    """Keep only entries that still look like a setting, whatever was passed."""
    out = {}
    for name, value in sorted((settings or {}).items()):
        if _RE_SETTING.match(str(name)) and isinstance(value, (int, float, bool)):
            out[str(name)] = value
    return out


def build_profile(client: str, session: str, t0: int, machine: dict,
                  gfx: dict | None = None, video: dict | None = None) -> dict:
    """The machine and how the game is set up.

    Written at the start of a session, and again whenever the graphics
    settings change - a settings change mid-session is exactly the moment the
    frame rate stops being comparable with what came before it.
    """
    video = video or {}
    return {
        "type": "profile",
        "schema": SCHEMA,
        "client": client,
        "session": session,
        "t0": t0,
        "machine": {name: machine.get(name, "") for name in PROFILE_FIELDS},
        "gfx": safe_settings(gfx),
        "video": {name: str(video.get(name, "")) for name in VIDEO_FIELDS},
    }


# --- writing --------------------------------------------------------------

class Spool:
    """Gzipped NDJSON, one file a day, pruned by age and by total size.

    Appending opens a fresh gzip member rather than rewriting the file, which
    readers join back together transparently - so a crash costs at most the
    record being written.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def write(self, record: dict) -> bool:
        line = json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with gzip.open(self._today(), "ab") as handle:
                handle.write(line)
        except OSError:
            return False
        return True

    def _today(self) -> Path:
        return self.directory / (time.strftime("%Y-%m-%d") + ".ndjson.gz")

    def files(self) -> list[Path]:
        try:
            return sorted(self.directory.glob("*.ndjson.gz"))
        except OSError:
            return []

    def read_all(self) -> list[dict]:
        """Every record on disk, oldest first. For looking at your own data."""
        out: list[dict] = []
        for path in self.files():
            try:
                with gzip.open(path, "rb") as handle:
                    for line in handle:
                        line = line.strip()
                        if line:
                            out.append(json.loads(line))
            except (OSError, ValueError):
                continue
        return out

    def prune(self, keep_days: int = KEEP_DAYS, max_bytes: int = MAX_BYTES) -> int:
        """Drop the oldest files past either limit. Returns how many went."""
        files = self.files()
        cutoff = time.time() - keep_days * 86400
        removed = 0
        for path in list(files):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    files.remove(path)
                    removed += 1
            except OSError:
                continue
        while files:
            try:
                total = sum(p.stat().st_size for p in files)
            except OSError:
                break
            if total <= max_bytes:
                break
            try:
                files[0].unlink()
                removed += 1
            except OSError:
                break
            files.pop(0)
        return removed


# --- collecting -----------------------------------------------------------

class TelemetryCollector(threading.Thread):
    """Samples once a second and spools a batch a minute.

    Everything it needs arrives as a callable, so it can be driven by fake
    monitors in a test without a game, a window or a network.
    """

    def __init__(self, spool: Spool, fps_stats, net_stats, hardware,
                 machine: dict, log_path=None, client_id: str = "",
                 enabled=None, live_dir=None) -> None:
        super().__init__(name="telemetry", daemon=True)
        self.spool = spool
        self._fps_stats = fps_stats
        self._net_stats = net_stats
        self._hardware = hardware
        self._machine = machine
        self._log_path = log_path
        self._live_dir = live_dir
        self._enabled = enabled or (lambda: True)

        self.client = client_id or uuid.uuid4().hex
        self.session = uuid.uuid4().hex[:16]
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._reader = LocationReader()
        self._seconds: list[Second] = []
        self._context: dict | None = None
        self._t0 = 0
        self._build = ""
        self._gfx: dict = {}
        self._video: dict = {}
        self._profile_key = None
        self._wrote_profile = False
        self.batches_written = 0

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick(time.time(), time.monotonic())
            except Exception:            # noqa: BLE001 - never take the app down
                pass
            self._stop.wait(max(0.0, SAMPLE_SECONDS - (time.monotonic() - started)))
        self.flush()

    def shutdown(self) -> None:
        self._stop.set()
        self.flush()

    # -- one sample --------------------------------------------------------

    def tick(self, wall: float, mono: float) -> None:
        """Take one second's reading. Called on the thread, or by a test."""
        if not self._enabled():
            self.flush()
            return

        fps = self._fps_stats()
        if getattr(fps, "status", "") != "ok":
            self.flush()                 # not in a game; do not fabricate a gap
            return

        net = self._net_stats()
        if not self._build:
            self._build = game_build(self._log_path() if callable(self._log_path)
                                     else self._log_path)
        place = self._read_place(mono)
        context = build_context(place, net, self._build,
                                "rtss" if getattr(fps, "per_frame", False) else "sampled")

        with self._lock:
            if self._context is not None and context != self._context:
                self._flush_locked()
            if self._context is None:
                self._context, self._t0 = context, int(wall)
            self._seconds.append(self._second(fps, net, len(self._seconds)))
            if len(self._seconds) >= BATCH_SECONDS:
                self._flush_locked()

    def _read_place(self, mono: float):
        path = self._log_path() if callable(self._log_path) else self._log_path
        if not path:
            return NOWHERE
        return self._reader.read(Path(path), mono)

    def _second(self, fps, net, dt: int) -> Second:
        """Only the frames from the last second, so windows do not overlap."""
        recent = [ms for age, ms in getattr(fps, "frame_times", []) if age <= SAMPLE_SECONDS]
        cpu_mhz, gpu_mhz = (self._hardware() if callable(self._hardware) else (0, 0))
        total = sum(recent)
        return Second(
            dt=dt,
            fps=(1000.0 * len(recent) / total) if total else float(getattr(fps, "fps", 0.0)),
            frame_ms=(total / len(recent)) if recent else float(getattr(fps, "frame_time_ms", 0.0)),
            worst_ms=max(recent) if recent else 0.0,
            cpu_mhz=int(cpu_mhz or 0),
            gpu_mhz=int(gpu_mhz or 0),
            ping_ms=float(getattr(net, "ping_ms", 0.0) or 0.0),
            frames=recent,
        )

    # -- writing out -------------------------------------------------------

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _write_profile_if_changed(self) -> None:
        """Emit a profile the first time, and again if the settings moved."""
        gfx, video = self._read_gamecfg()
        key = (tuple(sorted(gfx.items())), tuple(sorted(video.items())))
        if self._wrote_profile and key == self._profile_key:
            return
        record = build_profile(self.client, self.session, self._t0,
                               self._machine, gfx, video)
        if self.spool.write(record):
            self._wrote_profile = True
            self._profile_key = key
            self._gfx, self._video = gfx, video

    def _read_gamecfg(self) -> tuple[dict, dict]:
        live = self._live_dir() if callable(self._live_dir) else self._live_dir
        log = self._log_path() if callable(self._log_path) else self._log_path
        gfx = graphics_settings(live) if live else {}
        return gfx, (upscaler(log) if log else {})

    def _flush_locked(self) -> None:
        if not self._seconds or self._context is None:
            self._seconds, self._context = [], None
            return
        self._write_profile_if_changed()
        batch = build_batch(self.client, self.session, self._t0,
                            self._context, self._seconds)
        if self.spool.write(batch):
            self.batches_written += 1
        self._seconds, self._context = [], None

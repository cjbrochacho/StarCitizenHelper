"""Send the spooled batches, and survive not being able to.

The spool written in telemetry.py is already a durable queue, so this does not
invent a second one. It walks the files, posts what has not gone yet, and keeps
a cursor. If the server is down the batches simply stay on disk and go later;
if it is down for a fortnight they age out with everything else, which is the
only data loss here and it is bounded by the spool's own limits.

The cursor is a byte offset into each gzip file, which works because appending
a record opens a new gzip member: seeking to a recorded offset lands exactly on
a member boundary, and everything after it is readable on its own. That makes
resuming cheap - a long session does not re-read a day of history every minute.

Being wrong about the cursor is survivable. The server keys a batch on who sent
it and when it started, so a record sent twice is stored once. The cursor is an
optimisation, not the thing that keeps the data honest.

Nothing here runs on the UI thread, nothing here is unbounded, and no failure
path raises: the tool measures how smoothly the game runs and must never be the
reason it does not.
"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import socket
import threading
import time
import urllib.parse
from pathlib import Path

#: How often to look for something to send. The collector closes a batch a
#: minute, so anything faster is just a wasted wakeup.
POLL_SECONDS = 30.0

#: One request carries at most this much. A batch is around a kilobyte, so
#: fifty of them is a small post and a whole session's backlog is a few.
MAX_RECORDS = 50
MAX_BODY_BYTES = 256 * 1024

CONNECT_TIMEOUT = 20.0

#: Backoff after a failure, doubling to the cap. The first wait is long enough
#: that a restarting server is not hammered, short enough to catch it coming
#: back within a session.
BACKOFF_START = 30.0
BACKOFF_MAX = 15 * 60.0

STATUS_IDLE = "idle"
STATUS_OFF = "off"
STATUS_SENDING = "sending"
STATUS_OK = "ok"
STATUS_WAITING = "waiting"
STATUS_STOPPED = "stopped"


def cursor_file(root: Path) -> Path:
    return Path(root) / "assets" / ".uploaded"


class Cursor:
    """How far into each spool file has been sent."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._marks: dict[str, int] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._marks = {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError):
            self._marks = {}

    def save(self, known: set[str] | None = None) -> None:
        # Files age out of the spool, and their marks should go with them
        # rather than accumulating for the life of the install.
        if known is not None:
            self._marks = {k: v for k, v in self._marks.items() if k in known}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._marks), encoding="utf-8")
        except OSError:
            pass

    def get(self, name: str) -> int:
        return self._marks.get(name, 0)

    def set(self, name: str, offset: int) -> None:
        self._marks[name] = offset


def read_since(path: Path, offset: int) -> tuple[list[dict], int]:
    """Records after a byte offset, and the offset to record next time.

    Returns nothing and the offset unchanged if the file is mid-write - the
    next pass will pick it up, and a partial read is not worth guessing at.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size <= offset:
        return [], min(offset, size)

    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read()
    except OSError:
        return [], offset

    records: list[dict] = []
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as unzipped:
            for line in unzipped:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except (OSError, EOFError, ValueError, gzip.BadGzipFile):
        # A member still being written. Leave the cursor where it is.
        return [], offset
    return records, size


class Uploader(threading.Thread):
    """Posts spooled records, on its own thread, forgiving of the network."""

    def __init__(self, spool, root, url_provider, enabled=None,
                 on_stop=None, on_event=None, clock=None) -> None:
        super().__init__(name="telemetry-upload", daemon=True)
        self.spool = spool
        self.root = Path(root)
        self._url = url_provider
        self._enabled = enabled or (lambda: True)
        self._on_stop = on_stop or (lambda: None)
        self._on_event = on_event or (lambda message: None)
        # One clock, taken once. Backoff is written in one place and read in
        # another, and a deadline stamped on one clock but compared against a
        # different one is a bug that only shows up under load or under test.
        self._clock = clock or time.monotonic

        self.cursor = Cursor(cursor_file(self.root))
        # Named _stopping, not _stop: threading.Thread has a private _stop()
        # that join() calls, and shadowing it with an Event makes join() raise
        # TypeError on a perfectly ordinary Thread.
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._conn = None
        self._conn_key = None
        self._backoff = 0.0
        self._retry_at = 0.0

        self.status = STATUS_IDLE
        self.sent = 0
        self.failures = 0
        self.last_error = ""

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.tick()
            except Exception as exc:            # noqa: BLE001 - never take the app down
                self._note(STATUS_WAITING, str(exc))
            self._stopping.wait(POLL_SECONDS)
        self._close()

    def shutdown(self) -> None:
        self._stopping.set()
        self._close()

    def _note(self, status: str, error: str = "") -> None:
        with self._lock:
            self.status = status
            if error:
                self.last_error = error[:200]

    # -- one pass ----------------------------------------------------------

    def tick(self) -> int:
        """Send what is waiting. Returns how many records went."""
        now = self._clock()
        url = (self._url() or "").strip()
        if not url or not self._enabled():
            self._note(STATUS_OFF)
            return 0
        if now < self._retry_at:
            self._note(STATUS_WAITING)
            return 0

        files = self.spool.files()
        names = {f.name for f in files}
        sent = 0
        for path in files:
            if self._stopping.is_set():
                break
            records, end = read_since(path, self.cursor.get(path.name))
            if not records:
                self.cursor.set(path.name, end)
                continue
            self._note(STATUS_SENDING)
            for chunk in _chunks(records):
                verdict = self._post(url, chunk)
                if verdict == "stop":
                    self._on_stop()
                    self._note(STATUS_STOPPED)
                    self.cursor.save(names)
                    return sent
                if verdict != "ok":
                    self._fail()
                    self.cursor.save(names)
                    return sent
                sent += len(chunk)
            self.cursor.set(path.name, end)

        self.cursor.save(names)
        if sent:
            with self._lock:
                self.sent += sent
            self._succeed()
        else:
            self._note(STATUS_OK if self.sent else STATUS_IDLE)
        return sent

    def _succeed(self) -> None:
        self._backoff = 0.0
        self._retry_at = 0.0
        self._note(STATUS_OK)

    def _fail(self) -> None:
        self._backoff = min(BACKOFF_MAX, self._backoff * 2 or BACKOFF_START)
        self._retry_at = self._clock() + self._backoff
        with self._lock:
            self.failures += 1
        self._close()
        self._note(STATUS_WAITING)

    # -- the connection ----------------------------------------------------

    def _connect(self, url: str):
        """One connection, held open. Reconnecting per post would spend a TLS
        handshake on every batch, which is several kilobytes for one."""
        parts = urllib.parse.urlsplit(url)
        key = (parts.scheme, parts.hostname, parts.port)
        if self._conn is not None and self._conn_key == key:
            return self._conn, parts
        self._close()
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.hostname, parts.port,
                                               timeout=CONNECT_TIMEOUT)
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port,
                                              timeout=CONNECT_TIMEOUT)
        self._conn, self._conn_key = conn, key
        return conn, parts

    def _close(self) -> None:
        conn, self._conn, self._conn_key = self._conn, None, None
        if conn is not None:
            try:
                conn.close()
            except Exception:                    # noqa: BLE001
                pass

    def _post(self, url: str, records: list[dict]) -> str:
        """Send one body. Returns the server's verb, or "error"."""
        body = gzip.compress(
            b"\n".join(json.dumps(r, separators=(",", ":")).encode("utf-8")
                       for r in records), 6)
        path = urllib.parse.urlsplit(url).path or "/v1/ingest"
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(body)),
            "User-Agent": "StarCitizenHelper",
        }
        for attempt in (1, 2):
            try:
                conn, _ = self._connect(url)
                conn.request("POST", path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                return self._verdict(response.status, payload)
            except (http.client.HTTPException, OSError, socket.timeout) as exc:
                # A kept-open connection the server has since closed fails on
                # first use and works on the second, so one retry is worth it.
                self._close()
                if attempt == 2:
                    self._note(STATUS_WAITING, "%s: %s" % (type(exc).__name__, exc))
                    return "error"
        return "error"

    def _verdict(self, status: int, payload: bytes) -> str:
        try:
            answer = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            answer = {}
        verb = str(answer.get("status", ""))

        if verb == "stop":
            return "stop"
        if status == 200:
            return "ok"
        if status in (400, 413, 422):
            # The server will never accept this. Dropping it is the only way
            # not to send it forever; the cursor moves past it either way.
            self._note(STATUS_WAITING, "refused: %s" % (answer.get("reason") or status))
            return "ok"
        self._note(STATUS_WAITING, "http %d" % status)
        return "error"

    # -- reading out -------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {"status": self.status, "sent": self.sent,
                    "failures": self.failures, "error": self.last_error,
                    "waiting": max(0.0, self._retry_at - self._clock())}


def _chunks(records: list[dict]):
    """Split into bodies small enough to be one polite request each."""
    batch: list[dict] = []
    size = 0
    for record in records:
        length = len(json.dumps(record, separators=(",", ":")))
        if batch and (len(batch) >= MAX_RECORDS or size + length > MAX_BODY_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += length
    if batch:
        yield batch

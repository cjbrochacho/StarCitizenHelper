"""Latency to the game's datacenter, plus which server/shard we are on.

The sim server itself answers nothing - not ICMP, not TCP on any port (checked).
So latency is measured to the CIG backend host the game holds a TLS connection
to, which sits in the same cloud region as the sim server and does answer ICMP.
That is the closest thing to "ping to the server" that is actually reachable,
and it needs no elevation: IcmpSendEcho is what the ordinary ping command uses.

Server, port, shard and region come from the game's own log - the <Join PU>
line names all of them. Player population is deliberately not shown: the client
is only ever told about itself and the entities streamed in around it, never the
shard head-count, so any number here would be invented.
"""

from __future__ import annotations

import ctypes
import os
import re
import socket
import struct
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

WINDOW_SECONDS = 60.0
#: Matches the frame-rate poll so both series on the graph move together.
#: Measured against the live target: 10 Hz draws no rate limiting and no loss.
PING_INTERVAL = 0.1
#: Finding the game means enumerating every process, which is far too heavy to
#: repeat ten times a second, so the result is held between checks.
_PID_REFRESH_SECONDS = 2.0
_LOG_POLL_SECONDS = 3.0
_PING_TIMEOUT_MS = 1500

STATUS_NO_GAME = "no_game"
STATUS_NO_TARGET = "no_target"
STATUS_OK = "ok"

#: CIG shard names start pub_<region><n><letter>_..., e.g. pub_use1b_12326004.
#: Map the region token to something human. Order matters: longest first.
_REGIONS = [
    ("use", "US-East"),
    ("usw", "US-West"),
    ("usc", "US-Central"),
    ("euc", "EU-Central"),
    ("eun", "EU-North"),
    ("eu", "Europe"),
    ("apse", "Asia-Pacific"),
    ("aps", "Asia-Pacific"),
    ("ap", "Asia-Pacific"),
    ("aus", "Australia"),
]


# --- ICMP ----------------------------------------------------------------

class _IcmpEchoReply(ctypes.Structure):
    _fields_ = [
        ("Address", ctypes.c_uint32),
        ("Status", wintypes.DWORD),
        ("RoundTripTime", wintypes.DWORD),
        ("DataSize", wintypes.WORD),
        ("Reserved", wintypes.WORD),
        ("Data", ctypes.c_void_p),
        ("Options", ctypes.c_byte * 8),
    ]


iphlpapi.IcmpCreateFile.restype = wintypes.HANDLE
iphlpapi.IcmpCloseHandle.argtypes = (wintypes.HANDLE,)
iphlpapi.IcmpSendEcho.argtypes = (wintypes.HANDLE, ctypes.c_uint32, ctypes.c_void_p,
                                  wintypes.WORD, ctypes.c_void_p, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD)
iphlpapi.IcmpSendEcho.restype = wintypes.DWORD


class Pinger:
    """Non-elevated ICMP echo. Returns round-trip milliseconds, or None."""

    def __init__(self) -> None:
        self._handle = iphlpapi.IcmpCreateFile()
        self._payload = b"sctools-latency-probe"
        self._reply = ctypes.create_string_buffer(ctypes.sizeof(_IcmpEchoReply)
                                                  + len(self._payload) + 8)

    def ping(self, ip: str, timeout_ms: int = _PING_TIMEOUT_MS) -> float | None:
        """Round trip in milliseconds, or None if there was no reply.

        The reply's own RoundTripTime field is whole milliseconds, which is too
        coarse to show a decimal or to measure jitter with - successive pings
        differ by less than the quantisation step. Timing the call instead
        gives real sub-millisecond resolution. It reads a fraction of a
        millisecond high, because the measurement includes the API call as
        well as the network, but that offset is constant and small next to any
        real latency.
        """
        if not self._handle or self._handle == wintypes.HANDLE(-1).value:
            return None
        try:
            addr = struct.unpack("<I", socket.inet_aton(ip))[0]
        except OSError:
            return None

        started = time.perf_counter()
        got = iphlpapi.IcmpSendEcho(self._handle, addr, self._payload, len(self._payload),
                                    None, self._reply, ctypes.sizeof(self._reply), timeout_ms)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not got:
            return None
        reply = _IcmpEchoReply.from_buffer_copy(self._reply)
        if reply.Status != 0:
            return None
        return elapsed_ms

    def close(self) -> None:
        if self._handle:
            iphlpapi.IcmpCloseHandle(self._handle)
            self._handle = None


# --- the game's own TCP peers -------------------------------------------

_TCP_TABLE_OWNER_PID_CONNECTIONS = 5
_MIB_TCP_STATE_ESTAB = 5

iphlpapi.GetExtendedTcpTable.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                         wintypes.BOOL, wintypes.ULONG,
                                         ctypes.c_int, wintypes.ULONG)


class _MibTcpRowOwnerPid(ctypes.Structure):
    _fields_ = [("state", wintypes.DWORD), ("localAddr", wintypes.DWORD),
                ("localPort", wintypes.DWORD), ("remoteAddr", wintypes.DWORD),
                ("remotePort", wintypes.DWORD), ("owningPid", wintypes.DWORD)]


TH32CS_SNAPPROCESS = 2
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


def process_pid(exe: str) -> int:
    """PID of a running executable by name, or 0.

    Enumerating processes beats looking the game up by window: it still works
    when the window cannot be found, and it cannot be fooled by another window
    that happens to have "Star Citizen" in its title.
    """
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return 0
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        wanted = exe.casefold()
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == wanted:
                return entry.th32ProcessID
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return 0


def established_peers(pid: int, remote_port: int = 443) -> list[str]:
    """Remote IPs the given process holds established TCP connections to."""
    if not pid:
        return []
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, socket.AF_INET,
                                 _TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
    if not size.value:
        return []
    buffer = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, socket.AF_INET,
                                    _TCP_TABLE_OWNER_PID_CONNECTIONS, 0) != 0:
        return []

    count = struct.unpack("<I", buffer.raw[:4])[0]
    row_size = ctypes.sizeof(_MibTcpRowOwnerPid)
    peers: list[str] = []
    for index in range(count):
        row = _MibTcpRowOwnerPid.from_buffer_copy(buffer.raw[4 + index * row_size:
                                                             4 + (index + 1) * row_size])
        if row.owningPid != pid or row.state != _MIB_TCP_STATE_ESTAB:
            continue
        if socket.ntohs(row.remotePort & 0xFFFF) != remote_port:
            continue
        peers.append(socket.inet_ntoa(struct.pack("<I", row.remoteAddr)))
    return peers


# --- log parsing ---------------------------------------------------------

_JOIN_RE = re.compile(r"<Join PU> address\[([0-9.]+)\] port\[(\d+)\] shard\[([a-z0-9_]+)\]")


def region_of(shard: str) -> str:
    body = shard.split("_", 1)[1] if "_" in shard else shard  # drop the "pub_" tag
    for prefix, name in _REGIONS:
        if body.startswith(prefix):
            return name
    return "unknown"


class JoinReader:
    """Follows Game.log for <Join PU> lines, reading only newly appended bytes.

    The join happens once, near the top of a session's log, so a tail window
    would miss it in a long session. This scans the whole file on first read,
    then only the bytes added since - and resets when the log is replaced by a
    new launch (detected as the file shrinking).
    """

    def __init__(self) -> None:
        self._offset = 0
        self._latest: tuple[str, int, str] | None = None

    def read(self, log_path: Path) -> tuple[str, int, str] | None:
        try:
            size = log_path.stat().st_size
            if size < self._offset:  # a new session rotated the log
                self._offset, self._latest = 0, None
            with log_path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return self._latest
        for ip, port, shard in _JOIN_RE.findall(chunk.decode("latin-1", errors="replace")):
            self._latest = (ip, int(port), shard)
        return self._latest


def find_game_log() -> Path | None:
    """Locate LIVE\\Game.log from the RSI launcher's recorded library path."""
    store = Path(os.environ.get("APPDATA", "")) / "rsilauncher" / "logs" / "log.log"
    try:
        text = store.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    # The launcher logs the library path as a file URL; pull drive paths out.
    for raw in re.findall(r"[A-Za-z]:[\\/][^\"'<>|?*\n]{2,90}", text.replace("%20", " ")):
        candidate = Path(raw.replace("/", "\\")) / "StarCitizen" / "LIVE" / "Game.log"
        if candidate.exists():
            return candidate
    for guess in (r"C:\Program Files\Roberts Space Industries",
                  r"D:\Program Files\Roberts Space Industries"):
        candidate = Path(guess) / "StarCitizen" / "LIVE" / "Game.log"
        if candidate.exists():
            return candidate
    return None


# --- monitor -------------------------------------------------------------

@dataclass
class NetStats:
    status: str = STATUS_NO_GAME
    ping_ms: float = 0.0
    average: float = 0.0
    jitter: float = 0.0
    loss_pct: float = 0.0
    server: str = ""
    shard: str = ""
    region: str = ""
    target: str = ""
    #: (age seconds, rtt ms), oldest first, for plotting.
    history: list[tuple[float, float]] = field(default_factory=list)


class NetMonitor(threading.Thread):
    def __init__(self, pid_provider=None, process_name: str = "StarCitizen.exe") -> None:
        super().__init__(name="net-monitor", daemon=True)
        self._pid_provider = pid_provider or (lambda: process_pid(process_name))
        self.process_name = process_name
        self._pinger = Pinger()
        self._samples: deque[tuple[float, float | None]] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._log_path: Path | None = None
        self._join_reader = JoinReader()
        self._server = self._shard = self._region = ""
        self._target = ""
        self._target_checked = 0.0
        self._log_checked = 0.0
        self._pid = 0
        self._pid_checked = 0.0

    def run(self) -> None:
        self._log_path = find_game_log()
        while not self._stop.is_set():
            started = time.monotonic()
            self._tick()
            # A round trip takes tens of milliseconds; sleeping the full
            # interval on top of that would stretch the cadence past it.
            if self._stop.wait(max(0.0, PING_INTERVAL - (time.monotonic() - started))):
                break
        self._pinger.close()

    def shutdown(self) -> None:
        self._stop.set()

    # -- work -------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        if not self._pid or now - self._pid_checked > _PID_REFRESH_SECONDS:
            self._pid = self._pid_provider()
            self._pid_checked = now
        pid = self._pid

        if now - self._log_checked > _LOG_POLL_SECONDS:
            self._log_checked = now
            self._refresh_server()

        if pid and (not self._target or now - self._target_checked > 15.0):
            self._target_checked = now
            self._pick_target(pid)

        rtt = self._pinger.ping(self._target) if self._target else None
        with self._lock:
            if pid:
                self._samples.append((now, rtt))
            else:
                self._samples.clear()
                self._target = ""
            cutoff = now - WINDOW_SECONDS
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()

    def _refresh_server(self) -> None:
        if self._log_path is None:
            self._log_path = find_game_log()
        if self._log_path is None:
            return
        joined = self._join_reader.read(self._log_path)
        if joined is None:
            return
        ip, port, shard = joined
        self._server, self._shard = f"{ip}:{port}", shard
        self._region = region_of(shard)

    def _pick_target(self, pid: int) -> None:
        """Choose a reachable CIG host to ping - the sim server never answers.

        Prefer Google Cloud ranges (34./35.), where the game's servers live, so
        latency reflects the game region rather than a Cloudflare CDN edge that
        also happens to answer.
        """
        peers = list(dict.fromkeys(established_peers(pid)))  # dedupe, keep order
        peers.sort(key=lambda ip: 0 if ip.split(".")[0] in ("34", "35") else 1)
        for ip in peers:
            if self._pinger.ping(ip, timeout_ms=800) is not None:
                self._target = ip
                return

    # -- snapshot ---------------------------------------------------------

    def stats(self) -> NetStats:
        now = time.monotonic()
        with self._lock:
            samples = list(self._samples)
            server, shard, region, target = self._server, self._shard, self._region, self._target

        base = NetStats(server=server, shard=shard, region=region, target=target)
        if not samples:
            base.status = STATUS_NO_GAME
            return base
        if not target:
            base.status = STATUS_NO_TARGET
            return base

        replies = [(t, rtt) for t, rtt in samples if rtt is not None]
        base.status = STATUS_OK
        base.loss_pct = 100.0 * (len(samples) - len(replies)) / len(samples)
        if not replies:
            return base

        values = [rtt for _, rtt in replies]
        base.ping_ms = values[-1]
        base.average = sum(values) / len(values)
        # Jitter as mean absolute successive difference - the everyday meaning.
        diffs = [abs(b - a) for a, b in zip(values, values[1:])]
        base.jitter = sum(diffs) / len(diffs) if diffs else 0.0
        base.history = [(now - t, rtt) for t, rtt in replies]
        return base

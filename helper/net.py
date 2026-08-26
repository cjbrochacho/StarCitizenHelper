"""Latency to the game's datacenter, plus which server/shard we are on.

The sim server answers nothing - not ICMP, not TCP on any port. Nor can it be
reached indirectly: a TTL walk toward one dies at Google's peering edge, and
every region from Frankfurt to Sydney comes back as the same router at the same
few milliseconds, because Google's backbone does not report TTL once traffic is
on it.

Pinging whatever host the game holds a TLS connection to does not work either.
Those are CIG's platform services, and they sit in one fixed region, so the
number barely moves when the shard moves to the other side of the planet -
which is the one thing this figure exists to show.

What does work: the shard name says which Google Cloud region the server is in
(pub_euw1b is europe-west1, zone b), and Google publishes a per-region endpoint
that does answer ICMP. So latency is measured to the game's region rather than
to the game's machine. It is a proxy, and it is labelled as one, but it moves by
a hundred milliseconds when the region does, which the old number did not.

No elevation is needed: IcmpSendEcho is what the ordinary ping command uses.

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

# One table of region names, so the Performance tab and Server History cannot
# disagree about what pub_euw1b is called.
from .history import region_of

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

#: CIG's region tokens are Google Cloud's own region names with the punctuation
#: taken out, so pub_apse2a is asia-southeast2 zone a. Mapping them back gives
#: a host in the same datacenter as the sim server that will actually answer.
_GCP_REGIONS = {
    "use1": "us-east1", "use4": "us-east4", "use5": "us-east5",
    "usc1": "us-central1", "usw1": "us-west1", "usw2": "us-west2",
    "usw3": "us-west3", "usw4": "us-west4",
    "euw1": "europe-west1", "euw2": "europe-west2", "euw3": "europe-west3",
    "euw4": "europe-west4", "euw6": "europe-west6", "euw9": "europe-west9",
    "euc1": "europe-central2", "eun1": "europe-north1",
    "ape1": "asia-east1", "ape2": "asia-east2",
    "apse1": "asia-southeast1", "apse2": "asia-southeast2",
    "apne1": "asia-northeast1", "apne2": "asia-northeast2",
    "apne3": "asia-northeast3", "aps1": "asia-south1",
    "aus1": "australia-southeast1", "ause1": "australia-southeast1",
    "ause2": "australia-southeast2",
}

#: Google's regional Cloud Storage endpoint. Every region has one, they answer
#: ICMP, and they are pinned to their region rather than anycast to the nearest
#: edge - checked across seven regions, and the times line up with the map.
_REGION_ENDPOINT = "storage.{}.rep.googleapis.com"

#: pub_use1b_12326004_120 -> use1
_SHARD_REGION_RE = re.compile(r"^[a-z]+_([a-z]+\d+)[a-z]?_")

#: Resolved endpoint addresses, kept for the life of the process.
_endpoint_cache: dict[str, str | None] = {}


def gcp_region(shard: str) -> str:
    """The Google Cloud region a shard is running in, or "" if unrecognised."""
    match = _SHARD_REGION_RE.match(shard)
    return _GCP_REGIONS.get(match.group(1), "") if match else ""


def region_endpoint(shard: str) -> str | None:
    """An address in the shard's own region that answers ping, if there is one."""
    region = gcp_region(shard)
    if not region:
        return None
    if region not in _endpoint_cache:
        try:
            _endpoint_cache[region] = socket.gethostbyname(
                _REGION_ENDPOINT.format(region))
        except OSError:
            _endpoint_cache[region] = None
    return _endpoint_cache[region]


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
    #: True when the target is an endpoint in the shard's own cloud region, so
    #: the figure tracks the server's distance. False when it fell back to a
    #: CIG host, which reads much the same wherever the shard is.
    target_is_region: bool = False
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
        self._target_is_region = False
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
        if shard != self._shard:
            # A new shard may well be a new region, and the old endpoint would
            # keep reporting the distance to wherever we used to be.
            self._target = ""
            self._samples.clear()
        self._server, self._shard = f"{ip}:{port}", shard
        self._region = region_of(shard)

    def _pick_target(self, pid: int) -> None:
        """Choose something to ping that moves when the shard's region does.

        First choice is Google's endpoint for the region the shard names, since
        that is the only reachable thing whose distance tracks the server's.
        Failing that - an unrecognised region, or no DNS - fall back to a CIG
        host the game is connected to, which at least proves the link is alive
        even though it will read much the same from anywhere.
        """
        endpoint = region_endpoint(self._shard) if self._shard else None
        if endpoint and self._pinger.ping(endpoint, timeout_ms=1500) is not None:
            self._target, self._target_is_region = endpoint, True
            return

        peers = list(dict.fromkeys(established_peers(pid)))  # dedupe, keep order
        peers.sort(key=lambda ip: 0 if ip.split(".")[0] in ("34", "35") else 1)
        for ip in peers:
            if self._pinger.ping(ip, timeout_ms=800) is not None:
                self._target, self._target_is_region = ip, False
                return

    # -- snapshot ---------------------------------------------------------

    def stats(self) -> NetStats:
        now = time.monotonic()
        with self._lock:
            samples = list(self._samples)
            server, shard, region, target = self._server, self._shard, self._region, self._target
            regional = self._target_is_region

        base = NetStats(server=server, shard=shard, region=region, target=target,
                        target_is_region=regional)
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

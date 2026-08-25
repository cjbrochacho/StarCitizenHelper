"""Live frame statistics for the game, read from RivaTuner Statistics Server.

RTSS publishes a shared memory block describing every application it has
hooked, including a rolling frame count and the most recent frame time. Reading
it needs no elevation and puts no code of ours inside the game - RTSS has
already done the hooking, and we are only a reader.

The alternative is ETW frame tracing (what PresentMon does), which sees every
single present but needs administrator rights. This route trades that for
sampling: polling ten times a second catches roughly one frame in ten, so the
averages are solid while the percentile lows are an approximation drawn from
sampled frames rather than from every frame.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_size_t)
kernel32.MapViewOfFile.restype = wintypes.LPVOID
kernel32.UnmapViewOfFile.argtypes = (wintypes.LPVOID,)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.ShellExecuteW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int)
shell32.ShellExecuteW.restype = ctypes.c_ssize_t

SW_SHOWNORMAL = 1
SE_ERR_ACCESSDENIED = 5
ERROR_CANCELLED = 1223
#: ShellExecute reports success as anything above this.
_SHELL_SUCCESS = 32

FILE_MAP_READ = 0x0004
MAPPING_NAME = "RTSSSharedMemoryV2"

#: RTSS stores its signature as the C multi-character constant 'RTSS', which
#: lands in memory little-endian - so the bytes read back as "SSTR". Both
#: spellings are accepted rather than betting on one.
_SIGNATURES = (b"SSTR", b"RTSS")

# Header: nine DWORDs.
_OFF_APP_ENTRY_SIZE, _OFF_APP_ARR_OFFSET, _OFF_APP_ARR_SIZE = 8, 12, 16
_HEADER_SIZE = 36

# Fields within one application entry.
_APP_PROCESS_ID, _APP_NAME = 0, 4
_APP_NAME_LENGTH = 260
_APP_TIME0, _APP_TIME1, _APP_FRAMES, _APP_FRAME_TIME = 268, 272, 276, 280
_APP_ENTRY_MINIMUM = 284

#: How long a reading stays meaningful before the game counts as gone.
_STALE_SECONDS = 2.5

# While RTSS is attaching to a game it briefly reports a stale measurement
# window - a frame count against a span of minutes - which works out as a
# fraction of a frame per second and drags the average down for the next
# minute. RTSS's real window is about a second, so anything wildly longer,
# or a frame rate no running game would produce, is discarded.
_MAX_WINDOW_MS = 5000
_MIN_PLAUSIBLE_FPS = 1.0

WINDOW_SECONDS = 60.0
POLL_SECONDS = 0.1

STATUS_NO_RTSS = "no_rtss"
STATUS_NO_GAME = "no_game"
STATUS_OK = "ok"


def _u32(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset:offset + 4], "little")


@dataclass
class Reading:
    fps: float
    frame_time_ms: float


@dataclass
class Stats:
    """A snapshot of the last minute, safe to read from the GUI thread."""

    status: str = STATUS_NO_RTSS
    fps: float = 0.0
    average: float = 0.0
    low_1: float = 0.0
    minimum: float = 0.0
    frame_time_ms: float = 0.0
    #: (age in seconds, fps), oldest first, for plotting.
    history: list[tuple[float, float]] = field(default_factory=list)


class RtssSharedMemory:
    """Reader for the RTSS block. Re-opens itself if RTSS restarts."""

    def __init__(self, mapping_name: str = MAPPING_NAME) -> None:
        self.mapping_name = mapping_name
        self._handle = None
        self._view = None

    @property
    def open(self) -> bool:
        return self._view is not None

    def connect(self) -> bool:
        if self._view is not None:
            return True
        handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, self.mapping_name)
        if not handle:
            return False
        view = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
        if not view:
            kernel32.CloseHandle(handle)
            return False
        self._handle, self._view = handle, view
        return True

    def disconnect(self) -> None:
        if self._view:
            kernel32.UnmapViewOfFile(self._view)
        if self._handle:
            kernel32.CloseHandle(self._handle)
        self._handle = self._view = None

    def read(self, process_name: str) -> Reading | None:
        """Latest reading for one executable, or None if RTSS has no entry."""
        if not self.connect():
            return None

        header = ctypes.string_at(self._view, _HEADER_SIZE)
        if header[:4] not in _SIGNATURES:
            self.disconnect()
            return None

        entry_size = _u32(header, _OFF_APP_ENTRY_SIZE)
        array_offset = _u32(header, _OFF_APP_ARR_OFFSET)
        count = _u32(header, _OFF_APP_ARR_SIZE)
        if entry_size < _APP_ENTRY_MINIMUM or not count:
            return None

        wanted = process_name.lower()
        for index in range(count):
            # Only the head of each entry is interesting. RTSS entries run to
            # 12KB apiece across 256 slots, so copying them whole would mean
            # shifting megabytes on every poll.
            raw = ctypes.string_at(self._view + array_offset + index * entry_size,
                                   _APP_ENTRY_MINIMUM)
            if not _u32(raw, _APP_PROCESS_ID):
                continue
            name = raw[_APP_NAME:_APP_NAME + _APP_NAME_LENGTH].split(b"\x00")[0]
            # RTSS stores a full path, so match on the executable at the end.
            if not name.decode("latin-1").lower().endswith(wanted):
                continue

            start, end = _u32(raw, _APP_TIME0), _u32(raw, _APP_TIME1)
            frames = _u32(raw, _APP_FRAMES)
            frame_time_us = _u32(raw, _APP_FRAME_TIME)

            window = end - start
            if window <= 0 or window > _MAX_WINDOW_MS or not frames or not frame_time_us:
                return None
            fps = frames * 1000.0 / window
            if fps < _MIN_PLAUSIBLE_FPS:
                return None
            return Reading(fps=fps, frame_time_ms=frame_time_us / 1000.0)
        return None


def rtss_executable() -> Path | None:
    """Where RTSS is installed, if it is."""
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "RivaTuner Statistics Server" / "RTSS.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "RivaTuner Statistics Server" / "RTSS.exe",
    ]
    return next((path for path in candidates if path.exists()), None)


def start_rtss() -> str | None:
    """Launch RTSS. Returns None on success, or why it failed.

    RTSS ships a manifest demanding administrator rights, so CreateProcess -
    what subprocess uses - refuses outright with ERROR_ELEVATION_REQUIRED (740)
    rather than asking. ShellExecute is the call that knows how to raise a UAC
    prompt, so it is the one to use here.

    Only ever called when the user explicitly asks for it.
    """
    executable = rtss_executable()
    if executable is None:
        return "RivaTuner Statistics Server is not installed"

    result = shell32.ShellExecuteW(None, "open", str(executable), None,
                                   str(executable.parent), SW_SHOWNORMAL)
    if result > _SHELL_SUCCESS:
        return None
    if result in (SE_ERR_ACCESSDENIED, ERROR_CANCELLED):
        return "RivaTuner needs administrator rights and the prompt was declined"
    return f"Windows refused to start RivaTuner (ShellExecute code {result})"


class FpsMonitor(threading.Thread):
    """Polls RTSS on a timer and keeps the last minute of readings."""

    def __init__(self, process_name: str = "StarCitizen.exe",
                 mapping_name: str = MAPPING_NAME) -> None:
        super().__init__(name="fps-monitor", daemon=True)
        self.process_name = process_name
        self._memory = RtssSharedMemory(mapping_name)
        self._samples: deque[tuple[float, float, float]] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = STATUS_NO_RTSS
        self._last_seen = 0.0

    def run(self) -> None:
        while not self._stop.is_set():
            self._poll()
            self._stop.wait(POLL_SECONDS)
        self._memory.disconnect()

    def shutdown(self) -> None:
        self._stop.set()

    def _poll(self) -> None:
        now = time.monotonic()
        reading = self._memory.read(self.process_name)

        with self._lock:
            if reading is not None:
                self._status = STATUS_OK
                self._last_seen = now
                self._samples.append((now, reading.fps, reading.frame_time_ms))
            elif not self._memory.open:
                self._status = STATUS_NO_RTSS
                self._samples.clear()
            elif now - self._last_seen > _STALE_SECONDS:
                # RTSS is running but has nothing for the game right now.
                self._status = STATUS_NO_GAME

            cutoff = now - WINDOW_SECONDS
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()

    def stats(self) -> Stats:
        now = time.monotonic()
        with self._lock:
            status = self._status
            samples = list(self._samples)

        if not samples:
            return Stats(status=status)

        frame_rates = [fps for _, fps, _ in samples]
        slowest_first = sorted((frame_time for _, _, frame_time in samples), reverse=True)

        # The 1% low is the mean of the worst one percent of frame times,
        # reported the way benchmarks do it: as the frame rate they represent.
        worst_count = max(1, len(slowest_first) // 100)
        worst_mean = sum(slowest_first[:worst_count]) / worst_count

        return Stats(
            status=status,
            fps=frame_rates[-1],
            average=sum(frame_rates) / len(frame_rates),
            low_1=1000.0 / worst_mean if worst_mean else 0.0,
            minimum=1000.0 / slowest_first[0] if slowest_first[0] else 0.0,
            frame_time_ms=samples[-1][2],
            history=[(now - stamp, fps) for stamp, fps, _ in samples],
        )

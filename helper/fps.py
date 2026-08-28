"""Live frame statistics for the game, measured from outside it.

RivaTuner got its numbers by loading itself into the game: on a Vulkan title
like Star Citizen it registers an implicit layer, and the game's own log lists
it at startup as VK_LAYER_RTSS. That is what the game's instability warning is
about, and when the layer fails to initialise the game does not start at all.

PresentMon asks Windows instead. Every present goes through the graphics
kernel, which reports it over ETW, so frames can be counted without touching
the game process. Nothing is loaded, nothing is hooked, and a crash in here
cannot take the game down with it. It also reports how long the GPU was busy
in each frame, which the shared-memory route could not see at all.

Two things about this are worth knowing before changing any of it.

*Permission.* Opening an ETW session normally wants administrator. Membership
of the Performance Log Users group is enough instead, and the installers for
NVIDIA FrameView and PresentMon itself both grant it, so on many machines it is
already there and no prompt is ever needed. `can_capture()` reports whether it
is, without starting anything.

*Filtering.* PresentMon is never asked to select the game for us. Star Citizen
runs under EasyAntiCheat, which blocks an unelevated process from querying it,
and PresentMon decides what to record at the moment it discovers a process: if
it cannot read the name then, the process is `<unknown>` and neither
`--process_name` nor `--process_id` will ever match it - it simply records
nothing, with no error. Both were measured doing exactly that while an
unfiltered capture of the same game at the same moment returned 541 rows in 15
seconds, correctly labelled `StarCitizen.exe`. It also explains the
intermittency that led here: the filter works when PresentMon happens to be
tracing before the game starts, because then it learns the name from the
process-start event instead of having to ask. So the capture is unfiltered and
rows are matched on ProcessID here, which is always known and never guessed.

*Display tracking.* By default PresentMon follows each frame all the way to the
screen, and a game sitting behind another window never confirms - which is
exactly when this tool is in front of it. It does not withhold those rows, it
just leaves the display columns as NA: measured over eight seconds with the
game in the background, 362 of 720 rows had no MsUntilDisplayed while every
single one carried MsBetweenPresents and MsGPUBusy. So the fix is not to turn
display tracking off - that would cost the GPU figures, which 2.3.1 refuses to
collect without it - but simply never to read a display-tracked column.
"""

from __future__ import annotations

import atexit
import csv
import ctypes
import io
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from .net import process_pid

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

#: How often the supervisor checks that a capture is running and healthy. The
#: frames themselves arrive on their own thread as PresentMon writes them, so
#: this cadence costs nothing and is not the resolution of anything.
POLL_SECONDS = 0.25
WINDOW_SECONDS = 60.0

#: Our own ETW session name. Fixed rather than random so that a previous run
#: killed off without cleanup can be found and stopped on the next start.
SESSION_NAME = "StarCitizenHelper"

STATUS_NO_SOURCE = "no_source"      # PresentMon binary not found
STATUS_NO_ACCESS = "no_access"      # found, but not allowed to trace
STATUS_NO_GAME = "no_game"          # tracing, but the game is not presenting
STATUS_OK = "ok"

#: A capture that has produced nothing for this long is treated as idle.
#:
#: This has to clear how unevenly PresentMon hands rows over, which is not the
#: same as how often the game presents. At 120fps the gaps measured a tidy
#: median of 1.00s, but in a stuttering session presenting around 22fps the
#: same capture delivered 1 row at 7s, 99 by 22s and 738 by 51s - bursts with
#: many seconds of nothing between them. The old 4s was inside that, so an
#: ordinary gap read as "the game is gone", and the HUD blanked a minute of
#: perfectly good frames every time it happened.
_STALE_SECONDS = 15.0
#: Tear a running capture down and start it again once it has gone this long
#: without delivering a row.
#:
#: The floor here is PresentMon's own startup: measured 7.16s from launch to
#: the CSV header, before any row. A watchdog shorter than that kills captures
#: that were merely slow to open and restarts them into the same delay, which
#: is a capture that can never deliver. The ceiling is WINDOW_SECONDS, since a
#: stall outlasting the window drains it to empty. Sit well clear of both.
_SILENT_SECONDS = 45.0
#: Consecutive restarts before giving up. Reset as soon as frames flow again,
#: so a bad minute cannot exhaust the budget for the rest of the session.
_MAX_RETRIES = 2

#: Backstop on the frame buffer. The window cutoff is what normally bounds it;
#: this only matters if a frame rate turns up that nobody expected.
_MAX_FRAMES = 40000

CREATE_NO_WINDOW = 0x08000000


# --- may we trace? --------------------------------------------------------

class _SidIdentifierAuthority(ctypes.Structure):
    _fields_ = [("Value", ctypes.c_byte * 6)]


advapi32.AllocateAndInitializeSid.argtypes = (
    ctypes.POINTER(_SidIdentifierAuthority), ctypes.c_byte,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p))
advapi32.AllocateAndInitializeSid.restype = wintypes.BOOL
advapi32.CheckTokenMembership.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
                                          ctypes.POINTER(wintypes.BOOL))
advapi32.CheckTokenMembership.restype = wintypes.BOOL
advapi32.FreeSid.argtypes = (ctypes.c_void_p,)

_SECURITY_NT_AUTHORITY = 5
_SECURITY_BUILTIN_DOMAIN_RID = 0x20
_DOMAIN_ALIAS_RID_ADMINS = 0x220            # BUILTIN\Administrators
_DOMAIN_ALIAS_RID_PERFLOG_USERS = 0x22F     # BUILTIN\Performance Log Users


def _in_builtin_group(rid: int) -> bool:
    """Is the caller's *effective* token a member of a BUILTIN alias?

    Effective is the point. An administrator running unelevated carries the
    group deny-only, and cannot open a trace session; CheckTokenMembership
    accounts for that and says no, which is the honest answer. Asked this way
    rather than by parsing `whoami /groups`, whose text is localised.
    """
    authority = _SidIdentifierAuthority((ctypes.c_byte * 6)(0, 0, 0, 0, 0,
                                                            _SECURITY_NT_AUTHORITY))
    sid = ctypes.c_void_p()
    if not advapi32.AllocateAndInitializeSid(ctypes.byref(authority), 2,
                                             _SECURITY_BUILTIN_DOMAIN_RID, rid,
                                             0, 0, 0, 0, 0, 0, ctypes.byref(sid)):
        return False
    try:
        member = wintypes.BOOL()
        if not advapi32.CheckTokenMembership(None, sid, ctypes.byref(member)):
            return False
        return bool(member.value)
    finally:
        advapi32.FreeSid(sid)


_can_capture: bool | None = None


def can_capture() -> bool:
    """Whether this account may open an ETW session, without starting one.

    Checked up front so the UI can say what is wrong before anything gets
    spawned, rather than showing a capture that quietly produces nothing.
    Answered once and kept: a token's group membership cannot change under a
    running process, which is why the fix for it ends in "sign out and back in".
    """
    global _can_capture
    if _can_capture is None:
        _can_capture = (_in_builtin_group(_DOMAIN_ALIAS_RID_PERFLOG_USERS)
                        or _in_builtin_group(_DOMAIN_ALIAS_RID_ADMINS))
    return _can_capture


# --- finding the binary ---------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

#: Ours first - see vendor/README.md. The rest are where a PresentMon build is
#: commonly already installed, which keeps the tool working from a checkout
#: that has no vendored binary. They are a fallback for finding Intel's
#: executable, not a dependency on the program that shipped it.
_SEARCH = (
    ROOT / "vendor" / "PresentMon.exe",
    Path(r"C:\Program Files (x86)\RivaTuner Statistics Server\Plugins\Client"
         r"\PresentMonDataProvider\PresentMon-2.3.1-x64.exe"),
    Path(r"C:\Program Files (x86)\RivaTuner Statistics Server\Plugins\Client"
         r"\PresentMonDataProvider\PresentMon-1.10.0-x64.exe"),
)


def presentmon_executable() -> Path | None:
    for candidate in _SEARCH:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# --- leftover sessions ----------------------------------------------------

# EVENT_TRACE_PROPERTIES is 120 bytes on x64 (a 48 byte WNODE_HEADER, then
# fifteen ULONGs, an 8 byte aligned HANDLE, and two name offsets). The logger
# name is written straight after it, and LoggerNameOffset - the last field,
# at byte 116 - points back at it.
_ETP_SIZE = 120
_ETP_LOGGER_NAME_OFFSET = 116
_EVENT_TRACE_CONTROL_STOP = 1

advapi32.ControlTraceW.argtypes = (ctypes.c_uint64, wintypes.LPCWSTR,
                                   ctypes.c_void_p, wintypes.DWORD)
advapi32.ControlTraceW.restype = wintypes.DWORD


def stop_trace_session(name: str = SESSION_NAME) -> bool:
    """Stop an ETW session left behind by a capture that was killed.

    A trace session outlives the process that made it, and while ours is still
    running a new capture silently records nothing - no error, no output, just
    an empty stream. Clearing it first is what makes a hard shutdown
    recoverable rather than needing a reboot.
    """
    buffer = ctypes.create_string_buffer(_ETP_SIZE + 2 * (len(name) + 1))
    fields = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32))
    fields[0] = len(buffer)                              # Wnode.BufferSize
    fields[_ETP_LOGGER_NAME_OFFSET // 4] = _ETP_SIZE     # LoggerNameOffset
    ctypes.memmove(ctypes.addressof(buffer) + _ETP_SIZE,
                   ctypes.create_unicode_buffer(name), 2 * (len(name) + 1))
    return advapi32.ControlTraceW(0, name, buffer, _EVENT_TRACE_CONTROL_STOP) == 0


# A session of ours left running stops every PresentMon capture on the machine
# without so much as an error, so this is worth a belt as well as braces.
atexit.register(stop_trace_session)


# --- the numbers ----------------------------------------------------------

@dataclass
class Stats:
    """A snapshot of the last minute, safe to read from the GUI thread."""

    status: str = STATUS_NO_SOURCE
    fps: float = 0.0
    average: float = 0.0
    low_1: float = 0.0
    minimum: float = 0.0
    frame_time_ms: float = 0.0
    #: True when the figures come from every frame rather than from samples.
    #: PresentMon reports every one, so this holds whenever there is enough of
    #: a window to say anything at all. Kept because the telemetry and the
    #: Performance tab both label the figures with where they came from.
    per_frame: bool = False
    #: Mean gap between one frame's time and the next - micro-stutter, the
    #: kind that never shows up as a big spike. Zero when unknown.
    swing_ms: float = 0.0
    #: That gap as a share of the average frame, so it can be read across
    #: different frame rates.
    swing_pct: float = 0.0
    #: Share of frames taking more than twice the median - discrete hitches.
    stutter_pct: float = 0.0
    #: Mean milliseconds the GPU was busy per frame. Next to the frame time it
    #: says whether the GPU or the CPU is the limit.
    gpu_busy_ms: float = 0.0
    #: (age in seconds, frame time in ms), oldest first - same axis as history.
    frame_times: list[tuple[float, float]] = field(default_factory=list)
    #: (age in seconds, fps), oldest first, for plotting.
    history: list[tuple[float, float]] = field(default_factory=list)


#: Frames behind the "right now" reading. Enough to be steady, few enough to
#: still move when the frame rate does.
_RECENT_FRAMES = 30


def summarise(frames: list[tuple[float, float, float]], status: str,
              now: float) -> Stats:
    """Turn (when, frame ms, gpu ms) into the figures the HUD shows."""
    if len(frames) < 2:
        return Stats(status=status)

    times = [ms for _, ms, _ in frames]
    gpu = [busy for _, _, busy in frames if busy > 0]
    slowest_first = sorted(times, reverse=True)

    # The 1% low is the mean of the worst one percent of frame times, reported
    # the way benchmarks do it: as the frame rate they represent.
    worst_count = max(1, len(slowest_first) // 100)
    worst_mean = sum(slowest_first[:worst_count]) / worst_count
    mean_frame = sum(times) / len(times)

    gaps = [abs(b - a) for a, b in zip(times, times[1:])]
    swing = sum(gaps) / len(gaps) if gaps else 0.0
    middle = sorted(times)[len(times) // 2]
    stutter = 100.0 * sum(1 for ms in times if ms > 2 * middle) / len(times)

    # One point per second of wall clock rather than one per frame, so a
    # 200fps minute plots as the same number of points as a 40fps one.
    history: list[tuple[float, float]] = []
    bucket: list[float] = []
    mark = frames[0][0]
    for stamp, ms, _ in frames:
        if stamp - mark >= 1.0 and bucket:
            history.append((now - mark, 1000.0 * len(bucket) / sum(bucket)))
            bucket, mark = [], stamp
        bucket.append(ms)
    if bucket:
        history.append((now - mark, 1000.0 * len(bucket) / sum(bucket)))

    recent = times[-_RECENT_FRAMES:]
    recent_total = sum(recent)
    return Stats(
        status=status,
        fps=1000.0 * len(recent) / recent_total if recent_total else 0.0,
        average=1000.0 / mean_frame if mean_frame else 0.0,
        low_1=1000.0 / worst_mean if worst_mean else 0.0,
        minimum=1000.0 / slowest_first[0] if slowest_first[0] else 0.0,
        frame_time_ms=recent_total / len(recent) if recent else 0.0,
        per_frame=True,
        swing_ms=swing,
        swing_pct=100.0 * swing / mean_frame if mean_frame else 0.0,
        stutter_pct=stutter,
        gpu_busy_ms=sum(gpu) / len(gpu) if gpu else 0.0,
        frame_times=[(now - stamp, ms) for stamp, ms, _ in frames],
        history=history,
    )


# --- the capture ----------------------------------------------------------

class FpsMonitor(threading.Thread):
    """Runs PresentMon while the game is up and keeps the last minute."""

    def __init__(self, process_name: str = "StarCitizen.exe") -> None:
        super().__init__(name="fps-monitor", daemon=True)
        self.process_name = process_name
        self._frames: deque[tuple[float, float, float]] = deque(maxlen=_MAX_FRAMES)
        self._lock = threading.Lock()
        self._capture_lock = threading.Lock()
        # Named _stopping, not _stop: threading.Thread has a private _stop()
        # that join() calls, and shadowing it with an Event makes join() raise
        # TypeError on a perfectly ordinary Thread.
        self._stopping = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        #: Which process the reader keeps rows for. 0 accepts nothing, which
        #: is the right answer before the game has been found.
        self._game_pid = 0
        # Settled before the first tick so the Performance tab never flashes
        # a missing-binary message on the way to the real one.
        self._status = self._idle_status()
        self._last_frame = 0.0
        self._started_at = 0.0
        self._retries = 0
        #: Why the capture was last restarted, and how many times. A capture
        #: that quietly stops is the hard kind of fault to report, so when one
        #: is noticed it gets said out loud rather than just fixed. The count
        #: matters as much as the reason: reporting only on a *change* of
        #: reason hides a restart that keeps happening for the same cause,
        #: which is exactly the shape a recurring fault has.
        self.last_reset = ""
        self.resets = 0

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        while not self._stopping.is_set():
            self._tick()
            self._stopping.wait(POLL_SECONDS)
        self._stop_capture()

    def shutdown(self) -> None:
        """Tear the capture down here rather than leaving it to the thread.

        Only setting the flag would leave the cleanup to a daemon thread that
        the interpreter is about to kill, which is how a session gets orphaned.
        """
        self._stopping.set()
        self._stop_capture()

    def _tick(self) -> None:
        """Keep a capture running exactly while the game is."""
        pid = process_pid(self.process_name)
        running = bool(pid)
        if running:
            # Kept current so the reader can pick this process out of an
            # unfiltered capture; a relaunched game gets a new one.
            self._game_pid = pid
        alive = self._proc is not None and self._proc.poll() is None
        reading = self._reader is not None and self._reader.is_alive()
        now = time.monotonic()

        if running and not alive:
            # A first start is not a restart; a process that was there and is
            # now gone is, and it went unreported until now.
            if self._proc is not None:
                self._note_reset("the capture process exited")
            self._start_capture()
        elif not running and alive:
            self._stop_capture()
            self._retries = 0
            with self._lock:
                self._frames.clear()
                self._status = self._idle_status()
        elif not running:
            with self._lock:
                self._status = self._idle_status()
        elif alive:
            stalled = self._stall_reason(now, reading)
            if stalled is not None and self._retries < _MAX_RETRIES:
                self._retries += 1
                self._note_reset(stalled)
                self._stop_capture()
                self._start_capture()

        with self._lock:
            cutoff = now - WINDOW_SECONDS
            while self._frames and self._frames[0][0] < cutoff:
                self._frames.popleft()
            if self._status == STATUS_OK:
                if now - self._last_frame > _STALE_SECONDS:
                    self._status = STATUS_NO_GAME
                else:
                    self._retries = 0          # delivering again; budget restored

    def _note_reset(self, reason: str) -> None:
        """Record a restart so the app can report every one, not just the first."""
        self.last_reset = reason
        self.resets += 1

    def _stall_reason(self, now: float, reading: bool) -> str | None:
        """Why a live capture should be torn down and started again.

        This used to ask only whether a capture had *never* produced a row
        (`not self._last_frame`), which left the worse case uncovered: a reader
        that dies after frames have been flowing. PresentMon stays alive and
        the game stays running, so no branch fired and nothing restarted it -
        the window then drained to empty over the following minute and the
        graph came back blank whenever the capture eventually recycled.
        """
        if not reading:
            return "the reader stopped"
        since = now - (self._last_frame or self._started_at)
        if since > _SILENT_SECONDS:
            return f"no frames for {since:.0f}s"
        return None

    def _idle_status(self) -> str:
        """What to report while the game is not running.

        A missing binary or a missing permission is worth saying now rather
        than at launch: before the game is up is exactly when there is time to
        do something about it.
        """
        if presentmon_executable() is None:
            return STATUS_NO_SOURCE
        return STATUS_NO_GAME if can_capture() else STATUS_NO_ACCESS

    def _start_capture(self) -> None:
        executable = presentmon_executable()
        if executable is None:
            with self._lock:
                self._status = STATUS_NO_SOURCE
            return
        if not can_capture():
            with self._lock:
                self._status = STATUS_NO_ACCESS
            return

        with self._capture_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            # Anything left over from a run that was killed would silently
            # swallow this capture, so clear it before asking for a new one.
            stop_trace_session()
            try:
                # Display tracking is left on. It cannot complete while the
                # game is behind another window, but it does not hold rows
                # back either - and turning it off would take the GPU figures
                # with it. See the module docstring.
                # No --process_name / --process_id: see the module docstring.
                # Everything presenting is captured and the rows for this pid
                # are picked out below.
                self._proc = subprocess.Popen(
                    [str(executable), "--output_stdout", "--no_console_stats",
                     "--no_track_input",
                     "--session_name", SESSION_NAME, "--stop_existing_session"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW)
            except OSError:
                self._proc = None
                with self._lock:
                    self._status = STATUS_NO_SOURCE
                return
            self._started_at = time.monotonic()
            self._last_frame = 0.0
            proc = self._proc

        with self._lock:
            self._status = STATUS_NO_GAME
        # Held onto so _tick can notice if it dies. Assigned before start()
        # only because the monitor thread is the one calling this, so no tick
        # can observe the gap.
        self._reader = threading.Thread(target=self._read, args=(proc,),
                                        name="frame-reader", daemon=True)
        self._reader.start()

    def _stop_capture(self) -> None:
        with self._capture_lock:
            proc, self._proc = self._proc, None
            self._reader = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        # terminate() gives PresentMon no chance to close its trace session,
        # and a stale one blocks every later capture, so always clear it.
        stop_trace_session()

    # -- reading -----------------------------------------------------------

    def _read(self, proc: subprocess.Popen) -> None:
        """Guard the reader, because a silent death is the expensive failure.

        Anything escaping here ends the thread, and with it every future frame,
        while PresentMon and the game both carry on looking healthy. Swallowing
        it is safe only because _tick watches whether this thread is still
        alive and restarts the capture when it is not - the alternative is a
        graph that empties over a minute and never refills. `readline` on a
        pipe whose process is being torn down is the usual way in.
        """
        try:
            self._consume(proc)
        except Exception:
            return

    def _consume(self, proc: subprocess.Popen) -> None:
        """Consume PresentMon's CSV on its own thread; it arrives in bursts."""
        stream = io.TextIOWrapper(proc.stdout, encoding="utf-8-sig",
                                  errors="replace")
        header = stream.readline()
        if not header:
            return                            # died before saying anything
        column = {name: index
                  for index, name in enumerate(header.strip().split(","))}
        try:
            frame_at = column["MsBetweenPresents"]
            time_at = column["TimeInMs"]
            pid_at = column["ProcessID"]
        except KeyError:
            # A build whose columns we do not recognise. Say so rather than
            # guessing at positions - see vendor/README.md.
            with self._lock:
                self._status = STATUS_NO_SOURCE
            return
        gpu_at = column.get("MsGPUBusy", -1)
        widest = max(frame_at, time_at, gpu_at, pid_at)

        # PresentMon timestamps from the start of its own capture. Pinning
        # that to our clock once keeps each frame at its true spacing rather
        # than bunching a whole batch at the moment it was handed over.
        origin = 0.0
        while not self._stopping.is_set():
            line = stream.readline()
            if not line:
                break
            try:
                row = next(csv.reader([line]))
                if len(row) <= widest:
                    continue                  # a row still being written
                if int(row[pid_at]) != self._game_pid:
                    continue                  # some other window presenting
                elapsed = float(row[time_at]) / 1000.0
                frame_ms = float(row[frame_at])
            except (ValueError, StopIteration, IndexError):
                continue
            if frame_ms <= 0.0:
                continue
            gpu_ms = 0.0
            if gpu_at >= 0:
                try:
                    gpu_ms = float(row[gpu_at])
                except ValueError:
                    gpu_ms = 0.0              # PresentMon writes NA early on
            now = time.monotonic()
            if not origin:
                origin = now - elapsed
            with self._lock:
                self._frames.append((origin + elapsed, frame_ms, gpu_ms))
                self._last_frame = now
                self._status = STATUS_OK

    # -- reading out -------------------------------------------------------

    def stats(self) -> Stats:
        now = time.monotonic()
        with self._lock:
            status = self._status
            frames = list(self._frames)
        return summarise(frames, status, now)

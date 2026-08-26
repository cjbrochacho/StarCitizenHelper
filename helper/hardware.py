"""What the CPU and GPU are, and how fast they are running right now.

Names are cheap and fixed, so they are read once. Clocks are not: neither chip
reports a current speed anywhere obvious.

The CPU's is worked out the way Task Manager does it - the performance counter
says what percentage of its nominal speed the chip is currently running at, so
125% of a 4.3 GHz part is 5.4 GHz. The registry's ~MHz is the nominal figure
and never moves; CallNtPowerInformation looks like the right call and simply
returns the maximum on modern parts.

The GPU has no Windows API for it at all. MSI Afterburner publishes it when it
is running, and NVIDIA's driver ships nvidia-smi which will answer in about
80ms, so both are tried in that order.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
import subprocess
import sys
import threading
import winreg
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
pdh = ctypes.WinDLL("pdh", use_last_error=True)

POLL_SECONDS = 1.0
#: nvidia-smi costs ~80ms to spawn, so the fallback path runs every other poll.
_NVIDIA_EVERY = 2

# --- CPU ------------------------------------------------------------------

_CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
#: Task Manager's own measure: current speed as a percentage of nominal.
_PERF_COUNTER = r"\Processor Information(_Total)\% Processor Performance"

PDH_FMT_DOUBLE = 0x00000200


class _CounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


pdh.PdhOpenQueryW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p)
pdh.PdhAddEnglishCounterW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR,
                                      ctypes.c_void_p, ctypes.c_void_p)
pdh.PdhCollectQueryData.argtypes = (wintypes.HANDLE,)
pdh.PdhGetFormattedCounterValue.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                            ctypes.c_void_p,
                                            ctypes.POINTER(_CounterValue))


def _tidy(name: str) -> str:
    """Trim the badging chip makers put in their model strings."""
    name = re.sub(r"\((?:R|TM|C)\)", "", name, flags=re.I)
    name = re.sub(r"\b(CPU|Processor)\b", "", name, flags=re.I)
    name = re.sub(r"\b\d+-Core\b", "", name, flags=re.I)
    name = re.sub(r"@.*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" -")


def cpu_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CPU_KEY) as key:
            return _tidy(winreg.QueryValueEx(key, "ProcessorNameString")[0])
    except OSError:
        return "Unknown CPU"


def cpu_nominal_mhz() -> int:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CPU_KEY) as key:
            return int(winreg.QueryValueEx(key, "~MHz")[0])
    except (OSError, ValueError):
        return 0


class CpuClock:
    """Current CPU speed, as nominal times the performance counter."""

    def __init__(self) -> None:
        self.nominal = cpu_nominal_mhz()
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        self._ready = False
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) == 0:
            if pdh.PdhAddEnglishCounterW(self._query, _PERF_COUNTER, 0,
                                         ctypes.byref(self._counter)) == 0:
                # A rate counter has nothing to report until it has two
                # readings, so prime it here and take the value next poll.
                pdh.PdhCollectQueryData(self._query)
                self._ready = True

    def mhz(self) -> int:
        if not self._ready or not self.nominal:
            return 0
        if pdh.PdhCollectQueryData(self._query) != 0:
            return 0
        value = _CounterValue()
        if pdh.PdhGetFormattedCounterValue(self._counter, PDH_FMT_DOUBLE, None,
                                           ctypes.byref(value)) != 0:
            return 0
        return int(self.nominal * value.doubleValue / 100.0)

    def close(self) -> None:
        if self._query:
            pdh.PdhCloseQuery(self._query)
            self._query = ctypes.c_void_p()


# --- GPU ------------------------------------------------------------------

class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001


def gpu_name() -> str:
    """The adapter actually driving a display, rather than the first listed.

    A laptop or an APU desktop lists its integrated graphics alongside the card
    doing the work, and the one attached to the desktop is the one that matters.
    """
    fallback = ""
    index = 0
    while True:
        device = _DisplayDevice()
        device.cb = ctypes.sizeof(_DisplayDevice)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        if device.DeviceString:
            if device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                return _tidy(device.DeviceString)
            fallback = fallback or _tidy(device.DeviceString)
        index += 1
    return fallback or "Unknown GPU"


_MAHM_CLOCK = re.compile(r"core\s*clock", re.I)


def _afterburner_core_clock() -> float:
    """GPU core clock from MSI Afterburner, when it happens to be running."""
    handle = kernel32.OpenFileMappingW(0x0004, False, "MAHMSharedMemory")
    if not handle:
        return 0.0
    view = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
    if not view:
        kernel32.CloseHandle(handle)
        return 0.0
    try:
        _, _, header_size, count, entry_size = struct.unpack(
            "<5I", ctypes.string_at(view, 20))
        for index in range(count):
            raw = ctypes.string_at(view + header_size + index * entry_size, entry_size)
            name = raw[:260].split(b"\x00")[0].decode("latin-1")
            if _MAHM_CLOCK.search(name):
                return float(struct.unpack("<f", raw[796:800])[0])
    except (OSError, ValueError, struct.error):
        pass
    finally:
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(handle)
    return 0.0


def _nvidia_core_clock() -> tuple[str, float]:
    """Name and current graphics clock from the tool NVIDIA's driver installs."""
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,clocks.gr",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000)          # no console window
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    if done.returncode != 0 or not done.stdout.strip():
        return "", 0.0
    parts = done.stdout.strip().splitlines()[0].split(",")
    if len(parts) < 2:
        return "", 0.0
    try:
        return parts[0].strip(), float(parts[1])
    except ValueError:
        return parts[0].strip(), 0.0


# --- the machine, once ----------------------------------------------------

class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_mb() -> int:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0
    return int(status.ullTotalPhys / (1024 * 1024))


def screen_size() -> str:
    """The primary display as WxH.

    Resolution drives GPU load, so it belongs beside the card whenever one
    machine's frame rate is compared with another's.
    """
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return f"{width}x{height}" if width and height else ""


#: Windows' own per-installation identifier. Readable without elevation, and
#: stable across reboots and reinstalls of this app.
_CRYPTO_KEY = r"SOFTWARE\Microsoft\Cryptography"

#: Mixed into the hash so the result cannot be matched against another
#: product's hash of the same GUID. It is not a secret; it is a namespace.
_ID_SALT = b"star-citizen-helper/telemetry/v1"


def machine_id() -> str:
    """A stable, non-reversible id for this PC.

    The raw values are hashed rather than sent. A machine GUID in the clear is
    a durable handle that joins against anything else holding the same number,
    and unlike the random client id it is not something a person can reset -
    so what leaves here is a salted digest and never the identifier itself.
    """
    parts = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CRYPTO_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
    except OSError:
        pass
    # Without the GUID this is weak, but it still separates most machines.
    parts.extend([cpu_name(), gpu_name(), str(os.cpu_count() or 0)])
    digest = hashlib.sha256(_ID_SALT + "|".join(parts).encode("utf-8"))
    return digest.hexdigest()[:16]


def machine_profile() -> dict:
    """What this PC is. Fixed for a session, so it is read once and reused."""
    return {
        "machine_id": machine_id(),
        "cpu": cpu_name(),
        "cpu_mhz_nominal": cpu_nominal_mhz(),
        "cores": os.cpu_count() or 0,
        "gpu": gpu_name(),
        "ram_mb": ram_mb(),
        "screen": screen_size(),
        "os_build": str(getattr(sys.getwindowsversion(), "build", "")),
    }


# --- the monitor ----------------------------------------------------------

class HardwareMonitor(threading.Thread):
    """Names once, clocks every second."""

    def __init__(self) -> None:
        super().__init__(name="hardware-monitor", daemon=True)
        # Named _stopping, not _stop: threading.Thread has a private _stop()
        # that join() calls, and shadowing it with an Event makes join() raise
        # TypeError on a perfectly ordinary Thread.
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._cpu = CpuClock()
        self.cpu_name = cpu_name()
        self.gpu_name = gpu_name()
        self._cpu_mhz = 0
        self._gpu_mhz = 0.0
        self._nvidia_ok = True
        self._ticks = 0

    def run(self) -> None:
        while not self._stopping.is_set():
            cpu = self._cpu.mhz()
            gpu = _afterburner_core_clock()
            if not gpu and self._nvidia_ok and self._ticks % _NVIDIA_EVERY == 0:
                name, gpu = _nvidia_core_clock()
                if not name:
                    self._nvidia_ok = False        # not an NVIDIA box; stop asking
                elif self.gpu_name.startswith("Unknown"):
                    self.gpu_name = _tidy(name)
            elif not gpu:
                gpu = self._gpu_mhz                 # hold the last reading
            self._ticks += 1
            with self._lock:
                self._cpu_mhz, self._gpu_mhz = cpu, gpu
            self._stopping.wait(POLL_SECONDS)
        self._cpu.close()

    def shutdown(self) -> None:
        self._stopping.set()

    def readings(self) -> tuple[int, float]:
        with self._lock:
            return self._cpu_mhz, self._gpu_mhz

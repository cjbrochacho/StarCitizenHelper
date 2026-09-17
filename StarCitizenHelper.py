import json
import os
import subprocess
from fractions import Fraction
import sys
import time
import threading
import queue
import random
import webbrowser
import ctypes
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import keyboard

# Performance HUD: frame rate (via PresentMon/ETW) and latency/server details.
# Pure stdlib + ctypes - these add no new requirements.
from helper import theme
from helper.brand import BrandMark, WordMark
from helper.fps import FpsMonitor, presentmon_executable
from helper.hud import HudGraph
from helper.idle import IdleWatcher, note_injection, tick
from helper.chord import ChordRecorder
from helper.keybinds import (Filters, categories, conflicts, copy_text, counts, describe_action,
                             describe_detail, describe_export_status, describe_input,
                             describe_shipped_status, describe_status, find_actionmaps,
                             from_keyboard_names, installed_game_version, load_full_export,
                             load_shipped_defaults, merge, mode_of, read_actionmaps, row_tags,
                             row_values, sort_rows, to_keyboard_syntax, visible)
from helper.sheet import SheetRenderer
from helper.hardware import HardwareMonitor, machine_id, machine_profile
from helper.history import collect as collect_history
from helper.net import NetMonitor, find_game_log, process_pid
from helper.overlay import OverlayWindow
from helper.scroll import ScrollFrame, install_wheel_routing, style_scrollbars
from helper.telemetry import (CONTEXT_FIELDS, PROFILE_FIELDS, ROW_FIELDS,
                              SUMMARY_FIELDS, Spool, TelemetryCollector)
from helper.upload import Uploader
from helper.window import (apply_window_icon, force_foreground, foreground_hwnd,
                           parse_geometry, rect_is_visible, set_app_id, set_dpi_aware,
                           window_for_pid, window_rect)

#: The release this source belongs to. Tags are the real source of truth and a
#: checkout reads them directly, but a zip install has neither git nor tags, so
#: the number has to travel inside the source. Bumped when a release is tagged;
#: the tag name is this with a "v" in front.
#:
#: Dated, YYYY.MM.DD, since 2026.09.16 - with .N appended if a day needs a
#: second one. A version that says when it was made answers the question that
#: is actually asked of it. helper.update reads this line from the copy on
#: GitHub to say whether an install is current, so it stays on one line.
__version__ = '2026.09.17.1'

_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_FILE = os.path.join(_DIR, 'settings.json')

#: The key binding reference sheets, one PNG per page, in the order the tab
#: offers them. See data/keybinds/SOURCE.md for where they come from.
KEYBINDS_DIR = os.path.join(_DIR, 'data', 'keybinds')
SHEETS = (('Flight', 'flight.png'), ('FPS', 'fps.png'))      # page N = index + 1
SHEET_PDF = os.path.join(KEYBINDS_DIR, 'sheet.pdf')
SHEET_SCRIPT = os.path.join(KEYBINDS_DIR, 'render_sheet.ps1')
#: Renders of the PDF at zoom levels the user has asked for. Under assets/,
#: which the updater never touches and git never sees.
SHEET_CACHE = os.path.join(_DIR, 'assets', 'keybinds')
SHIPPED_DEFAULTS_PATH = os.path.join(KEYBINDS_DIR, 'defaults.xml')
#: The bindings table's columns: id, heading, width. Three of them sort.
BINDING_COLUMNS = (('mode', 'Mode', 60), ('action', 'Action', 260), ('bound', 'Bound to', 160),
                   ('map', 'Action map', 200), ('source', '', 50))
SORTABLE_COLUMNS = ('action', 'bound', 'map')
BOUND_CHOICES = (('All bindings', 'all'), ('Bound only', 'bound'), ('Unbound only', 'unbound'))
DEVICE_CHOICES = (('Any device', ''), ('Keyboard', 'keyboard'), ('Mouse', 'mouse'))
ALL_CATEGORIES = 'All categories'
CHORD_TIMEOUT_MS = 10000
#: 250% of a 2000px page is a 77 MB photo; 300% would be 111 MB.
ZOOM_MIN, ZOOM_MAX = 25, 250
#: A slider drag fires many changes a second; the render waits for it to stop.
RENDER_DEBOUNCE_MS = 250


def _read_revision():
    """(full sha, is a git checkout) for what's actually running, or (None, False).

    A git checkout is never touched by the self-updater (see helper.update),
    so .version there would just be whatever was installed before the clone
    and never again after - HEAD is read directly instead. Everywhere else,
    .version is the commit the updater last applied, which is exactly what
    is on disk since nothing else writes to this install. Both paths give a
    full 40-character sha, so a freshness check can compare it for equality
    against GitHub's answer without any prefix-matching guesswork.
    """
    git_dir = os.path.join(_DIR, '.git')
    if os.path.isdir(git_dir):
        try:
            with open(os.path.join(git_dir, 'HEAD'), encoding='utf-8') as f:
                head = f.read().strip()
            if head.startswith('ref:'):
                with open(os.path.join(git_dir, head.split(' ', 1)[1]), encoding='utf-8') as f:
                    sha = f.read().strip()
            else:
                sha = head
            if sha:
                return sha, True
        except OSError:
            pass
    try:
        with open(os.path.join(_DIR, 'assets', '.version'), encoding='utf-8') as f:
            sha = f.read().strip()
        return (sha, False) if sha else (None, False)
    except OSError:
        return None, False


def _describe():
    """(latest tag, commits since it) for a git checkout, else (None, 0).

    `git describe --tags --long` always answers in the one shape -
    "<tag>-<distance>-g<sha>" - and gives a distance of 0 when HEAD sits
    exactly on the tag. The plain form drops that suffix on an exact match,
    which would leave the caller unable to tell a bare "v3.0.0" meaning the
    release from a tag of that name with commits after it. The long form
    removes the special case rather than guessing at it.

    Goes through git rather than resolving refs/tags by hand: an annotated
    tag (what `git tag -a` makes) is its own object pointing at the commit
    instead of a ref straight to it, so reading one back without git means
    parsing a zlib-compressed git object for a single string. git is already
    known to be here - it is how a checkout arrived - so it does the work.
    """
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--long'], cwd=_DIR,
            capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return None, 0                       # no tags in this clone yet
        tag, distance, _sha = result.stdout.strip().rsplit('-', 2)
        return tag, int(distance)
    except Exception:
        return None, 0


def current_revision():
    """Short, human-showable id for what is actually running.

    A release shows its version plainly - "v2026.09.16". A checkout past the
    last tag says how far past it is - "v2026.09.16+18 (dev)" - because that
    is the state the app is normally run in while being worked on, and a bare
    commit hash there says nothing about which release it is near. The sha is
    left out of the string entirely. Whether this is the latest is a separate
    question, answered by comparing __version__ with the copy on GitHub.
    """
    sha, is_dev = _read_revision()
    if is_dev:
        tag, distance = _describe()
        if tag:
            return tag if distance == 0 else '%s+%d (dev)' % (tag, distance)
        return 'v%s (dev)' % __version__         # a clone with no tags fetched
    if not sha:
        return 'unreleased'
    return 'v%s' % __version__


#: Where telemetry is posted. Deliberately not a setting: the measurements are
#: only worth anything pooled in one place, and a per-install endpoint would
#: mean comparing a machine against whichever subset happened to share a URL.
#: Turning telemetry off is still the user's call - that switch is in the tab.
TELEMETRY_URL = 'http://gnxllkgfiapa5pdb8e4o4lsr.149.28.198.149.sslip.io/v1/ingest'

#: The same deployment's dashboard. Derived rather than written twice, so
#: moving the endpoint moves the link with it.
TELEMETRY_SITE = TELEMETRY_URL.rsplit('/v1/ingest', 1)[0]

DEFAULTS = {
    'keepalive_enabled':  True,
    'keepalive_key':      'tab',
    'altf4_guard':        True,
    'scan_toggle':        'tab+page up',
    'scan_interval':      2,
    'hold_start':         'shift+w+page up',
    'hold_keys':          'shift+w',
    'macros':             [],
    'hud_enabled':        True,
    'perf_capture_enabled': True,
    'overlay_enabled':    False,
    'overlay_locked':     True,
    'overlay_opacity':    90,
    'overlay_x':          None,
    'overlay_y':          None,
    'overlay_toggle_lock': 'ctrl+alt+l',
    'window_geometry':    None,          # written by the app on close
    'auto_update':        True,
    'telemetry_enabled':  True,
    'telemetry_notice_seen': False,
}

# Keys whose value must round-trip as a real JSON boolean, never as the
# string "true"/"false" (which Python's bool() always reads as truthy).
BOOLEAN_KEYS = (
    'keepalive_enabled', 'altf4_guard', 'hud_enabled', 'perf_capture_enabled',
    'overlay_enabled',
    'overlay_locked', 'auto_update', 'telemetry_enabled', 'telemetry_notice_seen',
)

# Settings edited as free-text Entry fields via _add_settings_tab. Booleans
# are persisted through their own toggle handler instead, so they must stay
# out of field_vars or a later "Save Settings" click clobbers them.
EDITABLE_FIELD_KEYS = (
    'keepalive_key', 'scan_toggle', 'scan_interval', 'hold_start', 'hold_keys',
    'overlay_toggle_lock',
)

# ── Win32 process helpers ─────────────────────────────────────────────────────

TH32CS_SNAPPROCESS = 2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize',              wintypes.DWORD),
        ('cntUsage',            wintypes.DWORD),
        ('th32ProcessID',       wintypes.DWORD),
        ('th32DefaultHeapID',   ctypes.c_size_t),
        ('th32ModuleID',        wintypes.DWORD),
        ('cntThreads',          wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase',      ctypes.c_long),
        ('dwFlags',             wintypes.DWORD),
        ('szExeFile',           wintypes.WCHAR * 260),
    ]


def process_running(exe):
    try:
        k = ctypes.windll.kernel32
        h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h == INVALID_HANDLE_VALUE:
            return False
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k.Process32FirstW(h, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.casefold() == exe.casefold():
                    return True
                ok = k.Process32NextW(h, ctypes.byref(entry))
            return False
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def foreground_is(exe):
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return False
        try:
            buf = ctypes.create_unicode_buffer(32768)
            n = wintypes.DWORD(len(buf))
            ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
            return bool(ok) and os.path.basename(buf.value).casefold() == exe.casefold()
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False


# ── Application ───────────────────────────────────────────────────────────────

#: Keepalive timings. Fixed rather than exposed - there is no reading of the
#: game that would make one number right and another wrong, so a field for
#: them was only ever a way to get them wrong.
IDLE_SECONDS = 60          # quiet keyboard and mouse before keepalive starts

#: Gap between taps once you are away, drawn fresh each time. The upper bound
#: has to stay under whatever the game's own idle timeout is, or the thing
#: this exists to prevent happens anyway.
KEEPALIVE_MIN_SECONDS = 30
KEEPALIVE_MAX_SECONDS = 180

#: How long a key is held. Varied rather than fixed: a keypress held for
#: exactly the same number of milliseconds every time is not something a
#: person does, and the game only needs the press to outlast a frame.
KEY_HOLD_MS = 40
KEY_HOLD_JITTER_MS = 12

#: How often Server History re-reads the game logs on its own, so a session
#: that just started (or just ended) shows up without a manual Refresh click.
HISTORY_REFRESH_MS = 120_000


CRASH_LOG = os.path.join(_DIR, 'assets', 'crash.log')
CRASH_LOG_MAX_BYTES = 2 * 1024 * 1024


def _report_crash(exc_type, exc, tb):
    """Record an unhandled error and say so.

    The app runs windowed, with no console behind it, so an uncaught error
    would otherwise vanish with the window. Written down and shown instead.
    """
    import traceback
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        if os.path.exists(CRASH_LOG) and os.path.getsize(CRASH_LOG) > CRASH_LOG_MAX_BYTES:
            os.replace(CRASH_LOG, CRASH_LOG + '.old')
        with open(CRASH_LOG, 'a', encoding='utf-8') as handle:
            handle.write(time.strftime('\n=== %Y-%m-%d %H:%M:%S ===\n'))
            traceback.print_exception(exc_type, exc, tb, file=handle)
    except OSError:
        pass
    try:
        messagebox.showerror(
            'Star Citizen Helper',
            '%s: %s\n\nDetails written to:\n%s'
            % (exc_type.__name__, exc, CRASH_LOG))
    except Exception:
        pass


sys.excepthook = _report_crash


APP_ID = 'StarCitizenHelper.App'
ICON_PATH = os.path.join(_DIR, 'assets', 'StarCitizenHelper.ico')


class App(tk.Tk):
    # Errors inside Tk callbacks never reach sys.excepthook.
    report_callback_exception = staticmethod(_report_crash)

    def __init__(self):
        # Before the first window exists, or the taskbar keeps grouping this
        # under the Python interpreter and showing its icon.
        set_app_id(APP_ID)
        # Also before the first window: unaware, screen coordinates come back
        # virtualised and land in the wrong place on mixed-DPI multi-monitor
        # setups - the performance overlay is the first thing here that
        # actually places itself by screen pixel, so the first to need this.
        set_dpi_aware()
        super().__init__()
        self.title('Star Citizen Helper')
        self._apply_icon()
        # These numbers were chosen while the process was DPI-unaware, when
        # Windows silently upscaled the whole window and they meant 96dpi
        # units. set_dpi_aware() above stops that happening, so they now mean
        # real pixels - which on a 200% display is half the intended window,
        # while Tk's font scaling doubles to match the true DPI. The result is
        # correctly sized text bursting out of a half-sized frame. Scaling the
        # geometry by the same factor the fonts got restores what these
        # numbers were always describing.
        # Load configuration - before the geometry, which reads it
        self.cfg = DEFAULTS.copy()
        try:
            with open(_SETTINGS_FILE, encoding='utf-8') as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass
        if not isinstance(self.cfg.get('macros'), list):
            self.cfg['macros'] = []
        for k in BOOLEAN_KEYS:
            self.cfg[k] = str(self.cfg.get(k, DEFAULTS[k])).strip().lower() not in ('false', '0', '')

        scale = self.winfo_fpixels('1i') / 96.0
        min_w, min_h = int(860 * scale), int(630 * scale)
        # Where it was last time, if that is still somewhere on a monitor.
        # Checked against the title bar strip rather than the whole window,
        # so "a sliver is on screen" does not count but "you can grab it"
        # does. Clamped to the minimum rather than thrown away.
        saved = parse_geometry(self.cfg.get('window_geometry') or '')
        if saved and rect_is_visible(saved[2], saved[3], saved[2] + saved[0], saved[3] + 40):
            width, height, x, y = saved
            self.geometry('%dx%d%+d%+d' % (max(width, min_w), max(height, min_h), x, y))
        else:
            self.geometry('%dx%d' % (980 * scale, 760 * scale))
        self.minsize(min_w, min_h)
        self.configure(bg='#101722')
        self.protocol('WM_DELETE_WINDOW', self.close)

        # Automation state
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.hotkey_handles = []
        self.game_running = False
        self.game_foreground = False
        self.game_check_at = 0
        self.guard_last_log = 0
        self.keep_active = bool(self.cfg.get('keepalive_enabled', True))
        self.guard_active = bool(self.cfg.get('altf4_guard', True))
        self.scan_active = False
        self.hold_active = False
        self.held_keys = []
        self.hold_pending = False
        self.hold_token = 0
        self.injected_until = 0        # suppresses KeepRunning cancel during bot keypresses
        self.fps_monitor = FpsMonitor()
        self._fps_reset_seen = 0
        self.net_monitor = NetMonitor()
        self.hardware = HardwareMonitor()
        self.telemetry = self._build_telemetry()
        self.uploader = Uploader(
            self.telemetry.spool, _DIR,
            url_provider=(lambda: TELEMETRY_URL),
            enabled=(lambda: bool(self.cfg.get('telemetry_enabled', True))),
            on_stop=self._telemetry_stopped_by_server,
        )
        self.hud = None
        self.overlay = None

        # Windows tracks desktop-wide idle time for us; our own taps are
        # filtered out of it so they cannot look like the user coming back.
        self.idle = IdleWatcher()
        self.next_keepalive = 0
        self.last_keepalive_at = None
        self.next_scan = 0
        self.running_macro = ''
        _, self._revision_is_git = _read_revision()
        self.revision = current_revision()
        self._online_version = None          # set by the freshness check
        self._checking_online = False

        self._build_ui()
        self._register_hotkeys()
        threading.Thread(target=self._check_latest_revision, daemon=True).start()
        if self.cfg.get('overlay_enabled'):
            self._toggle_overlay(True)

        # Suppressing hook: callback must return True to let a key through, False to block it.
        # Only installed while the guard is on, so switching it off leaves the
        # app with no say over the keyboard at all rather than a hook that
        # happens to pass everything through.
        self._alt_hook = None
        if self.guard_active:
            self._alt_hook = keyboard.hook(self._alt_f4_guard, suppress=True)
        keyboard.on_press(self._on_key_press)

        threading.Thread(target=self._automation_loop, daemon=True).start()
        # The master switch for frame capture. Off means PresentMon is never
        # launched and no ETW trace session is opened at all - which is the
        # only way to rule the measurement out as a cause of a stutter,
        # since hud_enabled only hides the readout that the capture feeds.
        if self.cfg.get('perf_capture_enabled', True):
            self.fps_monitor.start()
        else:
            self.log_queue.put('Frame capture disabled in settings - PresentMon not started')
        self.net_monitor.start()
        self.hardware.start()
        self.telemetry.start()
        self.uploader.start()
        # Asked after the window exists, so it cannot be missed behind it.
        self.after(1200, self._show_telemetry_notice)
        self.after(100, self._drain_log_queue)
        self.after(200, self._refresh_dashboard)
        self.after(100, self._refresh_hud)
        self._log('Ready. Global hotkeys registered.')

    # ── UI construction ───────────────────────────────────────────────────────

    def _apply_icon(self):
        """Title bar, taskbar and dialogs. The icon is written by the
        installer, so a fresh checkout may not have one yet."""
        if not os.path.exists(ICON_PATH):
            return
        try:
            self.iconbitmap(default=ICON_PATH)   # covers dialogs too
        except tk.TclError:
            pass
        self.update_idletasks()                  # the window must exist first
        apply_window_icon(self.winfo_id(), ICON_PATH)

    def _check_latest_revision(self):
        """Ask GitHub once whether this build is current. Best-effort, off-thread.

        A network call has no place blocking the window from appearing, so
        this runs on its own thread and hands the answer back to Tk via
        after(0, ...) rather than touching a widget from off the main thread.
        """
        try:
            from helper.update import online_version
            online = online_version()
        except Exception:
            online = None
        # A "Check now" that finishes after the window has gone must not try
        # to schedule anything on it.
        if not self.stop_event.is_set():
            self.after(0, self._apply_revision_freshness, online)

    def _apply_revision_freshness(self, online):
        """Always says something - including that it could not check.

        Before, an unreachable GitHub left the note bare, which looked exactly
        like a check that had not happened yet. Now there are three answers
        and every one of them is written down.
        """
        from helper.update import freshness
        self._online_version = online
        self._checking_online = False
        if getattr(self, 'wordmark', None) is not None:
            self.wordmark.set_note('%s (%s)' % (self.revision, freshness(__version__, online)))
        self._refresh_updates_tab()

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TNotebook', background='#101722')
        style.configure('TNotebook.Tab', background='#1c2938', foreground='#c9d7e6', padding=(16, 9))
        style.map('TNotebook.Tab', background=[('selected', '#2a4661')])
        style_scrollbars(style)
        # One dark table style, shared by every tab that has a table.
        style.configure('Dark.Treeview', background='#0f1721', foreground='#eaf4ff',
                        fieldbackground='#0f1721', borderwidth=0, rowheight=26)
        style.configure('Dark.Treeview.Heading', background='#1c2938',
                        foreground='#9eb2c6', borderwidth=0,
                        font=('Segoe UI Semibold', 9))
        style.map('Dark.Treeview', background=[('selected', '#2a4661')],
                  foreground=[('selected', '#ffffff')])

        # Header
        header = tk.Frame(self, bg='#101722')
        header.pack(fill='x', padx=22, pady=(18, 5))

        # Title on the left, performance HUD on the right, sharing one row.
        #
        # The HUD claims its width first. pack() hands out the cavity in the
        # order it is called, so whichever of these two goes first gets its
        # full request and the other takes the remainder. With the title first,
        # a long subtitle starved the HUD: measured at 859px for the title box
        # against a 936px header, leaving the graph 37 pixels. The subtitle is
        # decorative and clips harmlessly; the graph does not.
        title_box = tk.Frame(header, bg='#101722')
        BrandMark(title_box, size=46, background='#101722').pack(side='left',
                                                                 anchor='n', padx=(0, 12))
        self.wordmark = WordMark(title_box, 'STAR CITIZEN HELPER',
                 'Automation status and hotkey controls',
                 background='#101722', title_fill='#eef6ff',
                 subtitle_fill='#91a7bd',
                 note=self.revision, note_fill='#6b7f96')
        self.wordmark.pack(side='left', anchor='nw')

        if self.cfg.get('hud_enabled', True):
            hud_box = tk.Frame(header, bg=theme.BG)
            hud_box.pack(side='right', anchor='e', fill='x', expand=True, padx=(40, 0))
            self.cpu_label = tk.Label(hud_box, text='', bg=theme.BG,
                                      fg=theme.MUTED, font=('Consolas', 8),
                                      anchor='e', justify='right')
            self.cpu_label.pack(fill='x')
            self.gpu_label = tk.Label(hud_box, text='', bg=theme.BG,
                                      fg=theme.MUTED, font=('Consolas', 8),
                                      anchor='e', justify='right')
            self.gpu_label.pack(fill='x', pady=(0, 3))
            self.hud = HudGraph(hud_box)
            self.hud.configure(width=460)
            self.hud.pack(fill='x', expand=True)
            self.server_label = tk.Label(hud_box, text='', bg=theme.BG,
                                         fg=theme.MUTED, font=('Consolas', 8),
                                         anchor='e', justify='right')
            self.server_label.pack(fill='x', pady=(2, 0))

        # Packed last on purpose - see the note above the title box.
        title_box.pack(side='left', anchor='nw')

        # Active automations panel
        panel = tk.Frame(self, bg='#192433', highlightbackground='#2e435a', highlightthickness=1)
        panel.pack(fill='x', padx=22, pady=10)
        tk.Label(panel, text='ACTIVE AUTOMATIONS', bg='#192433', fg='#91a7bd',
                 font=('Segoe UI Semibold', 9)).pack(anchor='w', padx=14, pady=(10, 4))
        chips_row = tk.Frame(panel, bg='#192433')
        chips_row.pack(fill='x', padx=12, pady=(0, 12))
        self.chips = {}
        for name in ('Keepalive', 'Ship Scan', 'KeepRunning', 'Macro'):
            chip = tk.Label(chips_row, text=name + ': OFF', bg='#253448', fg='#b6c5d5',
                            font=('Segoe UI Semibold', 10), padx=12, pady=6)
            chip.pack(side='left', padx=(0, 8))
            if name != 'Macro':
                chip.bind('<Button-1>', lambda e, n=name: self._toggle_automation(n))
            self.chips[name] = chip

        guard_row = tk.Frame(panel, bg='#192433')
        guard_row.pack(fill='x', padx=14, pady=(0, 10))
        self.guard_button = tk.Button(
            guard_row, text='', command=self._toggle_altf4_guard,
            bg='#466f91', fg='white', relief='flat', padx=10, pady=3,
            font=('Segoe UI Semibold', 9), width=14)
        self.guard_button.pack(side='left', padx=(0, 10))
        self.guard_label = tk.Label(
            guard_row,
            text='Alt+F4 protection: checking for StarCitizen.exe…',
            bg='#192433', fg='#9ebee0', font=('Segoe UI Semibold', 10),
        )
        self.guard_label.pack(side='left')

        self.status_var = tk.StringVar(value='Waiting for input…')
        tk.Label(self, textvariable=self.status_var, bg='#101722', fg='#b5c9dc').pack(
            anchor='w', padx=24, pady=(0, 5))

        # Control buttons (always visible above the tab strip)
        controls = tk.Frame(self, bg='#192433', highlightbackground='#2e435a', highlightthickness=1)
        controls.pack(fill='x', padx=22, pady=(0, 10))
        btn_row = tk.Frame(controls, bg='#192433')
        btn_row.pack(fill='x', padx=12, pady=(10, 4))
        for label, cmd, color in [
            ('Save Settings',           self._save,      '#2a6f9e'),
            ('Backup Settings (.json)', self._backup,    '#466f91'),
            ('Import Settings (.json)', self._import,    '#466f91'),
            ('Stop & Release',          self._release,   '#a65a46'),
            ('EMERGENCY DISABLE ALL',   self._emergency, '#8b3f48'),
        ]:
            tk.Button(btn_row, text=label, command=cmd, bg=color, fg='white',
                      relief='flat', padx=12, pady=7).pack(side='left', padx=(0, 8))


        # Tab notebook
        self.field_vars = {
            k: tk.StringVar(value=str(self.cfg[k]))
            for k in EDITABLE_FIELD_KEYS
        }
        notebook = self.notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=22, pady=(0, 10))

        self._add_settings_tab(notebook, 'Keepalive', 'Inactivity keepalive',
            'Runs by itself - there is nothing to switch on. It stays quiet while you '
            'are at the computer, then sends a key every 30 seconds to 3 minutes once the '
            'keyboard and mouse have been still for a minute. F13-F24 and Scroll Lock are unbound in '
            'Star Citizen, so they keep you active without firing the scanner the way Tab '
            'does. Snap focus brings the game forward for the tap and hands focus straight '
            'back, so it keeps working while you are in another window.',
            [('Key to send',          'keepalive_key',      'tab')],
            extra_button=('Toggle Keepalive', self._toggle_keepalive))

        self._add_settings_tab(notebook, 'Scan Ships', 'Ship Scan',
            'Independent of inactivity: sends Tab continuously even while you use your keyboard or mouse.',
            [('Toggle hotkey',        'scan_toggle',   'Tab+Page Up'),
             ('Tab interval seconds', 'scan_interval', '2')],
            extra_button=('Toggle Ship Scan', self._toggle_scan))

        self._add_settings_tab(notebook, 'KeepRunning', 'Toggle held keys',
            'Press the same toggle hotkey to start or stop holding the selected keys.',
            [('Toggle hotkey', 'hold_start', 'Shift+W+Page Up'),
             ('Keys to hold',  'hold_keys',  'shift+w')])

        self._build_macros_tab(notebook)
        self._build_keybinds_tab(notebook)
        self._build_perf_tab(notebook)
        self._build_telemetry_tab(notebook)
        self._build_history_tab(notebook)
        self._build_log_tab(notebook)
        self._build_updates_tab(notebook)
        install_wheel_routing(self)

    def _add_checkbox(self, parent, label, key, note='', on_toggle=None):
        """A boolean setting that persists itself immediately on click.

        Kept out of field_vars/_sync_fields_to_cfg entirely, so it can never
        be corrupted into a string the way the old free-text fields were.
        `on_toggle`, if given, runs after the value is saved - for settings
        that need to actually do something right away, not just be recorded.
        """
        var = tk.BooleanVar(value=bool(self.cfg.get(key)))

        def _on_toggle():
            self.cfg[key] = var.get()
            self._persist()
            if on_toggle:
                on_toggle(var.get())

        row = tk.Frame(parent, bg=parent['bg'])
        row.pack(anchor='w', padx=18, pady=4)
        tk.Checkbutton(row, text=label, variable=var, command=_on_toggle,
                       bg=parent['bg'], fg='#eef6ff', selectcolor='#0f1721',
                       activebackground=parent['bg'], activeforeground='#eef6ff',
                       relief='flat', anchor='w').pack(side='left')
        if note:
            tk.Label(row, text=note, bg=parent['bg'], fg='#6f8398',
                     font=('Segoe UI', 8)).pack(side='left', padx=(6, 0))
        return var

    def _add_settings_tab(self, notebook, tab_name, title, desc, fields, extra_button=None):
        scroller = ScrollFrame(notebook, '#192433')
        notebook.add(scroller, text=tab_name)
        frame = scroller.inner
        tk.Label(frame, text=title, bg='#192433', fg='#eef6ff',
                 font=('Segoe UI Semibold', 14)).pack(anchor='w', padx=20, pady=(18, 4))
        tk.Label(frame, text=desc, bg='#192433', fg='#9eb2c6',
                 wraplength=780, justify='left').pack(anchor='w', padx=20, pady=(0, 12))
        for label, key, hint in fields:
            row = tk.Frame(frame, bg='#192433')
            row.pack(fill='x', padx=20, pady=8)
            tk.Label(row, text=label, bg='#192433', fg='#eef6ff',
                     width=25, anchor='w').pack(side='left')
            tk.Entry(row, textvariable=self.field_vars[key], bg='#0f1721', fg='#eaf4ff',
                     insertbackground='white', relief='flat', width=28).pack(
                         side='left', padx=8, ipady=5)
            tk.Label(row, text='default: ' + hint, bg='#192433', fg='#8ca2b9').pack(side='left')
        if extra_button:
            btn_text, btn_cmd = extra_button
            tk.Button(frame, text=btn_text, command=btn_cmd, bg='#2a6f9e', fg='white',
                      relief='flat', padx=16, pady=8).pack(anchor='w', padx=20, pady=18)

    def _build_macros_tab(self, notebook):
        frame = self._macros_tab = tk.Frame(notebook, bg='#192433')
        notebook.add(frame, text='Macros')
        tk.Label(frame, text='Tap Macros', bg='#192433', fg='#eef6ff',
                 font=('Segoe UI Semibold', 14)).pack(anchor='w', padx=20, pady=(18, 4))
        tk.Label(frame,
                 text='Create a global-hotkey macro that runs actions in order. '
                      'Use comma-separated actions such as:  1, 2, tab, shift+w. '
                      'Each is pressed and released, except two special forms: '
                      'wait:1.5 pauses for 1.5s, and hold:shift+w:2.0 holds shift+w '
                      'down for 2.0s before releasing.',
                 bg='#192433', fg='#9eb2c6', wraplength=760, justify='left').pack(
                     anchor='w', padx=20, pady=(0, 12))

        self.macro_name = tk.StringVar()
        self.macro_hotkey = tk.StringVar()
        self.macro_actions = tk.StringVar()
        self.macro_delay = tk.StringVar(value='0.10')
        for label, var, hint in [
            ('Name',                            self.macro_name,    'e.g. Countermeasures'),
            ('Hotkey',                          self.macro_hotkey,  'e.g. ctrl+alt+1'),
            ('Actions',                         self.macro_actions, 'e.g. 1, 2, tab'),
            ('Delay between actions (seconds)', self.macro_delay,   'e.g. 0.10'),
        ]:
            row = tk.Frame(frame, bg='#192433')
            row.pack(fill='x', padx=20, pady=6)
            tk.Label(row, text=label, bg='#192433', fg='#eef6ff',
                     width=28, anchor='w').pack(side='left')
            entry = tk.Entry(row, textvariable=var, bg='#0f1721', fg='#eaf4ff',
                             insertbackground='white', relief='flat', width=40)
            entry.pack(side='left', ipady=5)
            if var is self.macro_hotkey:
                self._macro_hotkey_entry = entry     # "Use in macro" lands the cursor here
            tk.Label(row, text=hint, bg='#192433', fg='#8ca2b9').pack(side='left', padx=8)

        tk.Button(frame, text='Add macro', command=self._add_macro, bg='#2a6f9e',
                  fg='white', relief='flat', padx=16, pady=8).pack(anchor='w', padx=20, pady=(10, 8))
        # The button row is packed first, at the bottom, so the list takes
        # what is left rather than the buttons being what gets cut off.
        macro_btn_row = tk.Frame(frame, bg='#192433')
        macro_btn_row.pack(side='bottom', fill='x', padx=20, pady=(6, 14))
        self.macro_listbox = tk.Listbox(frame, bg='#0f1721', fg='#d9eafa',
                                        selectbackground='#2a6f9e', relief='flat', height=7)
        self.macro_listbox.pack(fill='both', expand=True, padx=20, pady=4)
        tk.Button(macro_btn_row, text='Remove selected macro', command=self._remove_macro,
                  bg='#a65a46', fg='white', relief='flat', padx=14, pady=7).pack(
                      side='left', padx=(0, 8))
        tk.Button(macro_btn_row, text='Export selected macro', command=self._export_macro,
                  bg='#253448', fg='#eef6ff', relief='flat', padx=14, pady=7).pack(
                      side='left', padx=(0, 8))
        tk.Button(macro_btn_row, text='Import macro', command=self._import_macro,
                  bg='#253448', fg='#eef6ff', relief='flat', padx=14, pady=7).pack(side='left')
        self._refresh_macro_list()

    def _export_macro(self):
        sel = self.macro_listbox.curselection()
        if not sel:
            messagebox.showinfo('Export macro', 'Select a macro to export first.')
            return
        macro = self.cfg['macros'][sel[0]]
        path = filedialog.asksaveasfilename(
            title='Export macro',
            initialdir=self._documents_folder(),
            initialfile=macro['name'] + '.json',
            defaultextension='.json',
            filetypes=[('JSON files', '*.json')],
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(macro, f, indent=2)
            self._log('Macro exported: ' + path)
            messagebox.showinfo('Macro exported', 'Saved to:\n' + path)
        except OSError as e:
            messagebox.showerror('Could not export macro', str(e))

    def _import_macro(self):
        path = filedialog.askopenfilename(
            title='Import macro',
            initialdir=self._documents_folder(),
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            macros = data if isinstance(data, list) else [data]
            imported = []
            for m in macros:
                if not isinstance(m, dict) or not all(k in m for k in ('name', 'hotkey', 'actions')):
                    raise ValueError('A macro entry is missing its name, hotkey, or actions.')
                imported.append({
                    'name': str(m['name']),
                    'hotkey': str(m['hotkey']).strip().lower(),
                    'actions': str(m['actions']).strip().lower(),
                    'delay': float(m.get('delay', 0.1)),
                })
            self.cfg['macros'].extend(imported)
            self._refresh_macro_list()
            self._save()
            self._log('Imported %d macro%s from: %s'
                      % (len(imported), '' if len(imported) == 1 else 's', path))
            messagebox.showinfo('Macro imported',
                                'Imported %d macro%s.'
                                % (len(imported), '' if len(imported) == 1 else 's'))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            messagebox.showerror('Could not import macro', str(e))

    # -- Telemetry ------------------------------------------------------------

    def _build_telemetry(self):
        """The collector. Runs from launch; the enabled check is live.

        Passing a callable rather than a flag means switching it off in the UI
        takes effect on the next sample instead of at the next restart.
        """
        log = find_game_log()
        return TelemetryCollector(
            Spool(os.path.join(_DIR, 'assets', 'telemetry')),
            # Indirected on purpose: _toggle_perf_capture swaps in a fresh
            # FpsMonitor, and a bound method captured here would go on
            # reporting the dead one's empty window forever.
            fps_stats=(lambda: self.fps_monitor.stats()),
            net_stats=self.net_monitor.stats,
            hardware=self.hardware.readings,
            machine=machine_profile(),
            log_path=find_game_log,
            live_dir=(lambda: log.parent if log else None),
            client_id=machine_id(),
            enabled=(lambda: bool(self.cfg.get('telemetry_enabled', True))),
            # Deferred: the watcher is built after the collector is.
            idle=(lambda: self.idle.seconds()),
        )

    def _show_telemetry_notice(self):
        """Say what is being collected, once, before any of it has gone anywhere.

        Sending is on by default, which is only defensible if nobody has to go
        looking to find that out - so this appears unprompted on the first run
        after the feature arrives, with the off switch in the dialog rather
        than buried in a tab.
        """
        if self.cfg.get('telemetry_notice_seen'):
            return
        self.cfg['telemetry_notice_seen'] = True
        self._persist()
        keep = messagebox.askyesno(
            'Star Citizen Helper - performance data',
            'This build records how the game performs on your PC: frame rate, '
            'frame times, latency, your graphics settings and hardware, and '
            'which part of the game you were in.\n\n'
            'It never records your handle, your account, your position, or any '
            'raw line from your logs - only the fields listed in the Telemetry '
            'tab, where you can also read everything it has written.\n\n'
            'It is on by default. Keep it on?',
            default='yes', icon='question')
        if not keep:
            self.cfg['telemetry_enabled'] = False
            self._persist()
        self.log_queue.put('Telemetry ' + ('on.' if keep else 'off.'))
        self._refresh_telemetry_tab()

    def _build_telemetry_tab(self, notebook):
        scroller = ScrollFrame(notebook, '#101722')
        notebook.add(scroller, text='Telemetry')
        frame = scroller.inner

        tk.Label(frame, text='Performance data', bg='#101722', fg='#eef6ff',
                 font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, text='Anonymous measurements of how the game runs, so that slow '
                             'places and slow hardware can be found. Batched once a '
                             'minute into assets/telemetry - you can open and read every '
                             'byte of it below.',
                 bg='#101722', fg='#91a7bd', wraplength=760, justify='left'
                 ).pack(anchor='w', padx=18, pady=(0, 12))

        self.telemetry_status = tk.Label(frame, text='', bg='#101722', fg='#eef6ff',
                                         font=('Consolas', 10), justify='left')
        self.telemetry_status.pack(anchor='w', padx=18)

        row = tk.Frame(frame, bg='#101722')
        row.pack(anchor='w', padx=18, pady=(14, 6))
        self.telemetry_button = tk.Button(row, text='', command=self._toggle_telemetry,
                                          bg='#466f91', fg='white', relief='flat',
                                          padx=14, pady=6, width=16)
        self.telemetry_button.pack(side='left', padx=(0, 8))
        tk.Button(row, text='Open my data', command=self._open_my_data,
                  bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                  relief='flat', padx=14, pady=6).pack(side='left', padx=(0, 8))

        tk.Label(frame, text='Everything that is collected', bg='#101722', fg='#91a7bd',
                 font=('Segoe UI Semibold', 10)).pack(anchor='w', padx=18, pady=(10, 2))
        fields = tk.Text(frame, height=10, bg='#0f1721', fg='#b5c9dc', relief='flat',
                         font=('Consolas', 8), wrap='word', padx=10, pady=8)
        fields.pack(fill='x', padx=18, pady=(0, 14))
        fields.insert('1.0',
                      'machine       ' + ', '.join(PROFILE_FIELDS) + '\n\n'
                      'graphics      every SysSpec_ quality tier, plus Upscaling, '
                      'UpscalingModel, UpscalingTechnique, VSync, MotionBlur, '
                      'Sharpening, FOV, Gamma, Resolution\n\n'
                      'where         ' + ', '.join(CONTEXT_FIELDS) + '\n\n'
                      'each second   ' + ', '.join(ROW_FIELDS) + '\n\n'
                      'per batch     ' + ', '.join(SUMMARY_FIELDS) + '\n\n'
                      'Never collected: your handle, account id, player id, position, '
                      'IP address, file paths, or any raw line from the game log.')
        fields.config(state='disabled')
        self._tick_telemetry_tab()

    def _tick_telemetry_tab(self):
        """Its own slow cadence: the counts come from stat(), which has no
        business running on the hundred-millisecond HUD loop."""
        if self.stop_event.is_set():
            return
        self._refresh_telemetry_tab()
        self.after(2000, self._tick_telemetry_tab)

    def _refresh_telemetry_tab(self):
        if getattr(self, 'telemetry_status', None) is None:
            return
        on = bool(self.cfg.get('telemetry_enabled', True))
        try:
            files = self.telemetry.spool.files()
            size = sum(f.stat().st_size for f in files)
        except OSError:
            files, size = [], 0
        up = self.uploader.snapshot()
        if up['status'] == 'waiting' and up['waiting'] > 0:
            upload_line = 'retrying in %ds  (%d sent, %d failed)' % (
                round(up['waiting']), up['sent'], up['failures'])
        elif up['status'] == 'stopped':
            upload_line = 'stopped by the server'
        else:
            upload_line = '%s  (%d sent, %d failed)' % (up['status'], up['sent'],
                                                        up['failures'])
        self.telemetry_status.config(
            text='Collecting:  %s\nBatches:     %d this session\nOn disk:     %d file%s, %.1f KB\n'
                 'Uploading:   %s\nYour ID:     %s'
                 % ('ON' if on else 'OFF', self.telemetry.batches_written,
                    len(files), '' if len(files) == 1 else 's', size / 1024.0,
                    upload_line,
                    self.telemetry.client[:12] + '...'))
        self.telemetry_button.config(text='Turn it off' if on else 'Turn it on',
                                     bg='#a65a46' if on else '#466f91')

    def _telemetry_stopped_by_server(self):
        """The server asked every client to stand down, so this one does.

        Written to settings rather than held in memory: if the server is
        refusing data there is no sense resuming the moment the app restarts.
        """
        self.cfg['telemetry_enabled'] = False
        self._persist()
        self.log_queue.put('Telemetry stopped at the server\'s request.')

    def _toggle_telemetry(self):
        on = not bool(self.cfg.get('telemetry_enabled', True))
        self.cfg['telemetry_enabled'] = on
        self._persist()
        if not on:
            self.telemetry.flush()      # keep what was already measured
        self.log_queue.put('Telemetry ' + ('on.' if on else 'off.'))
        self._refresh_telemetry_tab()

    def _open_my_data(self):
        """This PC\'s own page on the dashboard - what was sent, read back.

        The spool this used to open is a queue, not an archive: a file
        lives in it only until it has been posted, so once uploading had
        caught up there was nothing left in there to look at.

        The path is /rig, not /machine: the dashboard names a PC a rig
        throughout, and the old address now 404s. The id is this machine's -
        the collector is keyed on it, so `client` is the same salted digest
        the profile sends as `machine_id`, which is what the page is keyed on.
        """
        url = '%s/rig/%s' % (TELEMETRY_SITE, self.telemetry.client)
        try:
            webbrowser.open(url)
        except OSError as exc:
            messagebox.showerror('Star Citizen Helper',
                                 'Could not open %s\n%s' % (url, exc))

    # ── Performance HUD ───────────────────────────────────────────────────────

    # -- Key Bindings ---------------------------------------------------------

    def _build_keybinds_tab(self, notebook):
        """The reference sheet for the game's bindings, and the full table.

        The sheet is a community chart of the *default* bindings, one page per
        mode. It ships as a PDF and as one 2000px PNG per page; any other zoom
        is rendered from the PDF on demand, in the background, and cached -
        Tk's own scaling drops pixels and the sheet's small text does not
        survive it. The table underneath is the player's actual bindings: the
        game's own export of everything, with actionmaps.xml laid over it.
        Nothing is loaded until the tab is first shown.
        """
        frame = self._keybinds_tab = tk.Frame(notebook, bg='#101722')
        notebook.add(frame, text='Key Bindings')
        self._sheet_mode = SHEETS[0][0]
        self._sheet_pct = 100
        self._sheet_fit = True
        self._sheet_base = {}                    # mode -> PhotoImage as shipped
        self._sheet_scaled = {}                  # mode -> (pct, PhotoImage, crisp)
        self._sheet_generation = 0               # bumps on every zoom/mode change
        self._sheet_render_after = None
        self._sheet_fit_after = None
        self._sheet_syncing = False
        self._sheet_rendering = False
        self._sheet_preview_only = False
        self._sheet_renderer = SheetRenderer(SHEET_PDF, SHEET_CACHE, SHEET_SCRIPT,
                                             log=self.log_queue.put)
        self._keybinds_loaded = False
        self._bindings = []                      # the merged table, filtered per refresh
        self._binding_conflicts = {}
        self._show_all_modes = tk.BooleanVar(value=False)

        tk.Label(frame, text='Key Bindings', bg='#101722', fg='#eef6ff',
                 font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, text='A community 4.6.0 sheet of the default bindings per mode; below it, '
                             'every binding the installed game has, with yours marked. Scroll and '
                             'Shift+scroll to pan, Ctrl+scroll or the slider to zoom, drag to move.',
                 bg='#101722', fg='#91a7bd', wraplength=900, justify='left'
                 ).pack(anchor='w', padx=18, pady=(0, 8))

        controls = tk.Frame(frame, bg='#101722')
        controls.pack(fill='x', padx=18, pady=(0, 6))
        self._sheet_mode_buttons = {}
        for mode, _file in SHEETS:
            button = tk.Button(controls, text=mode, command=lambda m=mode: self._select_sheet(m),
                               bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                               relief='flat', padx=14, pady=5, width=8)
            button.pack(side='left', padx=(0, 6))
            self._sheet_mode_buttons[mode] = button
        tk.Frame(controls, bg='#2e435a', width=1, height=26).pack(side='left', padx=10)
        self._sheet_zoom_buttons = {}
        for label, pct, fit in (('Fit', None, True), ('100%', 100, False), ('200%', 200, False)):
            button = tk.Button(controls, text=label,
                               command=lambda p=pct, f=fit: self._apply_zoom(p, fit=f),
                               bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                               relief='flat', padx=10, pady=5, width=5)
            button.pack(side='left', padx=(0, 4))
            self._sheet_zoom_buttons[label] = button
        self._sheet_slider = tk.Scale(controls, from_=ZOOM_MIN, to=ZOOM_MAX, orient='horizontal',
                                      resolution=1, showvalue=0, length=180,
                                      bg='#101722', fg='#eef6ff', troughcolor='#0f1721',
                                      highlightthickness=0, bd=0, command=self._on_zoom_slider)
        self._sheet_slider.pack(side='left', padx=(10, 6))
        self._sheet_entry = tk.Entry(controls, width=4, justify='right', bg='#0f1721',
                                     fg='#eaf4ff', insertbackground='white', relief='flat')
        self._sheet_entry.pack(side='left', ipady=3)
        self._sheet_entry.bind('<Return>', self._on_zoom_entry)
        self._sheet_entry.bind('<FocusOut>', self._on_zoom_entry)
        tk.Label(controls, text='%', bg='#101722', fg='#91a7bd').pack(side='left', padx=(2, 0))
        self._sheet_zoom_label = tk.Label(controls, text='', bg='#101722', fg='#6f8398',
                                          font=('Consolas', 9))
        self._sheet_zoom_label.pack(side='left', padx=(10, 0))

        # The sheet above, the table below, and a sash between them the user
        # can drag. Classic PanedWindow rather than ttk's: only this one has a
        # per-pane minimum, which is what keeps the table's head row in view
        # however far the sash goes.
        paned = self._keybinds_paned = tk.PanedWindow(
            frame, orient='vertical', bg='#101722', sashwidth=6, sashpad=0,
            sashrelief='flat', bd=0, opaqueresize=True)
        paned.pack(fill='both', expand=True, padx=18, pady=(0, 12))

        viewer = tk.Frame(paned, bg='#101722')
        viewer.columnconfigure(0, weight=1)
        viewer.rowconfigure(0, weight=1)
        self.sheet_canvas = tk.Canvas(viewer, bg='#0f1721', highlightthickness=0,
                                      xscrollincrement=1, yscrollincrement=1)
        ybar = ttk.Scrollbar(viewer, orient='vertical', command=self.sheet_canvas.yview)
        xbar = ttk.Scrollbar(viewer, orient='horizontal', command=self.sheet_canvas.xview)
        self.sheet_canvas.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.sheet_canvas.grid(row=0, column=0, sticky='nsew')
        ybar.grid(row=0, column=1, sticky='ns')
        xbar.grid(row=1, column=0, sticky='ew')
        canvas = self.sheet_canvas
        # Each returns 'break' so the window-wide wheel router never sees
        # these - the canvas owns its wheel entirely.
        canvas.bind('<MouseWheel>', lambda e: (canvas.yview_scroll(-int(e.delta / 120) * 40, 'units'), 'break')[1])
        canvas.bind('<Shift-MouseWheel>', lambda e: (canvas.xview_scroll(-int(e.delta / 120) * 40, 'units'), 'break')[1])
        canvas.bind('<Control-MouseWheel>', lambda e: (
            self._apply_zoom(self._sheet_pct + (10 if e.delta > 0 else -10), fit=False), 'break')[1])
        canvas.bind('<ButtonPress-1>', lambda e: canvas.scan_mark(e.x, e.y))
        canvas.bind('<B1-Motion>', lambda e: canvas.scan_dragto(e.x, e.y, gain=1))
        canvas.bind('<Configure>', self._on_sheet_resize)
        paned.add(viewer, stretch='always', minsize=160)

        panel = tk.Frame(paned, bg='#101722')
        head = self._bindings_head = tk.Frame(panel, bg='#101722')
        head.pack(fill='x', pady=(6, 0))
        self._bindings_open = False
        self._bindings_toggle = tk.Button(head, text='', command=self._toggle_bindings,
                                          bg='#101722', fg='#91a7bd', activebackground='#101722',
                                          activeforeground='#eef6ff', relief='flat', bd=0,
                                          font=('Segoe UI Semibold', 10), cursor='hand2')
        self._bindings_toggle.pack(side='left')
        tk.Button(head, text='Reload', command=self._reload_bindings,
                  bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                  relief='flat', padx=12, pady=3).pack(side='right')
        tk.Checkbutton(head, text='show all modes', variable=self._show_all_modes,
                       command=self._refresh_bindings, bg='#101722', fg='#c9d7e6',
                       selectcolor='#0f1721', activebackground='#101722',
                       activeforeground='#eef6ff', relief='flat').pack(side='right', padx=(0, 10))
        self._bindings_search = tk.Entry(head, width=22, bg='#0f1721', fg='#eaf4ff',
                                         insertbackground='white', relief='flat',
                                         disabledbackground='#0f1721', disabledforeground='#6f8398')
        self._bindings_search.pack(side='right', padx=(0, 12), ipady=3)
        self._bindings_search.bind('<KeyRelease>', lambda e: self._refresh_bindings())
        tk.Label(head, text='search', bg='#101722', fg='#6f8398').pack(side='right', padx=(0, 6))

        # The filter row. Menus say what they are, so no labels in front of
        # them - the row has to fit in the 824px the narrowest window gives.
        filters = self._bindings_filters = tk.Frame(panel, bg='#101722')
        self._bindings_bound = tk.StringVar(value=BOUND_CHOICES[0][0])
        self._bindings_category = tk.StringVar(value=ALL_CATEGORIES)
        self._bindings_device = tk.StringVar(value=DEVICE_CHOICES[0][0])
        self._bindings_yours = tk.BooleanVar(value=False)
        self._bindings_conflicts_only = tk.BooleanVar(value=False)
        self._dark_option_menu(filters, self._bindings_bound, [c[0] for c in BOUND_CHOICES], 12
                               ).pack(side='left', padx=(0, 8))
        self._bindings_category_menu = self._dark_option_menu(
            filters, self._bindings_category, [ALL_CATEGORIES], 26)
        self._bindings_category_menu.pack(side='left', padx=(0, 8))
        self._bindings_category_list = None
        self._dark_option_menu(filters, self._bindings_device, [c[0] for c in DEVICE_CHOICES], 10
                               ).pack(side='left', padx=(0, 8))
        for text, var in (('yours', self._bindings_yours), ('conflicts', self._bindings_conflicts_only)):
            tk.Checkbutton(filters, text=text, variable=var, command=self._refresh_bindings,
                           bg='#101722', fg='#c9d7e6', selectcolor='#0f1721',
                           activebackground='#101722', activeforeground='#eef6ff',
                           relief='flat').pack(side='left', padx=(0, 8))
        # One button, three states: idle, listening, and showing the chord
        # it heard - which clears it when clicked.
        self._bindings_key = ''
        self._chord = None
        self._chord_after = None
        self._chord_button = tk.Button(filters, text='Press a key...', command=self._on_chord_button,
                                       bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                                       relief='flat', padx=10, pady=2)
        self._chord_button.pack(side='left')

        table = self._bindings_table = tk.Frame(panel, bg='#101722')
        self._bindings_sort = (None, False)
        self.bindings_view = ttk.Treeview(table, columns=[c[0] for c in BINDING_COLUMNS],
                                          show='headings', style='Dark.Treeview')
        for name, title, width in BINDING_COLUMNS:
            self.bindings_view.column(name, width=width, anchor='w', stretch=(name == 'action'))
        self._paint_binding_headings()
        self.bindings_view.tag_configure('rebind', foreground=theme.ACCENT)
        self.bindings_view.tag_configure('conflict', foreground=theme.WARN)
        self.bindings_view.tag_configure('unbound', foreground='#6f8398')
        self.bindings_view.bind('<<TreeviewSelect>>', self._on_binding_selected)
        bar = ttk.Scrollbar(table, orient='vertical', command=self.bindings_view.yview)
        self.bindings_view.configure(yscrollcommand=bar.set)
        self.bindings_view.pack(side='left', fill='both', expand=True)
        bar.pack(side='left', fill='y')
        self._visible = []
        self._selected_binding = None
        # Not packed yet: _toggle_bindings does that, in front of the status line.

        # The selected row, spelled out, with what can be done with it.
        detail = self._bindings_detail_row = tk.Frame(panel, bg='#101722')
        self._bindings_detail = tk.Label(detail, text='', bg='#101722', fg='#c9d7e6',
                                         font=('Consolas', 9), anchor='w', justify='left')
        self._bindings_detail.pack(side='left', fill='x', expand=True)
        self._macro_button = tk.Button(detail, text='Use in macro', command=self._use_in_macro,
                                       bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                                       relief='flat', padx=10, pady=2, state='disabled')
        self._macro_button.pack(side='right')
        self._copy_button = tk.Button(detail, text='Copy', command=self._copy_binding,
                                      bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                                      relief='flat', padx=10, pady=2, state='disabled')
        self._copy_button.pack(side='right', padx=(0, 6))

        self.bindings_status = tk.Label(panel, text='', bg='#101722', fg='#6f8398',
                                        font=('Consolas', 9), anchor='w', justify='left')
        self.bindings_status.pack(side='bottom', fill='x', pady=(4, 0))
        paned.add(panel, stretch='never', minsize=100)

        self._paint_sheet_buttons()
        self._paint_bindings_toggle()
        self._sync_zoom_widgets()
        notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed, add='+')

    # -- the sheet ------------------------------------------------------------

    def _on_tab_changed(self, _event=None):
        if self._keybinds_loaded:
            return
        if self.notebook.select() != str(self._keybinds_tab):
            return
        self._keybinds_loaded = True
        self._reload_bindings()
        self._apply_zoom(fit=True)

    def _on_sheet_resize(self, _event=None):
        """A window drag fires dozens of these; re-fit once it settles."""
        if not (self._keybinds_loaded and self._sheet_fit):
            return
        if self._sheet_fit_after is not None:
            self.after_cancel(self._sheet_fit_after)
        self._sheet_fit_after = self.after(100, lambda: self._apply_zoom(fit=True))

    def _select_sheet(self, mode):
        if mode == self._sheet_mode:
            return
        self._sheet_mode = mode
        # One scaled page at a time. At 250% each is 77 MB; two is too many.
        for other in list(self._sheet_scaled):
            if other != mode:
                del self._sheet_scaled[other]
        self._paint_sheet_buttons()
        if self._keybinds_loaded:
            self._apply_zoom(reset_view=True)
            self._refresh_bindings()

    def _on_zoom_slider(self, value):
        # Scale runs its command for .set() as well, and not until Tk is
        # next idle - after any flag set around the call has been cleared.
        # The echo of our own set carries the value we already hold; a drag
        # that lands on the same value changes nothing either way.
        if self._sheet_syncing or int(float(value)) == self._sheet_pct:
            return
        self._apply_zoom(int(float(value)), fit=False)

    def _on_zoom_entry(self, _event=None):
        text = self._sheet_entry.get().strip().rstrip('%')
        try:
            self._apply_zoom(int(text), fit=False)
        except ValueError:
            self._sync_zoom_widgets()            # put back what it was

    def _fit_pct(self):
        base = self._sheet_image(self._sheet_mode)
        width = self.sheet_canvas.winfo_width()
        if base is None or width <= 1:
            return 100
        return int(width * 100 // base.width())

    def _apply_zoom(self, pct=None, fit=None, reset_view=False):
        """The one way the zoom changes, however it was asked for."""
        if fit is not None:
            self._sheet_fit = fit
        if self._sheet_fit:
            pct = self._fit_pct()
        if pct is None:
            pct = self._sheet_pct
        self._sheet_pct = max(ZOOM_MIN, min(ZOOM_MAX, int(pct)))
        self._sheet_generation += 1              # whatever is in flight is now stale
        self._sheet_rendering = False
        self._sync_zoom_widgets()
        if self._keybinds_loaded:
            self._show_sheet(reset_view=reset_view)
            self._schedule_render()

    def _sync_zoom_widgets(self):
        self._sheet_syncing = True
        try:
            self._sheet_slider.set(self._sheet_pct)
            self._sheet_entry.delete(0, 'end')
            self._sheet_entry.insert(0, str(self._sheet_pct))
        finally:
            self._sheet_syncing = False
        self._paint_sheet_buttons()

    def _paint_sheet_buttons(self):
        for mode, button in self._sheet_mode_buttons.items():
            button.config(bg='#2a4661' if mode == self._sheet_mode else '#253448')
        for label, button in self._sheet_zoom_buttons.items():
            selected = (label == 'Fit' and self._sheet_fit) or (
                not self._sheet_fit and label == '%d%%' % self._sheet_pct)
            button.config(bg='#2a4661' if selected else '#253448')

    def _paint_zoom_label(self):
        text = '%d%%' % self._sheet_pct
        if self._sheet_fit:
            text += '  (fit)'
        if self._sheet_rendering:
            text += '  rendering...'
        elif self._sheet_preview_only and self._sheet_pct != 100:
            text += '  (preview)'
        self._sheet_zoom_label.config(text=text)

    def _sheet_image(self, mode):
        """The page as shipped, or None if the file is not there.

        With the PNG missing but the PDF present, the renderer's 2000px copy
        serves instead - a partial install heals itself the first time.
        """
        if mode not in self._sheet_base:
            page = [m for m, _ in SHEETS].index(mode) + 1
            path = os.path.join(KEYBINDS_DIR, dict(SHEETS)[mode])
            if not os.path.isfile(path):
                cached = self._sheet_renderer.available(page, 2000)
                if cached is None and self._sheet_renderer.usable():
                    self._heal_sheet(mode, page)
                path = str(cached) if cached else path
            try:
                self._sheet_base[mode] = tk.PhotoImage(master=self, file=path)
            except tk.TclError:
                self._sheet_base[mode] = None
        return self._sheet_base[mode]

    def _heal_sheet(self, mode, page):
        """The shipped PNG is gone but the PDF is here: make the 2000px page."""
        if getattr(self, '_sheet_healing', None) == mode:
            return
        self._sheet_healing = mode
        renderer = self._sheet_renderer

        def work():
            path = renderer.render(page, 2000, cancelled=self.stop_event.is_set)
            if self.stop_event.is_set():
                return
            try:
                self.after(0, done, path)
            except (RuntimeError, tk.TclError):
                pass

        def done(path):
            self._sheet_healing = None
            if path is None or mode != self._sheet_mode:
                return
            self._sheet_base.pop(mode, None)     # was None; load the render instead
            self._apply_zoom()

        threading.Thread(target=work, daemon=True).start()

    def _show_sheet(self, reset_view=False):
        """Stage one: something on screen at once - the crisp render if it is
        cached, otherwise the shipped page scaled the fast, rough way."""
        canvas = self.sheet_canvas
        mode, pct = self._sheet_mode, self._sheet_pct
        base = self._sheet_image(mode)
        if base is None:
            canvas.delete('all')
            canvas.configure(scrollregion=(0, 0, 0, 0))
            canvas.create_text(20, 20, anchor='nw', fill='#91a7bd', font=('Segoe UI', 10),
                               text='Sheet not installed: data\\keybinds\\%s (or sheet.pdf) '
                                    '- run the updater.' % dict(SHEETS)[mode])
            self._sheet_zoom_label.config(text='')
            return
        keep = (0.0, 0.0) if reset_view else (canvas.xview()[0], canvas.yview()[0])
        cached = self._sheet_scaled.get(mode)
        if cached and cached[0] == pct:
            image = cached[1]
        elif pct == 100:
            image = base
            self._sheet_scaled[mode] = (100, base, True)
        else:
            ratio = Fraction(pct, 100).limit_denominator(12)
            image = tk.PhotoImage(master=self)
            # -zoom and -subsample together: written straight at the final
            # size, no intermediate at zoom alone.
            image.tk.call(image, 'copy', base, '-zoom', ratio.numerator, ratio.numerator,
                          '-subsample', ratio.denominator, ratio.denominator)
            self._sheet_scaled[mode] = (pct, image, False)
        canvas.delete('all')
        canvas.create_image(0, 0, anchor='nw', image=image)
        canvas.configure(scrollregion=(0, 0, image.width(), image.height()))
        canvas.xview_moveto(keep[0])
        canvas.yview_moveto(keep[1])
        self._paint_zoom_label()

    def _schedule_render(self):
        """Stage two, after the slider has stopped: the exact render."""
        if self._sheet_render_after is not None:
            self.after_cancel(self._sheet_render_after)
            self._sheet_render_after = None
        cached = self._sheet_scaled.get(self._sheet_mode)
        if cached and cached[0] == self._sheet_pct and cached[2]:
            return                               # already crisp
        if self._sheet_pct == 100 or not self._sheet_renderer.usable():
            self._sheet_preview_only = self._sheet_pct != 100
            self._paint_zoom_label()
            return
        self._sheet_render_after = self.after(RENDER_DEBOUNCE_MS, self._start_render)

    def _start_render(self):
        self._sheet_render_after = None
        base = self._sheet_image(self._sheet_mode)
        if base is None:
            return
        mode, pct, generation = self._sheet_mode, self._sheet_pct, self._sheet_generation
        page = [m for m, _ in SHEETS].index(mode) + 1
        width = base.width() * pct // 100
        ready = self._sheet_renderer.available(page, width)
        if ready is not None:
            self._sheet_rendered(generation, mode, pct, ready)
            return
        self._sheet_rendering = True
        self._paint_zoom_label()
        renderer = self._sheet_renderer

        def work():
            path = renderer.render(page, width, cancelled=lambda: (
                generation != self._sheet_generation or self.stop_event.is_set()))
            if self.stop_event.is_set():
                return
            try:
                self.after(0, self._sheet_rendered, generation, mode, pct, path)
            except (RuntimeError, tk.TclError):
                pass                             # the window went away meanwhile

        threading.Thread(target=work, daemon=True).start()

    def _sheet_rendered(self, generation, mode, pct, path):
        if generation != self._sheet_generation:
            return                               # zoom or page changed since; ignore
        self._sheet_rendering = False
        if path is None:
            self._sheet_preview_only = True
            self._paint_zoom_label()
            return
        try:
            image = tk.PhotoImage(master=self, file=str(path))
        except tk.TclError:
            self._sheet_preview_only = True
            self._paint_zoom_label()
            return
        self._sheet_preview_only = False
        self._sheet_scaled[mode] = (pct, image, True)
        self._show_sheet()

    # -- the table ------------------------------------------------------------

    def _dark_option_menu(self, parent, variable, values, width):
        """A drop-down in the app's colours. tk.OptionMenu takes no styling
        arguments, so it is dressed after the fact - the menubutton and the
        menu behind it both."""
        button = tk.OptionMenu(parent, variable, *values,
                               command=lambda _value: self._refresh_bindings())
        button.config(bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                      activeforeground='#eef6ff', relief='flat', bd=0, highlightthickness=0,
                      anchor='w', width=width, padx=8, pady=2, cursor='hand2')
        button['menu'].config(bg='#0f1721', fg='#eef6ff', activebackground='#2a4661',
                              activeforeground='#eef6ff', bd=0, relief='flat',
                              activeborderwidth=0, tearoff=0)
        return button

    def _toggle_bindings(self, open_=None):
        self._bindings_open = (not self._bindings_open) if open_ is None else open_
        if self._bindings_open:
            self._bindings_filters.pack(fill='x', pady=(4, 0), after=self._bindings_head)
            self._bindings_table.pack(fill='both', expand=True, pady=(4, 0),
                                      before=self.bindings_status)
            self._bindings_detail_row.pack(side='bottom', fill='x', pady=(4, 0),
                                           before=self.bindings_status)
            paned = self._keybinds_paned
            if paned.winfo_height() > 1:
                paned.sash_place(0, 0, int(paned.winfo_height() * 0.5))
        else:
            self._stop_chord()
            self._bindings_filters.pack_forget()
            self._bindings_table.pack_forget()
            self._bindings_detail_row.pack_forget()
        self._paint_bindings_toggle()

    def _paint_bindings_toggle(self):
        count = len(self.bindings_view.get_children())
        self._bindings_toggle.config(text='%s Bindings (%d)' % (
            '▾' if self._bindings_open else '▸', count))

    def _reload_bindings(self):
        """Read the files again; everything else is filtering what was read.

        The full list is the game's own defaults as shipped with the app,
        unless the game has written a complete export of its own - which 4.x
        does not, but is checked for all the same.
        """
        log = find_game_log()
        live = log.parent if log else None
        rebinds_path = find_actionmaps(live) if live else None
        rebinds = read_actionmaps(rebinds_path) if rebinds_path else []
        base_path, base = load_full_export(live) if live else (None, [])
        shipped_game = ''
        if base_path is None:
            base_path, base, shipped_game = load_shipped_defaults()
        self._bindings = merge(base, rebinds)
        self._binding_conflicts = conflicts(self._bindings)
        self._bindings_total = len(visible(self._bindings, self._binding_conflicts,
                                           Filters(show_all_modes=True)))
        self._bindings_source = (rebinds_path, base_path, len(rebinds), shipped_game,
                                 installed_game_version(live))
        self._refresh_bindings()

    def _current_filters(self):
        """What the widgets say, as one value the module can act on."""
        chosen = self._bindings_category.get()
        return Filters(
            mode=self._sheet_mode,
            show_all_modes=self._show_all_modes.get(),
            bound=dict(BOUND_CHOICES).get(self._bindings_bound.get(), 'all'),
            category='' if chosen == ALL_CATEGORIES else chosen,
            device=dict(DEVICE_CHOICES).get(self._bindings_device.get(), ''),
            yours_only=self._bindings_yours.get(),
            conflicts_only=self._bindings_conflicts_only.get(),
            needle='' if self._bindings_key else self._bindings_search.get().strip().lower(),
            key=self._bindings_key,
        )

    def _refresh_category_menu(self):
        """The category menu lists the maps of the page being looked at."""
        names = categories(self._bindings, self._sheet_mode, self._show_all_modes.get())
        if names == self._bindings_category_list:
            return
        self._bindings_category_list = names
        menu = self._bindings_category_menu['menu']
        menu.delete(0, 'end')
        for name in [ALL_CATEGORIES] + names:
            menu.add_command(label=name, command=lambda n=name: (
                self._bindings_category.set(n), self._refresh_bindings()))
        if self._bindings_category.get() not in names:
            self._bindings_category.set(ALL_CATEGORIES)

    def _refresh_bindings(self):
        """Fill the table from what was last read, through the filters."""
        self._refresh_category_menu()
        view = self.bindings_view
        remembered = self._selected_binding
        view.delete(*view.get_children())
        show_all = self._show_all_modes.get()
        view['displaycolumns'] = [c[0] for c in BINDING_COLUMNS] if show_all \
            else [c[0] for c in BINDING_COLUMNS if c[0] != 'mode']
        column, reverse = self._bindings_sort
        rows = sort_rows(visible(self._bindings, self._binding_conflicts, self._current_filters()),
                         column, reverse)
        self._visible = rows
        keys = self._binding_conflicts
        for i, b in enumerate(rows):
            mode = mode_of(b.actionmap)
            view.insert('', 'end', iid=str(i), values=row_values(b, mode),
                        tags=row_tags(b, mode, keys))
        if remembered in rows:
            view.selection_set(str(rows.index(remembered)))
        else:
            self._selected_binding = None
            self._paint_binding_detail()

        rebinds_path, base_path, yours, shipped_game, installed = getattr(
            self, '_bindings_source', (None, None, 0, '', ''))
        actions = len({(b.actionmap, b.action) for b in self._bindings})
        if base_path is None:
            status = 'No list of the game\'s defaults is installed - showing your %d rebind%s.' % (
                yours, '' if yours == 1 else 's')
        elif shipped_game or str(base_path) == SHIPPED_DEFAULTS_PATH:
            status = describe_shipped_status(shipped_game, installed, actions, yours)
            if rebinds_path is None:
                status += ' - Star Citizen not found, so none of them are yours yet'
        else:
            try:
                stale = rebinds_path is not None and \
                    rebinds_path.stat().st_mtime > base_path.stat().st_mtime + 1
            except OSError:
                stale = False
            status = describe_export_status(base_path, actions, yours, stale)
        shown, yours_shown, clashing = counts(rows, keys)
        status += '  ·  showing %s of %s · %d yours · %d conflicts' % (
            format(shown, ','), format(getattr(self, '_bindings_total', 0), ','),
            yours_shown, clashing)
        self.bindings_status.config(text=status)
        self._paint_bindings_toggle()

    # -- sorting, selecting, and what to do with a row ------------------------

    def _sort_bindings(self, column):
        current, reverse = self._bindings_sort
        if current != column:
            self._bindings_sort = (column, False)
        elif not reverse:
            self._bindings_sort = (column, True)
        else:
            self._bindings_sort = (None, False)         # back to the game's order
        self._paint_binding_headings()
        self._refresh_bindings()

    def _paint_binding_headings(self):
        column, reverse = self._bindings_sort
        for name, title, _width in BINDING_COLUMNS:
            text = title
            if name == column:
                text += '  ▼' if reverse else '  ▲'
            if name in SORTABLE_COLUMNS:
                self.bindings_view.heading(name, text=text,
                                           command=lambda c=name: self._sort_bindings(c))
            else:
                self.bindings_view.heading(name, text=text)

    def _on_binding_selected(self, _event=None):
        chosen = self.bindings_view.selection()
        self._selected_binding = self._visible[int(chosen[0])] if chosen else None
        self._paint_binding_detail()

    def _paint_binding_detail(self):
        b = self._selected_binding
        self._bindings_detail.config(text=describe_detail(b) if b else '')
        self._copy_button.config(state='normal' if b else 'disabled')
        self._macro_button.config(
            state='normal' if b and to_keyboard_syntax(b.input) else 'disabled')

    def _copy_binding(self):
        b = self._selected_binding
        if b is None:
            return
        self.clipboard_clear()
        self.clipboard_append(copy_text(b))
        self._copy_button.config(text='Copied')
        self.after(2000, lambda: self._copy_button.config(text='Copy'))

    def _use_in_macro(self):
        """Hand the binding to the Macros tab. Nothing is saved until Add macro."""
        b = self._selected_binding
        chord = to_keyboard_syntax(b.input) if b else None
        if not chord:
            return
        self.macro_name.set(describe_action(b.action))
        self.macro_actions.set(chord)
        self.macro_hotkey.set('')
        self.notebook.select(self._macros_tab)
        self._macro_hotkey_entry.focus_set()

    # -- press a key ------------------------------------------------------------

    def _on_chord_button(self):
        if self._bindings_key:                       # showing a chord: clear it
            self._bindings_key = ''
            self._bindings_search.config(state='normal')
            self._chord_button.config(text='Press a key...')
            self._refresh_bindings()
        elif self._chord is None:
            self._start_chord()

    def _start_chord(self):
        """Listen for one chord. The keys still go where they were going -
        the app's own hotkeys included - which is fine for a question."""
        self._chord_button.config(text='press a key... (Esc cancels)', fg='#91a7bd')
        # Both callbacks arrive on the keyboard library's thread.
        self._chord = ChordRecorder(
            on_done=lambda mods, key, keypad: self._post(self._chord_done, mods, key, keypad),
            on_cancel=lambda: self._post(self._chord_cancelled))
        self._chord.start()
        self._chord_after = self.after(CHORD_TIMEOUT_MS, self._stop_chord)

    def _post(self, fn, *args):
        try:
            self.after(0, fn, *args)
        except (RuntimeError, tk.TclError):
            pass

    def _stop_chord(self):
        if self._chord_after is not None:
            self.after_cancel(self._chord_after)
            self._chord_after = None
        if self._chord is not None:
            self._chord.cancel()                     # a no-op if it already answered

    def _chord_done(self, mods, key, keypad):
        self._chord = None
        self._stop_chord()
        self._bindings_key = from_keyboard_names(mods, key, keypad)
        self._bindings_search.config(state='disabled')
        self._chord_button.config(text='%s  ×' % describe_input(self._bindings_key), fg='#eef6ff')
        self._refresh_bindings()

    def _chord_cancelled(self):
        self._chord = None
        self._stop_chord()
        self._chord_button.config(text='Press a key...', fg='#eef6ff')

    def _build_perf_tab(self, notebook):
        """Frame rate and network detail, alongside the header graph."""
        scroller = ScrollFrame(notebook, '#101722')
        notebook.add(scroller, text='Performance')
        frame = scroller.inner

        tk.Label(frame, text='Performance & Server', bg='#101722', fg='#eef6ff',
                 font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, text='Frame rate is measured from outside the game: every present goes '
                             'through the graphics kernel, which reports it over ETW, so nothing '
                             'is loaded into Star Citizen to count them. Latency is measured to '
                             'the cloud region the shard is running in - the sim server itself '
                             'answers no probes, so this is the distance to its datacenter rather '
                             'than to the machine.',
                 bg='#101722', fg='#91a7bd', wraplength=760, justify='left'
                 ).pack(anchor='w', padx=18, pady=(0, 12))

        self.perf_rows = {}
        grid = tk.Frame(frame, bg='#101722')
        grid.pack(anchor='w', padx=18, fill='x')
        for row, label in enumerate(('Frame rate', 'Frame time', 'GPU busy', '1% low',
                                     'Frame swing', 'Stutter',
                                     'Server', 'Shard', 'Region', 'Latency', 'Jitter')):
            tk.Label(grid, text=label, bg='#101722', fg='#91a7bd',
                     font=('Segoe UI', 9), width=12, anchor='w').grid(row=row, column=0,
                                                                      sticky='w', pady=2)
            value = tk.Label(grid, text='--', bg='#101722', fg='#eef6ff',
                             font=('Consolas', 10), anchor='w')
            value.grid(row=row, column=1, sticky='w', pady=2)
            self.perf_rows[label] = value

        # No button here on purpose: the capture starts and stops itself with
        # the game. The only thing that can need a human is the group
        # membership below, and that is not something this app may grant.
        self.capture_note = tk.Label(frame, text='', bg='#101722', fg='#91a7bd',
                                     wraplength=760, justify='left')
        self.capture_note.pack(anchor='w', padx=18, pady=(16, 4))

        tk.Label(frame, text='Preferences', bg='#101722', fg='#91a7bd',
                 font=('Segoe UI Semibold', 10)).pack(anchor='w', padx=18, pady=(14, 2))
        self._add_checkbox(
            frame, 'Measure frame rate (runs PresentMon)', 'perf_capture_enabled',
            note='(off stops the ETW capture entirely - takes effect at once)',
            on_toggle=self._toggle_perf_capture)
        self._add_checkbox(frame, 'Show performance HUD in header', 'hud_enabled',
                           note='(restart required)')
        self._add_checkbox(frame, 'Show floating performance overlay', 'overlay_enabled',
                           note='(a separate window over the game, not injected into it)',
                           on_toggle=self._toggle_overlay)
        self.overlay_locked_var = self._add_checkbox(
            frame, 'Lock overlay position (click-through)', 'overlay_locked',
            note='(unlock to drag it, then lock it again)',
            on_toggle=self._toggle_overlay_lock)

        opacity_row = tk.Frame(frame, bg='#101722')
        opacity_row.pack(anchor='w', padx=18, pady=4)
        tk.Label(opacity_row, text='Overlay opacity', bg='#101722', fg='#eef6ff',
                 width=25, anchor='w').pack(side='left')
        opacity_scale = tk.Scale(opacity_row, from_=20, to=100, orient='horizontal',
                                 length=200, resolution=5, bg='#101722', fg='#eef6ff',
                                 troughcolor='#0f1721', highlightthickness=0, bd=0,
                                 command=lambda v: self._set_overlay_opacity(int(float(v))))
        opacity_scale.set(int(self.cfg.get('overlay_opacity', 90)))
        opacity_scale.pack(side='left', padx=8)

        hotkey_row = tk.Frame(frame, bg='#101722')
        hotkey_row.pack(fill='x', padx=18, pady=4)
        tk.Label(hotkey_row, text='Overlay lock/unlock hotkey', bg='#101722', fg='#eef6ff',
                 width=25, anchor='w').pack(side='left')
        tk.Entry(hotkey_row, textvariable=self.field_vars['overlay_toggle_lock'],
                bg='#0f1721', fg='#eaf4ff', insertbackground='white', relief='flat',
                width=28).pack(side='left', padx=8, ipady=5)
        tk.Label(hotkey_row, text='default: ctrl+alt+l - click Save Settings to apply',
                bg='#101722', fg='#8ca2b9').pack(side='left')

    def _toggle_perf_capture(self, enabled):
        """Start or stop the frame capture without restarting the app.

        A stopped FpsMonitor cannot be restarted - it is a Thread, and a
        Thread runs once - so turning this back on builds a fresh one. Every
        reader of the monitor goes through self.fps_monitor rather than a
        saved reference, so the swap is invisible to them.

        stats() answers on a monitor that was never started, reporting the
        no-source/no-game status and an empty window, so the HUD and the
        Performance tab keep working while this is off - they just show '--'.
        """
        if enabled:
            if not self.fps_monitor.is_alive():
                self.fps_monitor = FpsMonitor()
                # A fresh monitor counts its restarts from zero, and the
                # watcher in _refresh_hud reports any change - so without
                # this it announces a restart that never happened.
                self._fps_reset_seen = 0
                self.fps_monitor.start()
            self.log_queue.put('Frame capture enabled')
        else:
            # shutdown() kills PresentMon and closes the trace session; it
            # does not merely ask the thread to wind down at its leisure.
            self.fps_monitor.shutdown()
            self.log_queue.put('Frame capture disabled - PresentMon stopped')

    def _refresh_hud(self):
        """Feed the header graph and the Performance tab, ten times a second."""
        if self.stop_event.is_set():
            return
        try:
            fps_stats = self.fps_monitor.stats()
            net_stats = self.net_monitor.stats()

            # A capture that stops and silently restarts is the fault that is
            # hardest to report, because by the time it is noticed the evidence
            # has aged out of the window. Say it happened, and why.
            resets = getattr(self.fps_monitor, 'resets', 0)
            if resets != self._fps_reset_seen:
                self._fps_reset_seen = resets
                self.log_queue.put('Frame capture restarted (%d): %s' % (
                    resets, getattr(self.fps_monitor, 'last_reset', '')))

            if self.hud is not None:
                self.hud.update(fps_stats, net_stats)
                if net_stats.server:
                    region = ('  •  ' + net_stats.region) if net_stats.region not in ('', 'unknown') else ''
                    shard = ('  •  ' + net_stats.shard) if net_stats.shard else ''
                    self.server_label.config(text=net_stats.server + shard + region)
                else:
                    self.server_label.config(text='server unknown - not in a match')

            if self.overlay is not None:
                self.overlay.update(fps_stats, net_stats)

            if getattr(self, 'cpu_label', None) is not None:
                cpu_mhz, gpu_mhz = self.hardware.readings()
                self.cpu_label.config(text='%s   %s' % (
                    self.hardware.cpu_name,
                    ('%d MHz' % cpu_mhz) if cpu_mhz else '-- MHz'))
                self.gpu_label.config(text='%s   %s' % (
                    self.hardware.gpu_name,
                    ('%d MHz' % gpu_mhz) if gpu_mhz else '-- MHz'))

            if getattr(self, 'perf_rows', None):
                fps_ok = fps_stats.status == 'ok'
                net_ok = net_stats.status == 'ok'
                self.perf_rows['Frame rate'].config(
                    text=('%.2f fps  (avg %.2f)' % (fps_stats.fps, fps_stats.average)) if fps_ok else '--')
                self.perf_rows['Frame time'].config(
                    text=('%.2f ms' % fps_stats.frame_time_ms) if fps_ok else '--')
                self.perf_rows['GPU busy'].config(
                    text=('%.2f ms  (%.0f%% of frame)'
                          % (fps_stats.gpu_busy_ms,
                             100.0 * fps_stats.gpu_busy_ms / fps_stats.frame_time_ms))
                    if fps_ok and fps_stats.gpu_busy_ms and fps_stats.frame_time_ms else '--')
                self.perf_rows['1% low'].config(
                    text=('%.2f fps  (%s)' % (fps_stats.low_1,
                          'every frame' if fps_stats.per_frame else 'sampled'))
                    if fps_ok else '--')
                self.perf_rows['Frame swing'].config(
                    text=('%.2f ms  (%.0f%% of frame)' % (fps_stats.swing_ms,
                                                          fps_stats.swing_pct))
                    if fps_ok and fps_stats.per_frame else '--')
                self.perf_rows['Stutter'].config(
                    text=('%.2f%% of frames over twice the median' % fps_stats.stutter_pct)
                    if fps_ok and fps_stats.per_frame else '--')
                self.perf_rows['Server'].config(text=net_stats.server or '--')
                self.perf_rows['Shard'].config(text=net_stats.shard or '--')
                self.perf_rows['Region'].config(text=net_stats.region or '--')
                self.perf_rows['Latency'].config(
                    text=('%.2f ms  (avg %.2f, %.0f%% loss)%s'
                          % (net_stats.ping_ms, net_stats.average, net_stats.loss_pct,
                             '' if net_stats.target_is_region else '  — region unknown, not comparable'))
                    if net_ok else '--')
                self.perf_rows['Jitter'].config(
                    text=('%.2f ms' % net_stats.jitter) if net_ok else '--')

            if getattr(self, 'capture_note', None):
                if fps_stats.status == 'no_access':
                    self.capture_note.config(
                        text='Windows will not let this account measure frames. It needs to be a '
                             'member of the "Performance Log Users" group: run compmgmt.msc as '
                             'administrator, add your account under Local Users and Groups → '
                             'Groups → Performance Log Users, then sign out and back in. '
                             'Everything else on this tab works without it.')
                elif fps_stats.status == 'no_source':
                    self.capture_note.config(
                        text='PresentMon is missing from the vendor folder, so there is no frame '
                             'data. Reinstalling or updating the app puts it back.'
                        if presentmon_executable() is None else
                        'PresentMon is not reporting the columns this version reads - frame rate '
                             'unavailable.')
                elif fps_stats.status == 'no_game':
                    self.capture_note.config(text='Waiting for Star Citizen.')
                else:
                    self.capture_note.config(text='')
        except Exception as exc:               # never let the HUD kill the UI loop
            self.log_queue.put('HUD error: %s' % exc)
        self.after(100, self._refresh_hud)

    # -- Updates --------------------------------------------------------------

    def _build_updates_tab(self, notebook):
        """Whether this is the latest, and a way to make it so.

        The launcher updates on its own at every start, so most people never
        need this. It is here for the two questions the header note cannot
        answer on its own - "could it even reach GitHub?" and "what is the
        latest, then?" - and for the one action: updating now, on purpose,
        even with auto_update switched off.
        """
        scroller = ScrollFrame(notebook, '#101722')
        notebook.add(scroller, text='Updates')
        frame = scroller.inner

        tk.Label(frame, text='Updates', bg='#101722', fg='#eef6ff',
                 font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, text='The launcher checks GitHub every time it starts and installs '
                             'whatever is newer before the window opens. This is the same '
                             'check, on demand, with the answer shown - and a way to run the '
                             'update now, which closes the app and starts the launcher again '
                             'in update mode.',
                 bg='#101722', fg='#91a7bd', wraplength=760, justify='left'
                 ).pack(anchor='w', padx=18, pady=(0, 12))

        self.updates_status = tk.Label(frame, text='', bg='#101722', fg='#eef6ff',
                                       font=('Consolas', 10), justify='left')
        self.updates_status.pack(anchor='w', padx=18)

        row = tk.Frame(frame, bg='#101722')
        row.pack(anchor='w', padx=18, pady=(14, 6))
        tk.Button(row, text='Check now', command=self._check_updates_now,
                  bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                  relief='flat', padx=14, pady=6).pack(side='left', padx=(0, 8))
        self.update_button = tk.Button(row, text='Update now', command=self._update_now,
                                       bg='#466f91', fg='white', relief='flat',
                                       padx=14, pady=6, width=16)
        self.update_button.pack(side='left', padx=(0, 8))
        if self._revision_is_git:
            # Somebody's working copy. The updater would refuse it anyway;
            # better that the button says so than that it looks broken.
            self.update_button.config(state='disabled', bg='#253448')

        tk.Label(frame, text='Preferences', bg='#101722', fg='#91a7bd',
                 font=('Segoe UI Semibold', 10)).pack(anchor='w', padx=18, pady=(14, 2))
        self._add_checkbox(frame, 'Update automatically at launch', 'auto_update',
                           note='(Update now works either way)')
        self._refresh_updates_tab()

    def _refresh_updates_tab(self):
        """Three lines: whether GitHub answered, what it said, what is here."""
        if getattr(self, 'updates_status', None) is None:
            return
        if self._checking_online:
            github, online = 'checking...', '...'
        elif self._online_version:
            github, online = 'reachable', 'v' + self._online_version
        else:
            github, online = 'unreachable', 'unknown'
        lines = ['GitHub:      %s' % github,
                 'Latest:      %s' % online,
                 'Installed:   %s' % self.revision]
        if self._revision_is_git:
            lines.append('')
            lines.append('This is a git checkout - update it with git pull.')
            lines.append('Update now is disabled here so it cannot overwrite your work.')
        self.updates_status.config(text='\n'.join(lines))

    def _check_updates_now(self):
        """The startup check again, by request - same thread, same landing."""
        if self._checking_online:
            return
        self._checking_online = True
        self._refresh_updates_tab()
        threading.Thread(target=self._check_latest_revision, daemon=True).start()

    def _update_now(self):
        """Close, and hand over to the launcher in update mode.

        Not done in-process, on purpose. PresentMon.exe is held open while a
        capture runs, the launcher can only be replaced by the launcher, and
        new code is only picked up at import - so the honest way to update a
        running app is to stop being one. The launcher already knows how to
        do every step of this; /update just tells it the user asked.
        """
        if self._revision_is_git:
            return
        if not messagebox.askyesno(
                'Update now',
                'Close Star Citizen Helper and fetch the latest version?\n\n'
                'The launcher opens the app again when it is done.', parent=self):
            return
        launcher = os.path.join(_DIR, 'StarCitizenHelper.bat')
        try:
            # First, and fully: this terminates PresentMon and waits for it,
            # so vendor\PresentMon.exe is unlocked by the time cmd starts.
            self.close()
        finally:
            # The string form, with /s: a list would be re-quoted by
            # list2cmdline and cmd strips those quotes again when the path
            # has a space or a bracket in it. A new console because pythonw
            # has none, and the messages need somewhere to land. Nothing is
            # inherited, so no pipe of ours outlives us.
            subprocess.Popen(
                '%s /s /c ""%s" /update"' % (os.environ.get('COMSPEC', 'cmd.exe'), launcher),
                cwd=_DIR, close_fds=True, stdin=None, stdout=None, stderr=None,
                creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _build_history_tab(self, notebook):
        """Where you have been, so a crash does not lose your ship."""
        frame = tk.Frame(notebook, bg='#192433')
        notebook.add(frame, text='Server History')

        tk.Label(frame, text='Server history', bg='#192433', fg='#eef6ff',
                 font=('Segoe UI Semibold', 14)).pack(anchor='w', padx=20, pady=(18, 4))
        tk.Label(frame, text='The last few shards you were on, newest first. If the game '
                             'drops out and leaves your ship somewhere, this is which shard '
                             'to get back to. The name is built from the shard id: Star '
                             'Citizen gives its servers none of their own, and the build '
                             'number in the id changes every patch. Read from the game logs, '
                             'so sessions from before this app was installed are here too.',
                 bg='#192433', fg='#9eb2c6', wraplength=780,
                 justify='left').pack(anchor='w', padx=20, pady=(0, 12))

        columns = ('joined', 'duration', 'server', 'address')
        widths = (170, 90, 240, 200)
        self.history_view = ttk.Treeview(frame, columns=columns, show='headings',
                                         style='Dark.Treeview', height=10)
        for name, width in zip(columns, widths):
            self.history_view.heading(name, text=name.title())
            self.history_view.column(name, width=width,
                                     anchor='w' if name != 'duration' else 'e')
        self.history_view.tag_configure('current', foreground='#41b8f5')

        # Packed first and at the bottom - see the Macros tab for why.
        row = tk.Frame(frame, bg='#192433')
        row.pack(side='bottom', fill='x', padx=20, pady=12)
        self.history_view.pack(fill='both', expand=True, padx=20)
        tk.Button(row, text='Refresh', command=self._refresh_history, bg='#2a6f9e',
                  fg='white', relief='flat', padx=16, pady=6).pack(side='left')
        tk.Button(row, text='Copy selected', command=self._copy_history_row, bg='#253448',
                  fg='#eef6ff', relief='flat', padx=16, pady=6).pack(side='left', padx=(8, 0))
        tk.Button(row, text='Copy for support ticket', command=self._copy_history_for_support,
                 bg='#253448', fg='#eef6ff', relief='flat', padx=16, pady=6).pack(
                     side='left', padx=(8, 0))
        self.history_note = tk.Label(row, text='', bg='#192433', fg='#8ca2b9')
        self.history_note.pack(side='left', padx=(12, 0))

        # Off the startup path: reading logs should not delay the window.
        self.after(400, self._refresh_history)

    def _refresh_history(self):
        view = getattr(self, 'history_view', None)
        if view is None:
            return
        for item in view.get_children():
            view.delete(item)

        log = find_game_log()
        if log is None:
            self.history_note.config(text='No game logs found.')
        else:
            try:
                sessions = collect_history(log.parent)
            except Exception as exc:
                self.history_note.config(text='Could not read the logs: %s' % exc)
            else:
                self._history_shards = {}
                for item in sessions:
                    row = view.insert('', 'end', tags=('current',) if item.ongoing else (),
                                values=(item.joined.strftime('%a %d %b  %H:%M'),
                                        item.duration + (' *' if item.ongoing else ''),
                                        item.name, item.server))
                    self._history_shards[row] = item.shard
                self.history_note.config(
                    text='%d sessions  -  * is the one running now' % len(sessions)
                    if any(i.ongoing for i in sessions) else '%d sessions' % len(sessions))

        if not self.stop_event.is_set():
            self.after(HISTORY_REFRESH_MS, self._refresh_history)

    def _copy_history_row(self):
        view = getattr(self, 'history_view', None)
        selected = view.selection() if view else ()
        if not selected:
            self.history_note.config(text='Pick a row first.')
            return
        values = view.item(selected[0], 'values')
        shard = getattr(self, '_history_shards', {}).get(selected[0], '')
        text = '%s  -  %s  (%s, joined %s)' % (values[2], shard, values[3], values[0])
        self.clipboard_clear()
        self.clipboard_append(text)
        self.history_note.config(text='Copied: ' + values[2])

    def _copy_history_for_support(self):
        """Server info plus the current Performance tab readout, one paste."""
        view = getattr(self, 'history_view', None)
        selected = view.selection() if view else ()
        if not selected:
            self.history_note.config(text='Pick a row first.')
            return
        values = view.item(selected[0], 'values')
        shard = getattr(self, '_history_shards', {}).get(selected[0], '')
        lines = ['%s  -  %s  (%s, joined %s)' % (values[2], shard, values[3], values[0])]
        perf_rows = getattr(self, 'perf_rows', None) or {}
        for label in ('Frame rate', 'Frame time', 'GPU busy', '1% low',
                     'Frame swing', 'Stutter', 'Latency', 'Jitter'):
            row = perf_rows.get(label)
            if row is not None:
                lines.append('%s: %s' % (label, row.cget('text')))
        self.clipboard_clear()
        self.clipboard_append('\n'.join(lines))
        self.history_note.config(text='Copied session + performance report.')

    def _build_log_tab(self, notebook):
        frame = tk.Frame(notebook, bg='#192433')
        notebook.add(frame, text='Activity Log')
        btn_row = tk.Frame(frame, bg='#192433')
        btn_row.pack(fill='x', padx=14, pady=(10, 0))
        tk.Button(btn_row, text='Clear Log', command=self._clear_log,
                  bg='#466f91', fg='white', relief='flat', padx=10, pady=5).pack(anchor='e')
        self.log_output = tk.Text(frame, bg='#0f1721', fg='#cce0f0', relief='flat',
                                  state='disabled', font=('Consolas', 10))
        self.log_output.pack(fill='both', expand=True, padx=14, pady=(4, 14))

    # ── Macro management ──────────────────────────────────────────────────────

    def _refresh_macro_list(self):
        self.macro_listbox.delete(0, 'end')
        for m in self.cfg['macros']:
            self.macro_listbox.insert('end', f"{m['name']}  —  {m['hotkey']}  →  {m['actions']}")

    def _add_macro(self):
        name = self.macro_name.get().strip()
        hotkey = self.macro_hotkey.get().strip().lower()
        actions = self.macro_actions.get().strip().lower()
        try:
            delay = float(self.macro_delay.get())
        except ValueError:
            delay = -1
        if not name or not hotkey or not actions or delay < 0:
            messagebox.showerror('Macro details needed',
                                 'Enter a name, hotkey, actions, and a delay of 0 or greater.')
            return
        self.cfg['macros'].append({'name': name, 'hotkey': hotkey, 'actions': actions, 'delay': delay})
        self.macro_name.set('')
        self.macro_hotkey.set('')
        self.macro_actions.set('')
        self._refresh_macro_list()
        self._save()
        self._log('Macro added: ' + name)

    def _remove_macro(self):
        sel = self.macro_listbox.curselection()
        if not sel:
            return
        name = self.cfg['macros'].pop(sel[0])['name']
        self._refresh_macro_list()
        self._save()
        self._log('Macro removed: ' + name)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _sync_fields_to_cfg(self):
        for k, var in self.field_vars.items():
            if k.endswith(('idle', 'interval', '_ms')):
                self.cfg[k] = int(var.get())
            else:
                self.cfg[k] = var.get().strip().lower()

    def _save(self):
        try:
            self._sync_fields_to_cfg()
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
            self._register_hotkeys()
            self._log('Settings saved and hotkeys updated.')
        except ValueError:
            messagebox.showerror('Invalid value', 'Idle and interval values must be whole numbers.')
        except Exception as e:
            messagebox.showerror('Could not save settings', str(e))

    def _documents_folder(self):
        folder = os.path.join(os.path.expanduser('~'), 'Documents')
        os.makedirs(folder, exist_ok=True)
        return folder

    def _backup(self):
        try:
            self._sync_fields_to_cfg()
            path = os.path.join(self._documents_folder(), 'StarCitizenHelper_hotkeys_backup.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
            self._log('Hotkey backup saved: ' + path)
            messagebox.showinfo('Backup saved', 'Your hotkeys and macros were saved to:\n' + path)
        except ValueError:
            messagebox.showerror('Invalid value', 'Idle and interval values must be whole numbers.')
        except Exception as e:
            messagebox.showerror('Could not create backup', str(e))

    def _import(self):
        path = filedialog.askopenfilename(
            title='Import Star Citizen Helper hotkeys',
            initialdir=self._documents_folder(),
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('The selected JSON must contain a settings object.')
            imported = {k: data[k] for k in DEFAULTS if k in data}
            if not imported:
                raise ValueError('No Star Citizen Helper settings were found in this JSON file.')
            if 'macros' in imported:
                if not isinstance(imported['macros'], list):
                    raise ValueError('The macros value must be a list.')
                for m in imported['macros']:
                    if not isinstance(m, dict) or not all(k in m for k in ('name', 'hotkey', 'actions')):
                        raise ValueError('A macro entry is missing its name, hotkey, or actions.')
            for k in ('scan_interval',):
                if k in imported:
                    imported[k] = int(imported[k])
            self.cfg.update(imported)
            for k in BOOLEAN_KEYS:
                self.cfg[k] = str(self.cfg.get(k, DEFAULTS[k])).strip().lower() not in ('false', '0', '')
            for k, var in self.field_vars.items():
                var.set(str(self.cfg[k]))
            self._refresh_macro_list()
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
            self._register_hotkeys()
            self._log('Imported hotkeys and macros from: ' + path)
            messagebox.showinfo('Import complete', 'Hotkeys and macros were imported and activated.')
        except (OSError, json.JSONDecodeError, ValueError) as e:
            messagebox.showerror('Could not import hotkeys', str(e))
        except Exception as e:
            messagebox.showerror('Could not import hotkeys', str(e))

    # ── Hotkey registration ───────────────────────────────────────────────────

    def _register_hotkeys(self):
        for h in self.hotkey_handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.hotkey_handles = []
        try:
            for key, fn in [
                ('scan_toggle',   self._toggle_scan),
                ('hold_start',    self._toggle_hold),
                ('overlay_toggle_lock', self._toggle_overlay_lock_via_hotkey),
            ]:
                self.hotkey_handles.append(keyboard.add_hotkey(self.cfg[key], fn, suppress=False))
            for m in self.cfg['macros']:
                self.hotkey_handles.append(
                    keyboard.add_hotkey(m['hotkey'], lambda x=m: self._run_macro(x), suppress=False)
                )
        except Exception as e:
            self.log_queue.put(str(e))

    # ── Alt+F4 guard ──────────────────────────────────────────────────────────

    def _alt_f4_guard(self, event):
        # keyboard suppresses an event when this callback returns False.
        if not self.guard_active:
            return True
        if (event.event_type == keyboard.KEY_DOWN
                and event.name == 'f4'
                and keyboard.is_pressed('alt')
                and foreground_is('StarCitizen.exe')):
            now = time.monotonic()
            if now - self.guard_last_log > 1:
                self.guard_last_log = now
                self.log_queue.put('Alt+F4 blocked while Star Citizen is the foreground app.')
            return False
        return True

    # ── Activity tracking ─────────────────────────────────────────────────────

    def _on_key_press(self, event):
        if (event.event_type != keyboard.KEY_DOWN
                or not self.hold_active
                or time.monotonic() < self.injected_until):
            return
        token = self.hold_token
        name = event.name or 'unknown key'
        threading.Thread(target=self._cancel_hold_after_key, args=(name, token), daemon=True).start()

    def _cancel_hold_after_key(self, name, token):
        time.sleep(0.07)
        if self.hold_active and token == self.hold_token and time.monotonic() >= self.injected_until:
            self._release()
            self.log_queue.put('KeepRunning auto-disabled by key press: ' + name)

    # ── Key injection helpers ─────────────────────────────────────────────────

    def _tap(self, key, hold=0.0):
        # Update injected_until around the press so our own output doesn't cancel
        # KeepRunning, and record the tick window so the idle clock ignores it too.
        started = tick()
        self.injected_until = time.monotonic() + 0.20
        if hold > 0:
            # The game polls input on its own frame cadence and can miss a very
            # short tap, so hold the key down briefly.
            keyboard.press(key)
            time.sleep(hold)
            keyboard.release(key)
        else:
            keyboard.press_and_release(key)
        self.injected_until = time.monotonic() + 0.20
        note_injection(started, tick())

    def _hold_seconds(self):
        return random.uniform(KEY_HOLD_MS - KEY_HOLD_JITTER_MS,
                              KEY_HOLD_MS + KEY_HOLD_JITTER_MS) / 1000.0

    def _send_tab(self, source):
        self._tap('tab', self._hold_seconds())
        self.log_queue.put(source + ': sent Tab')

    def _send_keepalive(self):
        """Send the keepalive key, snapping the game forward first if needed."""
        key = self.cfg.get('keepalive_key') or 'tab'
        hold = self._hold_seconds()

        if self.game_foreground:
            self._tap(key, hold)
            self.log_queue.put('Keepalive: sent ' + key.upper())
            return

        # Snap focus: injected input only reaches whichever window has focus,
        # so borrow it for a moment and hand it straight back.
        target = window_for_pid(process_pid('StarCitizen.exe'))
        previous = foreground_hwnd()
        if not force_foreground(target):
            self.log_queue.put('Keepalive: could not bring Star Citizen forward.')
            return
        time.sleep(0.08)          # let the game settle before it reads the key
        try:
            self._tap(key, hold)
        finally:
            if previous and previous != target:
                force_foreground(previous)
        self.log_queue.put('Keepalive: sent ' + key.upper() + ' via snap focus')

    # ── Automation controls ───────────────────────────────────────────────────

    def _toggle_keepalive(self):
        """Keepalive runs by itself; this is only for switching it off."""
        self.keep_active = not self.keep_active
        self.next_keepalive = time.monotonic()
        self.log_queue.put('Keepalive ' + ('enabled.' if self.keep_active else 'disabled.'))
        self._remember_keepalive()

    def _remember_keepalive(self):
        """Persist the on/off state, so a deliberate 'off' survives a restart."""
        self.cfg['keepalive_enabled'] = self.keep_active
        self._persist()

    def _persist(self):
        """Write settings.json, quietly - a failed save must not stop the app."""
        try:
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
        except OSError:
            pass

    def _toggle_altf4_guard(self):
        """Let Alt+F4 through, or start swallowing it again.

        The hook is added and removed rather than left in place returning
        True, so 'off' means the app is not touching the keyboard at all.
        """
        self.guard_active = not self.guard_active
        if self.guard_active and self._alt_hook is None:
            self._alt_hook = keyboard.hook(self._alt_f4_guard, suppress=True)
        elif not self.guard_active and self._alt_hook is not None:
            try:
                keyboard.unhook(self._alt_hook)
            except Exception:
                pass
            self._alt_hook = None
        self.cfg['altf4_guard'] = self.guard_active
        self._persist()
        self.log_queue.put('Alt+F4 protection ' +
                           ('on - Alt+F4 is blocked while the game is in front.'
                            if self.guard_active else
                            'off - Alt+F4 will close the game.'))

    def _toggle_overlay(self, enabled):
        """Create or destroy the floating copy of the header HUD.

        A Toplevel can be made or torn down at any time, unlike the header
        HUD which is only ever built once - so unlike hud_enabled, this one
        needs no restart.
        """
        if enabled and self.overlay is None:
            try:
                x, y = self.cfg.get('overlay_x'), self.cfg.get('overlay_y')
                position = (x, y) if x is not None and y is not None else None
                anchor = None if position else window_rect(
                    window_for_pid(process_pid('StarCitizen.exe')))
                self.overlay = OverlayWindow(
                    self, position=position, anchor_rect=anchor,
                    locked=bool(self.cfg.get('overlay_locked', True)),
                    opacity_percent=int(self.cfg.get('overlay_opacity', 90)),
                    on_dragged=self._overlay_dragged)
                self.log_queue.put('Performance overlay on.')
            except Exception as exc:
                self.overlay = None
                self.log_queue.put('Could not open the performance overlay: %s' % exc)
        elif not enabled and self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None
            self.log_queue.put('Performance overlay off.')

    def _overlay_dragged(self, x, y):
        """Remember where the overlay was dropped, so it starts there next time."""
        self.cfg['overlay_x'] = x
        self.cfg['overlay_y'] = y
        self._persist()

    def _toggle_overlay_lock(self, locked):
        self.cfg['overlay_locked'] = locked
        self._persist()
        if self.overlay is not None:
            self.overlay.set_locked(locked)
        if getattr(self, 'overlay_locked_var', None) is not None:
            self.overlay_locked_var.set(locked)
        self.log_queue.put('Overlay ' + ('locked (click-through).' if locked
                                          else 'unlocked - drag it, then lock it again.'))

    def _toggle_overlay_lock_via_hotkey(self):
        """The global hotkey has no args to carry the new state, unlike the checkbox."""
        self._toggle_overlay_lock(not self.cfg.get('overlay_locked', True))

    def _set_overlay_opacity(self, percent):
        self.cfg['overlay_opacity'] = int(percent)
        self._persist()
        if self.overlay is not None:
            self.overlay.set_opacity(int(percent))

    def _toggle_scan(self):
        self.scan_active = not self.scan_active
        self.next_scan = time.monotonic()
        self.log_queue.put('Ship Scan ' + ('enabled.' if self.scan_active else 'disabled.'))

    def _toggle_automation(self, name):
        if name == 'Keepalive':
            self._toggle_keepalive()
        elif name == 'Ship Scan':
            self.scan_active = not self.scan_active
            if self.scan_active:
                self.next_scan = time.monotonic()
            self.log_queue.put('Ship Scan ' + ('enabled.' if self.scan_active else 'disabled.'))
        elif name == 'KeepRunning':
            if self.hold_active or self.hold_pending:
                self._release()
                self.log_queue.put('KeepRunning toggled off (by click).')
            else:
                self._toggle_hold()

    def _run_macro(self, m):
        if self.running_macro:
            self.log_queue.put('Macro ignored: another macro is running.')
            return
        threading.Thread(target=self._execute_macro, args=(m,), daemon=True).start()

    def _execute_macro(self, m):
        self.running_macro = m['name']
        self.log_queue.put('Macro started: ' + m['name'])
        try:
            for action in m['actions'].split(','):
                action = action.strip()
                if not action:
                    continue
                kind, _, rest = action.partition(':')
                if kind == 'wait' and rest:
                    time.sleep(max(0.0, float(rest)))
                elif kind == 'hold' and ':' in rest:
                    self._hold_action(rest)
                else:
                    self._tap(action)
                time.sleep(float(m.get('delay', 0.1)))
            self.log_queue.put('Macro finished: ' + m['name'])
        except Exception as e:
            self.log_queue.put('Macro error (' + m['name'] + '): ' + str(e))
        finally:
            self.running_macro = ''

    def _hold_action(self, spec):
        """A macro step that holds keys down for a duration, e.g. 'shift+w:1.0'."""
        keys_part, _, seconds_part = spec.partition(':')
        keys = [k.strip() for k in keys_part.split('+') if k.strip()]
        seconds = max(0.0, float(seconds_part))
        started = tick()
        self.injected_until = time.monotonic() + seconds + 0.20
        for key in keys:
            keyboard.press(key)
        time.sleep(seconds)
        for key in reversed(keys):
            keyboard.release(key)
        self.injected_until = time.monotonic() + 0.20
        note_injection(started, tick())

    def _toggle_hold(self):
        # One hotkey controls both states.
        if self.hold_active or self.hold_pending:
            self._release()
            self.log_queue.put('KeepRunning toggled off.')
            return
        keys = [x.strip() for x in self.cfg['hold_keys'].split('+') if x.strip()]
        if not keys:
            self.log_queue.put('No keys configured for KeepRunning.')
            return
        # Wait for the toggle hotkey to be physically released before pressing hold keys.
        self.hold_pending = True
        self.hold_token += 1
        token = self.hold_token
        self.log_queue.put('KeepRunning arming: ' + '+'.join(keys))
        threading.Thread(target=self._activate_hold, args=(keys, token), daemon=True).start()

    def _activate_hold(self, keys, token):
        time.sleep(0.35)
        if self.stop_event.is_set() or token != self.hold_token:
            return
        try:
            started = tick()
            self.injected_until = time.monotonic() + 0.20
            for key in keys:
                keyboard.press(key)
            self.held_keys = keys
            self.hold_active = True
            self.hold_pending = False
            self.injected_until = time.monotonic() + 0.20
            note_injection(started, tick())   # window spans the presses
            self.log_queue.put('KeepRunning toggled on: ' + '+'.join(self.held_keys))
        except Exception as e:
            self.held_keys = []
            self.hold_active = False
            self.hold_pending = False
            self.log_queue.put('Could not hold keys: ' + str(e))

    def _release(self):
        self.hold_token += 1
        self.hold_pending = False
        started = tick()
        for key in reversed(self.held_keys):
            try:
                keyboard.release(key)
            except Exception:
                pass
        if self.held_keys:
            note_injection(started, tick())   # releases are our input too
        if self.hold_active:
            self.log_queue.put('KeepRunning released.')
        self.held_keys = []
        self.hold_active = False

    def _emergency(self):
        self._release()
        for key in ('shift', 'ctrl', 'alt', 'win', 'w', 'a', 's', 'd', 'tab'):
            try:
                keyboard.release(key)
            except Exception:
                pass
        self.log_queue.put('Emergency release sent.')

    # ── Background automation loop ────────────────────────────────────────────

    def _automation_loop(self):
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.game_foreground:
                if self.scan_active and now >= self.next_scan:
                    self._send_tab('Ship Scan')
                    self.next_scan = now + max(1, int(self.cfg['scan_interval']))
            elif self.hold_active or self.hold_pending:
                self._release()
                self.log_queue.put('KeepRunning auto-paused (Star Citizen not foreground)')

            # Snap focus means keepalive only needs the game to be running,
            # not to be in front - being in another window is the usual case.
            if (self.keep_active and self.game_running
                    and self.idle.seconds() >= IDLE_SECONDS
                    and now >= self.next_keepalive):
                self._send_keepalive()
                self.last_keepalive_at = now
                self.next_keepalive = now + random.uniform(KEEPALIVE_MIN_SECONDS,
                                                           KEEPALIVE_MAX_SECONDS)
            time.sleep(0.05)

    # ── Tkinter periodic callbacks ────────────────────────────────────────────

    def _drain_log_queue(self):
        try:
            while True:
                self._log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.after(100, self._drain_log_queue)

    def _refresh_dashboard(self):
        now = time.monotonic()
        if now >= self.game_check_at:
            self.game_running = process_running('StarCitizen.exe')
            self.game_foreground = self.game_running and foreground_is('StarCitizen.exe')
            self.game_check_at = now + 1

        self.guard_button.config(
            text='Enable Alt+F4' if self.guard_active else 'Block Alt+F4',
            bg='#466f91' if self.guard_active else '#a65a46')

        if not self.guard_active:
            self.guard_label.config(
                text='Alt+F4 protection: OFF — Alt+F4 will close the game',
                fg='#e0a0a8')
        elif not self.game_running:
            self.guard_label.config(
                text='Alt+F4 protection: INACTIVE — StarCitizen.exe not detected',
                fg='#9ebee0')
        elif self.game_foreground:
            self.guard_label.config(
                text='Alt+F4 protection: ACTIVE — Star Citizen is foreground; Alt+F4 is blocked',
                fg='#7de0a9')
        else:
            self.guard_label.config(
                text='Alt+F4 protection: ARMED — StarCitizen.exe detected; activates when it is foreground',
                fg='#f3cf7a')

        def update_chip(name, on, suffix=''):
            self.chips[name].config(
                text=name + ': ' + ('ON' if on else 'OFF') + suffix,
                bg='#1f7852' if on else '#253448',
                fg='#effff5' if on else '#b6c5d5',
            )

        update_chip('Keepalive', self.keep_active)
        update_chip('Ship Scan', self.scan_active)
        hold_on = self.hold_active or self.hold_pending
        hold_suffix = ''
        if hold_on:
            hold_suffix = ' (' + ('+'.join(self.held_keys) if self.hold_active else 'arming') + ')'
        update_chip('KeepRunning', hold_on, hold_suffix)
        update_chip('Macro', bool(self.running_macro),
                    ' (' + self.running_macro + ')' if self.running_macro else '')

        status = 'Physical inactivity: ' + str(int(self.idle.seconds())) + 's'
        if self.scan_active:
            status += '   •   Ship Scan Tab in ' + format(max(0, self.next_scan - now), '.1f') + 's'
        if self.keep_active:
            if self.last_keepalive_at is None:
                status += '   •   Keepalive: armed, no tap sent yet'
            else:
                since = int(now - self.last_keepalive_at)
                status += ('   •   Keepalive: last tap %ds ago (cap %ds)'
                          % (since, KEEPALIVE_MAX_SECONDS))
        self.status_var.set(status)

        if not self.stop_event.is_set():
            self.after(200, self._refresh_dashboard)

    def _log(self, text):
        self.log_output.config(state='normal')
        self.log_output.insert('end', '[' + time.strftime('%H:%M:%S') + '] ' + text + '\n')
        self.log_output.see('end')
        self.log_output.config(state='disabled')

    def _clear_log(self):
        self.log_output.config(state='normal')
        self.log_output.delete('1.0', 'end')
        self.log_output.config(state='disabled')

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def close(self):
        # Remembered only from a normal window: a maximised or minimised one
        # would save a size that means nothing once it is neither. geometry()
        # rather than winfo_geometry(): on Windows the latter is the client
        # area, and round-tripping it walks the window down a title bar's
        # height every launch.
        try:
            if self.state() == 'normal' and self.winfo_viewable():
                self.cfg['window_geometry'] = self.geometry()
                self._persist()
        except Exception:
            pass
        self.stop_event.set()
        try:
            self._stop_chord()
        except Exception:
            pass
        try:
            self.fps_monitor.shutdown()
            self.net_monitor.shutdown()
            self.hardware.shutdown()
            self.telemetry.shutdown()
            self.uploader.shutdown()
        except Exception:
            pass
        if self.overlay is not None:
            try:
                self.overlay.destroy()
            except Exception:
                pass
            self.overlay = None
        self.scan_active = False
        self.keep_active = False
        self._emergency()
        try:
            if self._alt_hook is not None:
                keyboard.unhook(self._alt_hook)
        except Exception:
            pass
        try:
            keyboard.unhook_all_hotkeys()
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()


if __name__ == '__main__':
    App().mainloop()

import json
import os
import sys
import time
import threading
import queue
import random
import uuid
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
from helper.hardware import HardwareMonitor, machine_profile
from helper.history import collect as collect_history
from helper.net import NetMonitor, find_game_log, process_pid
from helper.telemetry import (CONTEXT_FIELDS, PROFILE_FIELDS, ROW_FIELDS,
                              SUMMARY_FIELDS, Spool, TelemetryCollector)
from helper.upload import Uploader
from helper.window import (apply_window_icon, force_foreground, foreground_hwnd,
                           set_app_id, window_for_pid)

_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_FILE = os.path.join(_DIR, 'settings.json')

DEFAULTS = {
    'keepalive_enabled':  True,
    'keepalive_key':      'tab',
    'altf4_guard':        True,
    'scan_toggle':        'ctrl+alt+page up',
    'scan_interval':      2,
    'hold_start':         'shift+w+page up',
    'hold_keys':          'shift+w',
    'macros':             [],
    'hud_enabled':        True,
    'auto_update':        True,
    'telemetry_enabled':  True,
    'telemetry_client_id': '',
    'telemetry_notice_seen': False,
    'telemetry_url':      '',
}

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

CRASH_LOG = os.path.join(_DIR, 'assets', 'crash.log')


def _report_crash(exc_type, exc, tb):
    """Record an unhandled error and say so.

    The app runs windowed, with no console behind it, so an uncaught error
    would otherwise vanish with the window. Written down and shown instead.
    """
    import traceback
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
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
        super().__init__()
        self.title('Star Citizen Helper')
        self._apply_icon()
        self.geometry('980x760')
        self.minsize(860, 630)
        self.configure(bg='#101722')
        self.protocol('WM_DELETE_WINDOW', self.close)

        # Load configuration
        self.cfg = DEFAULTS.copy()
        try:
            with open(_SETTINGS_FILE, encoding='utf-8') as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass
        if not isinstance(self.cfg.get('macros'), list):
            self.cfg['macros'] = []

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
        self.net_monitor = NetMonitor()
        self.hardware = HardwareMonitor()
        self.telemetry = self._build_telemetry()
        self.uploader = Uploader(
            self.telemetry.spool, _DIR,
            url_provider=(lambda: self.cfg.get('telemetry_url', '')),
            enabled=(lambda: bool(self.cfg.get('telemetry_enabled', True))),
            on_stop=self._telemetry_stopped_by_server,
        )
        self.hud = None

        # Windows tracks desktop-wide idle time for us; our own taps are
        # filtered out of it so they cannot look like the user coming back.
        self.idle = IdleWatcher()
        self.next_keepalive = 0
        self.next_scan = 0
        self.running_macro = ''

        self._build_ui()
        self._register_hotkeys()

        # Suppressing hook: callback must return True to let a key through, False to block it.
        # Only installed while the guard is on, so switching it off leaves the
        # app with no say over the keyboard at all rather than a hook that
        # happens to pass everything through.
        self._alt_hook = None
        if self.guard_active:
            self._alt_hook = keyboard.hook(self._alt_f4_guard, suppress=True)
        keyboard.on_press(self._on_key_press)

        threading.Thread(target=self._automation_loop, daemon=True).start()
        self.fps_monitor.start()
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

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TNotebook', background='#101722')
        style.configure('TNotebook.Tab', background='#1c2938', foreground='#c9d7e6', padding=(16, 9))
        style.map('TNotebook.Tab', background=[('selected', '#2a4661')])

        # Header
        header = tk.Frame(self, bg='#101722')
        header.pack(fill='x', padx=22, pady=(18, 5))

        # Title on the left, performance HUD on the right, sharing one row.
        title_box = tk.Frame(header, bg='#101722')
        title_box.pack(side='left', anchor='nw')
        BrandMark(title_box, size=46, background='#101722').pack(side='left',
                                                                 anchor='n', padx=(0, 12))
        WordMark(title_box, 'STAR CITIZEN HELPER',
                 'Automation status and hotkey controls',
                 background='#101722', title_fill='#eef6ff',
                 subtitle_fill='#91a7bd').pack(side='left', anchor='nw')

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
            k: tk.StringVar(value=str(v))
            for k, v in self.cfg.items()
            if k != 'macros'
        }
        notebook = ttk.Notebook(self)
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
            [('Toggle hotkey',        'scan_toggle',   'Ctrl+Alt+Page Up'),
             ('Tab interval seconds', 'scan_interval', '2')],
            extra_button=('Toggle Ship Scan', self._toggle_scan))

        self._add_settings_tab(notebook, 'KeepRunning', 'Toggle held keys',
            'Press the same toggle hotkey to start or stop holding the selected keys.',
            [('Toggle hotkey', 'hold_start', 'Shift+W+Page Up'),
             ('Keys to hold',  'hold_keys',  'shift+w')])

        self._build_macros_tab(notebook)
        self._build_perf_tab(notebook)
        self._build_telemetry_tab(notebook)
        self._build_history_tab(notebook)
        self._build_log_tab(notebook)

    def _add_settings_tab(self, notebook, tab_name, title, desc, fields, extra_button=None):
        frame = tk.Frame(notebook, bg='#192433')
        notebook.add(frame, text=tab_name)
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
        frame = tk.Frame(notebook, bg='#192433')
        notebook.add(frame, text='Macros')
        tk.Label(frame, text='Tap Macros', bg='#192433', fg='#eef6ff',
                 font=('Segoe UI Semibold', 14)).pack(anchor='w', padx=20, pady=(18, 4))
        tk.Label(frame,
                 text='Create a global-hotkey macro that taps actions in order. '
                      'Use comma-separated actions such as:  1, 2, tab, shift+w. '
                      'Each action is pressed and released.',
                 bg='#192433', fg='#9eb2c6', wraplength=820, justify='left').pack(
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
            tk.Entry(row, textvariable=var, bg='#0f1721', fg='#eaf4ff',
                     insertbackground='white', relief='flat', width=40).pack(side='left', ipady=5)
            tk.Label(row, text=hint, bg='#192433', fg='#8ca2b9').pack(side='left', padx=8)

        tk.Button(frame, text='Add macro', command=self._add_macro, bg='#2a6f9e',
                  fg='white', relief='flat', padx=16, pady=8).pack(anchor='w', padx=20, pady=(10, 8))
        self.macro_listbox = tk.Listbox(frame, bg='#0f1721', fg='#d9eafa',
                                        selectbackground='#2a6f9e', relief='flat', height=7)
        self.macro_listbox.pack(fill='both', expand=True, padx=20, pady=4)
        tk.Button(frame, text='Remove selected macro', command=self._remove_macro,
                  bg='#a65a46', fg='white', relief='flat', padx=14, pady=7).pack(
                      anchor='w', padx=20, pady=(6, 14))
        self._refresh_macro_list()

    # -- Telemetry ------------------------------------------------------------

    def _build_telemetry(self):
        """The collector. Runs from launch; the enabled check is live.

        Passing a callable rather than a flag means switching it off in the UI
        takes effect on the next sample instead of at the next restart.
        """
        client_id = str(self.cfg.get('telemetry_client_id') or '')
        if not client_id:
            client_id = uuid.uuid4().hex
            self.cfg['telemetry_client_id'] = client_id
            self._persist()
        log = find_game_log()
        return TelemetryCollector(
            Spool(os.path.join(_DIR, 'assets', 'telemetry')),
            fps_stats=self.fps_monitor.stats,
            net_stats=self.net_monitor.stats,
            hardware=self.hardware.readings,
            machine=machine_profile(),
            log_path=find_game_log,
            live_dir=(lambda: log.parent if log else None),
            client_id=client_id,
            enabled=(lambda: bool(self.cfg.get('telemetry_enabled', True))),
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
        frame = tk.Frame(notebook, bg='#101722')
        notebook.add(frame, text='Telemetry')

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
        for label, command in (('Open my data', self._open_telemetry_folder),
                               ('Reset my ID', self._reset_telemetry_id)):
            tk.Button(row, text=label, command=command, bg='#253448', fg='#eef6ff',
                      activebackground='#2a4661', relief='flat', padx=14, pady=6
                      ).pack(side='left', padx=(0, 8))

        endpoint = tk.Frame(frame, bg='#101722')
        endpoint.pack(anchor='w', fill='x', padx=18, pady=(4, 2))
        tk.Label(endpoint, text='Send to', bg='#101722', fg='#91a7bd',
                 font=('Segoe UI', 9), width=9, anchor='w').pack(side='left')
        self.telemetry_url_var = tk.StringVar(value=str(self.cfg.get('telemetry_url', '')))
        entry = tk.Entry(endpoint, textvariable=self.telemetry_url_var, bg='#0f1721',
                         fg='#eaf4ff', insertbackground='#eaf4ff', relief='flat',
                         font=('Consolas', 9))
        entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        tk.Button(endpoint, text='Save', command=self._save_telemetry_url,
                  bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                  relief='flat', padx=12, pady=3).pack(side='left')
        tk.Label(frame, text='Leave this empty and nothing is sent anywhere - measurements '
                             'are still written locally where you can read them.',
                 bg='#101722', fg='#6f8398', font=('Segoe UI', 8),
                 wraplength=760, justify='left').pack(anchor='w', padx=18, pady=(0, 10))

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
        if not str(self.cfg.get('telemetry_url', '')).strip():
            upload_line = 'local only, no endpoint set'
        elif up['status'] == 'waiting' and up['waiting'] > 0:
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
                    str(self.cfg.get('telemetry_client_id', ''))[:12] + '...'))
        self.telemetry_button.config(text='Turn it off' if on else 'Turn it on',
                                     bg='#a65a46' if on else '#466f91')

    def _save_telemetry_url(self):
        """Where batches are posted. Empty means nowhere, which is the default."""
        url = self.telemetry_url_var.get().strip()
        if url and not url.startswith(('http://', 'https://')):
            messagebox.showerror('Star Citizen Helper',
                                 'That needs to start with http:// or https://')
            return
        self.cfg['telemetry_url'] = url
        self._persist()
        self.log_queue.put('Telemetry endpoint ' + (url or 'cleared - nothing is sent.'))
        self._refresh_telemetry_tab()

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

    def _open_telemetry_folder(self):
        folder = self.telemetry.spool.directory
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror('Star Citizen Helper',
                                 'Could not open %s\n%s' % (folder, exc))

    def _reset_telemetry_id(self):
        """A new id, unlinked from everything sent under the old one."""
        self.cfg['telemetry_client_id'] = uuid.uuid4().hex
        self._persist()
        self.telemetry.client = self.cfg['telemetry_client_id']
        self.log_queue.put('Telemetry ID reset.')
        self._refresh_telemetry_tab()

    # ── Performance HUD ───────────────────────────────────────────────────────

    def _build_perf_tab(self, notebook):
        """Frame rate and network detail, alongside the header graph."""
        frame = tk.Frame(notebook, bg='#101722')
        notebook.add(frame, text='Performance')

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

    def _refresh_hud(self):
        """Feed the header graph and the Performance tab, ten times a second."""
        if self.stop_event.is_set():
            return
        try:
            fps_stats = self.fps_monitor.stats()
            net_stats = self.net_monitor.stats()

            if self.hud is not None:
                self.hud.update(fps_stats, net_stats)
                if net_stats.server:
                    region = ('  •  ' + net_stats.region) if net_stats.region not in ('', 'unknown') else ''
                    shard = ('  •  ' + net_stats.shard) if net_stats.shard else ''
                    self.server_label.config(text=net_stats.server + shard + region)
                else:
                    self.server_label.config(text='server unknown - not in a match')

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

        style = ttk.Style(self)
        style.configure('History.Treeview', background='#0f1721', foreground='#eaf4ff',
                        fieldbackground='#0f1721', borderwidth=0, rowheight=26)
        style.configure('History.Treeview.Heading', background='#1c2938',
                        foreground='#9eb2c6', borderwidth=0,
                        font=('Segoe UI Semibold', 9))
        style.map('History.Treeview', background=[('selected', '#2a4661')],
                  foreground=[('selected', '#ffffff')])

        columns = ('joined', 'duration', 'server', 'address')
        widths = (170, 90, 240, 200)
        self.history_view = ttk.Treeview(frame, columns=columns, show='headings',
                                         style='History.Treeview', height=10)
        for name, width in zip(columns, widths):
            self.history_view.heading(name, text=name.title())
            self.history_view.column(name, width=width,
                                     anchor='w' if name != 'duration' else 'e')
        self.history_view.tag_configure('current', foreground='#41b8f5')
        self.history_view.pack(fill='both', expand=True, padx=20)

        row = tk.Frame(frame, bg='#192433')
        row.pack(fill='x', padx=20, pady=12)
        tk.Button(row, text='Refresh', command=self._refresh_history, bg='#2a6f9e',
                  fg='white', relief='flat', padx=16, pady=6).pack(side='left')
        tk.Button(row, text='Copy selected', command=self._copy_history_row, bg='#253448',
                  fg='#eef6ff', relief='flat', padx=16, pady=6).pack(side='left', padx=(8, 0))
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
            return
        try:
            sessions = collect_history(log.parent)
        except Exception as exc:
            self.history_note.config(text='Could not read the logs: %s' % exc)
            return

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
                if action:
                    self._tap(action)
                    time.sleep(float(m.get('delay', 0.1)))
            self.log_queue.put('Macro finished: ' + m['name'])
        except Exception as e:
            self.log_queue.put('Macro error (' + m['name'] + '): ' + str(e))
        finally:
            self.running_macro = ''

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
            status += '   •   Keepalive armed'
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
        self.stop_event.set()
        try:
            self.fps_monitor.shutdown()
            self.net_monitor.shutdown()
            self.hardware.shutdown()
            self.telemetry.shutdown()
            self.uploader.shutdown()
        except Exception:
            pass
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

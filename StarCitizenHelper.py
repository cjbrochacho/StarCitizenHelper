import json
import os
import time
import threading
import queue
import ctypes
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import keyboard

# Performance HUD: frame rate (via RivaTuner) and latency/server details.
# Pure stdlib + ctypes - these add no new requirements.
import sc_theme
from sc_fps import FpsMonitor, start_rtss, rtss_executable
from sc_net import NetMonitor
from sc_hud import HudGraph
from sc_idle import IdleWatcher, note_injection, tick

__version__ = '1.2'

_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_FILE = os.path.join(_DIR, 'settings.json')

DEFAULTS = {
    'keepalive_on':       'shift+tab+page up',
    'keepalive_off':      'shift+tab+page down',
    'keepalive_idle':     60,
    'keepalive_interval': 10,
    'scan_toggle':        'ctrl+alt+page up',
    'scan_interval':      2,
    'hold_start':         'shift+w+page up',
    'hold_keys':          'shift+w',
    'macros':             [],
    'hud_enabled':        True,
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

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Star Citizen Helper v' + __version__)
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
        self.settings_source = (
            'Local settings.json' if os.path.exists(_SETTINGS_FILE)
            else 'Built-in defaults (not saved yet)'
        )

        # Automation state
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.hotkey_handles = []
        self.game_running = False
        self.game_foreground = False
        self.game_check_at = 0
        self.guard_last_log = 0
        self.keep_active = False
        self.scan_active = False
        self.hold_active = False
        self.held_keys = []
        self.hold_pending = False
        self.hold_token = 0
        self.injected_until = 0        # suppresses KeepRunning cancel during bot keypresses
        self.fps_monitor = FpsMonitor()
        self.net_monitor = NetMonitor()
        self.hud = None
        self._rtss_attempt = 0.0

        # Windows tracks desktop-wide idle time for us; our own taps are
        # filtered out of it so they cannot look like the user coming back.
        self.idle = IdleWatcher()
        self.next_keepalive = 0
        self.next_scan = 0
        self.running_macro = ''

        self._build_ui()
        self._register_hotkeys()

        # Suppressing hook: callback must return True to let a key through, False to block it.
        self._alt_hook = keyboard.hook(self._alt_f4_guard, suppress=True)
        keyboard.on_press(self._on_key_press)

        threading.Thread(target=self._automation_loop, daemon=True).start()
        self.fps_monitor.start()
        self.net_monitor.start()
        self.after(100, self._drain_log_queue)
        self.after(200, self._refresh_dashboard)
        self.after(100, self._refresh_hud)
        self._log('Ready. Global hotkeys registered.')

    # ── UI construction ───────────────────────────────────────────────────────

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
        title_box.pack(side='left', anchor='w')
        tk.Label(title_box, text='STAR CITIZEN HELPER v' + __version__, bg='#101722',
                 fg='#eef6ff', font=('Segoe UI Semibold', 18)).pack(anchor='w')
        tk.Label(title_box, text='Automation status and hotkey controls',
                 bg='#101722', fg='#91a7bd').pack(anchor='w')

        if self.cfg.get('hud_enabled', True):
            hud_box = tk.Frame(header, bg=sc_theme.BG)
            hud_box.pack(side='right', anchor='e', fill='x', expand=True, padx=(40, 0))
            self.hud = HudGraph(hud_box, on_start_rtss=self._start_rtss)
            self.hud.configure(width=460)
            self.hud.pack(fill='x', expand=True)
            self.server_label = tk.Label(hud_box, text='', bg=sc_theme.BG,
                                         fg=sc_theme.MUTED, font=('Consolas', 8),
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

        self.guard_label = tk.Label(
            panel,
            text='Alt+F4 protection: checking for StarCitizen.exe…',
            bg='#192433', fg='#9ebee0', font=('Segoe UI Semibold', 10),
        )
        self.guard_label.pack(anchor='w', padx=14, pady=(0, 10))

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

        self.json_status_var = tk.StringVar()
        tk.Label(controls, textvariable=self.json_status_var, bg='#192433',
                 fg='#9ebee0', anchor='w').pack(fill='x', padx=14, pady=(2, 10))
        self._update_settings_label()

        # Tab notebook
        self.field_vars = {
            k: tk.StringVar(value=str(v))
            for k, v in self.cfg.items()
            if k != 'macros'
        }
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=22, pady=(0, 10))

        self._add_settings_tab(notebook, 'Keepalive', 'Inactivity keepalive',
            'After no physical mouse/keyboard activity, sends Tab at the chosen interval.',
            [('Enable hotkey',        'keepalive_on',       'Shift+Tab+Page Up'),
             ('Disable hotkey',       'keepalive_off',      'Shift+Tab+Page Down'),
             ('Idle seconds',         'keepalive_idle',     '60'),
             ('Tab interval seconds', 'keepalive_interval', '10')])

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

    # ── Performance HUD ───────────────────────────────────────────────────────

    def _build_perf_tab(self, notebook):
        """Frame rate and network detail, alongside the header graph."""
        frame = tk.Frame(notebook, bg='#101722')
        notebook.add(frame, text='Performance')

        tk.Label(frame, text='Performance & Server', bg='#101722', fg='#eef6ff',
                 font=('Segoe UI Semibold', 13)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, text='Frame rate comes from RivaTuner Statistics Server. Latency is '
                             'measured to the datacenter the game connects to - the sim server itself '
                             'answers no probes.',
                 bg='#101722', fg='#91a7bd', wraplength=760, justify='left'
                 ).pack(anchor='w', padx=18, pady=(0, 12))

        self.perf_rows = {}
        grid = tk.Frame(frame, bg='#101722')
        grid.pack(anchor='w', padx=18, fill='x')
        for row, label in enumerate(('Frame rate', 'Frame time', '1% low',
                                     'Server', 'Shard', 'Region', 'Latency', 'Jitter')):
            tk.Label(grid, text=label, bg='#101722', fg='#91a7bd',
                     font=('Segoe UI', 9), width=12, anchor='w').grid(row=row, column=0,
                                                                      sticky='w', pady=2)
            value = tk.Label(grid, text='--', bg='#101722', fg='#eef6ff',
                             font=('Consolas', 10), anchor='w')
            value.grid(row=row, column=1, sticky='w', pady=2)
            self.perf_rows[label] = value

        self.rtss_button = tk.Button(frame, text='Start RivaTuner', command=self._start_rtss,
                                     bg='#253448', fg='#eef6ff', activebackground='#2a4661',
                                     relief='flat', padx=14, pady=6)
        self.rtss_button.pack(anchor='w', padx=18, pady=(16, 4))
        self.rtss_note = tk.Label(frame, text='', bg='#101722', fg='#91a7bd',
                                  wraplength=760, justify='left')
        self.rtss_note.pack(anchor='w', padx=18)

    def _start_rtss(self):
        """RivaTuner needs administrator rights, so this raises a UAC prompt."""
        if time.monotonic() - self._rtss_attempt < 8.0:
            return  # it takes a moment to appear; do not stack UAC prompts
        self._rtss_attempt = time.monotonic()
        problem = start_rtss()
        if problem is None:
            self.log_queue.put('Starting RivaTuner - approve the administrator prompt if asked.')
        else:
            self.log_queue.put('RivaTuner: ' + problem)

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

            if getattr(self, 'perf_rows', None):
                fps_ok = fps_stats.status == 'ok'
                net_ok = net_stats.status == 'ok'
                self.perf_rows['Frame rate'].config(
                    text=('%.0f fps  (avg %.0f)' % (fps_stats.fps, fps_stats.average)) if fps_ok else '--')
                self.perf_rows['Frame time'].config(
                    text=('%.1f ms' % fps_stats.frame_time_ms) if fps_ok else '--')
                self.perf_rows['1% low'].config(
                    text=('%.0f fps' % fps_stats.low_1) if fps_ok else '--')
                self.perf_rows['Server'].config(text=net_stats.server or '--')
                self.perf_rows['Shard'].config(text=net_stats.shard or '--')
                self.perf_rows['Region'].config(text=net_stats.region or '--')
                self.perf_rows['Latency'].config(
                    text=('%.0f ms  (avg %.1f, %.0f%% loss)'
                          % (net_stats.ping_ms, net_stats.average, net_stats.loss_pct))
                    if net_ok else '--')
                self.perf_rows['Jitter'].config(
                    text=('%.2f ms' % net_stats.jitter) if net_ok else '--')

            if getattr(self, 'rtss_note', None):
                if fps_stats.status == 'no_rtss':
                    self.rtss_note.config(
                        text='RivaTuner is not running, so there is no frame data. It must be '
                             'started before Star Citizen to hook the game.'
                        if rtss_executable() else
                        'RivaTuner Statistics Server is not installed - frame rate unavailable.')
                    self.rtss_button.config(state='normal' if rtss_executable() else 'disabled')
                elif fps_stats.status == 'no_game':
                    self.rtss_note.config(text='RivaTuner is running; waiting for the game.')
                    self.rtss_button.config(state='disabled')
                else:
                    self.rtss_note.config(text='')
                    self.rtss_button.config(state='disabled')
        except Exception as exc:               # never let the HUD kill the UI loop
            self.log_queue.put('HUD error: %s' % exc)
        self.after(100, self._refresh_hud)

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

    def _update_settings_label(self):
        self.json_status_var.set(
            'Active settings: ' + self.settings_source + '   •   Local file: ' + _SETTINGS_FILE)

    def _sync_fields_to_cfg(self):
        for k, var in self.field_vars.items():
            if k.endswith(('idle', 'interval')):
                self.cfg[k] = int(var.get())
            else:
                self.cfg[k] = var.get().strip().lower()

    def _save(self):
        try:
            self._sync_fields_to_cfg()
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
            self.settings_source = 'Local settings.json (saved)'
            self._update_settings_label()
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
            self.settings_source = 'Local settings.json • backup created: ' + os.path.basename(path)
            self._update_settings_label()
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
            for k in ('keepalive_idle', 'keepalive_interval', 'scan_interval'):
                if k in imported:
                    imported[k] = int(imported[k])
            self.cfg.update(imported)
            for k, var in self.field_vars.items():
                var.set(str(self.cfg[k]))
            self._refresh_macro_list()
            with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2)
            self.settings_source = 'Imported JSON: ' + os.path.basename(path)
            self._update_settings_label()
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
                ('keepalive_on',  self._enable_keepalive),
                ('keepalive_off', self._disable_keepalive),
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

    def _tap(self, key):
        # Update injected_until around the press so our own output doesn't cancel
        # KeepRunning, and record the tick window so the idle clock ignores it too.
        started = tick()
        self.injected_until = time.monotonic() + 0.20
        keyboard.press_and_release(key)
        self.injected_until = time.monotonic() + 0.20
        note_injection(started, tick())

    def _send_tab(self, source):
        self._tap('tab')
        self.log_queue.put(source + ': sent Tab')

    # ── Automation controls ───────────────────────────────────────────────────

    def _enable_keepalive(self):
        self.keep_active = True
        self.log_queue.put('Keepalive enabled.')

    def _disable_keepalive(self):
        self.keep_active = False
        self.log_queue.put('Keepalive disabled.')

    def _toggle_scan(self):
        self.scan_active = not self.scan_active
        self.next_scan = time.monotonic()
        self.log_queue.put('Ship Scan ' + ('enabled.' if self.scan_active else 'disabled.'))

    def _toggle_automation(self, name):
        if name == 'Keepalive':
            self.keep_active = not self.keep_active
            self.log_queue.put('Keepalive ' + ('enabled.' if self.keep_active else 'disabled.'))
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
                if (self.keep_active
                        and self.idle.seconds() >= max(1, int(self.cfg['keepalive_idle']))
                        and now >= self.next_keepalive):
                    self._send_tab('Keepalive')
                    self.next_keepalive = now + max(1, int(self.cfg['keepalive_interval']))
            else:
                if self.hold_active or self.hold_pending:
                    self._release()
                    self.log_queue.put('KeepRunning auto-paused (Star Citizen not foreground)')
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

        if not self.game_running:
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

        paused = ' (paused)' if not self.game_foreground else ''

        def update_chip(name, on, suffix=''):
            self.chips[name].config(
                text=name + ': ' + ('ON' if on else 'OFF') + suffix,
                bg='#1f7852' if on else '#253448',
                fg='#effff5' if on else '#b6c5d5',
            )

        update_chip('Keepalive', self.keep_active, paused)
        update_chip('Ship Scan', self.scan_active, paused)
        hold_on = self.hold_active or self.hold_pending
        hold_suffix = ''
        if hold_on:
            hold_suffix = ' (' + ('+'.join(self.held_keys) if self.hold_active else 'arming') + ')'
        update_chip('KeepRunning', hold_on, hold_suffix + paused)
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
        except Exception:
            pass
        self.scan_active = False
        self.keep_active = False
        self._emergency()
        try:
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

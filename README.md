# Star Citizen Helper v1.2

A Windows utility that automates common keyboard actions in Star Citizen so you can stay active in-game hands-free — keepalive Tab presses, continuous ship scanning, held-key macros (Shift+W), and fully custom hotkey macros.

---

## Requirements

- Windows 10 or 11
- Python 3.8 or newer — [Microsoft Store](https://apps.microsoft.com/search?query=python) (easiest) or [python.org](https://www.python.org/downloads/)

---

## Getting started

1. **Install Python** if you haven't already (see above). The Microsoft Store version adds Python to your PATH automatically.

2. **Double-click `Run_StarCitizenHelper.bat`.**
   - It installs the required packages on first run.
   - On subsequent launches it verifies them and starts the app immediately.

3. **Launch Star Citizen.** The app detects `StarCitizen.exe` automatically — all automations are gated and only fire while Star Citizen is the foreground window.

> If Python isn't found, the launcher walks you through installing it and fixing your PATH. You do not need to open a terminal manually.

---

## The interface

At the top of the window is the **ACTIVE AUTOMATIONS** bar — four status chips (Keepalive, Ship Scan, KeepRunning, Macro). Each chip shows whether that automation is ON or OFF and can be clicked to toggle it without using a hotkey.

Below that are the control buttons, which are always visible regardless of which tab is open:

| Button | What it does |
|---|---|
| **Save Settings** | Writes current field values to `settings.json` and re-registers all hotkeys |
| **Backup Settings** | Saves a copy of your current config to `Documents\StarCitizenHelper_hotkeys_backup.json` |
| **Import Settings** | Loads a previously backed-up config file |
| **Stop & Release** | Releases KeepRunning held keys |
| **EMERGENCY DISABLE ALL** | Releases Shift, Ctrl, Alt, Win, W, A, S, D, and Tab immediately |

---

## Features

### Keepalive

Automatically sends a **Tab keypress** after a period of physical inactivity, then repeats at a set interval. Useful for staying active in-game while you're AFK at the keyboard.

**Default hotkeys**

| | Hotkey |
|---|---|
| Enable | `Shift + Tab + Page Up` |
| Disable | `Shift + Tab + Page Down` |

**Settings**

| Field | Default | Description |
|---|---|---|
| Idle seconds | `60` | How long with no mouse/keyboard input before the first Tab is sent |
| Tab interval seconds | `10` | How often Tab is sent after the idle threshold is crossed |

**Example:** with defaults, if you haven't touched the mouse or keyboard for 60 seconds, Tab is sent. It then repeats every 10 seconds until you move the mouse or press a key.

---

### Ship Scan

Continuously sends Tab on a fixed interval, **regardless of whether you are actively using input**. Designed for cycling through contacts on your ship's scanning UI.

**Default hotkey:** `Ctrl + Alt + Page Up` (toggle on/off)

**Settings**

| Field | Default | Description |
|---|---|---|
| Tab interval seconds | `2` | How often Tab is sent |

You can also click the **Toggle Ship Scan** button inside the tab, or click the Ship Scan chip in the top bar.

---

### KeepRunning

Holds one or more keys down continuously — by default `Shift + W` (run forward). A single hotkey acts as a toggle: press once to start, press again to stop.

**Default hotkey:** `Shift + W + Page Up` (toggle on/off)

**Settings**

| Field | Default | Description |
|---|---|---|
| Keys to hold | `shift+w` | Keys held down, `+`-separated |

**Auto-release conditions**
- You press any physical key
- Star Citizen loses foreground focus (e.g. you Alt-Tab away)

There is a **350 ms arming delay** after you press the hotkey, so the keys you used for the toggle combo are fully released before the hold begins.

**Example:** pressing `Shift + W + Page Up` starts holding Shift+W so your character runs. Pressing the same hotkey again (or any other key) releases them.

---

### Macros

Create named hotkey macros that tap a sequence of keys in order. Each macro is registered as a global hotkey and runs in the background without blocking the UI.

**To add a macro:**

1. Open the **Macros** tab.
2. Fill in the fields:

| Field | Example | Description |
|---|---|---|
| Name | `Countermeasures` | Display label shown while the macro runs |
| Hotkey | `ctrl+alt+1` | Global hotkey that triggers this macro |
| Actions | `h, h, h` | Comma-separated keys tapped in sequence |
| Delay between actions | `0.10` | Seconds between each keypress |

3. Click **Add macro**. It is saved and registered immediately.

**Action syntax** — use the same format as keyboard shortcuts:
- Single keys: `1`, `2`, `tab`, `f`, `space`, `enter`
- Modified keys: `shift+w`, `ctrl+c`, `alt+f4`
- Sequences: `1, 2, tab, shift+w` — each item is pressed and released individually

**Example macros**

| Name | Hotkey | Actions | Delay | What it does |
|---|---|---|---|---|
| Countermeasures | `ctrl+alt+1` | `h, h, h` | `0.10` | Taps H three times (deploy countermeasures) |
| Power reset | `ctrl+alt+2` | `u, u, u` | `0.15` | Cycles power systems |
| Quick shield cycle | `ctrl+alt+3` | `f5, f6, f7` | `0.20` | Redistributes shield power |

Only one macro can run at a time — triggering a second while one is active logs a warning and does nothing.

---

## Hotkey reference

| Feature | Default hotkey | Notes |
|---|---|---|
| Keepalive ON | `Shift + Tab + Page Up` | |
| Keepalive OFF | `Shift + Tab + Page Down` | |
| Ship Scan toggle | `Ctrl + Alt + Page Up` | |
| KeepRunning toggle | `Shift + W + Page Up` | Single hotkey starts and stops |
| Custom macros | User-defined | Set in the Macros tab |

All hotkeys can be changed in their respective tabs. Click **Save Settings** after editing.

---

## Settings file

`settings.json` is stored in the same folder as the app and loaded automatically on startup. You can edit it by hand or use **Backup / Import** in the app.

```json
{
  "keepalive_on":       "shift+tab+page up",
  "keepalive_off":      "shift+tab+page down",
  "keepalive_idle":     60,
  "keepalive_interval": 10,
  "scan_toggle":        "ctrl+alt+page up",
  "scan_interval":      2,
  "hold_start":         "shift+w+page up",
  "hold_keys":          "shift+w",
  "macros": [
    {
      "name":    "Countermeasures",
      "hotkey":  "ctrl+alt+1",
      "actions": "h, h, h",
      "delay":   0.1
    }
  ]
}
```

---

## Alt+F4 protection

The app registers a low-level keyboard hook that intercepts Alt+F4 **only when Star Citizen is the foreground window**, preventing an accidental keypress from closing the game. The guard label in the top bar shows its current state:

- **INACTIVE** — StarCitizen.exe is not running
- **ARMED** — StarCitizen.exe is running but not in the foreground
- **ACTIVE** — Star Citizen is foreground; Alt+F4 is blocked

---

## Troubleshooting

**The launcher says Python was not found.**
Install Python from the Microsoft Store or python.org (tick "Add Python to PATH" during install). Then close and re-run the launcher.

**Hotkeys don't respond in-game.**
Make sure Star Citizen is the active foreground window. All automations are paused when any other window is in focus.

**KeepRunning stops immediately after starting.**
A physical key press cancels it. Make sure you fully release the toggle hotkey before pressing other keys — the 350 ms arming delay handles most cases, but fast typists may need a slightly longer delay (not currently configurable).

**A macro hotkey conflicts with a game binding.**
Choose a combo that Star Citizen doesn't use, e.g. `ctrl+alt+1` through `ctrl+alt+9`.

**The app window disappears instantly on launch.**
Run `Run_StarCitizenHelper.bat` directly — it pauses on errors so you can read the message. If a dependency failed to install, try running the bat as Administrator.

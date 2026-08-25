# Star Citizen Helper

A Windows utility for Star Citizen. It keeps your session alive while you are away, automates
the keypresses you would otherwise spam by hand, and shows what your frame rate and your
connection are actually doing — all from one window that sits behind the game.

- **Keepalive** — taps a key once you have genuinely stepped away, so the server never drops you
- **Ship Scan** — repeats Tab on a timer for cycling scan contacts
- **KeepRunning** — holds Shift+W (or any keys) down so you keep moving
- **Macros** — your own hotkeys, each tapping a sequence of keys
- **Performance HUD** — frame rate, latency, and which server and shard you are on
- **Alt+F4 guard** — swallows Alt+F4 while the game has focus

---

## Getting started

**Double-click `StarCitizenHelper.bat`.** That is the only thing you ever run.

The first launch sets everything up — installs Python if you don't have it, fetches the one
package it needs, draws the icon, and puts a **Star Citizen Helper** shortcut on your desktop —
then starts the app. Every launch after that just starts the app.

There is no separate installer step to remember. Each step checks first and acts only if
something is missing, so a normal launch adds roughly 150 ms.

> You never need to open a terminal. If Python cannot be installed automatically, the script
> opens the download page and tells you which box to tick.

Then **launch Star Citizen**. The app finds `StarCitizen.exe` on its own.

### Requirements

- Windows 10 or 11
- Python 3.8+ — installed for you if missing, per-user, so no administrator prompt
- *Optional:* [RivaTuner Statistics Server](https://www.guru3d.com/download/rtss-rivatuner-statistics-server-download/)
  for the frame-rate half of the HUD. You already have it if you use MSI Afterburner.
  Everything else works without it.

The only third-party package is `keyboard`. Everything else is the Python standard library.

### How Python gets installed

In order: **winget** (built into Windows 11) for Python 3.13, then 3.12, then 3.11 — and if
winget is missing or fails, a **direct download from python.org** matching your machine, x64 or
ARM64 or 32-bit. Both routes install per-user, so neither needs administrator rights.

Two things that trip naive scripts up are handled: the **Microsoft Store `python.exe` stub**,
which sits on PATH even with no Python installed and opens the Store rather than reporting a
version; and the fact that **PATH is never refreshed inside an already-open window**, so
straight after an install it looks where Python actually landed instead of trusting PATH.

### Desktop shortcut

Created on first launch. Delete it and it stays deleted — it is not put back behind your back.
If you move the project folder, delete `assets/.shortcut-made` and start the app once to get a
shortcut pointing at the new location.

A shortcut cannot simply be shipped in the repository: a `.lnk` stores absolute paths — to the
target, its working directory, *and* the Python interpreter — so one built on another machine
would point at folders you do not have.

---

## The window

The header shows the **performance HUD** with the current server underneath. Below it, the
**ACTIVE AUTOMATIONS** bar holds four chips — Keepalive, Ship Scan, KeepRunning, Macro — each
showing ON or OFF, and each clickable to toggle without touching a hotkey. Under that is the
Alt+F4 guard state and how long you have been physically inactive.

These buttons are always visible, whichever tab is open:

| Button | What it does |
|---|---|
| **Save Settings** | Writes the fields to `settings.json` and re-registers every hotkey |
| **Backup Settings (.json)** | Copies your config to `Documents\StarCitizenHelper_hotkeys_backup.json` |
| **Import Settings (.json)** | Loads a previously backed-up config |
| **Stop & Release** | Releases any keys KeepRunning is holding |
| **EMERGENCY DISABLE ALL** | Instantly releases Shift, Ctrl, Alt, Win, W, A, S, D and Tab |

Six tabs: **Keepalive**, **Scan Ships**, **KeepRunning**, **Macros**, **Performance**,
**Activity Log**.

---

## Hotkeys

| Feature | Default | Notes |
|---|---|---|
| Keepalive on | `Shift + Tab + Page Up` | |
| Keepalive off | `Shift + Tab + Page Down` | |
| Ship Scan toggle | `Ctrl + Alt + Page Up` | |
| KeepRunning toggle | `Shift + W + Page Up` | One hotkey starts and stops |
| Macros | yours to choose | Set per macro in the Macros tab |

All of them are editable in their tab. Click **Save Settings** afterwards. Hotkeys are global
and do not swallow the keys, so the game still sees them.

---

## Keepalive

Sends a key once you have not touched the keyboard or mouse for a while, then repeats, so the
server never counts you as idle.

Idle time comes from Windows itself, which tracks it desktop-wide. That catches input a hook can
miss, including inside a fullscreen game, and it needs no extra library. Keys this app sends are
filtered back out of that reading, so its own taps cannot be mistaken for you coming back — the
clock keeps running across them.

| Field | Default | Description |
|---|---|---|
| Enable hotkey | `shift+tab+page up` | |
| Disable hotkey | `shift+tab+page down` | |
| Idle seconds | `60` | Quiet time before the first key is sent |
| Interval seconds | `10` | How often it repeats while you stay away |
| Key to send | `tab` | See below |
| Snap focus (on/off) | `off` | Keep working while you are in another window |
| Key hold ms | `40` | How long the key is held. Applies to Ship Scan too |

**Which key.** `tab` fires the ship scanner every time, which you will see on screen. `f13`–`f24`
and `scroll lock` do not exist on a normal keyboard and are unbound in Star Citizen, so they keep
you active without anything happening. The catch: an unbound key may not count as activity if the
game's idle timer only counts inputs it has a binding for. If you still get dropped on `f13`, go
back to `tab`.

**Snap focus.** Normally keepalive only fires while Star Citizen is the active window, so it does
nothing while you are in a browser. Switch this on and it pulls the game forward for the tap and
hands focus straight back, about a tenth of a second. Injected keys only ever reach the focused
window, so this is the only way to reach the game from another app without elevation. Invisible
while you are genuinely away; disruptive if you are typing, and jarring if the game runs in
exclusive fullscreen rather than borderless.

**Key hold.** The game polls input on its own frame cadence and can miss a very short tap. Raise
to ~80 ms if presses are not registering.

---

## Ship Scan

Repeats Tab on a fixed interval whether or not you are using the keyboard — for cycling contacts
on the scanning UI.

| Field | Default | Description |
|---|---|---|
| Toggle hotkey | `ctrl+alt+page up` | |
| Tab interval seconds | `2` | Floors at 1 second |

**Key hold ms** on the Keepalive tab applies here too. You can also use the **Toggle Ship Scan**
button in the tab, or the Ship Scan chip.

---

## KeepRunning

Holds keys down until you stop it — `shift+w` by default, so your character keeps running. One
hotkey toggles both ways.

| Field | Default | Description |
|---|---|---|
| Toggle hotkey | `shift+w+page up` | |
| Keys to hold | `shift+w` | `+`-separated |

It releases automatically when you press any physical key, or when Star Citizen stops being the
foreground window. There is a **350 ms arming delay** after the hotkey so the keys in the combo
are fully released before the hold starts.

---

## Macros

Named hotkeys that tap a sequence of keys in order. Each is registered globally and runs off the
UI thread, so nothing blocks.

| Field | Example | Description |
|---|---|---|
| Name | `Countermeasures` | Shown while it runs |
| Hotkey | `ctrl+alt+1` | Global, triggers this macro |
| Actions | `h, h, h` | Comma-separated keys, tapped in order |
| Delay between actions | `0.10` | Seconds between each |

Actions take the same syntax as hotkeys: single keys (`1`, `tab`, `space`), modified keys
(`shift+w`, `ctrl+c`), or a sequence (`1, 2, tab`) where each item is pressed and released on its
own. Only one macro runs at a time; triggering a second while one is going logs a warning and
does nothing. Pick combos the game does not use — `ctrl+alt+1` through `ctrl+alt+9` are safe.

---

## Performance HUD

A rolling 60-second graph in the header, two sparklines sharing one canvas, each scaled
independently. Both are sampled **every 100 ms**, so the two move together:

- **cyan — frame rate**, riding the top when it is high
- **amber — latency**, riding the bottom when it is low

so "all good" reads as two lines hugging opposite edges. Underneath sits the current server:
`IP:port • shard • region`. Readouts are to two decimal places.

The **Performance** tab breaks the same figures out in full: frame rate, frame time, 1% low,
server, shard, region, latency and jitter.

**Frame rate** comes from RivaTuner's shared memory. RTSS has to be running *before* Star Citizen
starts — it hooks a game as the game launches and cannot attach to one already running. If RTSS
is installed but not running the graph says so, and the Performance tab offers a **Start
RivaTuner** button. RTSS needs administrator rights, so expect a UAC prompt.

**Latency** is measured to the datacenter the game is connected to. The sim server itself answers
nothing — not ICMP, not TCP on any port — so the ping goes to the backend host the game holds a
live connection to, in the same cloud region. No elevation needed.

It is timed around the call rather than read from the reply's round-trip field, which reports
whole milliseconds only: too coarse for a decimal, and too coarse for jitter, which varies by less
than that step. The trade-off is a reading a fraction of a millisecond above what `ping.exe` would
say, since the measurement includes the API call as well as the network.

**There is no player count**, deliberately. The client is only ever told about itself and the
entities streamed in around it, never the shard head-count, so any number here would be invented.

Set `"hud_enabled": false` in `settings.json` to hide the header graph.

---

## Alt+F4 protection

A low-level hook swallows Alt+F4 **only while Star Citizen is the foreground window**, so a
mis-hit cannot close the game. The header shows its state:

- **INACTIVE** — `StarCitizen.exe` is not running
- **ARMED** — running, but not in front
- **ACTIVE** — in front, and Alt+F4 is being blocked

---

## Settings file

`settings.json` sits next to the app and loads at startup. Edit it by hand or use
**Backup** / **Import**.

```json
{
  "keepalive_on":       "shift+tab+page up",
  "keepalive_off":      "shift+tab+page down",
  "keepalive_idle":     60,
  "keepalive_interval": 10,
  "keepalive_key":      "tab",
  "keepalive_snap":     "off",
  "key_hold_ms":        40,
  "scan_toggle":        "ctrl+alt+page up",
  "scan_interval":      2,
  "hold_start":         "shift+w+page up",
  "hold_keys":          "shift+w",
  "hud_enabled":        true,
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

## Project layout

```
StarCitizenHelper.py     the app
StarCitizenHelper.bat    the only thing you run: sets up if needed, then launches

helper/
  __init__.py            marks the package
  fps.py                 frame data from the RTSS shared memory block
  net.py                 latency, and server/shard/region from the game's log
  hud.py                 the header graph and its readout
  idle.py                desktop-wide idle detection
  window.py              finds the game window; snap focus; taskbar icon
  brand.py               the radar mark and the header wordmark
  theme.py               colour palette
  shortcut.py            draws the icon and writes the .lnk

assets/                  generated icon and shortcut marker (git-ignored)
settings.json            written on first save (git-ignored)
```

Everything under `helper/` is standard library plus `ctypes`.

---

## Troubleshooting

**Python was not found.**
Run `StarCitizenHelper.bat` — it installs Python for you. If it cannot reach the internet, install
by hand from [python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH", and
run it again.

**Hotkeys do nothing in-game.**
Star Citizen has to be the active window. Ship Scan, KeepRunning and keepalive are all paused
while another window has focus — the exception is keepalive with **snap focus** on, which is
built to work while you are elsewhere.

**Keys register in Notepad but not in the game.**
Raise **Key hold ms** to around 80. The game can miss a very short tap.

**The frame-rate line is blank, or says "waiting for the game".**
RivaTuner has to start *before* Star Citizen. Start RTSS, then restart the game. Latency works
either way.

**Latency shows `--`.**
The ping targets a backend host the game itself is connected to, so there is no target until the
game is running and signed in.

**KeepRunning stops the moment it starts.**
A physical keypress cancels it. Release the toggle combo fully first — the 350 ms arming delay
covers most cases.

**The window vanishes instantly, or never appears.**
The app runs windowed, with no console behind it, so an unhandled error is written to
`assets/crash.log` and shown in a message box. Check that file. Startup problems that the
launcher can catch — no Python, no tkinter, missing package — are reported by
`StarCitizenHelper.bat` before it hands over.

---

## A note on automation

Automating input sits in a grey area in most games' terms of service. Everything here only
repeats keys you could press yourself, but use it at your own risk.

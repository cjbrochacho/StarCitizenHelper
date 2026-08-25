# Star Citizen Helper

A Windows utility for Star Citizen. It keeps your session alive while you are away, automates
the keypresses you would otherwise spam by hand, and shows what your frame rate and your
connection are actually doing — all from one window that sits behind the game.

- **Keepalive** — runs by itself, tapping a key once you have genuinely stepped away, so the server never drops you
- **Ship Scan** — repeats Tab on a timer for cycling scan contacts
- **KeepRunning** — holds Shift+W (or any keys) down so you keep moving
- **Macros** — your own hotkeys, each tapping a sequence of keys
- **Performance HUD** — frame rate, latency, and which server and shard you are on
- **Server history** — the last ten shards you were on, so a crash cannot lose your ship
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

Seven tabs: **Keepalive**, **Scan Ships**, **KeepRunning**, **Macros**, **Performance**,
**Server History**, **Activity Log**.

---

## Hotkeys

| Feature | Default | Notes |
|---|---|---|
| Ship Scan toggle | `Ctrl + Alt + Page Up` | |
| KeepRunning toggle | `Shift + W + Page Up` | One hotkey starts and stops |
| Macros | yours to choose | Set per macro in the Macros tab |

All of them are editable in their tab. Click **Save Settings** afterwards. Hotkeys are global
and do not swallow the keys, so the game still sees them.

Keepalive has no hotkey: it runs on its own, and the **Toggle Keepalive** button on its tab —
or the Keepalive chip — is there for the times you want it off.

---

## Keepalive

**Runs by itself. There is nothing to switch on.** It stays quiet while you are at the
computer, and sends a key only once the keyboard and mouse have been still for the idle time,
then repeats, so the server never counts you as idle.

To turn it off, use **Toggle Keepalive** on its tab or click the Keepalive chip. That choice is
remembered, so an off stays off across restarts.

Idle time comes from Windows itself, which tracks it desktop-wide. That catches input a hook can
miss, including inside a fullscreen game, and it needs no extra library. Keys this app sends are
filtered back out of that reading, so its own taps cannot be mistaken for you coming back — the
clock keeps running across them.

| Field | Default | Description |
|---|---|---|
| Key to send | `tab` | See below |
| Snap focus (on/off) | `off` | Keep working while you are in another window |

It waits for **a minute** of no keyboard or mouse, then sends a key every **30 seconds to 3
minutes** — a fresh gap drawn each time — while you stay away. These live as constants at the
top of `StarCitizenHelper.py` rather than as settings, since nothing about the game makes one
number right and another wrong.

**The upper bound matters.** It has to stay below the game's own idle timeout or you get
dropped anyway, which is the thing this exists to prevent. That threshold is not something
this tool can measure, so if 3 minutes turns out to be too long, lower
`KEEPALIVE_MAX_SECONDS`.

**Which key.** `tab` fires the ship scanner every time, which you will see on screen. `f13`–`f24`
and `scroll lock` do not exist on a normal keyboard and are unbound in Star Citizen, so they keep
you active without anything happening. The catch: an unbound key may not count as activity if the
game's idle timer only counts inputs it has a binding for. If you still get dropped on `f13`, go
back to `tab`.

The choice also decides what leaves your machine. A bound key like `tab` produces a real in-game
action, which the server handles and can timestamp like any other. An unbound key fires nothing,
so there is no action for anything upstream to see — the client keeps streaming its usual state
either way.

Worth being clear-eyed about: varying the gap defeats a naive "exactly every N seconds" pattern,
but the thing that marks out an idle keepalive is not its rhythm, it is that nothing else is
happening. No amount of jitter makes an otherwise silent session look like someone playing.
Automation is against the spirit of most games' rules whatever the timing, which is why the note
at the bottom of this file says what it says.

**Snap focus.** Normally keepalive only fires while Star Citizen is the active window, so it does
nothing while you are in a browser. Switch this on and it pulls the game forward for the tap and
hands focus straight back, about a tenth of a second. Injected keys only ever reach the focused
window, so this is the only way to reach the game from another app without elevation. Invisible
while you are genuinely away; disruptive if you are typing, and jarring if the game runs in
exclusive fullscreen rather than borderless.

**How long the key is held** varies between roughly 28 and 52 ms rather than being the same
every time — a keypress held for exactly the same duration on every repeat is not something a
person does, and the game only needs the press to outlast a frame.

---

## Ship Scan

Repeats Tab on a fixed interval whether or not you are using the keyboard — for cycling contacts
on the scanning UI.

| Field | Default | Description |
|---|---|---|
| Toggle hotkey | `ctrl+alt+page up` | |
| Tab interval seconds | `2` | Floors at 1 second |

You can also use the **Toggle Ship Scan** button in the tab, or the Ship Scan chip.

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
independently. Both are read **every 100 ms**, so the two move together:

- **cyan — frame rate**, riding the top when it is high
- **amber — latency**, riding the bottom when it is low

so "all good" reads as two lines hugging opposite edges. Underneath sits the current server:
`IP:port • shard • region`. Readouts are to two decimal places.

The **Performance** tab breaks the same figures out in full: frame rate, frame time, 1% low,
server, shard, region, latency and jitter.

Behind the two lines sit **frame time bars** — one per pixel column, showing the worst frame in
that column, on the same time axis as the lines. The frame rate line is an average over each
sample, so a single slow frame barely dents it; the bars come from every frame RivaTuner drew,
so a stutter shows up as a spike. The worst frame is drawn rather than the mean, because
averaging is what hides it. The faint dashed line marks the 1% low.

Frame times come from RivaTuner's own per-frame ring buffer, so **every frame is counted** — the
1% low and the minimum are real percentiles over thousands of frames rather than estimates from
periodic samples. The Performance tab says which it is using.

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

## Server history

The **Server History** tab lists the last ten servers you were on, newest first, with when you
joined and how long you stayed. The session running now is highlighted and marked `*`.

Star Citizen gives its servers no names, so the readable one is built from the shard id:
`pub_use1b_12326004_120` becomes **US-East 1B #120**. The build number in the middle changes
with every patch — the same server was `pub_use1b_12269732_120` before the last one — so it is
left out, and the name stays put across updates. **Copy selected** includes the raw shard id,
which is the form to quote in a support ticket.

The point of it: if the game drops out and leaves your ship parked somewhere, you need to know
*which shard* to get back to. That is exactly what is hardest to remember after a crash, and
the game does not show it to you anywhere.

**Copy selected** puts the shard, server, region and time on the clipboard, which is the form
worth pasting into a support ticket or an org chat.

It reads Star Citizen's own logs — the current one plus the backups the game keeps — so
sessions from before this app was installed are there too, and nothing needs to have been
running at the time. Logs are read newest first and reading stops once there are ten, so it
costs a few milliseconds rather than working through months of backups.

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
  "keepalive_enabled":  true,
  "keepalive_key":      "tab",
  "keepalive_snap":     "off",
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
  history.py             past shards, read out of the game logs
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
The press may be too short for the game's frame cadence to catch. `KEY_HOLD_MS` near the top of
`StarCitizenHelper.py` sets how long keys are held; raising it to around 80 gives the game more
of a window.

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

# Star Citizen Helper

**Latest release: v2 — 2026-09-03.** See [all releases](https://github.com/cjbrochacho/StarCitizenHelper/tags)
for the full history; each one is a tagged commit, so `git checkout v2` always gets you exactly
that point, not a moving target.

A Windows utility for Star Citizen. It keeps your session alive while you are away, automates
the keypresses you would otherwise spam by hand, and shows what your frame rate and your
connection are actually doing — all from one window that sits behind the game.

- **Keepalive** — runs by itself, tapping a key once you have genuinely stepped away, so the server never drops you
- **Ship Scan** — repeats Tab on a timer for cycling scan contacts
- **KeepRunning** — holds Shift+W (or any keys) down so you keep moving
- **Macros** — your own hotkeys, each tapping a sequence of keys
- **Performance HUD** — frame rate, GPU busy, latency, CPU and GPU clocks, and which server you
  are on — measured from outside the game, with nothing loaded into it
- **Server history** — the last ten shards you were on, so a crash cannot lose your ship
- **Alt+F4 guard** — swallows Alt+F4 while the game has focus, with a button to switch it off
- **Performance data** — anonymous measurements of how the game runs, on by default, one click off

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
- *For the frame-rate half of the HUD:* membership of the Windows **Performance Log
  Users** group. Many machines already have it — the NVIDIA FrameView and PresentMon
  installers both grant it. The Performance tab says so if yours does not, and how to
  fix it. Everything else works without it.

Nothing has to be installed alongside the game, and nothing is loaded into it.

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

**Snap focus** is how it reaches the game from another window, and it is not optional — being
in a browser instead of the game is the case keepalive exists for. It pulls the game forward for
the tap and hands focus straight back, about a tenth of a second. Injected keys only ever reach
the *focused* window, so short of elevation there is no other way in. The minute of idle before
anything fires is what keeps this invisible: you are only ever interrupted when you were not
there. It is still jarring if the game runs in exclusive fullscreen rather than borderless.

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
own. Two special forms add timing without a new key: `wait:1.5` pauses for 1.5 seconds without
touching the keyboard, and `hold:shift+w:2.0` presses `shift+w` down, keeps it down for 2.0
seconds, then releases it — for a macro step that needs to be held rather than tapped. Only one
macro runs at a time; triggering a second while one is going logs a warning and does nothing.
Pick combos the game does not use — `ctrl+alt+1` through `ctrl+alt+9` are safe.

---

## Performance HUD

Above the graph sit the two chips doing the work and how fast they are running right now:
the CPU on one line, the GPU on the next, both refreshed every second. Neither speed is
something Windows will simply tell you. The CPU's is worked out the way Task Manager does it,
from the performance counter that reports the current speed as a percentage of the chip's
nominal one, so 125% of a 4.3 GHz part reads 5.4 GHz. The GPU has no Windows API at all: MSI
Afterburner publishes its core clock when it is running, and failing that NVIDIA's driver ships
`nvidia-smi`, which answers in about 80 ms and so is asked every other second rather than every
one. On a machine with neither, the model still shows and the clock reads `-- MHz`.

A rolling 60-second graph in the header, two sparklines sharing one canvas, each scaled
independently. Both are read **every 100 ms**, so the two move together:

- **cyan — frame rate**, riding the top when it is high
- **amber — latency**, riding the bottom when it is low

so "all good" reads as two lines hugging opposite edges. Underneath sits the current server:
`IP:port • shard • region`. Readouts are to two decimal places.

The **Performance** tab breaks the same figures out in full: frame rate, frame time, GPU busy,
1% low, frame swing, stutter, server, shard, region, latency and jitter.

Two of those are about *consistency* rather than speed, which is a different question from how
fast the game is running:

| Stat | What it catches |
|---|---|
| **Frame swing** | How much each frame differs from the one before, as a time and as a share of the average frame. This is micro-stutter: a run of 8 ms, 14 ms, 8 ms, 14 ms averages out to a healthy frame rate and a healthy 1% low, and still feels rough. Under about 10% is smooth; 25% and up is where the alternation becomes visible. |
| **Stutter** | The share of frames taking more than twice the median — discrete hitches rather than steady unevenness. A frame rate can look excellent with a fraction of a percent here and still catch your eye. |

Both need every frame to mean anything, so they show `--` if the figures are ever coming from
samples rather than from every frame.

Each line has a **faint dashed reference** in its own colour: 60 fps for the frame rate, 50 ms
for latency. They are fixed marks rather than derived ones, so the question they answer is the
one you actually ask at a glance — above the number I care about, or below it. Both are drawn
against their own series' scale, and the scale never eases down far enough to push a mark off
the top, so they stay readable even when the data never reaches them.

**Every frame is counted**, so the 1% low and the minimum are real percentiles over thousands of
frames rather than estimates from periodic samples.

### Where the frame data comes from

Not from inside the game. Every present goes through the Windows graphics kernel, which reports
it over ETW; [PresentMon](https://github.com/GameTechDev/PresentMon) reads those events and this
app reads PresentMon. Nothing is injected, nothing is hooked, and a crash in the measuring
cannot take the game with it.

That last part is the whole reason for it. The obvious alternative is an overlay — RivaTuner,
Afterburner, Steam, Discord, GeForce Experience — and they all measure a game by loading
themselves into it. On a Vulkan title like Star Citizen that means registering an implicit
layer, which the game's own log lists at startup and its instability warning is about. When such
a layer fails to initialise, the game does not start at all.

The cost is a permission. Opening an ETW session normally wants administrator, but membership of
the **Performance Log Users** group is enough instead, and the NVIDIA FrameView and PresentMon
installers both grant it — so on many machines it is already there and nothing ever prompts. If
yours is not, the Performance tab says so and tells you where to fix it (`compmgmt.msc` → Local
Users and Groups → Groups → Performance Log Users, then sign out and back in). It is not
something this app can grant itself, and it does not ask for elevation to try.

One wrinkle worth recording, because it looks like a bug: PresentMon normally follows each frame
all the way to the screen, and a game sitting behind another window never confirms — which is
exactly when you are looking at this tool. It does not withhold those frames, it just leaves the
display-tracked columns empty, so this reads present-to-present timing and never touches them.
Measured over eight seconds with the game in the background, 362 of 720 frames had no display
timestamp and all 720 had the frame time and the GPU time.

**GPU busy** is the one figure an overlay could not give: milliseconds the GPU actually spent on
each frame. Next to the frame time it answers the question the frame rate never does — a 8.6 ms
frame with 3.3 ms of GPU work is a machine waiting on its CPU, and no graphics setting will fix
it.

**Latency** is measured to the cloud region the shard is running in. No elevation needed.

Getting there took ruling two things out. The sim server answers nothing — not ICMP, not TCP on
any port — and it cannot be reached indirectly either: a TTL walk towards one dies at Google's
peering edge, and every region from Frankfurt to Sydney comes back as the *same* router at the
*same* few milliseconds, because Google's backbone stops reporting TTL once traffic is on it.
Pinging the host the game holds a TLS connection to is no better: those are CIG's platform
services, they sit in one fixed region, and the number barely moves when the shard moves to the
other side of the planet — which is the single thing this figure exists to show.

What works is that the shard name *is* a Google Cloud region with the punctuation removed:
`pub_euw1b` is `europe-west1`, zone b; `pub_apse2a` is `asia-southeast2`. Google publishes a
per-region endpoint that does answer ICMP, so the ping goes there. Measured across seven regions
from one machine, that reads 39 ms to `us-east1`, 116 ms to `europe-west1` and 235 ms to
`asia-southeast2` — the spread you would expect from a map.

This is a **proxy, and it is labelled as one**: it is the distance to the shard's datacenter, not
to the shard's machine. The last hop inside Google's network is not included, but that is small
next to an ocean. If the shard is in a region the table does not recognise, it falls back to
pinging a CIG host and says `region unknown, not comparable` next to the reading, rather than
quietly showing you a number that means something else.

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
- **OFF** — the guard is switched off and Alt+F4 will close the game

**Enable Alt+F4** next to that line switches the guard off, for when closing the game is
exactly what you want; the button then reads **Block Alt+F4** to put it back. The choice is
saved, so it survives a restart. Switching it off removes the keyboard hook rather than
leaving one in place that passes everything through — off means the app is not touching the
keyboard at all.

---

## Performance data

The tool records how the game runs — frame rate, frame times, latency, your graphics
settings and hardware, and which part of the 'verse you were in — so that slow places and
slow hardware can be found across many players rather than guessed at from one.

**It is on by default, and it says so.** The first launch after this arrives shows a notice
with the off switch in the dialog, not buried behind it. The **Telemetry** tab lists every
field that is collected, counts what has been written, and has **Open my data** — the files
are gzipped JSON and you can read every byte of them.

Batches are written to `assets/telemetry/` and pruned after a fortnight or 32 MB, whichever
comes first.

**Nothing is sent anywhere until you set an endpoint.** `telemetry_url` is empty by default, and
with it empty the measurements only ever exist on your own disk. Set it in the Telemetry tab and
batches are posted once every 30 seconds.

The thing on the other end — the ingestor, the store and the dashboard — is a separate project
and is never installed here. The two halves share one HTTP endpoint and nothing else, which is
what lets a player update this without anybody redeploying that.

The spool is the queue, so an outage costs nothing: if the server is unreachable the batches stay
on disk and go later, and a cursor records how far along each file has been sent. Being wrong
about that cursor is survivable too — the server identifies a batch by who sent it and when it
started, so a batch sent twice is stored once. There is a test that wipes the cursor and re-sends
everything, and the row count does not move.

The server can also switch a client off: if it answers `stop`, collection is turned off and the
choice is written to settings, because a server refusing data has no reason to start receiving it
again at the next restart.

**What is never collected:** your handle, account id, player id, position, IP address, file
paths, or any raw line from the game log. That last one is the point of the design — payloads
are assembled field by field from an allowlist rather than filtered, so a field can only be
sent because someone added it by name. It matters because the log line that best identifies
where you are also carries your handle, and `attributes.xml` holds 143 entries including your
key bindings.

Two identifiers travel with the data. `client` is a random id made on first run, which
**Reset my ID** replaces with a new one that cannot be linked to anything sent before it.
`machine_id` is a salted digest of Windows' machine GUID — stable across reinstalls so one PC
is counted once, hashed because nobody can reset their hardware, and salted so it will not
match another product's hash of the same number.

Measurements are aggregated on your PC before they are written: a second of frames becomes one
row, and percentiles are computed once per batch, because a 1% low over sixty frames is one
frame rather than a percentile.

Set `"telemetry_enabled": false` in `settings.json`, or press **Turn it off**, and it stops
within a second.

---

## Updating

The launcher brings the install up to date before it starts the app, so there is nothing to
download by hand and no reason to visit GitHub again after the first time.

It asks GitHub for the newest commit on `main`, and if that is not what is installed it fetches
the source archive and writes it over the install. `settings.json` is never touched, and neither
is anything in `assets/` — your icon, your shortcut marker and the record of which commit you are
on all survive. Files that were removed upstream are removed locally too, so a renamed module
cannot linger and get imported by mistake.

Two things it deliberately will not do:

- **Overwrite the launcher while it is running.** `cmd.exe` reads a `.bat` by file offset as it
  goes, so replacing one mid-run makes it execute whatever now sits at that offset. A new launcher
  is left in `assets/pending.bat` and swapped in as the very last line of the script, after the app
  has already started.
- **Touch a git checkout.** If there is a `.git` directory it does nothing at all, because
  overwriting a working copy with the tip of `main` would throw away uncommitted work. Git is
  already handling updates there.

Nothing here can stop the app starting. No network, GitHub unreachable, a truncated download, an
archive that does not look like this project — each one means "no update today" and the app runs
anyway. Files are written to a temporary name and renamed into place, so an update interrupted
half way leaves the old file rather than a broken one.

Set `"auto_update": false` in `settings.json` to pin the version you have.

---

## Settings file

`settings.json` sits next to the app and loads at startup. Edit it by hand or use
**Backup** / **Import**.

```json
{
  "keepalive_enabled":  true,
  "keepalive_key":      "tab",
  "altf4_guard":        true,
  "scan_toggle":        "ctrl+alt+page up",
  "scan_interval":      2,
  "hold_start":         "shift+w+page up",
  "hold_keys":          "shift+w",
  "hud_enabled":        true,
  "auto_update":        true,
  "telemetry_enabled":  true,
  "telemetry_url":      "",
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
  fps.py                 frame data from PresentMon, out of process
  net.py                 latency, and server/shard/region from the game's log
  hardware.py            CPU and GPU model, and their current clocks
  gamecfg.py             the game's graphics settings, and what the driver did
  history.py             past shards, read out of the game logs
  location.py            where in the 'verse you are, as a rollup path
  telemetry.py           one-second windows batched to disk
  upload.py              posts the spool, and survives not being able to
  update.py              fetches and applies the newest commit at launch
  hud.py                 the header graph and its readout
  idle.py                desktop-wide idle detection
  window.py              finds the game window; snap focus; taskbar icon
  brand.py               the radar mark and the header wordmark
  theme.py               colour palette
  shortcut.py            draws the icon and writes the .lnk

vendor/
  PresentMon.exe         Intel PresentMon, MIT - where the frame data comes from
  LICENSE-PresentMon.txt its licence, kept with it

assets/                  generated icon and shortcut marker (git-ignored)
settings.json            written on first save (git-ignored)
```

Everything under `helper/` is standard library plus `ctypes`. The one binary is
`vendor/PresentMon.exe`, which is run as a child process and never loaded into anything.

---

## Troubleshooting

**Python was not found.**
Run `StarCitizenHelper.bat` — it installs Python for you. If it cannot reach the internet, install
by hand from [python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH", and
run it again.

**Hotkeys do nothing in-game.**
Star Citizen has to be the active window: Ship Scan and KeepRunning both stop while another
window has focus. Keepalive is the exception — it uses snap focus and is built to run while you
are somewhere else.

**Keys register in Notepad but not in the game.**
The press may be too short for the game's frame cadence to catch. `KEY_HOLD_MS` near the top of
`StarCitizenHelper.py` sets how long keys are held; raising it to around 80 gives the game more
of a window.

**The frame-rate line says "waiting for the game".**
Nothing to do — the capture starts and stops with `StarCitizen.exe` and takes a couple of
seconds to produce its first frames. Unlike an overlay it does not have to be running before the
game, so launching them in either order is fine.

**The frame-rate line says "not allowed to measure frames".**
Your account is not in the **Performance Log Users** group. Run `compmgmt.msc` as administrator,
add yourself under Local Users and Groups → Groups → Performance Log Users, then sign out and
back in. Everything except the frame-rate figures works without it.

**Latency shows `--`.**
The target comes from the shard named in the game's log, so there is nothing to ping until the
game is running and has joined a server.

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

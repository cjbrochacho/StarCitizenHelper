# vendor

`PresentMon.exe` — Intel PresentMon 2.3.1 (x64), MIT licensed, see
`LICENSE-PresentMon.txt`. Upstream: <https://github.com/GameTechDev/PresentMon>

    sha256  364E5D98D4D134BD54DD25C22ED2CA2F4883F8BC3ED6502BEE0C151E3436D30C
    size    421,312 bytes

## Why it is checked in

It is where the frame rate comes from. `helper/fps.py` runs it while the game
is up and reads its CSV, which is how frames are counted without loading
anything into the game - see that module's docstring for the reasoning.

It ships in the repository rather than being fetched on first run because the
updater already replaces the whole tree from the GitHub zip, so a binary sitting
here reaches every install through the path that already exists. A download at
first run would be a second way for the tool to fail on a bad network, for no
gain.

## Replacing it

Drop a newer `PresentMon-*-x64.exe` in as `PresentMon.exe`. The CSV columns
`fps.py` reads - `TimeInMs`, `MsBetweenPresents`, `MsGPUBusy` - are looked up by
name, so a release that adds or reorders columns is fine; one that renames those
three is not, and `fps.py` reports `no_source` rather than guessing.

# Where these sheets come from

`sheet.pdf` is a community-made key binding reference sheet; `flight.png` and
`fps.png` are renders of its two pages at 2000 px. The app shows the PNGs at
100% and renders every other zoom level from the PDF on demand (see
`helper/sheet.py`), so both ship:

| | |
|---|---|
| Title on the sheet | **4.6.0 Keybindings · Flight** / **4.6.0 Keybindings · FPS** |
| Made by | *Those Guys With Ships* and *Texas Space Navy* (logos on the sheet) |
| Original file | `SC4_6_0_KeyboardMouseOnly_v1.pdf` (2 pages, 792 × 612 pt landscape), shipped here as `sheet.pdf` |
| Game version | Star Citizen 4.6.0 |
| Rendered | 2026-09-17, 2000 px wide, with `render_sheet.ps1` beside this file |

The sheet shows the game's **default** keyboard and mouse bindings. It is not
generated from the game's data, and the app does not alter it - what the
Key Bindings tab adds underneath is the player's own rebinds, read live from
`actionmaps.xml`.

## Refreshing for a new patch

Save the new sheet over `sheet.pdf`, then from Windows PowerShell 5.1:

```
powershell -NoProfile -ExecutionPolicy Bypass -File render_sheet.ps1 -Pdf sheet.pdf
```

That rewrites both PNGs. Update the table above and the version mentioned in
the README's Key Bindings section. If the page order or count changes, pass
`-Names` accordingly - the tab shows whatever names `SHEETS` in
`StarCitizenHelper.py` lists. Renders the app made from the old PDF are keyed
on its contents and are dropped on their own.

## Redistribution

**Permission from the sheet's authors has not yet been confirmed.** The sheet
is credited here and in the README; before this app is distributed publicly
with the renders included, ask *Those Guys With Ships* / *Texas Space Navy*
whether they are happy for it to be shipped this way, and record the answer
here. That question now covers the PDF itself as well as renders of it -
shipping the original is the stronger act of the two.

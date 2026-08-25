"""Palette for the performance HUD, matched to the Star Citizen Helper window."""

# Helper's own window colours, so the HUD reads as part of the app.
BG = "#101722"           # window background
PANEL = "#192433"        # raised panel
BORDER = "#2e435a"
TEXT = "#eef6ff"
MUTED = "#91a7bd"
OK = "#1f7852"           # Helper's "automation on" green
WARN = "#f0a24b"
ERROR = "#ff6b6b"

#: Graph-only shades. Deliberately faint - the plot should read as a hairline
#: across the header, not as a panel sitting on top of it.
ACCENT = "#41b8f5"       # frame rate line (cyan)
LAT = "#e0a54e"          # latency line (amber)
FPS_LOW = "#295a74"      # faint cyan for the 1% low reference
GRID = "#1a222e"

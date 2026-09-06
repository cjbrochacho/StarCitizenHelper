"""What the collector must not get wrong:

    python test_telemetry.py

No framework and no dependencies, like the rest of this project. It drives the
collector with fake monitors, so it needs no game, no window and no network.

The case it exists for: PresentMon does not hand frames over evenly. It
delivers bursts with seconds of silence between them, and a frame that arrives
outside its own second used to be dropped by every second and lost. A batch
whose ten seconds fell inside one silence pooled nothing and still reported a
1% low, a minimum, a swing and a stutter - all zero, all never measured. In a
fortnight of real play, 64 of 5,427 batches did that.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper.telemetry import (BATCH_SECONDS, MIN_POOL_FRAMES, POOL_SECONDS,
                              Second, TelemetryCollector, build_batch)

PASSED = 0
FAILED = 0


def check(what, fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print("  [PASS] %s" % what)
    except AssertionError as exc:
        FAILED += 1
        print("  [FAIL] %s\n         %s" % (what, exc))


class FakeFps:
    """A frame monitor. `frame_times` is (age in seconds, frame in ms)."""

    def __init__(self, frame_times=(), fps=60.0, status="ok", per_frame=True):
        self.frame_times = list(frame_times)
        self.fps = fps
        self.frame_time_ms = 1000.0 / fps if fps else 0.0
        self.status = status
        self.per_frame = per_frame


class FakeNet:
    ping_ms = 42.0
    region = "US-East"
    shard = "pub_use1b_12326004_120"


class FakeSpool:
    def __init__(self):
        self.written = []

    def write(self, record):
        self.written.append(record)
        return True


def collector(fps_source):
    """A collector wired to fakes, with nothing on disk and no game log."""
    return TelemetryCollector(
        FakeSpool(),
        fps_stats=fps_source,
        net_stats=lambda: FakeNet(),
        hardware=lambda: (5200, 2800),
        machine={"machine_id": "m", "cpu": "CPU", "gpu": "GPU"},
        log_path=None,
        client_id="c",
        enabled=lambda: True,
        idle=lambda: 0.0,
    )


# --- the pool ------------------------------------------------------------

print("\n1. a frame is pooled once, however late it arrives")

def _burst():
    held = FakeFps()
    tel = collector(lambda: held)
    # Two silent ticks: the monitor reports a frame rate but hands over no
    # frames, which is what a delivery gap looks like from here.
    tel.tick(1000.0, 100.0)
    tel.tick(1001.0, 101.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 0, "expected nothing pooled during the silence, got %d" % pooled
    # Now the burst lands, carrying frames aged up to three seconds - every
    # one of them older than the second it arrives in.
    held.frame_times = [(2.5, 8.0), (2.0, 8.0), (1.5, 8.0), (0.5, 8.0)]
    tel.tick(1002.0, 102.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 4, "the burst should be pooled whole, got %d" % pooled


check("a burst delivered outside its second is still pooled", lambda: _burst())


def _once():
    held = FakeFps(frame_times=[(0.5, 8.0), (0.2, 8.0)])
    tel = collector(lambda: held)
    tel.tick(1000.0, 100.0)
    # The same frames, a second older, still in the monitor's window.
    held.frame_times = [(1.5, 8.0), (1.2, 8.0)]
    tel.tick(1001.0, 101.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 2, "each frame belongs to one batch only, got %d" % pooled


check("and never pooled twice", lambda: _once())


def _floor():
    # The monitor holds a minute of frames; a collector that has just started
    # must not attribute all of it to its first batch.
    old = [(float(age), 8.0) for age in range(59, 0, -1)]
    tel = collector(lambda: FakeFps(frame_times=old))
    tel.tick(1000.0, 100.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled <= POOL_SECONDS + 1, \
        "reached back past one batch: %d frames" % pooled
    assert pooled > 0, "reached back nothing at all"


check("a fresh collector does not sweep the whole window", lambda: _floor())


def _row():
    # A burst spanning three seconds must not make the row read as though
    # that many frames were drawn in one.
    held = FakeFps(frame_times=[(2.5, 8.0), (2.0, 8.0), (0.5, 4.0)], fps=60.0)
    tel = collector(lambda: held)
    tel.tick(1000.0, 100.0)
    second = tel._seconds[0]
    assert len(second.frames) == 3, "the pool takes all three"
    # Only the 4 ms frame is inside the last second, so the row is 250 fps
    # over that one frame - not a rate invented from the whole burst.
    assert abs(second.fps - 250.0) < 0.01, "row fps was %r" % second.fps
    assert abs(second.worst_ms - 4.0) < 0.01, "row worst was %r" % second.worst_ms


check("the row still describes its own second", lambda: _row())


# --- what a batch may claim ----------------------------------------------

print("\n2. a batch that measured no frames says so")

def _zeros():
    ctx = {"frame_source": "presentmon", "path": "stanton", "system": "stanton"}
    seconds = [Second(dt=i, fps=59.7, frame_ms=16.75) for i in range(BATCH_SECONDS)]
    batch = build_batch("c", "s", 1000, ctx, seconds)
    assert batch["sum"]["frames"] == 0, batch["sum"]["frames"]
    assert batch["sum"]["fps_avg"] == 0.0
    assert batch["sum"]["low_1"] == 0.0


check("its summary is zeros", lambda: _zeros())


def _sampled():
    ctx = {"frame_source": "presentmon", "path": "stanton", "system": "stanton"}
    seconds = [Second(dt=i, fps=59.7) for i in range(BATCH_SECONDS)]
    batch = build_batch("c", "s", 1000, ctx, seconds)
    assert batch["ctx"]["frame_source"] == "sampled", \
        "a frameless batch called itself %r" % batch["ctx"]["frame_source"]


check("and does not claim it measured per-frame", lambda: _sampled())


def _kept():
    ctx = {"frame_source": "presentmon", "path": "stanton", "system": "stanton"}
    seconds = [Second(dt=i, frames=[8.0] * 60) for i in range(BATCH_SECONDS)]
    batch = build_batch("c", "s", 1000, ctx, seconds)
    assert batch["ctx"]["frame_source"] == "presentmon"
    assert batch["sum"]["frames"] == 600, batch["sum"]["frames"]
    assert batch["sum"]["fps_avg"] > 0.0


check("a batch that did measure keeps its instrument", lambda: _kept())


def _one():
    assert MIN_POOL_FRAMES == 2
    ctx = {"frame_source": "presentmon"}
    batch = build_batch("c", "s", 1000, ctx, [Second(dt=0, frames=[8.0])])
    assert batch["sum"]["frames"] == 0, "a lone frame must not become a reading"
    assert batch["ctx"]["frame_source"] == "sampled"


check("one frame is not a percentile", lambda: _one())


# --- the whole path ------------------------------------------------------

print("\n3. end to end, the gap no longer produces a zero batch")

def _endtoend():
    held = FakeFps()
    tel = collector(lambda: held)
    # Ten ticks. The instrument stays silent for the first nine and delivers
    # everything on the tenth, which is the shape that produced the zeros.
    for i in range(BATCH_SECONDS - 1):
        tel.tick(1000.0 + i, 100.0 + i)
    held.frame_times = [(9.0 - i * 0.1, 8.0) for i in range(90)]
    tel.tick(1000.0 + BATCH_SECONDS - 1, 100.0 + BATCH_SECONDS - 1)
    written = [r for r in tel.spool.written if r["type"] == "batch"]
    assert len(written) == 1, "expected one batch, got %d" % len(written)
    summary = written[0]["sum"]
    assert summary["frames"] == 90, summary["frames"]
    assert summary["fps_avg"] > 0.0, "still zero: %r" % summary
    assert summary["low_1"] > 0.0, "still zero: %r" % summary
    assert written[0]["ctx"]["frame_source"] == "presentmon"


check("a batch spanning a delivery gap reports real figures", lambda: _endtoend())


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "TELEMETRY VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)

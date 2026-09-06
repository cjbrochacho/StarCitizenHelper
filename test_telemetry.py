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

from helper.telemetry import (BATCH_SECONDS, MIN_POOL_FRAMES, Second,
                              TelemetryCollector, build_batch, build_context)

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
    """A frame monitor. `frame_stamps` is (monotonic stamp, frame in ms)."""

    def __init__(self, frame_stamps=(), fps=60.0, status="ok", per_frame=True):
        self.frame_stamps = list(frame_stamps)
        self.fps = fps
        self.frame_time_ms = 1000.0 / fps if fps else 0.0
        self.status = status
        self.per_frame = per_frame

    def aged(self, now, ages_and_ms):
        """Frames written as ages at `now`, which is how they read here."""
        self.frame_stamps = [(now - age, ms) for age, ms in ages_and_ms]
        return self


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
    held.aged(102.0, [(2.5, 8.0), (2.0, 8.0), (1.5, 8.0), (0.5, 8.0)])
    tel.tick(1002.0, 102.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 4, "the burst should be pooled whole, got %d" % pooled


check("a burst delivered outside its second is still pooled", lambda: _burst())


def _once():
    held = FakeFps().aged(100.0, [(0.5, 8.0), (0.2, 8.0)])
    tel = collector(lambda: held)
    tel.tick(1000.0, 100.0)
    # The same frames, a second older, still in the monitor's window.
    held.aged(101.0, [(1.5, 8.0), (1.2, 8.0)])
    tel.tick(1001.0, 101.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 2, "each frame belongs to one batch only, got %d" % pooled


check("and never pooled twice", lambda: _once())


def _floor():
    # The monitor holds a minute of frames whenever the collector starts, and
    # those were drawn before it was collecting. The first tick takes its own
    # second and leaves the rest of the window alone.
    window = [(float(age), 8.0) for age in range(59, 0, -1)]
    window += [(0.5, 8.0), (0.2, 8.0)]
    tel = collector(lambda: FakeFps().aged(100.0, window))
    tel.tick(1000.0, 100.0)
    pooled = sum(len(s.frames) for s in tel._seconds)
    assert pooled == 2, "took %d of a 61-frame window, wanted the 2 fresh" % pooled

check("a fresh collector does not sweep the whole window", lambda: _floor())


def _row():
    # A burst spanning three seconds must not make the row read as though
    # that many frames were drawn in one. One tick first to set the
    # watermark, then the silence and the burst.
    held = FakeFps(fps=60.0).aged(100.0, [(0.5, 8.0)])
    tel = collector(lambda: held)
    tel.tick(1000.0, 100.0)
    held.aged(103.0, [(2.5, 8.0), (2.0, 8.0), (0.5, 4.0)])
    tel.tick(1003.0, 103.0)
    second = tel._seconds[-1]
    assert len(second.frames) == 3, (
        "the pool takes the whole burst, took %d" % len(second.frames))
    # Only the 4 ms frame is inside the last second, so the row is 250 fps
    # over that one frame - not a rate invented from the whole burst.
    assert abs(second.fps - 250.0) < 0.01, "row fps was %r" % second.fps
    assert abs(second.worst_ms - 4.0) < 0.01, "row worst was %r" % second.worst_ms

check("the row still describes its own second", lambda: _row())


def _steady():
    # A continuous stream, the ordinary case: ten ticks at a true 120 fps, and
    # a monitor whose window keeps the last minute. The batch must hold the
    # frames that were actually drawn and not one more.
    #
    # This is the case real data caught. Rebuilding a frame's stamp from its
    # age used a different reading of the clock than the monitor had used, so
    # the boundary frames came back on the next tick too and a ten-second
    # batch at 120 fps reported 1,620 frames instead of 1,200 - a count a
    # third high, and that count is the weight every population average uses.
    rate, step = 120.0, 1.0 / 120.0
    held = FakeFps(fps=rate)
    tel = collector(lambda: held)
    drawn = 0
    for tick in range(BATCH_SECONDS):
        now = 100.0 + tick
        # Every frame of the last minute, as the monitor really holds them,
        # and read at an instant slightly after the tick's own clock - which
        # is what made the reconstruction drift.
        held.frame_stamps = [(now + 0.004 - i * step, 1000.0 * step)
                             for i in range(int(rate * 60))][::-1]
        drawn = int(rate) * (tick + 1)
        tel.tick(1000.0 + tick, now)
    written = [r for r in tel.spool.written if r["type"] == "batch"]
    assert len(written) == 1, "expected one batch, got %d" % len(written)
    pooled = written[0]["sum"]["frames"]
    # One tick's worth of slack: the first tick reaches back a second.
    assert abs(pooled - drawn) <= rate,         "pooled %d frames for %d drawn" % (pooled, drawn)
    fps_avg = written[0]["sum"]["fps_avg"]
    assert abs(fps_avg - rate) < 1.0, "fps_avg %r for a steady %r" % (fps_avg, rate)


check("a steady stream is counted once, not a third high", lambda: _steady())


# --- only what was measured per frame --------------------------------------

print("\n2. nothing is collected unless it was measured per frame")


def _not_per_frame():
    # Before PresentMon has its trace open the monitor falls back to periodic
    # sampling. A 1% low from samples is one sample, so none of it is kept.
    held = FakeFps(per_frame=False).aged(100.0, [(0.5, 8.0)])
    tel = collector(lambda: held)
    for tick in range(BATCH_SECONDS + 2):
        tel.tick(1000.0 + tick, 100.0 + tick)
    assert not tel._seconds, "recorded %d sampled seconds" % len(tel._seconds)
    assert not tel.spool.written, "wrote %d sampled records" % len(tel.spool.written)


check("a sampled tick records nothing", lambda: _not_per_frame())


def _dropped():
    # The instrument is per-frame but silent for the whole batch: there is no
    # percentile to take, so the batch is not written rather than written as
    # a row of zeros.
    held = FakeFps(per_frame=True)
    tel = collector(lambda: held)
    for tick in range(BATCH_SECONDS):
        tel.tick(1000.0 + tick, 100.0 + tick)
    written = [r for r in tel.spool.written if r["type"] == "batch"]
    assert not written, "wrote a batch with no frames in it"


check("a batch that pooled no frames is not written", lambda: _dropped())


def _one():
    # One frame has no spread, so it is not a percentile either.
    assert MIN_POOL_FRAMES == 2
    held = FakeFps(per_frame=True)
    tel = collector(lambda: held)
    for tick in range(BATCH_SECONDS):
        held.aged(100.0 + tick, [(0.5, 8.0)] if tick == 0 else [])
        tel.tick(1000.0 + tick, 100.0 + tick)
    written = [r for r in tel.spool.written if r["type"] == "batch"]
    assert not written, "one frame became a batch"


check("one frame is not a percentile", lambda: _one())


def _no_source():
    # There is one instrument now, so the field naming it is not sent.
    ctx = build_context(None, FakeNet(), "019d99a0")
    assert "frame_source" not in ctx, "context still carries a frame source"
    seconds = [Second(dt=i, frames=[8.0] * 60) for i in range(BATCH_SECONDS)]
    batch = build_batch("c", "s", 1000, ctx, seconds)
    assert "frame_source" not in batch["ctx"], "batch still carries a frame source"
    assert batch["sum"]["frames"] == 600, batch["sum"]["frames"]
    assert batch["sum"]["fps_avg"] > 0.0


check("no frame source is sent at all", lambda: _no_source())


# --- the whole path ------------------------------------------------------

print("\n3. end to end, the gap no longer produces a zero batch")

def _endtoend():
    held = FakeFps()
    tel = collector(lambda: held)
    # Ten ticks. The instrument stays silent for the first nine and delivers
    # everything on the tenth, which is the shape that produced the zeros.
    for i in range(BATCH_SECONDS - 1):
        tel.tick(1000.0 + i, 100.0 + i)
    held.aged(100.0 + BATCH_SECONDS - 1, [(9.0 - i * 0.1, 8.0) for i in range(90)])
    tel.tick(1000.0 + BATCH_SECONDS - 1, 100.0 + BATCH_SECONDS - 1)
    written = [r for r in tel.spool.written if r["type"] == "batch"]
    assert len(written) == 1, "expected one batch, got %d" % len(written)
    summary = written[0]["sum"]
    assert summary["frames"] == 90, summary["frames"]
    assert summary["fps_avg"] > 0.0, "still zero: %r" % summary
    assert summary["low_1"] > 0.0, "still zero: %r" % summary
    assert "frame_source" not in written[0]["ctx"]


check("a batch spanning a delivery gap reports real figures", lambda: _endtoend())


print("\n%s  (%d passed, %d failed)"
      % ("FAILED" if FAILED else "TELEMETRY VERIFIED", PASSED, FAILED))
sys.exit(1 if FAILED else 0)

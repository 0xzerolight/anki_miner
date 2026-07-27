"""Responsiveness receipt: first paint, event-loop gaps, idle CPU, RSS.

The sampler is SPLIT, because a GUI-thread-only sampler is structurally blind to
the exact defect class this exists to measure: a ``QTimer`` cannot fire while the
main thread is blocked, and ``stall_watchdog`` is blind to theme switching
because that path is wrapped in ``paused_stall_detection()``.

* A daemon ``threading.Thread`` at 50 ms records monotonic gaps ONLY, no widget
  access. It observes a freeze precisely because it is off the GUI thread; its
  own loop gap is the freeze signal.
* A GUI-thread ``QTimer`` at 100 ms publishes a widget snapshot under a lock.
  The daemon stamps each with its own wall clock, so a freeze also shows up as
  snapshot age climbing.
* Theme switching is additionally wall-clocked directly, because the watchdog
  cannot see it by construction.

This is the honest *subset* of the packaged-build receipt the plan asks for:
everything here is measured in a source checkout under the offscreen platform,
so absolute numbers are not a packaged user's numbers. Before/after comparisons
on the same machine are the usable part.

Usage::

    .venv/bin/python scripts/ui_atlas/timeline.py --out /tmp/atlas
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import isolation  # noqa: E402

isolation.bootstrap()

import contextlib  # noqa: E402
from contextlib import ExitStack  # noqa: E402
from unittest.mock import patch  # noqa: E402

import cells  # noqa: E402
import psutil  # noqa: E402

_LOCK = threading.Lock()
_LATEST: dict = {}
_EVENTS: list[dict] = []
_GAPS: list[dict] = []
_STOP = threading.Event()
T0 = time.monotonic()

#: Seconds of quiet sampling used for the idle-CPU figure.
IDLE_SECONDS = 3.0

#: Times each phase-profiled theme apply is repeated (see ``run_scenarios``).
PROFILE_REPEATS = 5


def log(m: str) -> None:
    print(f"[timeline] {m}", flush=True)


def mark(kind: str, **kw) -> None:
    _EVENTS.append({"t": round(time.monotonic() - T0, 4), "event": kind, **kw})


def _daemon() -> None:
    """Off-GUI-thread heartbeat: the only thing that can SEE a GUI freeze."""
    last = time.monotonic()
    while not _STOP.is_set():
        time.sleep(0.05)
        now = time.monotonic()
        with _LOCK:
            snap = dict(_LATEST)
        age = now - snap.get("_published", now)
        _GAPS.append(
            {
                "t": round(now - T0, 4),
                "loop_gap_ms": round((now - last) * 1000, 1),
                "snapshot_age_ms": round(age * 1000, 1),
                "scenario": snap.get("_scenario"),
            }
        )
        last = now


def publish(window) -> None:
    """GUI-thread snapshot of the state the gaps have to be interpreted against."""
    from PyQt6.QtWidgets import QAbstractButton, QProgressBar

    try:
        leaf = window.tabs.currentWidget()
        bars = leaf.findChildren(QProgressBar) if leaf else []
        bar = bars[0] if bars else None
        snap = {
            "_published": time.monotonic(),
            "_scenario": _LATEST.get("_scenario"),
            "tab": type(leaf).__name__ if leaf else None,
            "bar": (
                None
                if bar is None
                else {
                    "min": bar.minimum(),
                    "max": bar.maximum(),
                    "value": bar.value(),
                    "format": bar.format(),
                    "indeterminate": bar.minimum() == 0 and bar.maximum() == 0,
                    "visible": bar.isVisible(),
                }
            ),
            "buttons": {
                b.objectName() or b.text(): b.isEnabled()
                for b in (leaf.findChildren(QAbstractButton) if leaf else [])
                if b.isVisible() and (b.objectName() or b.text())
            },
        }
        with _LOCK:
            _LATEST.clear()
            _LATEST.update(snap)
    except Exception:
        pass


def scenario(name: str) -> None:
    with _LOCK:
        _LATEST["_scenario"] = name
    mark("scenario_start", name=name)


def run_scenarios(window, app, *, themes: int) -> None:
    """Each scenario targets a documented GUI-thread-block suspect."""
    from PyQt6.QtWidgets import QWidget

    from anki_miner.gui.resources.styles.theme import Theme

    process = psutil.Process()

    # --- theme change: wrapped in paused_stall_detection(), so the watchdog
    #     cannot see this freeze by construction. Wall-clock it directly. This
    #     is the number D39-C exists to move.
    scenario("theme_change")
    keys = sorted(Theme.get_available_themes())[:themes] if themes else sorted(Theme.get_available_themes())
    for mode in keys:
        t = time.perf_counter()
        try:
            Theme.set_mode(mode)
            Theme.apply_to_app(app)
        except Exception as exc:
            mark("theme_error", mode=mode, error=str(exc)[:120])
            continue
        dt = (time.perf_counter() - t) * 1000
        mark("theme_applied", mode=mode, ms=round(dt, 1))
        app.processEvents()
    applied = [e["ms"] for e in _EVENTS if e["event"] == "theme_applied"]
    if applied:
        log(
            f"theme apply over {len(applied)} themes: "
            f"median {statistics.median(applied):.0f}ms  worst {max(applied):.0f}ms"
        )

    # --- attribute the theme cost to its phases. A total is not actionable; the
    #     split says whether the block is the palette write, compiling the sheet,
    #     or Qt re-resolving every widget's rules (which is what D39-C targets).
    #     Repeated and reported as a minimum as well as a median, because this
    #     machine may be shared: the minimum of N is the closest available
    #     estimate of the cost with no competing load.
    scenario("theme_profile")
    for mode in ("light", "catppuccin-mocha") * PROFILE_REPEATS:
        try:
            phases = {}
            t = time.perf_counter()
            palette = Theme.build_palette(mode)
            phases["build_palette_ms"] = round((time.perf_counter() - t) * 1000, 1)

            t = time.perf_counter()
            app.setPalette(palette)
            phases["set_palette_ms"] = round((time.perf_counter() - t) * 1000, 1)

            t = time.perf_counter()
            sheet = Theme.get_stylesheet(mode)
            phases["get_stylesheet_ms"] = round((time.perf_counter() - t) * 1000, 1)

            t = time.perf_counter()
            app.setStyleSheet(sheet)
            phases["set_stylesheet_ms"] = round((time.perf_counter() - t) * 1000, 1)

            mark(
                "theme_profile",
                mode=mode,
                sheet_bytes=len(sheet),
                widgets=len(window.findChildren(QWidget)),
                **phases,
            )
            log(f"theme {mode}: {phases}")
            app.processEvents()
        except Exception as exc:
            mark("theme_profile_error", mode=mode, error=str(exc)[:160])

    # --- tab switching: time from the navigation call to a settled repaint.
    #     This is the click-to-first-paint figure for in-app navigation.
    scenario("tab_switch")
    for label, main_key, sub_key in cells.screens_to_visit():
        t = time.perf_counter()
        cells.reveal(window, main_key, sub_key)
        app.processEvents()
        window.grab()  # force the paint the user would be waiting on
        dt = (time.perf_counter() - t) * 1000
        mark("tab_switch", screen=label, ms=round(dt, 1))
    switches = [e["ms"] for e in _EVENTS if e["event"] == "tab_switch"]
    if switches:
        log(f"tab switch: median {statistics.median(switches):.0f}ms  worst {max(switches):.0f}ms")

    # --- idle: nothing is asked of the app. Everything measured after this
    #     point is the cost of merely being open.
    scenario("idle")
    process.cpu_percent(None)  # prime the counter
    idle_start = time.monotonic()
    while time.monotonic() - idle_start < IDLE_SECONDS:
        app.processEvents()
        time.sleep(0.02)
    mark(
        "idle_sample",
        seconds=IDLE_SECONDS,
        cpu_percent=round(process.cpu_percent(None), 2),
        rss_bytes=process.memory_info().rss,
        threads=process.num_threads(),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "artifacts"))
    ap.add_argument("--language", default="en")
    ap.add_argument("--font-scale", type=float, default=1.0)
    ap.add_argument("--themes", type=int, default=0, help="limit the theme sweep (0 = every shipped theme)")
    args = ap.parse_args()

    out = Path(args.out) / "timeline"
    out.mkdir(parents=True, exist_ok=True)
    isolation.preflight_instance_lock()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from tests._home_isolation import guard_real_home, restore_home_patches, set_test_home

    saved = set_test_home(isolation.SCRATCH_HOME)
    fake = isolation.prepared_config(language=args.language, font_scale=args.font_scale)

    daemon = threading.Thread(target=_daemon, daemon=True)
    failures: list[str] = []

    def body():
        app = QApplication.instance()
        try:
            window = cells.find_main_window()
            if window is None:
                failures.append("no MainWindow")
                return
            window.resize(1280, 800)
            app.processEvents()
            window.grab()
            mark("first_paint", rss_bytes=psutil.Process().memory_info().rss)
            pub = QTimer()
            pub.timeout.connect(lambda: publish(window))
            pub.start(100)
            run_scenarios(window, app, themes=args.themes)
            pub.stop()
            window.close()
        except Exception:
            traceback.print_exc()
            failures.append("exception in scenarios")
        finally:
            app.quit()

    mark("process_start")
    try:
        with guard_real_home(isolation.REAL_HOME), guard_real_home(isolation.DESKTOP_DIR), ExitStack() as stack:
            stack.enter_context(isolation.patched_modals())
            stack.enter_context(isolation.patched_destructive_boot())
            stack.enter_context(isolation.patched_gl_widget())
            stack.enter_context(isolation.patched_background_work())

            import anki_miner.gui.app as app_mod
            from anki_miner.gui.main_window import MainWindow

            orig_show = MainWindow.show

            def wrapped_show(self):
                mark("window_show")
                orig_show(self)
                daemon.start()
                QTimer.singleShot(0, body)

            stack.enter_context(patch.object(MainWindow, "show", wrapped_show))
            with contextlib.suppress(SystemExit):
                app_mod.main()
    finally:
        _STOP.set()
        with contextlib.suppress(Exception):
            fake.stop()
        restore_home_patches(saved)

    (out / "events.jsonl").write_text("\n".join(json.dumps(e) for e in _EVENTS), encoding="utf-8")
    (out / "gaps.jsonl").write_text("\n".join(json.dumps(g) for g in _GAPS), encoding="utf-8")

    # The GUI-freeze signal is snapshot AGE, not the daemon's own loop gap. Qt
    # releases the GIL inside setStyleSheet/setPalette, so the sampler thread
    # keeps ticking at 50 ms through a multi-second GUI-thread block; what stops
    # is the GUI-thread publisher. Ranking on loop_gap_ms would report ~77 ms
    # while the window was frozen for 2.7 s.
    worst = sorted(_GAPS, key=lambda g: -g["snapshot_age_ms"])[:15]
    (out / "worst_gaps.json").write_text(json.dumps(worst, indent=1), encoding="utf-8")

    summary = {
        "first_paint_ms": next(
            (round(e["t"] * 1000, 1) for e in _EVENTS if e["event"] == "first_paint"),
            None,
        ),
        "window_show_ms": next(
            (round(e["t"] * 1000, 1) for e in _EVENTS if e["event"] == "window_show"),
            None,
        ),
        "longest_event_loop_gap_ms": worst[0]["snapshot_age_ms"] if worst else None,
        "longest_event_loop_gap_scenario": worst[0]["scenario"] if worst else None,
        "worst_sampler_loop_gap_ms": max((g["loop_gap_ms"] for g in _GAPS), default=None),
        "theme_apply_ms": _stats([e["ms"] for e in _EVENTS if e["event"] == "theme_applied"]),
        "theme_set_stylesheet_ms": _stats([e["set_stylesheet_ms"] for e in _EVENTS if e["event"] == "theme_profile"]),
        "theme_set_palette_ms": _stats([e["set_palette_ms"] for e in _EVENTS if e["event"] == "theme_profile"]),
        "theme_profile": [e for e in _EVENTS if e["event"] == "theme_profile"],
        "tab_switch_ms": _stats([e["ms"] for e in _EVENTS if e["event"] == "tab_switch"]),
        "idle": next((e for e in _EVENTS if e["event"] == "idle_sample"), None),
        "samples": len(_GAPS),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    log(json.dumps(summary))
    for f in failures:
        log(f"FAILURE: {f}")
    return 1 if failures else 0


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values),
        "best": round(min(values), 1),
        "median": round(statistics.median(values), 1),
        "p95": round(sorted(values)[max(0, int(len(values) * 0.95) - 1)], 1),
        "worst": round(max(values), 1),
    }


if __name__ == "__main__":
    raise SystemExit(main())

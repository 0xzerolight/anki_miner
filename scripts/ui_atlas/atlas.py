"""Static screen atlas: render and check every screen at one layout cell.

Drives the REAL ``anki_miner.gui.app.main()``. ``tests/e2e/app_driver.AppDriver``
is not a substitute: it mounts one tab, so it cannot see the composed window, the
pinned action bars, the status strip or the settings navigator. Because
``main()`` ends in ``sys.exit(app.exec())``, the capture loop is armed from
*inside* the event loop by wrapping ``MainWindow.show``.

Cells
-----

``reference``
    1280x800, English, text scale 1.0. The comfortable cell.
``hostile``
    1024x768 (the app's own window minimum), German, text scale 1.5. This is
    where the 2026-07-25 audit found seven screens with the run button at or
    below the window edge and a Settings tab strip that overflowed into scroll
    arrows.
``hostile_pseudo``
    The hostile cell plus a translator that lengthens *every* string by 25
    characters. The German catalog only covers strings that existed when it was
    last regenerated, so a de-only run silently renders newer UI in English and
    under-reports width pressure. This cell is the honest substitute; it is not
    German, and it is labelled as such wherever it is reported.
``firstrun``
    Reference geometry with ``first_run_setup_done`` cleared.

Usage::

    .venv/bin/python scripts/ui_atlas/atlas.py --cell hostile --out /tmp/atlas
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import isolation  # noqa: E402

isolation.bootstrap()  # env + sys.path BEFORE any anki_miner import

import contextlib  # noqa: E402
from contextlib import ExitStack  # noqa: E402
from unittest.mock import patch  # noqa: E402

from cells import CELLS, PSEUDO_PADDING, find_main_window, reveal, screens_to_visit  # noqa: E402

import probe  # noqa: E402

__all__ = ["CELLS", "PSEUDO_PADDING", "find_main_window", "reveal", "screens_to_visit"]

STATE: dict = {"failures": [], "screens": 0, "findings": [], "dumps": []}


def log(msg: str) -> None:
    print(f"[atlas] {msg}", flush=True)


def capture_all(cell: str, out_dir: Path) -> None:
    """Runs inside the Qt event loop, after the window is shown."""
    from PyQt6.QtWidgets import QApplication, QDialog

    app = QApplication.instance()
    try:
        window = find_main_window()
        if window is None:
            STATE["failures"].append("MainWindow not found")
            return

        w, h, _font_scale, _locale, _fr, _pseudo = CELLS[cell]
        window.resize(w, h)
        app.processEvents()

        # Any startup dialog that survived the modal patches is a real finding.
        for d in QApplication.topLevelWidgets():
            if isinstance(d, QDialog) and d.isVisible():
                STATE["failures"].append(f"unexpected visible startup dialog: {type(d).__name__}")
                d.reject()

        for label, main_key, sub_key in screens_to_visit():
            try:
                reveal(window, main_key, sub_key)
            except Exception as exc:
                STATE["failures"].append(f"navigate {label}: {type(exc).__name__}: {exc}")
                continue

            settled = probe.settle(app, window)
            achieved = (window.width(), window.height())
            if achieved != (w, h):
                log(f"WARN {label}: achieved size {achieved} != requested {(w, h)}")

            dump = probe.dump_tree(window, label)
            dump["cell"] = cell
            dump["layout_settled"] = settled
            dump["requested_size"] = [w, h]
            STATE["dumps"].append(dump)

            findings = probe.run_checkers(window, label)
            for f in findings:
                f["cell"] = cell
            STATE["findings"].extend(findings)

            png = out_dir / f"{cell}__{label.replace('.', '_')}.png"
            # window.grab(), never a helper that calls adjustSize() — that would
            # resize the window being measured.
            window.grab().save(str(png), "PNG")
            STATE["screens"] += 1
            log(f"{label}: {len(dump['widgets'])} widgets, {len(findings)} hits, settled={settled}")

        window.close()
    except Exception:
        traceback.print_exc()
        STATE["failures"].append("exception in capture_all")
    finally:
        app.quit()


def lengthening_translators():
    """Patch ``app.install_translators`` so every string gains PSEUDO_PADDING chars.

    Installed before ``MainWindow`` is constructed, because widgets capture their
    ``tr()`` strings at construction time and this app has no live
    ``retranslateUi`` path.
    """
    from PyQt6.QtCore import QTranslator

    import anki_miner.gui.app as app_mod

    original = app_mod.install_translators

    class _Longer(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):
            if not source:
                return ""
            return f"{source}{'·' * PSEUDO_PADDING}"

    def _install(app, language):
        installed = original(app, language)
        longer = _Longer()
        app.installTranslator(longer)
        installed.append(longer)
        return installed

    return patch.object(app_mod, "install_translators", _install)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cell", required=True, choices=list(CELLS))
    ap.add_argument("--out", default=str(HERE / "artifacts"))
    ap.add_argument("--timeout-ms", type=int, default=180_000)
    args = ap.parse_args()

    out_dir = Path(args.out) / args.cell
    out_dir.mkdir(parents=True, exist_ok=True)

    isolation.preflight_instance_lock()

    from PyQt6.QtCore import QTimer

    from tests._home_isolation import guard_real_home, restore_home_patches, set_test_home

    saved = set_test_home(isolation.SCRATCH_HOME)  # module bindings + CONFIG_FILE

    width, height, font_scale, locale, first_run, pseudo = CELLS[args.cell]
    fake = isolation.prepared_config(language=locale, font_scale=font_scale, first_run=first_run)
    log(f"fake AnkiConnect at {fake.url}; cell={args.cell} {width}x{height} {locale} scale={font_scale}")

    rc = 0
    try:
        with guard_real_home(isolation.REAL_HOME), guard_real_home(isolation.DESKTOP_DIR), ExitStack() as stack:
            stack.enter_context(isolation.patched_modals())
            stack.enter_context(isolation.patched_destructive_boot())
            stack.enter_context(isolation.patched_gl_widget())
            stack.enter_context(isolation.patched_background_work())
            if pseudo:
                stack.enter_context(lengthening_translators())

            import anki_miner.gui.app as app_mod
            from anki_miner.gui.main_window import MainWindow

            orig_show = MainWindow.show

            def wrapped_show(self):
                orig_show(self)
                QTimer.singleShot(0, lambda: capture_all(args.cell, out_dir))
                QTimer.singleShot(args.timeout_ms, lambda: STATE["failures"].append("backstop timer fired"))

            stack.enter_context(patch.object(MainWindow, "show", wrapped_show))

            with contextlib.suppress(SystemExit):
                app_mod.main()
    finally:
        with contextlib.suppress(Exception):
            fake.stop()
        restore_home_patches(saved)

    if not isolation.store_recovery_fired():
        log("NOTE: store-recovery stub never fired (boot may not have reached it)")

    isolation.dump_modal_log(out_dir / "modals.jsonl")
    (out_dir / "dumps.json").write_text(json.dumps(STATE["dumps"], indent=1), encoding="utf-8")
    (out_dir / "findings_raw.json").write_text(json.dumps(STATE["findings"], indent=1), encoding="utf-8")

    # Dedup: a defect in shared chrome (header, status bar) is ONE finding seen on
    # many screens, not N findings. Group by identity, list the screens it hits.
    grouped: dict[tuple, dict] = {}
    for f in STATE["findings"]:
        key = (f.get("checker"), f.get("widget"), f.get("detail"))
        g = grouped.setdefault(key, {**f, "screens": []})
        g["screens"].append(f.get("screen"))
        g.pop("screen", None)
    deduped = sorted(grouped.values(), key=lambda g: (-len(g["screens"]), g.get("checker") or ""))
    for g in deduped:
        g["screen_count"] = len(g["screens"])
        g["scope"] = "app-chrome" if len(g["screens"]) >= 20 else "screen-local"
    (out_dir / "findings.json").write_text(json.dumps(deduped, indent=1), encoding="utf-8")
    log(f"deduped {len(STATE['findings'])} raw -> {len(deduped)} distinct findings")

    log(f"screens={STATE['screens']} findings={len(STATE['findings'])} failures={len(STATE['failures'])}")
    for f in STATE["failures"]:
        log(f"FAILURE: {f}")
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

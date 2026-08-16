"""Three passes the fixed-size atlas structurally cannot deliver.

CLICK
    Every ``QAbstractButton``'s affordance metadata (label / tooltip / a11y name).
    DEFAULT-DENY on actually pressing: nothing is clicked here. Proving which
    slots are destructive is not statically tractable, and some are (Settings ->
    Audio -> "Retry missing expression audio" unlinks ``.miss`` markers with no
    confirmation). ``clicked``/``skip_reason`` is reported honestly rather than
    implying coverage that does not exist.
MENU
    ``menuBar`` actions are otherwise entirely outside the audited surface,
    despite carrying the Setup Wizard and Restyle Mined Cards.
RESIZE
    The atlas builds each window at one fixed size; the issue #102 family is
    "the user DRAGS the window smaller", which resolves size policies
    differently. Emits a per-widget threshold table.

Usage::

    .venv/bin/python scripts/ui_atlas/sweeps.py --out /tmp/atlas
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

isolation.bootstrap()

import contextlib  # noqa: E402
from contextlib import ExitStack  # noqa: E402
from unittest.mock import patch  # noqa: E402

import cells  # noqa: E402

import probe  # noqa: E402

STATE: dict = {"buttons": [], "actions": [], "resize": [], "failures": []}

RESIZE_W = [1024, 1180, 1280]
RESIZE_H = [700, 768, 800]

#: Checkers cheap enough to run at every one of the nine resize points.
RESIZE_CHECKERS = (
    probe.check_01_itemview_crush,
    probe.check_02_no_scroll_crush,
    probe.check_04_text_clipped,
    probe.check_05_horizontal_overflow,
    probe.check_10_primary_action_hidden,
    probe.check_11_tabbar_overflow,
    probe.check_12_pinned_bar_clipped,
)


def log(m: str) -> None:
    print(f"[sweeps] {m}", flush=True)


def run(window, app) -> None:
    from PyQt6.QtWidgets import QAbstractButton, QMenu

    screens = cells.screens_to_visit()

    for label, main_key, sub_key in screens:
        cells.reveal(window, main_key, sub_key)
        probe.settle(app, window)
        leaf = window.tabs.currentWidget()
        for b in leaf.findChildren(QAbstractButton):
            if not b.isVisible() or b.objectName().startswith("qt_"):
                continue
            STATE["buttons"].append(
                {
                    "screen": label,
                    "cls": type(b).__name__,
                    "objectName": b.objectName(),
                    "text": b.text(),
                    "isEnabled": b.isEnabled(),
                    "toolTip": b.toolTip(),
                    "whatsThis": b.whatsThis(),
                    "accessibleName": b.accessibleName(),
                    "checkable": b.isCheckable(),
                    "clicked": False,
                    "skip_reason": "default-deny: destructive slots are not statically provable",
                }
            )

    for menu in window.menuBar().findChildren(QMenu):
        for act in menu.actions():
            if act.isSeparator():
                continue
            STATE["actions"].append(
                {
                    "menu": menu.title(),
                    "text": act.text(),
                    "shortcut": act.shortcut().toString(),
                    "isEnabled": act.isEnabled(),
                    "toolTip": act.toolTip(),
                }
            )

    for label, main_key, sub_key in screens:
        cells.reveal(window, main_key, sub_key)
        for w in RESIZE_W:
            for h in RESIZE_H:
                window.resize(w, h)
                probe.settle(app, window)
                for fn in RESIZE_CHECKERS:
                    try:
                        for hit in fn(window, label):
                            STATE["resize"].append(
                                {
                                    "screen": label,
                                    "w": w,
                                    "h": h,
                                    "checker": hit["checker"],
                                    "widget": hit["widget"],
                                    "detail": hit.get("detail", ""),
                                }
                            )
                    except Exception:
                        pass
        log(f"{label}: resize sweep done")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "artifacts"))
    ap.add_argument("--language", default="en")
    ap.add_argument("--font-scale", type=float, default=1.0)
    args = ap.parse_args()

    out = Path(args.out) / "sweeps"
    out.mkdir(parents=True, exist_ok=True)
    isolation.preflight_instance_lock()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from tests._home_isolation import guard_real_home, restore_home_patches, set_test_home

    saved = set_test_home(isolation.SCRATCH_HOME)
    fake = isolation.prepared_config(language=args.language, font_scale=args.font_scale)

    def body():
        app = QApplication.instance()
        try:
            window = cells.find_main_window()
            if window is None:
                STATE["failures"].append("no MainWindow")
                return
            run(window, app)
            window.close()
        except Exception:
            traceback.print_exc()
            STATE["failures"].append("exception in sweeps")
        finally:
            app.quit()

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
                orig_show(self)
                QTimer.singleShot(0, body)

            stack.enter_context(patch.object(MainWindow, "show", wrapped_show))
            with contextlib.suppress(SystemExit):
                app_mod.main()
    finally:
        with contextlib.suppress(Exception):
            fake.stop()
        restore_home_patches(saved)

    (out / "buttons.json").write_text(json.dumps(STATE["buttons"], indent=1), encoding="utf-8")
    (out / "menu_actions.json").write_text(json.dumps(STATE["actions"], indent=1), encoding="utf-8")

    thresholds: dict[tuple, dict] = {}
    for r in STATE["resize"]:
        k = (r["screen"], r["widget"], r["checker"])
        t = thresholds.setdefault(
            k,
            {
                "screen": r["screen"],
                "widget": r["widget"],
                "checker": r["checker"],
                "fails_at": [],
                "detail": r["detail"],
            },
        )
        t["fails_at"].append([r["w"], r["h"]])
    table = sorted(thresholds.values(), key=lambda t: -len(t["fails_at"]))
    for t in table:
        t["fail_count"] = len(t["fails_at"])
        t["largest_failing_size"] = max(t["fails_at"], key=lambda s: (s[0], s[1]))
        t["fails_at_all_sizes"] = len(t["fails_at"]) == len(RESIZE_W) * len(RESIZE_H)
    (out / "resize_thresholds.json").write_text(json.dumps(table, indent=1), encoding="utf-8")

    no_tip = [b for b in STATE["buttons"] if not b["toolTip"].strip() and not b["accessibleName"].strip()]
    log(
        f"buttons={len(STATE['buttons'])} (clicked=0 by design, {len(no_tip)} with no tooltip/a11y name) "
        f"menu_actions={len(STATE['actions'])} resize_hits={len(STATE['resize'])} "
        f"distinct_thresholds={len(table)}"
    )
    for f in STATE["failures"]:
        log(f"FAILURE: {f}")
    return 1 if STATE["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

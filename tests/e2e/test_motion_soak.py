"""Serial diagnostic soak: real animation timing, run five times over.

Everything else in the suite runs with the autouse instant-motion fixture, so no
animation ever actually elapses. That is what keeps several hundred assertions
off the wall clock, and it also means nothing in the default gate has ever seen
an animation *run*. This file is the one place that does.

It is a **diagnostic**, in the sense consolidation fixed for W4-T10:

* the hard signal is ``detect_divergence(...).verdict != "FAIL"`` — a suspect
  count (top-level widgets, Python threads, pooled QThreads, temp files) growing
  session over session, or a GUI-thread stall;
* RSS slope and idle CPU are **recorded and printed, never gated**. They are
  process-noise metrics and this file runs on whatever machine happens to be
  free;
* there are no BusyIndicator assertions — W4-T5 was cut, so there is no spinner
  to assert on.

Marked ``motion`` so the autouse instant fixture steps aside (that fixture keys
on the marker), and ``e2e`` so it stays out of the default gate. Run it with::

    .venv/bin/pytest -n0 -m e2e tests/e2e/test_motion_soak.py -s
"""

from __future__ import annotations

import time

import psutil
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPropertyAnimation
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.gui.utils import motion
from tests.e2e.instrumentation import capture_snapshot, detect_divergence

pytestmark = [pytest.mark.e2e, pytest.mark.motion]

#: Batches of real transitions. Five is the minimum a slope means anything over.
BATCHES = 5

#: Transitions per batch, per component.
REPEATS = 6

#: Themes cycled per batch. Two radically different ones is the useful case:
#: the palette and the whole stylesheet are replaced each way.
SOAK_THEMES = ("light", "catppuccin-mocha")


class _Gallery(QWidget):
    """Every animated surface in the app, in one widget.

    Deliberately not the composed ``MainWindow``: this measures the motion
    layer, and a real window drags in workers, timers and background tasks whose
    own lifetimes would swamp the signal being looked for.
    """

    def __init__(self) -> None:
        super().__init__()
        from anki_miner.gui.widgets.base.animated_tab_bar import install_animated_tab_bar
        from anki_miner.gui.widgets.base.status_badge import StatusBadge
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton
        from anki_miner.gui.widgets.progress_widget import ProgressWidget

        self.resize(720, 480)
        layout = QVBoxLayout(self)

        self.button = ModernButton("Run", variant="primary")
        layout.addWidget(self.button)

        self.badge = StatusBadge("Anki")
        layout.addWidget(self.badge)

        self.progress = ProgressWidget()
        layout.addWidget(self.progress)

        self.tabs = QTabWidget()
        install_animated_tab_bar(self.tabs)
        self.tabs.addTab(QWidget(), "One")
        self.tabs.addTab(QWidget(), "Two")
        self.tabs.addTab(QWidget(), "Three")
        layout.addWidget(self.tabs)

    def animations(self) -> list[QPropertyAnimation]:
        return [a for a in self.findChildren(QPropertyAnimation) if a.objectName().startswith("am-motion-")]


def _drain_deferred_deletes() -> None:
    """Actually destroy anything ``deleteLater``'d, so counts mean something.

    ``processEvents()`` does not deliver ``DeferredDelete``; without this the
    widget count only falls whenever the event loop happens to get around to it,
    and the series looks like a leak.
    """
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _run_batch(gallery: _Gallery, qapp, qtbot, batch: int) -> None:
    """One batch of real transitions across every animated surface."""
    from anki_miner.gui.resources.styles.theme import Theme

    for step in range(REPEATS):
        gallery.button.pressed.emit()
        gallery.button.released.emit()

        gallery.badge.set_status("pass" if step % 2 else "fail")

        # Only a forward move on a determinate bar animates; a reset snaps.
        gallery.progress.set_progress(step + 1, REPEATS)

        gallery.tabs.setCurrentIndex(step % gallery.tabs.count())

        for theme in SOAK_THEMES:
            Theme.set_mode(theme)
            Theme.apply_to_app(qapp, theme)

        qapp.processEvents()

    # Let every animation finish rather than tearing down mid-flight: an
    # animation stopped by teardown is a different (and much easier) case than
    # one that ran to completion, and it is completion that has to be clean.
    qtbot.waitUntil(lambda: not gallery.animations() or not motion.active_animations(gallery.button), timeout=4000)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        running = [a for a in gallery.animations() if a.state() == QPropertyAnimation.State.Running]
        if not running:
            break
        qapp.processEvents()
        time.sleep(0.01)

    gallery.progress.reset()
    qapp.processEvents()
    _drain_deferred_deletes()


@pytest.mark.timeout(0)
def test_real_motion_over_five_batches_does_not_diverge(qtbot, qapp, isolated_home, capsys):
    """Five batches of real animation must not leak widgets, threads or handles."""
    from anki_miner.gui.resources.styles.theme import Theme

    original_sheet = qapp.styleSheet()
    original_mode = Theme.get_current_mode()
    process = psutil.Process()

    gallery = _Gallery()
    qtbot.addWidget(gallery)
    gallery.show()
    qtbot.waitExposed(gallery)

    # Warm up before measuring: the first theme apply compiles a stylesheet and
    # the first animation builds its curve, and neither cost recurs.
    _run_batch(gallery, qapp, qtbot, batch=-1)
    _drain_deferred_deletes()

    snapshots = []
    try:
        for batch in range(BATCHES):
            _run_batch(gallery, qapp, qtbot, batch=batch)
            snapshots.append(capture_snapshot(test_home=isolated_home, index=batch, label=f"motion-batch-{batch}"))
    finally:
        Theme.set_mode(original_mode)
        qapp.setStyleSheet(original_sheet)

    report = detect_divergence(snapshots, mode="inprocess")

    # Diagnostics: printed with -s, never gated. RSS and CPU are process noise.
    process.cpu_percent(None)
    idle_start = time.monotonic()
    while time.monotonic() - idle_start < 1.0:
        qapp.processEvents()
        time.sleep(0.02)
    with capsys.disabled():
        print(f"\n[motion-soak] verdict={report.verdict} flags={report.flags}")
        print(f"[motion-soak] suspect_deltas={report.suspect_deltas}")
        print(f"[motion-soak] rss_bytes={[s.rss_bytes for s in snapshots]}")
        print(f"[motion-soak] idle_cpu_percent={process.cpu_percent(None):.1f} threads={process.num_threads()}")

    assert report.verdict != "FAIL", f"motion soak diverged: {report.flags}"


@pytest.mark.timeout(0)
def test_the_motion_marker_actually_restores_real_timing(qtbot, qapp):
    """Without this the whole soak is vacuous.

    ``tests/conftest.py`` installs an autouse fixture that forces
    ``motion.instant()`` for every test *not* marked ``motion``. In instant mode
    ``animate()`` still returns an animation object, so merely finding one
    proves nothing — only a ``Running`` state proves time is passing.
    """
    gallery = _Gallery()
    qtbot.addWidget(gallery)
    gallery.show()
    qtbot.waitExposed(gallery)

    gallery.button.pressed.emit()

    running = [a for a in gallery.animations() if a.state() == QPropertyAnimation.State.Running]
    assert running, "instant motion is still in force — the soak would measure nothing"


@pytest.mark.timeout(0)
def test_no_animation_survives_the_gallery(qtbot, qapp):
    """Tearing the surfaces down must leave nothing running behind them.

    The soak above proves counts do not grow; this proves the reason they do
    not — Qt parent/child ownership destroys every animation with its target.
    """
    from PyQt6 import sip

    gallery = _Gallery()
    gallery.show()
    qtbot.waitExposed(gallery)
    gallery.button.pressed.emit()
    gallery.badge.set_status("fail")
    gallery.tabs.setCurrentIndex(1)
    animations = gallery.animations()
    assert animations, "the gallery produced no animations — the soak would be vacuous"

    sip.delete(gallery)

    assert all(sip.isdeleted(a) for a in animations)

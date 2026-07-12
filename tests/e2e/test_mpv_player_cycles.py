"""Real-libmpv open/close cycle test for the mpv preview backend.

The committed regression net for the segfault/abort class the migration
retired: the render-context lifecycle (create → render → detach/free →
terminate) is exercised against a REAL libmpv, in the exact ordering
``SubtitlePlayerWidget._teardown_player`` uses. Unit tests mock the mpv
boundary and can never catch a real teardown-ordering abort.

Skipped wherever libmpv is unavailable (CI). Runs on a dev machine with a
system libmpv (or ``ANKI_MINER_LIBMPV`` pointing at one) as part of the
recurring pre-release manual gate. Under ``QT_QPA_PLATFORM=offscreen`` the
QOpenGLWidget still creates a real GL context once shown (offscreen platform
plugin provides one), so the render context genuinely initializes; frames may
not present, but creation/free/terminate ordering — the crash surface — is
fully exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from anki_miner.utils.mpv_loader import mpv_available

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not mpv_available(), reason="libmpv not available on this machine"),
]

CYCLES = 5


@pytest.fixture(scope="module")
def tiny_clip(tmp_path_factory) -> Path:
    """A 2-second test clip generated with the system ffmpeg."""
    out = tmp_path_factory.mktemp("mpv_cycles") / "clip.mkv"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not out.exists():
        pytest.skip(f"ffmpeg unavailable/failed: {result.stderr.decode(errors='replace')[-200:]}")
    return out


def test_widget_open_close_cycles_with_real_libmpv(qtbot, tiny_clip):
    """N create/load/play/teardown cycles against real libmpv must not abort.

    Each cycle builds a fresh SubtitlePlayerWidget (fresh MPV core + render
    context), shows it (GL init → render-context creation), loads and briefly
    plays the clip, then releases through the production teardown path.
    """
    from PyQt6.QtWidgets import QApplication

    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

    app = QApplication.instance()
    assert app is not None

    for _cycle in range(CYCLES):
        widget = SubtitlePlayerWidget()
        qtbot.addWidget(widget)
        widget.resize(360, 240)
        widget.show()
        qtbot.waitExposed(widget)

        widget.set_source(tiny_clip, [(0.0, 1.0, "テスト")])
        assert widget.player is not None

        qtbot.waitUntil(lambda w=widget: w._file_loaded, timeout=8000)
        widget.play()
        qtbot.waitUntil(
            lambda w=widget: (w.player.time_pos or 0) > 0.1 if w.player is not None else False,
            timeout=8000,
        )
        widget.pause()

        # Production teardown ordering: player swap -> detach -> terminate.
        widget.release()
        assert widget.player is None

        # qtbot owns the widget's destruction (addWidget); just close and let
        # the per-test teardown delete it — mixing in a manual deleteLater
        # leaves qtbot closing an already-deleted wrapper.
        widget.close()
        app.processEvents()


def test_seek_and_eof_replay_with_real_libmpv(qtbot, tiny_clip):
    """Pending-seek gating and EOF replay against a real core."""
    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

    widget = SubtitlePlayerWidget()
    qtbot.addWidget(widget)
    widget.resize(360, 240)
    widget.show()
    qtbot.waitExposed(widget)

    widget.set_source(tiny_clip, [])
    # Seek issued before file-loaded must queue, not raise (mpv errors on
    # pre-load seeks — the reason the gate exists).
    widget.seek_seconds(1.0)
    qtbot.waitUntil(lambda: widget._file_loaded, timeout=8000)
    qtbot.waitUntil(
        lambda: widget.player is not None and (widget.player.time_pos or 0) >= 0.9,
        timeout=8000,
    )

    # Play to EOF (keep-open auto-pauses on the last frame)...
    widget.play()
    qtbot.waitUntil(lambda: widget._at_eof, timeout=15000)
    # ...then Play must replay from the start, not dead-end at EOF.
    widget.play()
    qtbot.waitUntil(
        lambda: not widget._at_eof and widget.player is not None and (widget.player.time_pos or 99) < 1.0,
        timeout=8000,
    )

    widget.release()

"""Tests for MpvVideoWidget (offscreen; no libmpv on CI).

Under QT_QPA_PLATFORM=offscreen an unshown QOpenGLWidget never fires
initializeGL, so attach() on an unshown widget only stores the player — these
tests drive the internal hooks directly where GL behavior matters.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget
from anki_miner.utils.mpv_loader import MpvUnavailableError

MODULE = "anki_miner.gui.widgets.mpv_video_widget"


class TestAttachDetach:
    def test_attach_before_gl_only_stores(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        player = MagicMock()
        widget.attach(player)
        assert widget._player is player
        assert widget._render_ctx is None  # GL never initialized offscreen/unshown

    def test_detach_idempotent_and_forgets_player(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget.attach(MagicMock())
        widget.detach()
        widget.detach()
        assert widget._player is None
        assert widget._render_ctx is None

    def test_detach_frees_context_before_forgetting_player(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        player = MagicMock()
        widget.attach(player)
        fake_ctx = MagicMock()
        widget._render_ctx = fake_ctx
        with patch.object(MpvVideoWidget, "makeCurrent"), patch.object(MpvVideoWidget, "doneCurrent"):
            widget.detach()
        fake_ctx.free.assert_called_once()
        assert widget._render_ctx is None
        assert widget._player is None


class TestRenderContextCreation:
    def test_libmpv_absent_is_silent_noop(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget._player = MagicMock()
        failures = []
        widget.render_failed.connect(failures.append)
        with patch(f"{MODULE}.load_mpv", side_effect=MpvUnavailableError("no libmpv")):
            widget._create_render_context()
        assert widget._render_ctx is None
        assert failures == []  # silent branch: CI / pip-without-libmpv

    def test_ctx_creation_failure_emits_render_failed(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget._player = MagicMock()
        failures = []
        widget.render_failed.connect(failures.append)
        fake_mpv = MagicMock()
        fake_mpv.MpvRenderContext.side_effect = RuntimeError("GL init failed")
        with patch(f"{MODULE}.load_mpv", return_value=fake_mpv):
            widget._create_render_context()
        assert widget._render_ctx is None
        assert widget._get_proc_cb is None
        assert failures == ["GL init failed"]

    def test_successful_creation_keeps_proc_cb_referenced(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget._player = MagicMock()
        fake_mpv = MagicMock()
        with patch(f"{MODULE}.load_mpv", return_value=fake_mpv):
            widget._create_render_context()
        # ctypes trampoline must stay referenced (GC -> segfault in mpv thread)
        assert widget._get_proc_cb is not None
        assert widget._render_ctx is not None
        assert widget._render_ctx.update_cb == widget._on_mpv_update


class TestPaintAndFree:
    def test_paintgl_without_ctx_is_noop(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget.paintGL()  # must not raise

    def test_paintgl_renders_with_dpr_scaled_fbo(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget.resize(200, 100)
        ctx = MagicMock()
        widget._render_ctx = ctx
        with (
            patch.object(MpvVideoWidget, "devicePixelRatioF", return_value=2.0),
            patch.object(MpvVideoWidget, "defaultFramebufferObject", return_value=7),
        ):
            widget.paintGL()
        kwargs = ctx.render.call_args.kwargs
        assert kwargs["flip_y"] is True
        assert kwargs["opengl_fbo"]["fbo"] == 7
        assert kwargs["opengl_fbo"]["w"] == widget.width() * 2
        assert kwargs["opengl_fbo"]["h"] == widget.height() * 2

    def test_paintgl_render_exception_is_swallowed_and_logged_once(self, qtbot, caplog):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        ctx = MagicMock()
        ctx.render.side_effect = RuntimeError("mpv_render_context_render failed")
        widget._render_ctx = ctx
        with caplog.at_level(logging.WARNING, logger=MODULE):
            widget.paintGL()  # must not raise
            widget.paintGL()  # second failure: must not log again
        warnings = [r for r in caplog.records if r.name == MODULE and r.levelno >= logging.WARNING]
        assert len(warnings) == 1, "render failure must be logged once per render context, not per frame"
        assert ctx.render.call_count == 2

    def test_free_wraps_make_current(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        order = []
        ctx = MagicMock()
        ctx.free.side_effect = lambda: order.append("free")
        widget._render_ctx = ctx
        with (
            patch.object(MpvVideoWidget, "makeCurrent", side_effect=lambda: order.append("makeCurrent")),
            patch.object(MpvVideoWidget, "doneCurrent", side_effect=lambda: order.append("doneCurrent")),
        ):
            widget._free_render_context()
        assert order == ["makeCurrent", "free", "doneCurrent"]
        assert widget._get_proc_cb is None

    def test_update_cb_only_emits(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        received = []
        widget._mpv_frame_update.connect(lambda: received.append(True))
        widget._on_mpv_update()
        assert received == [True]


class TestSizeFloor:
    """The widget declared no size, so a splitter could squeeze it to nothing.

    That is how the word curator ended up rendering video into a sliver. The
    floor lives here rather than on the player so that hiding the view -- which
    both audio-only paths do -- takes the reservation with it.
    """

    def test_the_frame_reserves_a_16_9_box(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        assert (widget.minimumWidth(), widget.minimumHeight()) == (320, 180)

    def test_the_floor_does_not_track_the_ui_font_scale(self, qtbot):
        """Video pixels are not text -- the one place a literal is correct."""
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        font = widget.font()
        font.setPointSizeF(font.pointSizeF() * 2)
        widget.setFont(font)
        assert widget.minimumHeight() == 180

    def test_a_hidden_frame_stops_reserving_anything(self, qtbot):
        """Both audio-only paths hide the view, and the "audio still plays"
        notice must not be framed by an empty black box.
        """
        host = QWidget()
        qtbot.addWidget(host)
        layout = QVBoxLayout(host)
        widget = MpvVideoWidget()
        layout.addWidget(widget)
        with_video = host.minimumSizeHint().height()
        widget.setVisible(False)
        assert host.minimumSizeHint().height() < with_video


class TestRenderReady:
    def test_successful_creation_emits_render_ready(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget._player = MagicMock()
        ready = []
        widget.render_ready.connect(lambda: ready.append(True))
        with patch(f"{MODULE}.load_mpv", return_value=MagicMock()):
            widget._create_render_context()
        assert ready == [True]
        assert widget.has_render_context is True

    def test_failure_does_not_emit_render_ready(self, qtbot):
        widget = MpvVideoWidget()
        qtbot.addWidget(widget)
        widget._player = MagicMock()
        ready = []
        widget.render_ready.connect(lambda: ready.append(True))
        fake_mpv = MagicMock()
        fake_mpv.MpvRenderContext.side_effect = RuntimeError("GL init failed")
        with patch(f"{MODULE}.load_mpv", return_value=fake_mpv):
            widget._create_render_context()
        assert ready == []
        assert widget.has_render_context is False


class TestGlProbe:
    """The context libmpv renders into is logged once, and never breaks GL init.

    Diagnostic for the "OpenGL error INVALID_ENUM after creating texture" class
    of failure: the app sets no QSurfaceFormat, so what mpv gets is whatever the
    platform handed us, and a bug report without it is unanswerable.
    """

    @pytest.fixture(autouse=True)
    def _reset_probe_flag(self, monkeypatch):
        monkeypatch.setattr(f"{MODULE}._gl_probe_logged", False, raising=True)

    def _fake_ctx(self):
        ctx = MagicMock()
        fmt = ctx.format.return_value
        fmt.majorVersion.return_value = 2
        fmt.minorVersion.return_value = 0
        fmt.profile.return_value.name = "NoProfile"
        fmt.renderableType.return_value.name = "DefaultRenderableType"
        ctx.isOpenGLES.return_value = False
        ctx.openGLModuleType.return_value.name = "LibGL"
        ctx.hasExtension.return_value = True
        return ctx

    def test_logs_the_granted_format_once(self, caplog):
        from anki_miner.gui.widgets import mpv_video_widget

        ctx = self._fake_ctx()
        with caplog.at_level(logging.INFO, logger=MODULE):
            mpv_video_widget._log_gl_probe(ctx)
            mpv_video_widget._log_gl_probe(ctx)

        records = [r for r in caplog.records if r.name == MODULE]
        assert len(records) == 1, "probe must log once per process, not per widget"
        message = records[0].getMessage()
        assert "got=2.0" in message
        assert "profile=NoProfile" in message
        assert "norm16=True" in message

    def test_a_broken_context_does_not_raise(self, caplog):
        """Never raise inside GL init — the widget's stated invariant."""
        from anki_miner.gui.widgets import mpv_video_widget

        ctx = MagicMock()
        ctx.format.side_effect = RuntimeError("no context")
        with caplog.at_level(logging.DEBUG, logger=MODULE):
            mpv_video_widget._log_gl_probe(ctx)

        assert not [r for r in caplog.records if r.name == MODULE and r.levelno >= logging.INFO]

    def test_a_missing_extension_query_still_logs(self, caplog):
        from anki_miner.gui.widgets import mpv_video_widget

        ctx = self._fake_ctx()
        ctx.hasExtension.side_effect = RuntimeError("no extension list")
        with caplog.at_level(logging.INFO, logger=MODULE):
            mpv_video_widget._log_gl_probe(ctx)

        records = [r for r in caplog.records if r.name == MODULE]
        assert len(records) == 1
        assert "norm16=unknown" in records[0].getMessage()

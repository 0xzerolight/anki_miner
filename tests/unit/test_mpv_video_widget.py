"""Tests for MpvVideoWidget (offscreen; no libmpv on CI).

Under QT_QPA_PLATFORM=offscreen an unshown QOpenGLWidget never fires
initializeGL, so attach() on an unshown widget only stores the player — these
tests drive the internal hooks directly where GL behavior matters.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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

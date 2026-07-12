"""QOpenGLWidget view rendering an mpv core via the libmpv render API.

Why the render API and not ``wid`` embedding: ``wid`` works on X11/win32/macOS
only — it cannot work on Wayland (no foreign window embedding). The render API
(``render_gl.h``) draws into our own GL framebuffer, giving one code path on
every platform.

Threading contract (render.h):
- mpv's update callback fires on an mpv-internal thread. It must do nothing
  but emit a queued Qt signal — calling libmpv or touching widgets there is
  undefined behavior.
- Every ``mpv_render_*`` call (including ``free``) needs the widget's GL
  context current on the calling thread.
- The render context MUST be freed before the owning ``MPV`` handle is
  terminated, or libmpv aborts the process. The owner calls :meth:`detach`
  before ``terminate()``; the ``aboutToBeDestroyed`` hookup is the safety net
  for the reverse widget-destruction order (Qt emits it with the doomed GL
  context current).

This is a dumb view: it owns only the render context, never the player, and
holds no playback policy. The controller (SubtitlePlayerWidget) owns the MPV
handle and its lifecycle.
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QByteArray, pyqtSignal
from PyQt6.QtGui import QOpenGLContext
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from anki_miner.utils.mpv_loader import MpvUnavailableError, load_mpv

logger = logging.getLogger(__name__)


class MpvVideoWidget(QOpenGLWidget):
    """Renders video frames from an attached mpv core.

    Failure modes are deliberately split (never raise inside GL callbacks):

    - libmpv absent (``MpvUnavailableError``): silent no-op. The controller
      already gates on ``mpv_available()`` and never attaches in this state;
      the guard here only protects CI / exotic call orders.
    - libmpv loaded but render-context creation fails (broken GL, VNC/VM,
      software stack missing): log at WARNING and emit :attr:`render_failed`
      so the controller can show a visible "audio still plays" notice instead
      of a silent black box.
    """

    #: Emitted on the GUI thread when render-context creation failed although
    #: mpv itself is available. Payload: human-readable reason.
    render_failed = pyqtSignal(str)

    #: Internal: emitted from mpv's update thread, queued to the GUI thread.
    _mpv_frame_update = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._player: Any = None
        self._render_ctx: Any = None
        self._gl_ready = False
        # ctypes callback trampolines MUST stay referenced for the lifetime of
        # the render context — if Python GC collects them, mpv's C thread
        # calls into freed memory and the process segfaults.
        self._get_proc_cb: Any = None
        self._mpv_frame_update.connect(self.update)

    # ------------------------------------------------------------------ API

    def attach(self, player: Any) -> None:
        """Bind an mpv core to this view.

        Safe to call before or after GL initialization: whichever of
        attach/initializeGL runs second creates the render context.
        """
        self._player = player
        if self._gl_ready and self._render_ctx is None:
            self.makeCurrent()
            try:
                self._create_render_context()
            finally:
                self.doneCurrent()

    def detach(self) -> None:
        """Free the render context and forget the player. Idempotent.

        MUST be called before the owner terminates the mpv core: freeing a
        render context against a dead core (or terminating a core with a live
        render context) is a hard process abort in libmpv.
        """
        self._free_render_context()
        self._player = None

    # ------------------------------------------------------------- Qt hooks

    def initializeGL(self) -> None:
        self._gl_ready = True
        glctx = self.context()
        if glctx is not None:
            # Qt destroys the GL context before Python __del__ runs; freeing
            # here (Qt emits with the context current) is the safety net when
            # widget destruction precedes an explicit detach().
            glctx.aboutToBeDestroyed.connect(self._free_render_context)
        if self._player is not None and self._render_ctx is None:
            self._create_render_context()

    def paintGL(self) -> None:
        if self._render_ctx is None:
            return
        ratio = self.devicePixelRatioF()
        self._render_ctx.render(
            flip_y=True,
            opengl_fbo={
                "fbo": self.defaultFramebufferObject(),
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
            },
        )

    # ------------------------------------------------------------- internals

    def _create_render_context(self) -> None:
        """Create the MpvRenderContext. GL context must be current."""
        try:
            mpv_module = load_mpv()
        except MpvUnavailableError:
            # CI / libmpv-less installs: the controller never attaches a real
            # player in this state, so stay silent rather than notifying.
            logger.debug("libmpv unavailable; MpvVideoWidget stays inert")
            return

        def get_proc_address(_ctx: Any, name: bytes) -> int:
            glctx = QOpenGLContext.currentContext()
            if glctx is None:
                return 0
            return int(glctx.getProcAddress(QByteArray(name)))

        try:
            self._get_proc_cb = mpv_module.MpvGlGetProcAddressFn(get_proc_address)
            self._render_ctx = mpv_module.MpvRenderContext(
                self._player,
                "opengl",
                opengl_init_params={"get_proc_address": self._get_proc_cb},
            )
            self._render_ctx.update_cb = self._on_mpv_update
        except Exception as exc:  # noqa: BLE001 - never raise inside GL callbacks
            self._render_ctx = None
            self._get_proc_cb = None
            logger.warning("mpv render context creation failed: %s", exc)
            self.render_failed.emit(str(exc))

    def _free_render_context(self) -> None:
        """Free the render context with the GL context current. Idempotent."""
        if self._render_ctx is None:
            return
        render_ctx, self._render_ctx = self._render_ctx, None
        self.makeCurrent()
        try:
            render_ctx.free()
        finally:
            self.doneCurrent()
            self._get_proc_cb = None

    def _on_mpv_update(self) -> None:
        # Runs on an mpv-internal thread: ONLY emit (queued to the GUI thread,
        # where update() schedules paintGL). Anything else here is UB.
        self._mpv_frame_update.emit()

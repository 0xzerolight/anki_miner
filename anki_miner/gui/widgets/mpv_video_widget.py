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

import contextlib
import logging
import os
from typing import Any

from PyQt6.QtCore import QByteArray, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QOpenGLContext, QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from anki_miner.utils.mpv_loader import MpvUnavailableError, load_mpv

logger = logging.getLogger(__name__)

# GL_R16/GL_RG16 are core in desktop GL 3.0+; on GLES they exist only via this
# extension. 10-bit video (yuv420p10) is uploaded to exactly those formats, so
# its presence separates "the driver cannot do 16-bit planes" from everything
# else. On a desktop-GL context it reads False and means nothing -- there the
# formats are core.
_NORM16_EXTENSION = b"GL_EXT_texture_norm16"

# One probe line per process, not per widget construction.
_gl_probe_logged = False


def _log_gl_probe(glctx: QOpenGLContext) -> None:
    """Log the GL context libmpv will render into, once per process.

    Diagnostic for the "OpenGL error INVALID_ENUM after creating texture" class
    of failure: libmpv picks its texture internal formats from what the context
    actually advertises, and this app never calls
    ``QSurfaceFormat.setDefaultFormat``, so the answer is whatever the platform
    handed us -- Qt's untouched default *requests* 2.0/NoProfile. 10-bit video
    needs ``GL_R16``/``GL_RG16``, core in desktop GL 3.0+ and absent from GLES
    without :data:`_NORM16_EXTENSION`.

    ``glctx.format()`` is the format actually granted, not the one requested,
    which is the number that decides whether those formats exist. (PyQt6 exposes
    no ``QOpenGLContext.functions()``, so there is no ``glGetString`` route to
    the driver strings; the granted format plus the extension check answer the
    same question.)

    INFO and permanent: a black-video or INVALID_ENUM bug report is
    unanswerable without it, and it costs one line per launch.
    """
    global _gl_probe_logged
    if _gl_probe_logged:
        return
    _gl_probe_logged = True
    try:
        fmt = glctx.format()
        default = QSurfaceFormat.defaultFormat()
        norm16: object = "unknown"
        with contextlib.suppress(Exception):
            norm16 = glctx.hasExtension(QByteArray(_NORM16_EXTENSION))
        logger.info(
            "mpv GL context: got=%d.%d profile=%s renderable=%s es=%s module=%s "
            "norm16=%s default_request=%d.%d/%s platform=%s",
            fmt.majorVersion(),
            fmt.minorVersion(),
            fmt.profile().name,
            fmt.renderableType().name,
            glctx.isOpenGLES(),
            glctx.openGLModuleType().name,
            norm16,
            default.majorVersion(),
            default.minorVersion(),
            default.profile().name,
            QGuiApplication.platformName(),
        )
    except Exception:  # noqa: BLE001 - a probe must never break GL init
        logger.debug("GL probe failed", exc_info=True)


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

    #: Emitted on the GUI thread once the render context exists. LOAD-BEARING
    #: for the controller: issuing ``loadfile`` before this fires makes mpv's
    #: video-out init fail permanently for that file ("vo/libmpv: No render
    #: context set." -> audio-only black pane) — both consumer dialogs call
    #: set_source in __init__, before the widget is shown/GL exists, so the
    #: controller MUST defer loading until this signal.
    render_ready = pyqtSignal()

    #: Emitted on the GUI thread when render-context creation failed although
    #: mpv itself is available. Payload: human-readable reason.
    render_failed = pyqtSignal(str)

    #: Internal: emitted from mpv's update thread, queued to the GUI thread.
    _mpv_frame_update = pyqtSignal()

    def __init__(self, parent=None) -> None:
        # LOG BEFORE super(). Constructing a QOpenGLWidget brings up a real GL
        # context, and on a host whose driver will not load that aborts the
        # process outright — a field report died exactly here, with no Python
        # traceback to catch. This line is what a bug report is read against: if
        # it is the last thing in the log, death was at or before GL bring-up.
        logger.info(
            "video surface: constructing QOpenGLWidget platform=%s session=%s",
            QGuiApplication.platformName(),
            os.environ.get("XDG_SESSION_TYPE", "-"),
        )
        super().__init__(parent)
        # The widget declared no size at all, so a splitter could squeeze the
        # frame to nothing and the word curator did exactly that. A 16:9 box is
        # the smallest thing that still reads as video.
        #
        # It belongs HERE rather than on SubtitlePlayerWidget: a hidden child
        # contributes nothing to its parent's minimum, so both audio-only paths
        # (libmpv absent, and render-context failure) drop the reservation on
        # their own instead of framing the "audio still plays" notice with an
        # empty black rectangle.
        #
        # A pixel constant is right here and wrong almost everywhere else in
        # this codebase (see widgets/base/sizing.py): video pixels are not
        # text, so this floor must NOT track the UI font scale. And no
        # setHeightForWidth -- QSplitter ignores it, so it would promise an
        # aspect ratio nothing enforces.
        self.setMinimumSize(320, 180)
        self._player: Any = None
        self._render_ctx: Any = None
        self._gl_ready = False
        # Rate-limits the paintGL render-failure warning to once per render
        # context, not once per dropped frame (60fps of the same fault would
        # otherwise flood the log). Reset alongside context (re)creation.
        self._render_error_logged = False
        # ctypes callback trampolines MUST stay referenced for the lifetime of
        # the render context — if Python GC collects them, mpv's C thread
        # calls into freed memory and the process segfaults.
        self._get_proc_cb: Any = None
        self._mpv_frame_update.connect(self.update)

    @property
    def has_render_context(self) -> bool:
        """True once the mpv render context exists (loadfile is safe)."""
        return self._render_ctx is not None

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
            _log_gl_probe(glctx)
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
        try:
            self._render_ctx.render(
                flip_y=True,
                opengl_fbo={
                    "fbo": self.defaultFramebufferObject(),
                    "w": int(self.width() * ratio),
                    "h": int(self.height() * ratio),
                },
            )
        except Exception as exc:  # noqa: BLE001 - never raise inside GL callbacks
            # python-mpv's errcheck raises builtins (RuntimeError/ValueError/
            # SystemError/MemoryError) when the core returns a negative status
            # from render(). An exception escaping this Qt virtual reaches the
            # app excepthook, which pops a modal QMessageBox synchronously
            # inside paint dispatch with the GL context current -- worse than
            # the dropped frame this swallows.
            if not self._render_error_logged:
                self._render_error_logged = True
                logger.warning("mpv render failed, skipping frame: %s", exc)

    # ------------------------------------------------------------- internals

    def _create_render_context(self) -> None:
        """Create the MpvRenderContext. GL context must be current."""
        self._render_error_logged = False
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
            return
        self.render_ready.emit()

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

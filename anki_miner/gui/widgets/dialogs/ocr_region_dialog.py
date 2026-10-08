"""Set the Video OCR subtitle region on a frame of the video (Utilities → Video OCR).

Window-modal and opened with ``open()``. Every ffmpeg / OCR call runs through
``run_off_thread`` parented to ``worker_parent`` (the tab), never to this dialog:
``deleteLater`` on a dialog that owns a live QThread aborts the process. Results
that land after ``done()`` are dropped (``_closing``), and a stale frame request
is superseded by generation, as in SentenceEditDialog.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.image_fit import fit_transform, widget_to_image
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.services.video_ocr.frame_source import Region, crop_frame, still_at
from anki_miner.services.video_ocr.scanner import read_region_text
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

logger = logging.getLogger(__name__)

_SLIDER_STEPS = 1000
_DEBOUNCE_MS = 250
_MIN_DRAG_PX = 8  # in image pixels: a click is not a region
_THUMB_WIDTH = 240
_REGION_COLOR = QColor(255, 80, 60)


def _to_pixmap(bgr: np.ndarray) -> QPixmap:
    h, w = bgr.shape[:2]
    rgb = bgr[:, :, ::-1].tobytes()  # a packed C-order RGB888 buffer
    return QPixmap.fromImage(QImage(rgb, w, h, 3 * w, QImage.Format.Format_RGB888).copy())


class _RegionCanvas(QWidget):
    """A frame fitted to the pane; drag to draw the region (emitted as frame fractions)."""

    region_drawn = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 270)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap: QPixmap | None = None
        self._region: Region | None = None
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None
        self.message = ""

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap, self.message = pixmap, ""
        self.update()

    def set_message(self, text: str) -> None:
        self.message = text
        self.update()

    def set_region(self, region: Region | None) -> None:
        self._region = region
        self.update()

    def _fit(self) -> tuple[float, float, float, int, int] | None:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale, dx, dy = fit_transform(self.width(), self.height(), pw, ph)
        return (scale, dx, dy, pw, ph) if scale > 0 else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self._fit() is not None:
            self._drag_start = self._drag_now = event.position()
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_start is not None:
            self._drag_now = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_start is None:
            return
        start, end = self._drag_start, event.position()
        self._drag_start = self._drag_now = None
        region = self._region_from(start, end)
        if region is not None:
            self._region = region
            self.region_drawn.emit(region)
        self.update()

    def _region_from(self, a: QPointF, b: QPointF) -> Region | None:
        fit = self._fit()
        if fit is None:
            return None
        scale, dx, dy, pw, ph = fit
        x1, y1 = widget_to_image(min(a.x(), b.x()), min(a.y(), b.y()), scale, dx, dy)
        x2, y2 = widget_to_image(max(a.x(), b.x()), max(a.y(), b.y()), scale, dx, dy)
        x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(float(pw), x2), min(float(ph), y2)
        if x2 - x1 < _MIN_DRAG_PX or y2 - y1 < _MIN_DRAG_PX:
            return None
        # Rounded to what config stores, so the tab's region equals the persisted one.
        return Region(*Region(x1 / pw, y1 / ph, (x2 - x1) / pw, (y2 - y1) / ph).as_config())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            if self.message:
                painter.setPen(QColor(128, 128, 128))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
                return
            fit = self._fit()
            if fit is None or self._pixmap is None:
                return
            scale, dx, dy, pw, ph = fit
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(dx, dy, pw * scale, ph * scale), self._pixmap, QRectF(0, 0, pw, ph))
            painter.setPen(QPen(_REGION_COLOR, 2))
            if self._region is not None:
                r = self._region
                painter.drawRect(
                    QRectF(dx + r.x * pw * scale, dy + r.y * ph * scale, r.w * pw * scale, r.h * ph * scale)
                )
            if self._drag_start is not None and self._drag_now is not None:
                painter.drawRect(QRectF(self._drag_start, self._drag_now).normalized())
        finally:
            painter.end()


class OcrRegionDialog(QDialog):
    """Pick a frame with dialogue on it and drag a box around the subtitle area."""

    def __init__(
        self,
        config,
        video: Path,
        region: Region | None,
        *,
        worker_parent: QObject,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        add_min_max_buttons(self)
        self.setWindowTitle(self.tr("Set subtitle region"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(960, 680)
        self._config, self._video, self._worker_parent = config, video, worker_parent
        self._region = region
        self._frame: np.ndarray | None = None
        self._duration: float | None = None
        self._gen = 0
        self._inflight = False
        self._pending = False
        self._closing = False
        self._test_gen = 0

        layout = QVBoxLayout(self)
        hint = QLabel(
            self.tr("Move the slider to a moment with dialogue on screen, then drag a box around the subtitles.")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.canvas = _RegionCanvas()
        self.canvas.set_region(region)
        self.canvas.set_message(self.tr("Loading a frame…"))
        self.canvas.region_drawn.connect(self._on_region_drawn)
        layout.addWidget(self.canvas, 1)

        slider_row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, _SLIDER_STEPS)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._on_slider)
        self.time_label = QLabel("")
        slider_row.addWidget(self.slider, 1)
        slider_row.addWidget(self.time_label)
        layout.addLayout(slider_row)

        test_row = QHBoxLayout()
        self.test_button = ModernButton(self.tr("Test this frame"), variant="secondary")
        self.test_button.setEnabled(False)
        self.test_button.clicked.connect(self._on_test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        layout.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok_button is not None  # the box was built with Ok
        self.ok_button = ok_button
        self.ok_button.setEnabled(region is not None)
        layout.addWidget(buttons)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_still)

        ffprobe = resolve_ffprobe(config)
        run_off_thread(
            worker_parent,
            lambda: get_media_duration_seconds(video, ffprobe),
            self._on_duration,
            lambda _message: self._on_duration(None),
        )

    def done(self, a0: int) -> None:  # noqa: D102 - Qt override
        self._closing = True
        super().done(a0)

    def region(self) -> Region | None:
        return self._region

    def region_thumbnail(self) -> QPixmap | None:
        if self._frame is None or self._region is None:
            return None
        return _to_pixmap(crop_frame(self._frame, self._region)).scaledToWidth(
            _THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation
        )

    def _seconds_at_slider(self) -> float:
        return self.slider.value() / _SLIDER_STEPS * self._duration if self._duration else 0.0

    def _on_duration(self, duration: object) -> None:
        if self._closing:
            return
        self._duration = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
        self.slider.setEnabled(self._duration is not None)
        if self._duration is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(_SLIDER_STEPS // 10)  # past opening logos
            self.slider.blockSignals(False)
        self._show_time()
        self._request_still()

    def _show_time(self) -> None:
        seconds = int(self._seconds_at_slider())
        self.time_label.setText(f"{seconds // 60}:{seconds % 60:02d}")

    def _on_slider(self, _value: int) -> None:
        self._show_time()
        self._debounce.start()

    def _request_still(self) -> None:
        if self._closing:
            return
        self._gen += 1
        if self._inflight:
            self._pending = True
            return
        self._dispatch_still(self._seconds_at_slider(), self._gen)

    def _dispatch_still(self, t: float, gen: int) -> None:
        self._inflight = True
        config, video = self._config, self._video
        run_off_thread(
            self._worker_parent,
            lambda: still_at(config, video, t),
            lambda frame: self._on_still(gen, frame),
            lambda message: self._on_still_failed(gen, message),
        )

    def _on_still(self, gen: int, frame: object) -> None:
        if self._closing:
            return
        self._inflight = False
        if gen == self._gen and isinstance(frame, np.ndarray):
            self._frame = frame
            self.canvas.set_pixmap(_to_pixmap(frame))
            self.test_button.setEnabled(self._region is not None)
        self._drain()

    def _on_still_failed(self, gen: int, message: str) -> None:
        if self._closing:
            return
        self._inflight = False
        logger.warning("Video OCR region dialog: frame failed: %s", message)
        if gen == self._gen:
            self.canvas.set_message(self.tr("This frame could not be read. Try another point in the video."))
        self._drain()

    def _drain(self) -> None:
        if self._pending and not self._closing:
            self._pending = False
            self._request_still()

    def _on_region_drawn(self, region: object) -> None:
        if isinstance(region, Region):
            self._region = region
            self.ok_button.setEnabled(True)
            self.test_button.setEnabled(self._frame is not None)
            self.test_result.setText("")

    def _on_test(self) -> None:
        if self._frame is None or self._region is None:
            return
        self._test_gen += 1
        gen, config = self._test_gen, self._config
        crop = crop_frame(self._frame, self._region)
        self.test_result.setText(self.tr("Reading…"))
        run_off_thread(
            self._worker_parent,
            lambda: read_region_text(config, crop),
            lambda text: self._on_test_done(gen, text),
            lambda message: self._on_test_failed(gen, message),
        )

    def _on_test_done(self, gen: int, text: object) -> None:
        if self._closing or gen != self._test_gen:
            return
        self.test_result.setText(str(text) if text else self.tr("No text found in the box."))

    def _on_test_failed(self, gen: int, message: str) -> None:
        if self._closing or gen != self._test_gen:
            return
        logger.warning("Video OCR region dialog: test read failed: %s", message)
        self.test_result.setText(self.tr("The OCR engine could not read this frame."))

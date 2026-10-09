"""OcrRegionDialog: drag-to-region mapping, OK gating, late results after close, Test this frame."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPixmap

from anki_miner.gui.widgets.dialogs import ocr_region_dialog as mod
from anki_miner.gui.widgets.dialogs.ocr_region_dialog import OcrRegionDialog, _RegionCanvas
from anki_miner.services.video_ocr.frame_source import Region

_FRAME = np.full((500, 1000, 3), 90, dtype=np.uint8)


def test_fit_helpers_moved_without_changing_page_image_view():
    from anki_miner.gui.utils import image_fit
    from anki_miner.gui.widgets.page_image_view import _PageCanvas

    assert _PageCanvas.fit_transform(500, 250, 1000, 500) == image_fit.fit_transform(500, 250, 1000, 500)
    assert image_fit.widget_to_image(60, 35, 0.5, 10, 10) == (100.0, 50.0)


def test_a_drag_maps_to_frame_fractions_and_is_rounded(qtbot):
    from anki_miner.gui.utils import image_fit

    canvas = _RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(500, 270)  # the canvas minimum is 480x270; Qt clamps a smaller resize
    assert image_fit.fit_transform(canvas.width(), canvas.height(), 1000, 500) == (0.5, 0.0, 10.0)
    canvas.set_pixmap(QPixmap(1000, 500))
    drawn: list[Region] = []
    canvas.region_drawn.connect(drawn.append)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(50, 210))
    qtbot.mouseMove(canvas, QPoint(450, 250))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(450, 250))
    (region,) = drawn
    assert region.x == pytest.approx(0.1, abs=0.01) and region.y == pytest.approx(0.8, abs=0.01)
    assert region.w == pytest.approx(0.8, abs=0.01) and region.h == pytest.approx(0.16, abs=0.01)
    assert region == Region(*region.as_config())  # rounded at the source (spec §5.2)


def test_a_click_without_a_drag_draws_nothing(qtbot):
    canvas = _RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(500, 250)
    canvas.set_pixmap(QPixmap(1000, 500))
    drawn: list[Region] = []
    canvas.region_drawn.connect(drawn.append)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    assert drawn == []


@pytest.fixture
def dialog(qtbot, test_config, tmp_path):
    host = _RegionCanvas()  # any QObject parent for the off-thread workers
    qtbot.addWidget(host)
    with (
        patch.object(mod, "get_media_duration_seconds", return_value=60.0),
        patch.object(mod, "still_at", return_value=_FRAME),
    ):
        dlg = OcrRegionDialog(test_config, tmp_path / "v.mkv", None, worker_parent=host)
        qtbot.addWidget(dlg)
        qtbot.waitUntil(lambda: dlg._frame is not None, timeout=3000)
    return dlg


def test_ok_waits_for_a_region(dialog):
    assert not dialog.ok_button.isEnabled()
    dialog._on_region_drawn(Region(0.1, 0.8, 0.8, 0.15))
    assert dialog.ok_button.isEnabled()
    assert dialog.region() == Region(0.1, 0.8, 0.8, 0.15)
    assert dialog.region_thumbnail() is not None


def test_a_frame_landing_after_close_is_ignored(dialog):
    before = dialog._frame
    dialog.done(0)
    dialog._on_still(dialog._gen, np.zeros((10, 10, 3), dtype=np.uint8))
    assert dialog._frame is before


def test_test_this_frame_shows_the_scans_reading(dialog, qtbot):
    dialog._on_region_drawn(Region(0.1, 0.8, 0.8, 0.15))
    with patch.object(mod, "read_region_text", return_value="「行くぞ」"):
        dialog.test_button.click()
        qtbot.waitUntil(lambda: dialog.test_result.text() == "「行くぞ」", timeout=3000)


def test_a_new_frame_clears_the_reading_and_drops_a_late_one(dialog, qtbot):
    dialog._on_region_drawn(Region(0.1, 0.8, 0.8, 0.15))
    with patch.object(mod, "read_region_text", return_value="「行くぞ」"):
        dialog.test_button.click()
        qtbot.waitUntil(lambda: dialog.test_result.text() == "「行くぞ」", timeout=3000)
    old_test_gen = dialog._test_gen
    dialog._on_still(dialog._gen, np.full((500, 1000, 3), 30, dtype=np.uint8))
    assert dialog.test_result.text() == ""
    dialog._on_test_done(old_test_gen, "x")  # a read of the frame no longer shown
    assert dialog.test_result.text() == ""


def test_redrawing_the_box_drops_a_late_reading(dialog):
    dialog._on_region_drawn(Region(0.1, 0.8, 0.8, 0.15))
    old_test_gen = dialog._test_gen
    dialog._on_region_drawn(Region(0.2, 0.7, 0.6, 0.2))
    dialog._on_test_done(old_test_gen, "x")  # a read of the box no longer drawn
    assert dialog.test_result.text() == ""


def test_a_failed_frame_retires_the_old_one(dialog):
    dialog._on_region_drawn(Region(0.1, 0.8, 0.8, 0.15))
    assert dialog.test_button.isEnabled()
    old_test_gen = dialog._test_gen
    dialog._on_still_failed(dialog._gen, "boom")
    assert not dialog.test_button.isEnabled()
    assert dialog.region_thumbnail() is None
    dialog._on_test_done(old_test_gen, "x")  # a read of the frame no longer shown
    assert dialog.test_result.text() == ""

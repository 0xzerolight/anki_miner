"""A16: a manga with no OCR data points at the tool that makes one."""

from __future__ import annotations

import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"
_PROFILE = "anki_miner.gui.widgets.reading_manga_tab.get_profile"
NO_OCR_ARCHIVE = (
    "No .mokuro data found for 'Vol 1.cbz'. Expected 'Vol 1.mokuro' alongside it, "
    "or a .mokuro member inside the archive."
)
NO_OCR_FOLDER = (
    "'series' is not a recognized reading source: no .mokuro volumes or embedded-.mokuro "
    "archives inside it and no matching .mokuro sidecar beside it."
)


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingMangaTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget


def _offer_manga_ocr(monkeypatch, offered: bool) -> None:
    capabilities = frozenset({"manga_ocr"}) if offered else frozenset()
    monkeypatch.setattr(_PROFILE, lambda code: SimpleNamespace(capabilities=capabilities))


def _issue(tab):
    return tab.issue_banner().current_issue()


def test_an_archive_without_ocr_points_at_manga_ocr(tab, tmp_path, monkeypatch):
    _offer_manga_ocr(monkeypatch, True)
    archive = tmp_path / "Vol 1.cbz"
    archive.touch()

    tab._report_detection_failure(archive, NO_OCR_ARCHIVE)

    issue = _issue(tab)
    assert issue.summary == ("This manga has no text layer yet. Manga OCR can make one for the folder this file is in.")
    assert issue.action_text == "Open Manga OCR"
    assert issue.details == NO_OCR_ARCHIVE


def test_a_folder_without_ocr_names_the_folder(tab, tmp_path, monkeypatch):
    _offer_manga_ocr(monkeypatch, True)

    tab._report_detection_failure(tmp_path, NO_OCR_FOLDER)

    assert _issue(tab).summary == "This manga has no text layer yet. Manga OCR can make one for this folder."


def test_where_manga_ocr_is_not_offered_the_banner_says_so_plainly(tab, tmp_path, monkeypatch):
    _offer_manga_ocr(monkeypatch, False)

    tab._report_detection_failure(tmp_path, NO_OCR_FOLDER)

    issue = _issue(tab)
    assert issue.summary == "This manga has no text layer yet, so it can't be mined."
    assert issue.action_text == ""


def test_other_failures_stay_plain(tab, tmp_path):
    archive = tmp_path / "broken.cbz"
    archive.touch()

    tab._report_detection_failure(archive, "Cannot read archive 'broken.cbz': File is not a zip file")

    assert _issue(tab).summary == "Anki Miner can't mine this file."


def test_other_failures_on_a_folder_say_folder(tab, tmp_path):
    tab._report_detection_failure(tmp_path, "Permission denied")

    assert _issue(tab).summary == "Anki Miner can't mine this folder."


def test_the_action_opens_manga_ocr_on_that_folder(tab, tmp_path, monkeypatch):
    revealed: list = []
    tool = SimpleNamespace(folder_selector=MagicMock())

    class _Window:
        def reveal_capability(self, target):
            revealed.append(target)

        def findChild(self, cls):  # noqa: N802 - mirrors QObject.findChild
            return tool

    monkeypatch.setattr(tab, "window", lambda: _Window())

    tab._open_manga_ocr(tmp_path)

    assert revealed == [CapabilityTarget("subtitles", "mokuro")]
    tool.folder_selector.set_path.assert_called_once_with(str(tmp_path))


def test_a_real_plain_cbz_reaches_the_pointer(tab, tmp_path, monkeypatch, qtbot):
    _offer_manga_ocr(monkeypatch, True)
    archive = tmp_path / "Plain Vol 1.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("001.jpg", b"not really a jpeg")
    tab.volume_file_selector.set_path(str(archive))

    tab._on_mine_clicked()
    qtbot.waitUntil(lambda: not tab._detection_pending, timeout=5000)

    issue = _issue(tab)
    assert issue is not None
    assert issue.action_text == "Open Manga OCR"


def test_the_real_japanese_profile_offers_manga_ocr():
    """After E17 merged, Japanese mining offers the Manga OCR hand-off without any patching."""
    from anki_miner.gui.widgets.reading_manga_tab import _MANGA_OCR_CAPABILITY
    from anki_miner.languages.registry import get_profile

    assert _MANGA_OCR_CAPABILITY in get_profile("ja").capabilities
    assert _MANGA_OCR_CAPABILITY not in get_profile("ko").capabilities

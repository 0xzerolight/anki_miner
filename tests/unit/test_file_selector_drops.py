"""Drops that say what they will do, and refuse out loud (decision D50-B).

Before this, ``FileSelector.dropEvent`` set the field to whatever URL arrived:
a subtitle dropped on the video picker "worked" and then failed at run time, a
folder dropped on a file picker did nothing visible, and a remote URL produced
an empty path. Nothing lit up while the drag was in the air, so there was no
way to tell a valid target from a dead one until you let go.

The drop still must NOT write the remembered-folder history (D7): only a
non-empty ``QFileDialog`` return is a statement about where the user keeps this
kind of file. ``tests/unit/test_file_selector_browse_dir.py`` owns that pin;
this file re-states it for the refusal path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent

from anki_miner.gui.utils import session_state
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector, accepts_suffixes


def _mime(*urls: str, text: str | None = None) -> QMimeData:
    data = QMimeData()
    if urls:
        data.setUrls([QUrl(url) for url in urls])
    if text is not None:
        data.setText(text)
    return data


def _enter(widget: FileSelector, data: QMimeData) -> QDragEnterEvent:
    event = QDragEnterEvent(
        QPointF(1.0, 1.0).toPoint(),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(event)
    return event


def _drop(widget: FileSelector, data: QMimeData) -> QDropEvent:
    event = QDropEvent(
        QPointF(1.0, 1.0),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)
    return event


@pytest.fixture
def video_selector(qtbot) -> FileSelector:
    widget = FileSelector(
        label="Video File:",
        file_mode=True,
        drop_validator=accepts_suffixes((".mkv", ".mp4"), "This field takes a video file."),
    )
    qtbot.addWidget(widget)
    return widget


class TestAValidTargetLightsUp:
    def test_the_field_is_marked_valid_while_the_drag_is_over_it(self, video_selector, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        _enter(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        assert video_selector.input.property("dropState") == "valid"

    def test_it_says_what_it_will_take(self, video_selector, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        _enter(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        assert "Video File" in video_selector.status_label.text()

    def test_a_label_less_selector_falls_back_to_the_kind(self, qtbot, tmp_path):
        widget = FileSelector(label="", file_mode=False)
        qtbot.addWidget(widget)
        folder = tmp_path / "season"
        folder.mkdir()

        _enter(widget, _mime(QUrl.fromLocalFile(str(folder)).toString()))

        assert "folder" in widget.status_label.text()

    def test_the_drop_lands(self, video_selector, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        event = _drop(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        assert video_selector.get_path() == str(episode)
        assert event.isAccepted()

    def test_the_state_is_cleared_after_the_drop(self, video_selector, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()
        _enter(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        _drop(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        assert video_selector.input.property("dropState") == ""

    def test_the_state_is_cleared_when_the_drag_leaves(self, video_selector, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()
        _enter(video_selector, _mime(QUrl.fromLocalFile(str(episode)).toString()))

        video_selector.dragLeaveEvent(QDragLeaveEvent())

        assert video_selector.input.property("dropState") == ""


class TestARefusalIsSpokenNotSwallowed:
    def _refuse(self, widget: FileSelector, data: QMimeData, qtbot) -> tuple[str, QDropEvent]:
        with qtbot.waitSignal(widget.drop_rejected) as blocker:
            event = _drop(widget, data)
        return blocker.args[0], event

    def test_the_wrong_suffix_is_refused_with_the_consumer_reason(self, video_selector, tmp_path, qtbot):
        subtitle = tmp_path / "ep01.srt"
        subtitle.touch()

        reason, event = self._refuse(video_selector, _mime(QUrl.fromLocalFile(str(subtitle)).toString()), qtbot)

        assert reason == "This field takes a video file."
        assert event.isAccepted() is False
        assert video_selector.get_path() == ""
        assert video_selector.status_label.text() == reason

    def test_a_folder_on_a_file_field_is_refused(self, video_selector, tmp_path, qtbot):
        folder = tmp_path / "season"
        folder.mkdir()

        reason, _ = self._refuse(video_selector, _mime(QUrl.fromLocalFile(str(folder)).toString()), qtbot)

        assert "folder" in reason
        assert video_selector.get_path() == ""

    def test_a_file_on_a_folder_field_is_refused(self, qtbot, tmp_path):
        widget = FileSelector(label="Video Folder:", file_mode=False)
        qtbot.addWidget(widget)
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        reason, _ = self._refuse(widget, _mime(QUrl.fromLocalFile(str(episode)).toString()), qtbot)

        assert "file" in reason
        assert widget.get_path() == ""

    def test_a_remote_url_is_refused(self, video_selector, qtbot):
        reason, _ = self._refuse(video_selector, _mime("https://example.com/ep01.mkv"), qtbot)

        assert "local" in reason.lower()
        assert video_selector.get_path() == ""

    def test_an_empty_payload_is_refused(self, video_selector, qtbot):
        reason, _ = self._refuse(video_selector, _mime(), qtbot)

        assert reason
        assert video_selector.get_path() == ""

    def test_more_than_one_path_is_refused(self, video_selector, tmp_path, qtbot):
        first, second = tmp_path / "a.mkv", tmp_path / "b.mkv"
        first.touch()
        second.touch()

        reason, _ = self._refuse(
            video_selector,
            _mime(QUrl.fromLocalFile(str(first)).toString(), QUrl.fromLocalFile(str(second)).toString()),
            qtbot,
        )

        assert "one" in reason
        assert video_selector.get_path() == ""

    def test_the_field_is_marked_invalid_while_a_wrong_drag_hovers(self, video_selector, tmp_path):
        subtitle = tmp_path / "ep01.srt"
        subtitle.touch()

        _enter(video_selector, _mime(QUrl.fromLocalFile(str(subtitle)).toString()))

        assert video_selector.input.property("dropState") == "invalid"
        assert video_selector.status_label.text() == "This field takes a video file."

    def test_a_refused_drop_leaves_an_existing_path_alone(self, video_selector, tmp_path, qtbot):
        good = tmp_path / "ep01.mkv"
        good.touch()
        video_selector.set_path(str(good))
        subtitle = tmp_path / "ep01.srt"
        subtitle.touch()

        self._refuse(video_selector, _mime(QUrl.fromLocalFile(str(subtitle)).toString()), qtbot)

        assert video_selector.get_path() == str(good)

    def test_a_refused_drop_records_no_folder_history(self, qtbot, tmp_path):
        widget = FileSelector(
            label="Video File:",
            file_mode=True,
            history_key="reading.novels.inputs",
            drop_validator=accepts_suffixes((".mkv",), "This field takes a video file."),
        )
        qtbot.addWidget(widget)
        subtitle = tmp_path / "ep01.srt"
        subtitle.touch()

        _drop(widget, _mime(QUrl.fromLocalFile(str(subtitle)).toString()))

        assert session_state.remembered_directory("reading.novels.inputs") is None


class TestTheValidatorIsTheConsumersOwn:
    def test_no_validator_accepts_any_file(self, qtbot, tmp_path):
        widget = FileSelector(label="Anything:", file_mode=True)
        qtbot.addWidget(widget)
        anything = tmp_path / "notes.txt"
        anything.touch()

        _drop(widget, _mime(QUrl.fromLocalFile(str(anything)).toString()))

        assert widget.get_path() == str(anything)

    def test_a_validator_can_be_installed_after_construction(self, qtbot, tmp_path):
        widget = FileSelector(label="Anything:", file_mode=True)
        qtbot.addWidget(widget)
        widget.set_drop_validator(accepts_suffixes((".mkv",), "Video only."))
        anything = tmp_path / "notes.txt"
        anything.touch()

        _drop(widget, _mime(QUrl.fromLocalFile(str(anything)).toString()))

        assert widget.get_path() == ""

    def test_suffix_matching_is_case_insensitive(self, qtbot, tmp_path):
        widget = FileSelector(
            label="Video File:",
            file_mode=True,
            drop_validator=accepts_suffixes((".mkv",), "Video only."),
        )
        qtbot.addWidget(widget)
        shouty = tmp_path / "EP01.MKV"
        shouty.touch()

        _drop(widget, _mime(QUrl.fromLocalFile(str(shouty)).toString()))

        assert Path(widget.get_path()).name == "EP01.MKV"


class TestTheLitBorderActuallyWins:
    """QSS specificity is equal here, so load ORDER is what decides.

    ``QLineEdit[success="true"]`` and ``QLineEdit[dropState="valid"]`` are both a
    type selector plus one attribute. A field holding a valid path already
    carries ``success``, so the drop state has to come later in the sheet or a
    drag over a filled field would look like nothing was happening.
    """

    def test_the_drop_state_rules_come_after_the_validity_rules(self):
        from anki_miner.gui.resources.styles.theme import Theme

        qss = Theme.get_stylesheet("dark")

        assert qss.index('QLineEdit[dropState="valid"]') > qss.index('QLineEdit[success="true"]')
        assert qss.index('QLineEdit[dropState="invalid"]') > qss.index('QLineEdit[error="true"]')

    def test_both_drop_states_resolve_their_colour_tokens(self):
        from anki_miner.gui.resources.styles.theme import Theme

        qss = Theme.get_stylesheet("dark")
        for state in ("valid", "invalid"):
            block = qss.split(f'QLineEdit[dropState="{state}"]', 1)[1].split("}", 1)[0]
            assert "${" not in block

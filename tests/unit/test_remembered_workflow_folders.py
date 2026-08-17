"""File dialogs reopen in the folder last accepted for that workflow (D7).

``FileSelector``'s own contract lives in ``test_file_selector_browse_dir.py``.
What is pinned here is the wiring: which screens opted in, under which key, and
the two dialogs that never went through ``FileSelector`` at all — Reading →
Subtitles' multi-select and the tool tabs' output chooser.

Two things are as load-bearing as the remembering: one workflow's anchor never
overwrites another's, and Settings, profiles and Deck Builder stay out of the
history entirely.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import file_dialogs, session_state
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"

# Every key the app is allowed to write. Inputs and output are separate for the
# tools: results rarely land where the sources live.
_INPUT_KEYS = frozenset(
    {
        "video.single.inputs",
        "video.batch.inputs",
        "audio.inputs",
        "reading.manga.inputs",
        "reading.novels.inputs",
        "reading.subtitles.inputs",
        "reading.text.inputs",
        "tools.generate.inputs",
        "tools.retime.inputs",
        "tools.condense.inputs",
    }
)
_OUTPUT_KEYS = frozenset({"tools.generate.output", "tools.retime.output", "tools.condense.output"})
_ALL_KEYS = _INPUT_KEYS | _OUTPUT_KEYS


# ---------------------------------------------------------------------------
# Reading -> Subtitles: the multi-select dialog
# ---------------------------------------------------------------------------


@pytest.fixture
def reading_subtitles_tab(qtbot, test_config: AnkiMinerConfig):
    from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab

    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingSubtitlesTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        try:
            yield widget
        finally:
            widget.deleteLater()


def test_add_files_remembers_the_first_accepted_file(reading_subtitles_tab, monkeypatch, tmp_path):
    """A multi-select is one visit to one folder; the rest say nothing new."""
    folder = tmp_path / "season"
    folder.mkdir()
    first, second = folder / "ep01.srt", folder / "ep02.srt"
    first.touch()
    second.touch()
    monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(first), str(second)]))

    reading_subtitles_tab._on_add_files_clicked()

    assert session_state.remembered_directory("reading.subtitles.inputs") == str(folder)


def test_add_files_reopens_in_the_remembered_folder(reading_subtitles_tab, monkeypatch, tmp_path):
    remembered = tmp_path / "library" / "series"
    remembered.mkdir(parents=True)
    session_state.remember_accepted_path("reading.subtitles.inputs", str(remembered), file_mode=False)
    captured: dict[str, str] = {}

    def fake_names(*a, on_done, **kw):
        captured["dir"] = a[2]
        on_done([])

    monkeypatch.setattr(file_dialogs, "pick_open_files", fake_names)
    reading_subtitles_tab._on_add_files_clicked()

    assert captured["dir"] == str(remembered)


def test_add_files_cancel_records_nothing(reading_subtitles_tab, monkeypatch):
    monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([]))

    reading_subtitles_tab._on_add_files_clicked()

    assert session_state.remembered_directory("reading.subtitles.inputs") is None


def test_dropping_subtitle_files_records_nothing(reading_subtitles_tab, tmp_path):
    """A drop is not a visit to a folder, so it moves no anchor."""
    dropped = tmp_path / "dropped"
    dropped.mkdir()
    subtitle = dropped / "ep.srt"
    subtitle.touch()

    reading_subtitles_tab._add_paths([subtitle])

    assert reading_subtitles_tab.file_list.count() == 1  # vacuity guard
    assert session_state.remembered_directory("reading.subtitles.inputs") is None


# ---------------------------------------------------------------------------
# Tool tabs: the output-folder chooser
# ---------------------------------------------------------------------------


@pytest.fixture(params=["generate", "retime", "condense"])
def tool_tab(request, qtbot, test_config: AnkiMinerConfig):
    from anki_miner.gui.widgets.condense_tab import CondenseTab
    from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
    from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

    cls = {"generate": SubtitleCreationTab, "retime": SubtitleRetimeTab, "condense": CondenseTab}[request.param]
    tab = cls(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    yield tab
    tab.deleteLater()


def test_each_tool_has_its_own_output_key(tool_tab):
    assert tool_tab.OUTPUT_HISTORY_KEY in _OUTPUT_KEYS


def test_choosing_an_output_folder_remembers_it(tool_tab, monkeypatch, tmp_path):
    chosen = tmp_path / "results"
    chosen.mkdir()
    monkeypatch.setattr(file_dialogs, "pick_directory", lambda *a, on_done, **kw: on_done(str(chosen)))

    tool_tab._on_choose_output()

    assert session_state.remembered_directory(tool_tab.OUTPUT_HISTORY_KEY) == str(chosen)


def test_the_output_chooser_reopens_where_it_last_wrote(tool_tab, monkeypatch, tmp_path):
    remembered = tmp_path / "out" / "deep"
    remembered.mkdir(parents=True)
    session_state.remember_accepted_path(tool_tab.OUTPUT_HISTORY_KEY, str(remembered), file_mode=False)
    captured: dict[str, str] = {}

    def fake_existing(*a, on_done, **kw):
        captured["dir"] = a[2]
        on_done("")

    monkeypatch.setattr(file_dialogs, "pick_directory", fake_existing)
    tool_tab._on_choose_output()

    assert captured["dir"] == str(remembered)


def test_cancelling_the_output_chooser_records_nothing(tool_tab, monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    session_state.remember_accepted_path(tool_tab.OUTPUT_HISTORY_KEY, str(seed), file_mode=False)
    monkeypatch.setattr(file_dialogs, "pick_directory", lambda *a, on_done, **kw: on_done(""))

    tool_tab._on_choose_output()

    assert session_state.remembered_directory(tool_tab.OUTPUT_HISTORY_KEY) == str(seed)


def test_an_output_folder_does_not_disturb_the_input_anchor(tool_tab, monkeypatch, tmp_path):
    sources = tmp_path / "sources"
    results = tmp_path / "results"
    sources.mkdir()
    results.mkdir()
    inputs_key = tool_tab.OUTPUT_HISTORY_KEY.replace(".output", ".inputs")
    session_state.remember_accepted_path(inputs_key, str(sources), file_mode=False)
    monkeypatch.setattr(file_dialogs, "pick_directory", lambda *a, on_done, **kw: on_done(str(results)))

    tool_tab._on_choose_output()

    assert session_state.remembered_directory(inputs_key) == str(sources)
    assert session_state.remembered_directory(tool_tab.OUTPUT_HISTORY_KEY) == str(results)


# ---------------------------------------------------------------------------
# Key discipline across the whole app
# ---------------------------------------------------------------------------


def _selector_keys(root) -> set[str]:
    return {sel._history_key for sel in root.findChildren(FileSelector) if sel._history_key}


class TestOptIn:
    def test_no_selector_uses_an_unregistered_key(self, wired_window):
        """A typo in a key is a silently dead history; this is what catches it."""
        window, _titles, _tabs = wired_window
        assert _selector_keys(window) <= _ALL_KEYS

    def test_every_mining_workflow_opted_in(self, wired_window):
        window, _titles, _tabs = wired_window
        # reading.subtitles.inputs has no FileSelector (it is a direct
        # multi-select dialog), so it is covered by its own tests above.
        assert _selector_keys(window) == _INPUT_KEYS - {"reading.subtitles.inputs"}

    @pytest.mark.parametrize("class_name", ["SettingsTab", "DeckBuilderTab"])
    def test_excluded_screens_have_no_history(self, wired_window, class_name):
        """Settings resources, import/export, profiles and Deck Builder are out."""
        window, _titles, _tabs = wired_window
        index = next(i for i in range(window.tabs.count()) if type(window.tabs.widget(i)).__name__ == class_name)
        excluded = window.tabs.widget(index)
        assert excluded.findChildren(FileSelector)  # vacuity guard
        assert _selector_keys(excluded) == set()

    def test_workflow_anchors_do_not_overwrite_one_another(self, tmp_path):
        for i, key in enumerate(sorted(_ALL_KEYS)):
            folder = tmp_path / f"f{i}"
            folder.mkdir()
            session_state.remember_accepted_path(key, str(folder), file_mode=False)

        for i, key in enumerate(sorted(_ALL_KEYS)):
            assert session_state.remembered_directory(key) == str(tmp_path / f"f{i}")

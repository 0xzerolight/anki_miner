"""A language switch refuses without side effects, or commits completely.

The ordering here is the whole test: the durable queue snapshots are the user's
pending work, and a refused switch that had already discarded them is a data
loss dressed up as a refusal.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers import language_switch
from anki_miner.gui.utils import queue_state_store
from anki_miner.languages.registry import get_profile
from anki_miner.languages.zh import availability


@pytest.fixture(autouse=True)
def zh_stack_present(monkeypatch):
    """Assume the zh extra is installed, so only R11b's test exercises the probe.

    ``jieba``/``pypinyin``/``opencc`` are an optional extra, and the real zh
    profile carries a probe that reports them missing. Without this the whole
    file would pass or fail on what happens to be installed on the machine.
    """
    monkeypatch.setattr(
        language_switch,
        "get_profile",
        lambda code: replace(get_profile(code), unavailable_reason=None),
    )


@pytest.fixture(autouse=True)
def no_first_visit_prompt(monkeypatch):
    """This file is about the switch ORDER; the prompt has its own file.

    Every switch to zh here is a first visit, so the commit tail would open the
    real first-visit modal - and ``_FakeWindow`` is not a QWidget, so it opens
    parentless and blocks in ``exec``. Answer "no thanks" and get out of the way.
    """
    monkeypatch.setattr(language_switch, "_first_visit_choice", lambda *a, **k: language_switch.FIRST_VISIT_NONE)


class _FakeScreen:
    QUEUE_STATE_KEY = "queue.youtube"

    def __init__(self, rows: int) -> None:
        self.cleared = 0
        self._rows = rows

    def queue_snapshot(self) -> queue_state_store.QueueSnapshot:
        return queue_state_store.QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=tuple(
                queue_state_store.QueueItemSnapshot(
                    item_id=f"row-{i}", source=queue_state_store.url_source(f"https://x/{i}")
                )
                for i in range(self._rows)
            ),
        )

    def clear_queue(self) -> None:
        self.cleared += 1
        self._rows = 0


class _FakeWindow:
    def __init__(self, config: AnkiMinerConfig, *, rows: int = 0) -> None:
        self.config = config
        self.guard_ready = True
        self.resources_ready = True
        self.screen = _FakeScreen(rows)
        self.issues: list[str] = []
        self.prewarms = 0
        self.syncs = 0
        self.guard_kinds: list[str] = []
        # What a dirty Settings panel commits when the guard's preflight runs.
        self.pending_edit: dict[str, object] | None = None

    def get_config(self) -> AnkiMinerConfig:
        return self.config

    def update_config(self, config: AnkiMinerConfig) -> None:
        self.config = config

    @contextmanager
    def _dictionary_mutation_guard(self, kind: str):
        self.guard_kinds.append(kind)
        if self.guard_ready and self.pending_edit is not None:
            # The real guard's preflight commits pending Settings edits through
            # update_config, so the live config changes inside the guard.
            self.update_config(replace(self.config, **self.pending_edit))
        yield self.guard_ready

    def release_dictionary_resources(self) -> bool:
        return self.resources_ready

    def iter_queue_screens(self):
        return [self.screen]

    def show_screen_issue(self, issue, action=None) -> None:
        self.issues.append(issue.summary)

    def restart_prewarm(self) -> None:
        self.prewarms += 1

    def sync_mining_language_surfaces(self) -> None:
        self.syncs += 1


@pytest.fixture
def saved_snapshot():
    snapshot = queue_state_store.QueueSnapshot(
        key="queue.youtube",
        items=(queue_state_store.QueueItemSnapshot(item_id="a", source=queue_state_store.url_source("https://x/a")),),
    )
    queue_state_store.save(snapshot)
    assert "queue.youtube" in queue_state_store.stored_keys()
    return snapshot


def test_a_busy_refusal_keeps_the_saved_snapshots_and_the_language(test_config, saved_snapshot):
    window = _FakeWindow(test_config, rows=1)
    window.resources_ready = False

    assert language_switch.request_language_change(window, "zh") is False
    assert window.config.language == "ja"
    assert queue_state_store.stored_keys() == ("queue.youtube",)
    assert window.screen.cleared == 0
    assert window.issues


def test_a_guard_refusal_keeps_the_saved_snapshots(test_config, saved_snapshot):
    window = _FakeWindow(test_config, rows=1)
    window.guard_ready = False

    assert language_switch.request_language_change(window, "zh") is False
    assert queue_state_store.stored_keys() == ("queue.youtube",)
    assert window.screen.cleared == 0
    assert window.guard_kinds == ["language-switch"]


def test_declining_the_flush_keeps_the_saved_snapshots(test_config, saved_snapshot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window = _FakeWindow(test_config, rows=2)

    assert language_switch.request_language_change(window, "zh") is False
    assert window.config.language == "ja"
    assert queue_state_store.stored_keys() == ("queue.youtube",)
    assert window.screen.cleared == 0


def test_a_confirmed_switch_commits_then_flushes(test_config, saved_snapshot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window = _FakeWindow(test_config, rows=2)

    assert language_switch.request_language_change(window, "zh") is True
    assert window.config.language == "zh"
    assert window.screen.cleared == 1
    assert queue_state_store.stored_keys() == ()
    assert window.prewarms == 1
    assert window.syncs == 1


def test_an_empty_queue_never_asks(test_config, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("asked about an empty queue"))
    )
    window = _FakeWindow(test_config, rows=0)

    assert language_switch.request_language_change(window, "zh") is True
    assert window.config.language == "zh"


def test_switching_to_the_live_language_is_a_no_op(test_config):
    window = _FakeWindow(replace(test_config, language="zh"), rows=1)

    assert language_switch.request_language_change(window, "zh") is False
    assert window.guard_kinds == []


def test_a_failed_commit_is_reported_and_leaves_the_queues(test_config, saved_snapshot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window = _FakeWindow(test_config, rows=1)

    def _boom(_config):
        raise OSError("disk full")

    monkeypatch.setattr(window, "update_config", _boom)

    assert language_switch.request_language_change(window, "zh") is False
    assert queue_state_store.stored_keys() == ("queue.youtube",)
    assert window.issues


def test_a_settings_edit_the_guard_commits_survives_the_switch(test_config, monkeypatch):
    """R3: the config switched from is the one the guard's preflight committed.

    The guard commits pending Settings edits through ``update_config``, so a
    config read BEFORE the guard is already stale: switching from it writes the
    pre-edit value straight back and the user's edit disappears. ``subtitle_offset``
    is not language-scoped, so surviving the switch is the whole assertion.
    """
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window = _FakeWindow(test_config, rows=0)
    window.pending_edit = {"subtitle_offset": 2.5}

    assert language_switch.request_language_change(window, "zh") is True
    assert window.config.language == "zh"
    assert window.config.subtitle_offset == 2.5


def test_a_language_whose_stack_is_missing_is_refused_before_the_guard(test_config, saved_snapshot, monkeypatch):
    """R11b: an unavailable destination is refused with the profile's own reason."""
    reason = "Chinese mining needs jieba."
    monkeypatch.setattr(
        language_switch,
        "get_profile",
        lambda code: replace(get_profile(code), unavailable_reason=lambda: reason),
    )
    window = _FakeWindow(test_config, rows=1)

    assert language_switch.request_language_change(window, "zh") is False
    assert window.config.language == "ja"
    assert window.guard_kinds == []
    assert window.issues == [reason]
    assert queue_state_store.stored_keys() == ("queue.youtube",)
    assert window.screen.cleared == 0


def test_a_missing_optional_package_does_not_refuse_the_switch(test_config, monkeypatch):
    """R11b: the real zh probe gates on REQUIRED packages only.

    ``opencc`` absent leaves the variant lookups empty and mining working, so
    refusing the switch would disable a language over a degraded feature.
    """
    monkeypatch.setattr(language_switch, "get_profile", get_profile)  # the real probe, not the fixture's
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "opencc" else object())
    window = _FakeWindow(test_config, rows=0)

    assert language_switch.request_language_change(window, "zh") is True
    assert window.config.language == "zh"
    assert window.issues == []


def test_a_missing_required_package_still_refuses_the_switch(test_config, monkeypatch):
    monkeypatch.setattr(language_switch, "get_profile", get_profile)
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "jieba" else object())
    window = _FakeWindow(test_config, rows=0)

    assert language_switch.request_language_change(window, "zh") is False
    assert window.config.language == "ja"
    assert window.guard_kinds == []
    assert "jieba" in window.issues[0]


def test_every_durable_queue_screen_answers_clear_queue():
    """``flush_queues`` empties a screen through ``clear_queue`` and nothing else.

    A durable screen without one keeps its rows on screen while its saved
    snapshot is discarded underneath it - and ``getattr`` makes that silent.
    """
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
    from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    for screen in (AudiobookTab, BatchProcessingTab, ReadingSubtitlesTab, YouTubeTab):
        assert screen.QUEUE_STATE_KEY, screen.__name__
        assert callable(getattr(screen, "clear_queue", None)), screen.__name__

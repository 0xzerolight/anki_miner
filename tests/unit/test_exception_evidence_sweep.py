"""Behavioural checks for the T35 exception-evidence sweep.

Each test pins the same contract from a different bucket: a failure the app
swallows on purpose still leaves one record naming the operation, its subject,
the exception type AND the exception message -- and it does so at the level the
user-visible consequence deserves.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.services import deck_filter
from anki_miner.services.asr import _engine
from anki_miner.services.card_backfiller import TaggerFailures, log_tagger_failures
from anki_miner.services.deck_filter import DeckFilterOptions, scan_deck_filter
from anki_miner.services.word_filter import WordFilterService
from anki_miner.utils import file_pairing


def _record(caplog, prefix: str) -> logging.LogRecord:
    """The single record whose message starts with *prefix* (asserts uniqueness)."""
    matches = [record for record in caplog.records if record.getMessage().startswith(prefix)]
    assert len(matches) == 1, [record.getMessage() for record in caplog.records]
    return matches[0]


# ---------------------------------------------------------------------------
# file_pairing: an unreadable folder is a WARNING, not an empty folder
# ---------------------------------------------------------------------------


class TestFilePairingScanFailures:
    @pytest.fixture
    def unreadable(self, monkeypatch):
        def boom(_self):
            raise PermissionError("scan denied")

        monkeypatch.setattr(Path, "iterdir", boom)

    def test_resolve_output_paths_warns_and_falls_back(self, tmp_path, unreadable, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.utils.file_pairing"):
            resolved = file_pairing.resolve_output_paths(tmp_path, ["EP01.srt"])

        assert resolved == [tmp_path / "EP01.srt"]
        record = _record(caplog, "Ignored failure during scanning output folder")
        assert record.levelno == logging.WARNING
        assert record.name == "anki_miner.utils.file_pairing"
        assert str(tmp_path) in record.getMessage()
        assert "PermissionError" in record.getMessage()
        assert "scan denied" in record.getMessage()

    def test_find_sibling_subtitle_warns_and_returns_none(self, tmp_path, unreadable, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.utils.file_pairing"):
            assert file_pairing.find_sibling_subtitle(tmp_path / "EP01.mkv") is None

        record = _record(caplog, "Ignored failure during scanning")
        assert record.levelno == logging.WARNING
        assert "PermissionError: scan denied" in record.getMessage()

    def test_find_pairs_warns_and_returns_no_pairs(self, tmp_path, unreadable, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.utils.file_pairing"):
            pairs = file_pairing.FilePairMatcher.find_pairs_by_episode_number(tmp_path, tmp_path)

        assert pairs == []
        record = _record(caplog, "Ignored failure during scanning")
        assert record.levelno == logging.WARNING
        assert "PermissionError: scan denied" in record.getMessage()

    def test_readable_folder_logs_nothing(self, tmp_path, caplog):
        (tmp_path / "EP01.srt").write_text("", encoding="utf-8")
        with caplog.at_level(logging.DEBUG, logger="anki_miner.utils.file_pairing"):
            assert file_pairing.resolve_output_paths(tmp_path, ["EP01.srt"]) == [tmp_path / "EP01.srt"]
        assert caplog.records == []


# ---------------------------------------------------------------------------
# Tagger failures: counted across the scan, reported exactly once
# ---------------------------------------------------------------------------


class _ExplodingTagger:
    """Stands in for a tagger whose engine failed to load."""

    def __init__(self, message: str = "no dictionary at /nope/unidic"):
        self.message = message
        self.calls = 0

    def __call__(self, _text):
        self.calls += 1
        raise RuntimeError(self.message)


class _FakeAnkiService:
    def __init__(self, notes):
        self.notes = notes

    def find_notes(self, _query):
        return sorted(self.notes)

    def notes_info(self, note_ids):
        return [self.notes[nid] for nid in note_ids]

    def get_vocabulary_excluding_deck(self, _deck):
        return set()


def _note(note_id: int, expression: str) -> dict:
    return {
        "noteId": note_id,
        "modelName": "Core",
        "tags": [],
        "fields": {"Expression": {"value": expression, "order": 0}},
    }


class TestTaggerFailureAccounting:
    def test_fifty_failing_notes_produce_exactly_one_record(self, test_config, caplog, monkeypatch):
        # generate_reading is the first tagger call; make it raise for every note.
        tagger = _ExplodingTagger()
        monkeypatch.setattr(
            deck_filter,
            "generate_reading",
            lambda text, tag: (_ for _ in ()).throw(RuntimeError("no dictionary at /nope/unidic")),
        )
        expressions = [f"猫{index}犬" for index in range(50)]
        anki = _FakeAnkiService({index: _note(index, word) for index, word in enumerate(expressions)})
        services = SimpleNamespace(
            word_filter=WordFilterService(test_config),
            frequency_service=None,
            word_list_service=None,
            wordset_service=None,
            known_word_db=None,
            tagger=tagger,
        )

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.deck_filter"):
            plan = scan_deck_filter(
                anki,
                test_config,
                services,
                DeckFilterOptions(source_deck="Premade", target_deck="Premade (Filtered)"),
            )

        assert plan.scanned == 50
        record = _record(caplog, "Tagger failures:")
        message = record.getMessage()
        assert record.levelno == logging.WARNING
        assert "reading=50" in message
        assert "lemma=50" in message
        assert "RuntimeError: no dictionary at /nope/unidic" in message
        # The first five distinct card fronts, and no more: the counts above
        # already say how many notes the tagger actually choked on.
        assert "sample_words=猫0犬,猫1犬,猫2犬,猫3犬,猫4犬" in message
        assert "猫5犬" not in message

    def test_no_failures_emits_no_record(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="anki_miner.services.card_backfiller"):
            log_tagger_failures(logging.getLogger("anki_miner.services.card_backfiller"), TaggerFailures())
        assert caplog.records == []

    def test_first_exception_and_sample_words_are_bounded(self):
        failures = TaggerFailures()
        for index in range(9):
            failures.record("reading", f"語{index}", RuntimeError(f"boom {index}"))
        assert failures.counts["reading"] == 9
        assert failures.first_exc == "RuntimeError: boom 0"
        assert failures.sample_words == ["語0", "語1", "語2", "語3", "語4"]


# ---------------------------------------------------------------------------
# ASR Vulkan probe: a non-zero exit carries the argv and the child's stderr
# ---------------------------------------------------------------------------


class TestVulkanProbeEvidence:
    def test_nonzero_exit_logs_argv_and_stderr_tail(self, monkeypatch, caplog):
        def fake_run(argv, **_kwargs):
            return subprocess.CompletedProcess(
                argv,
                returncode=3,
                stdout="",
                stderr="vulkan: failed to load libvulkan.so.1\nvulkan: no compatible device\n",
            )

        monkeypatch.setattr(_engine.subprocess, "run", fake_run)

        with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr._engine"):
            assert _engine._probe_vulkan_device_count() == 0

        record = _record(caplog, "ASR Vulkan probe failed:")
        message = record.getMessage()
        assert "rc=3" in message
        assert "argv=" in message
        assert "libvulkan.so.1" in message
        assert "no compatible device" in message

    def test_spawn_failure_logs_argv_and_exception_message(self, monkeypatch, caplog):
        def fake_run(_argv, **_kwargs):
            raise OSError("Exec format error")

        monkeypatch.setattr(_engine.subprocess, "run", fake_run)

        with caplog.at_level(logging.DEBUG, logger="anki_miner.services.asr._engine"):
            assert _engine._probe_vulkan_device_count() == 0

        failure = _record(caplog, "ASR Vulkan probe failed:")
        assert "state=OSError" in failure.getMessage()
        assert "argv=" in failure.getMessage()
        detail = _record(caplog, "ASR Vulkan probe: devices=0 exc=")
        assert "OSError: Exec format error" in detail.getMessage()

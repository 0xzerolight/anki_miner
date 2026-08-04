"""Guard the logging classification of dense exception-handler modules."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel

_ROOT = Path(__file__).resolve().parents[2]
_TRIAGED_FILES = (
    Path("anki_miner/gui/widgets/panels/subtitles_settings_panel.py"),
    Path("anki_miner/gui/controllers/import_flow_common.py"),
    Path("anki_miner/gui/widgets/_mining_tab_base.py"),
    Path("anki_miner/gui/app.py"),
    Path("anki_miner/gui/controllers/dictionary_import_flow.py"),
)

_DICT_LOGGER = "anki_miner.gui.controllers.dictionary_import_flow"
_SUBTITLES_LOGGER = "anki_miner.gui.widgets.panels.subtitles_settings_panel"


def _bucket_marker(lines: list[str], lineno: int) -> str | None:
    """Return the bucket named on this line or the one above, if any."""
    nearby = " ".join(lines[max(0, lineno - 2) : lineno]).lower()
    return next((letter for letter in "abc" if f"bucket {letter}" in nearby), None)


def _is_contextlib_suppress(item: ast.withitem) -> bool:
    call = item.context_expr
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "contextlib"
        and call.func.attr == "suppress"
    )


def _unclassified_handler_lines(source: str) -> list[int]:
    """Find broad catches, pass handlers, and suppressions lacking a valid bucket."""
    lines = source.splitlines()
    missing: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler):
            catches_exception = isinstance(node.type, ast.Name) and node.type.id == "Exception"
            passes = any(isinstance(statement, ast.Pass) for statement in node.body)
            if (catches_exception or passes) and _bucket_marker(lines, node.lineno) is None:
                missing.add(node.lineno)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if not _is_contextlib_suppress(item):
                    continue
                call = item.context_expr
                assert isinstance(call, ast.Call)
                marker = _bucket_marker(lines, call.lineno)
                catches_exception = any(isinstance(arg, ast.Name) and arg.id == "Exception" for arg in call.args)
                if marker is None or catches_exception and marker != "c":
                    missing.add(call.lineno)
    return sorted(missing)


@pytest.mark.parametrize(
    "source",
    (
        "def f():\n    try:\n        pass\n    except RuntimeError:\n        pass\n",
        (
            "import contextlib\n\n"
            "def f():\n"
            "    # bucket A: broad suppression cannot hide a degradation.\n"
            "    with contextlib.suppress(Exception):\n"
            "        pass\n"
        ),
    ),
)
def test_structural_guard_rejects_unclassified_silence(source: str) -> None:
    assert _unclassified_handler_lines(source)


@pytest.mark.parametrize("relative_path", _TRIAGED_FILES, ids=str)
def test_broad_exception_handlers_have_bucket_markers(relative_path: Path) -> None:
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    missing = _unclassified_handler_lines(source)
    assert not missing, f"unclassified handlers in {relative_path}: {sorted(missing)}"


def test_corrupt_dictionary_metadata_logs_warning_with_source(qtbot, tmp_path, monkeypatch, caplog) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    config = MagicMock()
    config.dicts_root = tmp_path
    flow = DictionaryImportFlow(
        parent,
        MagicMock(),
        lambda: config,
        MagicMock(),
        MagicMock(),
    )
    db = tmp_path / "jitendex" / "index.sqlite"
    db.parent.mkdir(parents=True)
    db.touch()
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.read_yomitan_title",
        lambda _path: "Jitendex.org [2026-08-04]",
    )

    def corrupt_meta(_path: Path) -> dict:
        raise ValueError("corrupt metadata")

    monkeypatch.setattr("anki_miner.gui.controllers.dictionary_import_flow.read_meta", corrupt_meta)

    with caplog.at_level(logging.WARNING, logger=_DICT_LOGGER):
        assert not flow._catalog_slot_base_matches("jitendex", tmp_path / "source.zip")

    record = next(r for r in caplog.records if r.getMessage().startswith("Dictionary metadata unavailable:"))
    assert "source=jitendex" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.name == _DICT_LOGGER
    assert record.exc_info is None


def test_failed_optional_service_probe_logs_warning(qtbot, monkeypatch, caplog) -> None:
    panel = SubtitlesSettingsPanel(suppress_optional_startup=True)
    qtbot.addWidget(panel)

    def unavailable() -> bool:
        raise RuntimeError("engine probe failed")

    def run_sync(_owner, work, on_done, _on_error) -> None:
        on_done(work())

    monkeypatch.setattr("anki_miner.gui.widgets.panels.subtitles_settings_panel._engine.available", unavailable)
    monkeypatch.setattr("anki_miner.gui.widgets.panels.subtitles_settings_panel.run_off_thread", run_sync)

    with caplog.at_level(logging.WARNING, logger=_SUBTITLES_LOGGER):
        panel._refresh_state_async("small", None, None)

    record = next(r for r in caplog.records if r.getMessage().startswith("ASR probe degraded:"))
    assert "service=asr_engine" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.name == _SUBTITLES_LOGGER
    assert record.exc_info is None

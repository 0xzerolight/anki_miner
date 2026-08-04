"""Structural and runtime coverage for the T12 GUI logging sweep."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from anki_miner.gui.workers.install_worker import InstallWorker

_ROOT = Path(__file__).parents[2]

_SWEPT_GUI_MODULES = (
    "anki_miner/gui/controllers/recovery_controller.py",
    "anki_miner/gui/widgets/audiobook_tab.py",
    "anki_miner/gui/widgets/youtube_tab.py",
    "anki_miner/gui/widgets/panels/filtering_settings_panel.py",
    "anki_miner/gui/workers/deck_builder_worker.py",
    "anki_miner/gui/workers/episode_worker.py",
    "anki_miner/gui/workers/install_worker.py",
    "anki_miner/gui/workers/restyle_cards_worker.py",
    "anki_miner/gui/workers/update_worker.py",
    "anki_miner/gui/workers/validation_worker.py",
    "anki_miner/gui/workers/youtube_probe_worker.py",
    "anki_miner/gui/workers/ytdlp_update_worker.py",
)

_WORKER_RUN_CLASSES = {
    "anki_miner/gui/workers/deck_builder_worker.py": "DeckBuilderWorker",
    "anki_miner/gui/workers/episode_worker.py": "EpisodeWorkerThread",
    "anki_miner/gui/workers/install_worker.py": "InstallWorker",
    "anki_miner/gui/workers/restyle_cards_worker.py": "RestyleCardsWorker",
    "anki_miner/gui/workers/update_worker.py": "UpdateWorkerThread",
    "anki_miner/gui/workers/validation_worker.py": "ValidationWorkerThread",
    # Both public probe workers use this shared run body.
    "anki_miner/gui/workers/youtube_probe_worker.py": "_SingleCallProbeThread",
    "anki_miner/gui/workers/ytdlp_update_worker.py": "YtdlpUpdateWorker",
}


def _parse(relative_path: str) -> ast.Module:
    return ast.parse((_ROOT / relative_path).read_text(encoding="utf-8"))


def test_no_swept_gui_module_has_an_unused_logger() -> None:
    unused: list[str] = []
    for relative_path in _SWEPT_GUI_MODULES:
        tree = _parse(relative_path)
        logger_references = sum(isinstance(node, ast.Name) and node.id == "logger" for node in ast.walk(tree))
        if logger_references < 2:
            unused.append(relative_path)

    assert unused == []


def test_every_swept_worker_logs_a_start_line() -> None:
    missing: list[str] = []
    for relative_path, class_name in _WORKER_RUN_CLASSES.items():
        tree = _parse(relative_path)
        worker_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        run = next(
            node
            for node in worker_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
        )
        has_log_start = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "log_start"
            for node in ast.walk(run)
        )
        if not has_log_start:
            missing.append(relative_path)

    assert missing == []


def test_install_worker_start_record_uses_concrete_module(qtbot, caplog) -> None:
    worker = InstallWorker(lambda _worker: "installed")

    with caplog.at_level(logging.INFO, logger="anki_miner.gui"):
        worker.start()
        assert worker.wait(2_000)

    record = next(record for record in caplog.records if record.getMessage().startswith("InstallWorker started:"))
    assert "task=" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == "anki_miner.gui.workers.install_worker"

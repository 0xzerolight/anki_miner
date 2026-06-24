"""Tests for SubtitlesTab container.

Covers:
- Inner QTabWidget has exactly two tabs: "Generate" (index 0) and "Retime" (index 1).
- update_config fans out to both child tabs.
- iter_close_workers yields workers from both children.
- SubtitlesTab has no worker_thread attribute (or it is None-safe via getattr).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab

# ---------------------------------------------------------------------------
# Patch targets (suppress ASR engine + alass I/O during construction)
# ---------------------------------------------------------------------------

_ENGINE_AVAILABLE = "anki_miner.services.asr._engine.available"
_ALASS_RESOLVER = "anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass"
_SHUTIL_WHICH = "anki_miner.gui.widgets.subtitle_retime_tab.shutil.which"


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        asr_models_root=tmp_path / "asr_models",
        media_temp_folder=tmp_path / "tmp",
    )


def _make_tab(config: AnkiMinerConfig, qtbot) -> SubtitlesTab:
    """Construct a SubtitlesTab with engine/alass patched available."""
    with (
        patch(_ENGINE_AVAILABLE, return_value=True),
        patch(_ALASS_RESOLVER, return_value="/fake/alass"),
        patch("pathlib.Path.exists", return_value=True),
    ):
        tab = SubtitlesTab(config)
    qtbot.addWidget(tab)
    return tab


# ---------------------------------------------------------------------------
# Inner tab structure
# ---------------------------------------------------------------------------


def test_inner_tab_count(qtbot, tmp_path):
    """Inner QTabWidget must have exactly two tabs."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab._inner_tabs.count() == 2


def test_inner_tab_labels(qtbot, tmp_path):
    """First inner tab is 'Generate', second is 'Retime'."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab._inner_tabs.tabText(0) == "Generate"
    assert tab._inner_tabs.tabText(1) == "Retime"


def test_generate_tab_is_first(qtbot, tmp_path):
    """generate_tab is the widget at index 0."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab._inner_tabs.widget(0) is tab.generate_tab


def test_retime_tab_is_second(qtbot, tmp_path):
    """retime_tab is the widget at index 1."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab._inner_tabs.widget(1) is tab.retime_tab


# ---------------------------------------------------------------------------
# update_config propagation
# ---------------------------------------------------------------------------


def test_update_config_propagates_to_generate_tab(qtbot, tmp_path):
    """update_config must call generate_tab.update_config with the new config."""
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)

    new_config = dataclasses.replace(config, asr_model="small")
    tab.generate_tab.update_config = MagicMock()
    tab.retime_tab.update_config = MagicMock()

    tab.update_config(new_config)

    tab.generate_tab.update_config.assert_called_once_with(new_config)


def test_update_config_propagates_to_retime_tab(qtbot, tmp_path):
    """update_config must call retime_tab.update_config with the new config."""
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)

    new_config = dataclasses.replace(config, asr_model="small")
    tab.generate_tab.update_config = MagicMock()
    tab.retime_tab.update_config = MagicMock()

    tab.update_config(new_config)

    tab.retime_tab.update_config.assert_called_once_with(new_config)


def test_update_config_stores_config(qtbot, tmp_path):
    """update_config updates self.config."""
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)

    new_config = dataclasses.replace(config, asr_model="small")
    tab.update_config(new_config)

    assert tab.config is new_config


# ---------------------------------------------------------------------------
# iter_close_workers
# ---------------------------------------------------------------------------


def test_iter_close_workers_empty_when_no_workers(qtbot, tmp_path):
    """iter_close_workers yields nothing when neither child has an active worker."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    workers = list(tab.iter_close_workers())
    assert workers == []


def test_iter_close_workers_yields_generate_worker(qtbot, tmp_path):
    """iter_close_workers yields a worker from generate_tab when it is active."""
    tab = _make_tab(_make_config(tmp_path), qtbot)

    fake_gen_worker = MagicMock()
    tab.generate_tab.iter_close_workers = MagicMock(return_value=iter([fake_gen_worker]))
    tab.retime_tab.iter_close_workers = MagicMock(return_value=iter([]))

    workers = list(tab.iter_close_workers())
    assert fake_gen_worker in workers


def test_iter_close_workers_yields_retime_worker(qtbot, tmp_path):
    """iter_close_workers yields a worker from retime_tab when it is active."""
    tab = _make_tab(_make_config(tmp_path), qtbot)

    fake_retime_worker = MagicMock()
    tab.generate_tab.iter_close_workers = MagicMock(return_value=iter([]))
    tab.retime_tab.iter_close_workers = MagicMock(return_value=iter([fake_retime_worker]))

    workers = list(tab.iter_close_workers())
    assert fake_retime_worker in workers


def test_iter_close_workers_yields_both_when_both_active(qtbot, tmp_path):
    """iter_close_workers yields workers from BOTH children when both are active."""
    tab = _make_tab(_make_config(tmp_path), qtbot)

    fake_gen_worker = MagicMock(name="gen_worker")
    fake_retime_worker = MagicMock(name="retime_worker")
    tab.generate_tab.iter_close_workers = MagicMock(return_value=iter([fake_gen_worker]))
    tab.retime_tab.iter_close_workers = MagicMock(return_value=iter([fake_retime_worker]))

    workers = list(tab.iter_close_workers())
    assert fake_gen_worker in workers
    assert fake_retime_worker in workers
    assert len(workers) == 2


# ---------------------------------------------------------------------------
# No worker_thread attribute (background_tasks safety)
# ---------------------------------------------------------------------------


def test_no_worker_thread_attribute(qtbot, tmp_path):
    """SubtitlesTab must not have a worker_thread attribute.

    background_tasks._collect_close_laggards uses getattr(tab, "worker_thread", None)
    and joins it directly if not None.  Exposing this would mislead it into
    expecting a single worker; the correct path is via iter_close_workers.
    """
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert getattr(tab, "worker_thread", None) is None
    assert not hasattr(tab, "worker_thread")

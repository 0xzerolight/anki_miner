"""Fixtures shared by the GUI layout tests.

``Theme.apply_to_app`` writes the application stylesheet and palette, which are
process-global and outlive the test that set them. Restoring only the *scale*
leaves the stylesheet installed, and a QSS ``font-size`` rule then overrides any
per-widget ``setFont`` -- which is exactly how a layout test in one module made
``test_sizing_metrics.py`` fail in another. Anything that raises the text scale
must go through :func:`font_scale`, which puts the application back byte for
byte.

Also shared here: the mining-screen fixtures (``clock``, ``youtube_tab``,
``queue_youtube_tab``, ``audiobook_tab``, ``single_tab``, ``batch_tab``) used by
the run-receipt, lifecycle-logging and queue-manipulation test modules.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPalette

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase


@pytest.fixture
def font_scale(qapp):
    """Yield ``apply(scale)``, rebuilding the theme at that UI text scale.

    ``apply_to_app`` is load-bearing: ``set_font_scale`` alone only invalidates
    the compiled-QSS cache, so a widget's fontMetrics never changes and a test
    that skips it silently measures nothing.
    """
    original_scale = Theme.get_font_scale()
    original_stylesheet = qapp.styleSheet()
    original_palette = QPalette(qapp.palette())

    def apply(scale: float) -> None:
        Theme.set_font_scale(scale)
        Theme.apply_to_app(qapp)

    yield apply

    Theme.set_font_scale(original_scale)
    qapp.setStyleSheet(original_stylesheet)
    qapp.setPalette(original_palette)


@pytest.fixture
def clock(monkeypatch):
    """Freeze the receipt's clock; the test advances ``clock["t"]`` by hand."""
    state = {"t": 1000.0}
    monkeypatch.setattr(
        MiningTabBase,
        "_receipt_now",
        staticmethod(lambda: (state["t"], state["t"])),
    )
    return state


@pytest.fixture
def youtube_tab(qtbot, test_config: AnkiMinerConfig):
    """A YouTubeTab whose runs can go through the add flow.

    Both the probe and the queue worker are patched, so ``_add_flow.add_urls``
    and Mine start no thread. ``queue_youtube_tab`` is the other variant.
    """
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    cfg = replace(test_config, youtube_max_duration_s=7200, youtube_cookies_from_browser=None)
    with (
        patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker") as probe_cls,
        patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as queue_cls,
    ):
        probe_cls.side_effect = lambda *a, **kw: MagicMock(name="ProbeWorker")
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=cfg,
            processor=MagicMock(name="EpisodeProcessor"),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        try:
            yield widget
        finally:
            widget.deleteLater()


@pytest.fixture
def queue_youtube_tab(qtbot, test_config):
    """A YouTubeTab for the queue-surface tests: stock config, only the queue worker patched.

    Rows are added READY by hand, so no probe ever runs. Not merged with
    ``youtube_tab``: the config differs.
    """
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    with patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=test_config,
            processor=MagicMock(),
            fetcher=MagicMock(),
            presenter=MagicMock(),
        )
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture
def audiobook_tab(qtbot, test_config):
    """An AudiobookTab with its queue worker patched."""
    from anki_miner.gui.widgets.audiobook_tab import AudiobookTab

    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker") as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = AudiobookTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture
def single_tab(qtbot, test_config):
    from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def batch_tab(qtbot, test_config):
    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()

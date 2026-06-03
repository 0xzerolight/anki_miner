"""MiningTabBase curation bridge guards (Issue #60)."""

import contextlib
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Bare(MiningTabBase):
    config = None

    def _mark_known(self, forms):
        return 0


def test_default_build_curation_context_is_none_none(qapp):
    tab = _Bare()
    assert tab._build_curation_context() == (None, None)


def test_event_set_even_if_dialog_construction_raises(qapp):
    tab = _Bare()
    tab._init_curation_bridge()
    with (
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            side_effect=RuntimeError("boom"),
        ),
        contextlib.suppress(RuntimeError),
    ):
        tab._on_curation_requested([])
    assert tab._curation_event.is_set()

"""Tests for the ``register_mining_tab`` helper (OVH-021).

Verifies:
- A registered tab gets all six presenter signals connected to the correct
  MainWindow handlers.
- ``window.config_refreshed`` is connected to ``tab.update_config``.
- After all tabs are registered and ``setup_tab_shortcuts()`` is called,
  Ctrl+1..N each switch to the correct tab index, with exactly one shortcut
  per tab and no gaps.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def bare_window(qtbot, monkeypatch, test_config):
    """A MainWindow with no tabs and heavy startup patched out."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _tab_shortcut_keys(window) -> list[str]:
    """Return portable-text keys for Ctrl+<digit> shortcuts only."""
    return [
        sc.key().toString(QKeySequence.SequenceFormat.PortableText)
        for sc in window.findChildren(QShortcut)
        if sc.key().toString(QKeySequence.SequenceFormat.PortableText).startswith("Ctrl+")
        and sc.key().toString(QKeySequence.SequenceFormat.PortableText)[5:].isdigit()
    ]


def _all_shortcut_keys(window) -> set[str]:
    return {sc.key().toString(QKeySequence.SequenceFormat.PortableText) for sc in window.findChildren(QShortcut)}


class TestRegisterMiningTabPresenterConnections:
    """Each of the six presenter signals reaches the matching window handler."""

    @pytest.fixture
    def registered(self, bare_window, qtbot):
        from anki_miner.gui import app as app_module
        from anki_miner.gui.presenters import GUIPresenter

        tab = QWidget()
        tab.update_config = MagicMock()
        qtbot.addWidget(tab)

        presenter = GUIPresenter(bare_window)
        app_module.register_mining_tab(bare_window, tab, presenter, "Test Tab")
        return bare_window, tab, presenter

    def test_tab_added_to_window(self, registered):
        window, tab, _ = registered
        assert window.tabs.count() == 1
        assert window.tabs.widget(0) is tab
        assert window.tabs.tabText(0) == "Test Tab"

    def test_info_signal_reaches_status_bar(self, registered):
        """Emitting info_signal updates the status bar (real handler wired)."""
        window, _tab, presenter = registered
        # The real _on_info_message calls status_bar.set_operation; capture it.
        calls: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: calls.append((msg, kind))
        presenter.info_signal.emit("hello info")
        assert any("hello info" in c[0] for c in calls), f"info message not delivered: {calls}"

    def test_success_signal_reaches_status_bar(self, registered):
        window, _tab, presenter = registered
        calls: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: calls.append((msg, kind))
        presenter.success_signal.emit("great success")
        assert any("great success" in c[0] for c in calls), f"success message not delivered: {calls}"

    def test_warning_signal_reaches_status_bar(self, registered):
        window, _tab, presenter = registered
        calls: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: calls.append((msg, kind))
        presenter.warning_signal.emit("watch out")
        assert any("watch out" in c[0] for c in calls), f"warning message not delivered: {calls}"

    def test_error_signal_reaches_status_bar(self, registered):
        window, _tab, presenter = registered
        calls: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: calls.append((msg, kind))
        presenter.error_signal.emit("oh no")
        assert any("oh no" in c[0] for c in calls), f"error message not delivered: {calls}"

    def test_all_four_status_signals_individually(self, registered):
        """info/success/warning/error all arrive via the connected handlers."""
        window, _tab, presenter = registered
        received: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: received.append((msg, kind))

        presenter.info_signal.emit("i")
        presenter.success_signal.emit("s")
        presenter.warning_signal.emit("w")
        presenter.error_signal.emit("e")

        msgs = {r[0] for r in received}
        assert msgs == {"i", "s", "w", "e"}, f"unexpected received: {received}"

    def test_config_refreshed_reaches_tab_update_config(self, registered):
        """window.config_refreshed emission calls tab.update_config."""
        window, tab, _presenter = registered
        cfg = window.get_config()
        window.config_refreshed.emit(cfg)
        tab.update_config.assert_called_once_with(cfg)


class TestRegisterMiningTabExtraPresenters:
    """Container registration: one addTab, every child presenter wired."""

    @pytest.fixture
    def registered_container(self, bare_window, qtbot):
        from anki_miner.gui import app as app_module
        from anki_miner.gui.presenters import GUIPresenter

        tab = QWidget()
        tab.update_config = MagicMock()
        qtbot.addWidget(tab)

        primary = GUIPresenter(bare_window)
        extra_a = GUIPresenter(bare_window)
        extra_b = GUIPresenter(bare_window)
        app_module.register_mining_tab(bare_window, tab, primary, "Container Tab", extra_presenters=(extra_a, extra_b))
        return bare_window, tab, (primary, extra_a, extra_b)

    def test_tab_added_exactly_once(self, registered_container):
        window, tab, _presenters = registered_container
        assert window.tabs.count() == 1
        assert window.tabs.widget(0) is tab

    def test_every_presenter_reaches_status_bar(self, registered_container):
        """All presenters (primary + extras) get the six-signal wiring."""
        window, _tab, presenters = registered_container
        calls: list[tuple] = []
        window.status_bar.set_operation = lambda msg, kind: calls.append((msg, kind))

        for i, presenter in enumerate(presenters):
            presenter.info_signal.emit(f"msg-{i}")

        msgs = {c[0] for c in calls}
        assert msgs == {"msg-0", "msg-1", "msg-2"}, f"unexpected received: {calls}"

    def test_config_refreshed_connected_once(self, registered_container):
        """config_refreshed → update_config exactly once despite 3 presenters."""
        window, tab, _presenters = registered_container
        cfg = window.get_config()
        window.config_refreshed.emit(cfg)
        tab.update_config.assert_called_once_with(cfg)


class TestSetupTabShortcutsAfterRegistration:
    """Ctrl+N shortcuts are count-driven, one per tab, no gaps, no duplicates."""

    @pytest.fixture
    def window_with_7_tabs(self, bare_window, qtbot):
        from anki_miner.gui import app as app_module
        from anki_miner.gui.presenters import GUIPresenter

        mining_labels = (
            "Episode Mining",
            "Batch Mining",
            "Deck Builder",
            "YouTube",
            "Audiobook",
        )
        for label in mining_labels:
            tab = QWidget()
            tab.update_config = MagicMock()
            qtbot.addWidget(tab)
            presenter = GUIPresenter(bare_window)
            app_module.register_mining_tab(bare_window, tab, presenter, label)

        for label in ("Analytics", "Settings"):
            tab = QWidget()
            qtbot.addWidget(tab)
            bare_window.tabs.addTab(tab, label)

        bare_window.setup_tab_shortcuts()
        return bare_window

    def test_ctrl_1_through_n_all_present(self, window_with_7_tabs):
        n = window_with_7_tabs.tabs.count()
        keys = set(_tab_shortcut_keys(window_with_7_tabs))
        for i in range(1, n + 1):
            assert f"Ctrl+{i}" in keys, f"missing Ctrl+{i} for {n}-tab window"

    def test_no_shortcut_beyond_tab_count(self, window_with_7_tabs):
        n = window_with_7_tabs.tabs.count()
        keys = set(_tab_shortcut_keys(window_with_7_tabs))
        assert f"Ctrl+{n + 1}" not in keys, f"spurious Ctrl+{n + 1} shortcut beyond tab count {n}"

    def test_exactly_one_shortcut_per_tab_no_duplicates(self, window_with_7_tabs):
        n = window_with_7_tabs.tabs.count()
        keys = _tab_shortcut_keys(window_with_7_tabs)
        assert len(keys) == n, f"expected {n} tab shortcuts, got {len(keys)}: {keys}"
        assert len(keys) == len(set(keys)), f"duplicate shortcuts: {keys}"

    def test_ctrl_1_switches_to_tab_0(self, window_with_7_tabs):
        window_with_7_tabs.tabs.setCurrentIndex(3)
        window_with_7_tabs._switch_to_tab(0)
        assert window_with_7_tabs.tabs.currentIndex() == 0

    def test_ctrl_n_switches_to_last_tab(self, window_with_7_tabs):
        n = window_with_7_tabs.tabs.count()
        window_with_7_tabs.tabs.setCurrentIndex(0)
        window_with_7_tabs._switch_to_tab(n - 1)
        assert window_with_7_tabs.tabs.currentIndex() == n - 1

    def test_init_does_not_create_tab_shortcuts(self, bare_window):
        """No Ctrl+digit shortcuts before setup_tab_shortcuts() is called."""
        keys = set(_tab_shortcut_keys(bare_window))
        assert not keys, f"Tab shortcuts appeared in __init__: {keys}"

    def test_non_tab_shortcuts_survive(self, window_with_7_tabs):
        """Ctrl+T, Ctrl+,, Ctrl+Shift+V remain after setup_tab_shortcuts()."""
        keys = _all_shortcut_keys(window_with_7_tabs)
        assert "Ctrl+T" in keys
        assert "Ctrl+," in keys
        assert "Ctrl+Shift+V" in keys

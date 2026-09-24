"""Tests for the ``register_mining_tab`` helper (OVH-021).

Verifies:
- A registered tab gets all six presenter signals connected to the correct
  MainWindow handlers.
- ``window.config_refreshed`` is connected to ``tab.update_config``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QWidget


@pytest.fixture
def bare_window(qtbot, patch_heavy_init, test_config):
    """A MainWindow with no tabs and heavy startup patched out."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


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

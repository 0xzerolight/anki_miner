"""Tests for :meth:`MainWindow._on_update_check_result` banner handling.

Pins the update-banner branches and the singleton-reuse invariant (read the
comment at ``main_window.py:_on_update_check_result``): the banner is created
*once* and reused via ``update_info()`` on every subsequent check — never
reconstructed (tearing it down would race in-flight Qt callbacks).

Config-propagation on skip is already covered in
``test_main_window_config_propagation.py``; here we only assert the banner-hide
side effect of ``_on_skip_update_requested``.

Builds a real ``MainWindow`` with heavy startup side effects patched out, like
``test_main_window_config_propagation``.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.update_checker import UpdateInfo

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


def _info(version: str = "9.9.9") -> UpdateInfo:
    return UpdateInfo(
        version=version,
        release_page_url="https://example.com/releases/tag/v9.9.9",
        asset_url="https://example.com/anki-miner.deb",
        release_notes="notes",
    )


# ---------------------------------------------------------------------------
# Non-update payloads short-circuit
# ---------------------------------------------------------------------------


def test_none_result_creates_no_banner(main_window):
    """A None payload (no update / failed check) builds no banner."""
    with patch("anki_miner.gui.widgets.update_banner.UpdateBanner") as banner_cls:
        main_window._on_update_check_result(None)

    banner_cls.assert_not_called()
    assert main_window._update_banner is None


def test_non_update_info_object_creates_no_banner(main_window):
    """Any non-UpdateInfo object is ignored (isinstance guard)."""
    with patch("anki_miner.gui.widgets.update_banner.UpdateBanner") as banner_cls:
        main_window._on_update_check_result(object())

    banner_cls.assert_not_called()
    assert main_window._update_banner is None


def test_skipped_version_creates_no_banner(main_window):
    """An update the user chose to skip must not surface a banner."""
    main_window.config = replace(main_window.config, skipped_update_version="9.9.9")

    with patch("anki_miner.gui.widgets.update_banner.UpdateBanner") as banner_cls:
        main_window._on_update_check_result(_info("9.9.9"))

    banner_cls.assert_not_called()
    assert main_window._update_banner is None


def test_different_version_still_shows_when_other_skipped(main_window):
    """Skipping 1.0.0 must not suppress a banner for a different version."""
    main_window.config = replace(main_window.config, skipped_update_version="1.0.0")

    with (
        patch("anki_miner.gui.widgets.update_banner.UpdateBanner") as banner_cls,
        patch.object(main_window.central_layout, "insertWidget"),
    ):
        banner_cls.return_value = MagicMock(name="banner")
        main_window._on_update_check_result(_info("9.9.9"))

    banner_cls.assert_called_once()


# ---------------------------------------------------------------------------
# First result builds + wires + inserts the banner
# ---------------------------------------------------------------------------


def test_first_result_constructs_inserts_and_wires_skip(main_window):
    info = _info("9.9.9")
    fake_banner = MagicMock(name="banner")

    with (
        patch("anki_miner.gui.widgets.update_banner.UpdateBanner", return_value=fake_banner) as banner_cls,
        patch.object(main_window.central_layout, "insertWidget") as insert,
    ):
        main_window._on_update_check_result(info)

    banner_cls.assert_called_once_with(info, main_window)
    # Inserted right after the header (index 1).
    insert.assert_called_once_with(1, fake_banner)
    # Skip signal wired to the handler.
    fake_banner.skip_requested.connect.assert_called_once_with(main_window._on_skip_update_requested)
    assert main_window._update_banner is fake_banner


# ---------------------------------------------------------------------------
# Singleton-reuse invariant: second result reuses, never reconstructs
# ---------------------------------------------------------------------------


def test_second_result_reuses_singleton_not_reconstructed(main_window):
    """The banner is built once; a later check updates it in place."""
    first_info = _info("9.9.0")
    second_info = _info("9.9.9")
    fake_banner = MagicMock(name="banner")

    with (
        patch("anki_miner.gui.widgets.update_banner.UpdateBanner", return_value=fake_banner) as banner_cls,
        patch.object(main_window.central_layout, "insertWidget"),
    ):
        main_window._on_update_check_result(first_info)
        main_window._on_update_check_result(second_info)

    # Constructed exactly once across two results.
    banner_cls.assert_called_once()
    # Second result refreshed the existing instance and re-showed it ...
    fake_banner.update_info.assert_called_once_with(second_info)
    fake_banner.setVisible.assert_called_once_with(True)
    # ... and never re-inserted / re-wired.
    assert fake_banner.skip_requested.connect.call_count == 1
    assert main_window._update_banner is fake_banner


# ---------------------------------------------------------------------------
# Skip hides (never destroys) the banner
# ---------------------------------------------------------------------------


def test_skip_hides_banner_without_destroying_it(main_window):
    """_on_skip_update_requested hides the singleton; it is not torn down."""
    fake_banner = MagicMock(name="banner")
    main_window._update_banner = fake_banner

    main_window._on_skip_update_requested("9.9.9")

    fake_banner.setVisible.assert_called_once_with(False)
    # The singleton reference is retained for reuse on the next check.
    assert main_window._update_banner is fake_banner
    fake_banner.deleteLater.assert_not_called()


def test_skip_with_no_banner_is_noop(main_window):
    """Skip is safe when no banner was ever shown (defensive None guard)."""
    assert main_window._update_banner is None
    # Must not raise.
    main_window._on_skip_update_requested("9.9.9")
    assert main_window._update_banner is None

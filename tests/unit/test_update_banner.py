"""Tests for the UpdateBanner widget."""

import pytest

from anki_miner.gui.widgets.update_banner import UpdateBanner
from anki_miner.services.update_checker import UpdateInfo


def _info(version: str = "2.4.0", asset_url: str | None = None) -> UpdateInfo:
    return UpdateInfo(
        version=version,
        release_page_url="https://github.com/0xzerolight/anki_miner/releases/latest",
        asset_url=asset_url,
        release_notes="",
    )


def _banner(qtbot, asset_url: str | None) -> UpdateBanner:
    banner = UpdateBanner(_info(asset_url=asset_url))
    qtbot.addWidget(banner)
    return banner


# ---------------------------------------------------------------------------
# Label-by-target
# ---------------------------------------------------------------------------


class TestDownloadLabel:
    """Primary button label varies by the asset URL file extension."""

    def test_deb_label(self, qtbot):
        banner = UpdateBanner(_info(asset_url="https://example.com/anki-miner_2.4.0_amd64.deb"))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "Download .deb"

    def test_appimage_label(self, qtbot):
        banner = UpdateBanner(_info(asset_url="https://example.com/AnkiMiner-2.4.0-x86_64.AppImage"))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "Download AppImage"

    def test_installer_label_case_insensitive(self, qtbot):
        banner = UpdateBanner(_info(asset_url="https://example.com/AnkiMiner-2.4.0-Windows-x86_64-Setup.exe"))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "Download installer"

    def test_dmg_label(self, qtbot):
        banner = UpdateBanner(_info(asset_url="https://example.com/AnkiMiner-3.2.0-macOS-arm64.dmg"))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "Download disk image"

    def test_view_release_when_no_asset(self, qtbot):
        banner = UpdateBanner(_info(asset_url=None))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "View release"

    def test_unknown_extension_falls_back_to_view_release(self, qtbot):
        banner = UpdateBanner(_info(asset_url="https://example.com/something.weirdext"))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "View release"


# ---------------------------------------------------------------------------
# Skip button signal
# ---------------------------------------------------------------------------


class TestSkipButton:
    """Skip button emits ``skip_requested`` with the version."""

    def test_skip_emits_version(self, qtbot):
        banner = UpdateBanner(_info(version="2.5.1"))
        qtbot.addWidget(banner)
        captured: list[str] = []
        banner.skip_requested.connect(captured.append)

        banner._skip_btn.click()

        assert captured == ["2.5.1"]

    def test_skip_hides_banner(self, qtbot):
        banner = UpdateBanner(_info())
        qtbot.addWidget(banner)
        banner.setVisible(True)

        banner._skip_btn.click()

        assert banner.isVisible() is False


# ---------------------------------------------------------------------------
# update_info() — singleton reuse path
# ---------------------------------------------------------------------------


class TestUpdateInfoMutation:
    """update_info() mutates the existing banner without reconstruction."""

    def test_label_updated(self, qtbot):
        banner = UpdateBanner(_info(version="2.4.0", asset_url=None))
        qtbot.addWidget(banner)
        original_label_id = id(banner._label)

        new_info = _info(version="2.5.0", asset_url="https://example.com/foo.deb")
        banner.update_info(new_info)

        assert "v2.5.0" in banner._label.text()
        # Same QLabel instance (no reconstruction).
        assert id(banner._label) == original_label_id

    def test_button_label_updated(self, qtbot):
        banner = UpdateBanner(_info(asset_url=None))
        qtbot.addWidget(banner)
        assert banner._download_btn.text() == "View release"

        banner.update_info(_info(asset_url="https://example.com/foo.AppImage"))
        assert banner._download_btn.text() == "Download AppImage"

    def test_internal_info_updated(self, qtbot):
        """Subsequent download click should use the latest URL."""
        banner = UpdateBanner(_info(asset_url="https://example.com/old.deb"))
        qtbot.addWidget(banner)
        new_info = _info(asset_url="https://example.com/new.AppImage")
        banner.update_info(new_info)

        # The banner now points at the new URL.
        assert banner._info.asset_url == "https://example.com/new.AppImage"


# ---------------------------------------------------------------------------
# Dismiss
# ---------------------------------------------------------------------------


class TestDismissButton:
    """X button hides without destroying the widget (singleton-safe)."""

    def test_dismiss_hides_without_deleting(self, qtbot):
        banner = _banner(qtbot, None)
        banner.setVisible(True)

        banner._dismiss_btn.click()

        assert banner.isVisible() is False
        # Banner instance is still usable (not deleted) — accessing attributes
        # would raise RuntimeError if the C++ object had been freed.
        assert banner._info is not None


class TestCalmStyle:
    """E05: rendered like the screen issue banner, the Download action the only accent."""

    def test_the_action_is_the_only_primary(self, qtbot):
        banner = _banner(qtbot, "https://example.com/x.AppImage")

        assert banner._download_btn.objectName() == "primary"
        assert banner._skip_btn.objectName() == "ghost"
        assert banner._dismiss_btn.objectName() == "ghost"

    def test_dismiss_is_the_same_glyph_as_the_issue_banner(self, qtbot):
        banner = _banner(qtbot, None)

        assert banner._dismiss_btn.text() == "✕"
        assert banner._dismiss_btn.accessibleName() == "Close"

    def test_the_banner_paints_the_surface_colour(self, qtbot, qapp):
        from PyQt6.QtGui import QColor

        from anki_miner.gui.resources.styles.theme import Theme

        previous = qapp.styleSheet()
        qapp.setStyleSheet(Theme.get_stylesheet("dark"))
        try:
            banner = _banner(qtbot, None)
            banner.resize(600, 40)
            banner.show()
            qtbot.waitExposed(banner)
            # Sample the stretch between the label and the first button: a fixed x
            # lands on a button once the row's own width moves.
            gap_x = (banner._label.geometry().right() + banner._download_btn.geometry().left()) // 2
            painted = QColor.fromRgba(banner.grab().toImage().pixel(gap_x, banner.height() // 2))
            assert painted == QColor(Theme.get_colors("dark")["surface"])
        finally:
            qapp.setStyleSheet(previous)


def _in_place_info(version: str = "2.4.0") -> UpdateInfo:
    return UpdateInfo(
        version=version,
        release_page_url="https://github.com/0xzerolight/anki_miner/releases/latest",
        asset_url=f"https://github.com/0xzerolight/anki_miner/releases/download/v{version}/AnkiMiner-{version}-Linux-x86_64.AppImage",
        release_notes="",
        asset_sha256="a" * 64,
        target="appimage",
    )


class TestInPlaceUpdate:
    """Update now → Cancel → Restart now, with the browser download as the fallback."""

    @pytest.fixture(autouse=True)
    def _supported(self, monkeypatch):
        monkeypatch.setattr(
            "anki_miner.gui.widgets.update_banner.in_place_supported",
            lambda info: info.asset_sha256 is not None,
        )

    def _banner(self, qtbot, info=None) -> UpdateBanner:
        banner = UpdateBanner(info or _in_place_info())
        qtbot.addWidget(banner)
        return banner

    def test_offer_says_update_now_and_emits_install(self, qtbot):
        banner = self._banner(qtbot)
        captured: list[object] = []
        banner.install_requested.connect(captured.append)

        assert banner._download_btn.text() == "Update now"
        banner._download_btn.click()

        assert captured == [banner._info]

    def test_offer_without_in_place_support_keeps_the_browser_label(self, qtbot):
        info = _in_place_info()
        info.asset_sha256 = None
        banner = self._banner(qtbot, info)

        assert banner._download_btn.text() == "Download AppImage"

    def test_downloading_offers_cancel_and_hides_skip(self, qtbot):
        banner = self._banner(qtbot)
        fired: list[bool] = []
        banner.cancel_requested.connect(lambda: fired.append(True))

        banner.show_downloading()
        banner._download_btn.click()

        assert banner._download_btn.text() == "Cancel"
        assert banner._skip_btn.isHidden()
        assert "v2.4.0" in banner._label.text()
        assert fired == [True]

    def test_ready_offers_restart(self, qtbot):
        banner = self._banner(qtbot)
        fired: list[bool] = []
        banner.restart_requested.connect(lambda: fired.append(True))

        banner.show_ready()
        banner._download_btn.click()

        assert banner._download_btn.text() == "Restart now"
        assert banner._skip_btn.isHidden()
        assert fired == [True]

    def test_failed_falls_back_to_the_browser_download(self, qtbot, monkeypatch):
        from PyQt6.QtGui import QDesktopServices

        opened: list[str] = []
        monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
        banner = self._banner(qtbot)

        banner.show_failed()
        banner._download_btn.click()

        assert banner._download_btn.text() == "Download AppImage"
        assert not banner._skip_btn.isHidden()
        assert opened == [banner._info.asset_url]

    def test_a_later_check_does_not_reset_a_running_download(self, qtbot):
        banner = self._banner(qtbot)
        banner.show_downloading()

        banner.update_info(_in_place_info(version="2.5.0"))

        assert banner._download_btn.text() == "Cancel"
        assert "v2.4.0" in banner._label.text()

    def test_a_later_check_does_not_drop_a_finished_download(self, qtbot):
        banner = self._banner(qtbot)
        banner.show_ready()

        banner.update_info(_in_place_info(version="2.5.0"))

        assert banner._download_btn.text() == "Restart now"

    def test_a_failed_banner_takes_a_newer_offer(self, qtbot):
        banner = self._banner(qtbot)
        banner.show_failed()

        banner.update_info(_in_place_info(version="2.5.0"))

        assert banner._download_btn.text() == "Update now"
        assert "v2.5.0" in banner._label.text()

    def test_cancelled_download_returns_to_the_offer(self, qtbot):
        banner = self._banner(qtbot)
        banner.show_downloading()

        banner.show_offer()

        assert banner._download_btn.text() == "Update now"
        assert not banner._skip_btn.isHidden()

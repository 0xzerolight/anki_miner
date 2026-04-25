"""Tests for the UpdateBanner widget."""

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.update_banner import UpdateBanner
from anki_miner.services.update_checker import UpdateInfo

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _info(version: str = "2.4.0", asset_url: str | None = None) -> UpdateInfo:
    return UpdateInfo(
        version=version,
        release_page_url="https://github.com/0xzerolight/anki_miner/releases/latest",
        asset_url=asset_url,
        release_notes="",
    )


# ---------------------------------------------------------------------------
# Label-by-target
# ---------------------------------------------------------------------------


class TestDownloadLabel:
    """Primary button label varies by the asset URL file extension."""

    def test_deb_label(self):
        banner = UpdateBanner(_info(asset_url="https://example.com/anki-miner_2.4.0_amd64.deb"))
        assert banner._download_btn.text() == "Download .deb"

    def test_appimage_label(self):
        banner = UpdateBanner(
            _info(asset_url="https://example.com/AnkiMiner-2.4.0-x86_64.AppImage")
        )
        assert banner._download_btn.text() == "Download AppImage"

    def test_installer_label_case_insensitive(self):
        banner = UpdateBanner(
            _info(asset_url="https://example.com/AnkiMiner-2.4.0-Windows-x86_64-Setup.exe")
        )
        assert banner._download_btn.text() == "Download installer"

    def test_tar_gz_label(self):
        banner = UpdateBanner(_info(asset_url="https://example.com/AnkiMiner-macOS-arm64.tar.gz"))
        assert banner._download_btn.text() == "Download archive"

    def test_view_release_when_no_asset(self):
        banner = UpdateBanner(_info(asset_url=None))
        assert banner._download_btn.text() == "View release"

    def test_unknown_extension_falls_back_to_view_release(self):
        banner = UpdateBanner(_info(asset_url="https://example.com/something.weirdext"))
        assert banner._download_btn.text() == "View release"


# ---------------------------------------------------------------------------
# Skip button signal
# ---------------------------------------------------------------------------


class TestSkipButton:
    """Skip button emits ``skip_requested`` with the version."""

    def test_skip_emits_version(self):
        banner = UpdateBanner(_info(version="2.5.1"))
        captured: list[str] = []
        banner.skip_requested.connect(captured.append)

        banner._skip_btn.click()

        assert captured == ["2.5.1"]

    def test_skip_hides_banner(self):
        banner = UpdateBanner(_info())
        banner.setVisible(True)

        banner._skip_btn.click()

        assert banner.isVisible() is False


# ---------------------------------------------------------------------------
# update_info() — singleton reuse path
# ---------------------------------------------------------------------------


class TestUpdateInfoMutation:
    """update_info() mutates the existing banner without reconstruction."""

    def test_label_updated(self):
        banner = UpdateBanner(_info(version="2.4.0", asset_url=None))
        original_label_id = id(banner._label)

        new_info = _info(version="2.5.0", asset_url="https://example.com/foo.deb")
        banner.update_info(new_info)

        assert "v2.5.0" in banner._label.text()
        # Same QLabel instance (no reconstruction).
        assert id(banner._label) == original_label_id

    def test_button_label_updated(self):
        banner = UpdateBanner(_info(asset_url=None))
        assert banner._download_btn.text() == "View release"

        banner.update_info(_info(asset_url="https://example.com/foo.AppImage"))
        assert banner._download_btn.text() == "Download AppImage"

    def test_internal_info_updated(self):
        """Subsequent download click should use the latest URL."""
        banner = UpdateBanner(_info(asset_url="https://example.com/old.deb"))
        new_info = _info(asset_url="https://example.com/new.AppImage")
        banner.update_info(new_info)

        # The banner now points at the new URL.
        assert banner._info.asset_url == "https://example.com/new.AppImage"


# ---------------------------------------------------------------------------
# Dismiss
# ---------------------------------------------------------------------------


class TestDismissButton:
    """X button hides without destroying the widget (singleton-safe)."""

    def test_dismiss_hides_without_deleting(self):
        banner = UpdateBanner(_info())
        banner.setVisible(True)

        # Find the dismiss button by object name.
        from PyQt6.QtWidgets import QPushButton

        dismiss_btn = None
        for child in banner.findChildren(QPushButton):
            if child.objectName() == "dismissBtn":
                dismiss_btn = child
                break
        assert dismiss_btn is not None

        dismiss_btn.click()

        assert banner.isVisible() is False
        # Banner instance is still usable (not deleted) — accessing attributes
        # would raise RuntimeError if the C++ object had been freed.
        assert banner._info is not None

"""Regression tests for the Theme.get_stylesheet / apply_to_app cache.

Before v2.4.3 every preview click re-read the 1183-line common.qss from disk
and re-ran a regex across it. apply_to_app additionally called setStyleSheet("")
before setStyleSheet(qss), forcing Qt to unpolish + re-polish the whole widget
tree twice. These tests pin the caching + single-call behavior so a future
refactor that reintroduces either regression surfaces in CI.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources.styles.theme import Theme

_app = QApplication.instance() or QApplication([])


def _reset() -> None:
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)


class TestStylesheetCache:
    def test_compiled_qss_is_cached_per_mode(self):
        _reset()
        first = Theme.get_stylesheet("dark")
        second = Theme.get_stylesheet("dark")
        # Identity, not just equality — same cached string object is returned.
        assert first is second

    def test_template_is_read_from_disk_only_once(self, monkeypatch):
        _reset()
        # Drop both caches so the next call re-reads common.qss.
        Theme._qss_template = None
        Theme._compiled_qss = {}

        original_open = open
        opens: list[str] = []

        def tracking_open(file, *args, **kwargs):
            opens.append(str(file))
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr("anki_miner.gui.resources.styles.theme.open", tracking_open, raising=False)

        # Two different modes; the template must be read at most once.
        Theme.get_stylesheet("light")
        Theme.get_stylesheet("dark")
        common_opens = [p for p in opens if p.endswith("common.qss")]
        assert len(common_opens) == 1

    def test_initialize_invalidates_compiled_cache(self):
        _reset()
        Theme.get_stylesheet("light")
        assert "light" in Theme._compiled_qss
        # Re-initialize (e.g. user-dir swap) drops the per-mode cache.
        Theme.initialize(active="light", favorites=("light",), user_dir=None, state_listener=None)
        assert Theme._compiled_qss == {}


class TestApplyToAppDropsRedundantClear:
    def test_apply_to_app_calls_setstylesheet_exactly_once(self):
        _reset()

        calls: list[str] = []

        class FakeApp:
            def setStyleSheet(self, qss: str) -> None:  # noqa: N802 — Qt signature
                calls.append(qss)

            def setPalette(self, _palette) -> None:  # noqa: N802
                pass

        Theme.apply_to_app(FakeApp(), "dark")

        # One call, not two — the previous setStyleSheet("") clear has been removed.
        assert len(calls) == 1
        assert calls[0] != ""
        assert "QTableWidget" in calls[0]

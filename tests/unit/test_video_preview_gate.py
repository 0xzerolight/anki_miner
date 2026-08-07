"""The gate deciding whether a GL video surface may be constructed.

Qt-free by design: the gate is consulted from a widget constructor, so it must
not need a QApplication to answer.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.utils import video_preview


@pytest.fixture(autouse=True)
def _clean_state():
    video_preview._reset_for_tests()
    yield
    video_preview._reset_for_tests()


class TestDefaults:
    def test_enabled_with_no_override(self):
        """The preview is on unless the env var says otherwise. There is no
        setting, and adding one would put the decision back on the user."""
        assert video_preview.preview_enabled()


class TestEnvOverride:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv(video_preview.ENV_VAR, value)
        video_preview._reset_for_tests()
        assert not video_preview.preview_enabled()

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
    def test_other_values_leave_it_on(self, monkeypatch, value):
        """Fails OPEN. An exported empty string must not cost someone their video."""
        monkeypatch.setenv(video_preview.ENV_VAR, value)
        video_preview._reset_for_tests()
        assert video_preview.preview_enabled()

    def test_env_resolved_once_per_process(self, monkeypatch):
        """Cached so the construction site never pays a getenv per widget."""
        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        assert not video_preview.preview_enabled()
        monkeypatch.delenv(video_preview.ENV_VAR)
        assert not video_preview.preview_enabled()

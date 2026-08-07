"""The gate deciding whether a GL video surface may be constructed.

Qt-free by design: the gate is consulted from a widget constructor, so it must
not need a QApplication to answer.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.gui.utils import video_preview


@pytest.fixture(autouse=True)
def _clean_state():
    video_preview._reset_for_tests()
    yield
    video_preview._reset_for_tests()


def _config(enabled: bool) -> AnkiMinerConfig:
    return replace(AnkiMinerConfig(), video_preview_enabled=enabled)


class TestDefaults:
    def test_enabled_before_any_seed(self):
        """An unseeded process must behave exactly like today's build."""
        assert video_preview.preview_enabled()
        assert video_preview.suppressed_reason() == ""

    def test_config_default_is_on(self):
        """Flipping the dataclass default would only ever reach fresh installs,
        so the auto-disable marker — not this — is what rescues an affected user."""
        assert AnkiMinerConfig().video_preview_enabled is True


class TestConfigSeeding:
    def test_seed_disables(self):
        video_preview.seed_from_config(_config(False))
        assert not video_preview.preview_enabled()
        assert video_preview.suppressed_reason() == "setting"

    def test_seed_re_enables(self):
        video_preview.seed_from_config(_config(False))
        video_preview.seed_from_config(_config(True))
        assert video_preview.preview_enabled()

    def test_config_without_the_field_stays_enabled(self):
        """A stand-in config object must not silently turn video off."""

        class Bare:
            pass

        video_preview.seed_from_config(Bare())
        assert video_preview.preview_enabled()


class TestEnvOverride:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv(video_preview.ENV_VAR, value)
        video_preview._reset_for_tests()
        assert not video_preview.preview_enabled()
        assert video_preview.suppressed_reason() == "env"

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
    def test_other_values_leave_it_on(self, monkeypatch, value):
        """Fails OPEN. An exported empty string must not cost someone their video."""
        monkeypatch.setenv(video_preview.ENV_VAR, value)
        video_preview._reset_for_tests()
        assert video_preview.preview_enabled()

    def test_env_beats_config(self, monkeypatch):
        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        video_preview.seed_from_config(_config(True))
        assert not video_preview.preview_enabled()
        assert video_preview.suppressed_reason() == "env"

    def test_env_is_never_written_back_to_config(self, monkeypatch):
        """The override is a diagnostic lever, not a state change: it must be
        usable on a machine that dies before Settings is reachable, without
        mutating what that user has saved."""
        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        config = _config(True)
        video_preview.seed_from_config(config)
        assert config.video_preview_enabled is True

    def test_env_resolved_once_per_process(self, monkeypatch):
        """Cached so the construction site never pays a getenv per widget."""
        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        assert not video_preview.preview_enabled()
        monkeypatch.delenv(video_preview.ENV_VAR)
        assert not video_preview.preview_enabled()

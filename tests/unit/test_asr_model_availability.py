"""The one predicate every caller uses for "can transcription start now?".

Extracted from SubtitleCreationTab so the YouTube pre-run gate answers the
question the same way the Generate tab does.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.services.asr import model_availability


@pytest.fixture
def _no_ct2_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_availability.model_manager, "is_downloaded", lambda name, root: False)


def test_a_downloaded_ct2_model_serves_every_device(
    test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_availability.model_manager, "is_downloaded", lambda name, root: True)
    for device in ("auto", "cpu", "cuda", "vulkan"):
        assert model_availability.usable_model_installed(replace(test_config, asr_device=device)) is True


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_pure_ct2_devices_ignore_the_ggml_pair(
    device: str, test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch, _no_ct2_model: None
) -> None:
    """cpu/cuda never route to whisper.cpp, so its files cannot rescue them."""
    monkeypatch.setattr(model_availability._engine, "whisper_cpp_available", lambda: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_ggml_downloaded", lambda m, r: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_vad_downloaded", lambda r: True)

    assert model_availability.usable_model_installed(replace(test_config, asr_device=device)) is False


@pytest.mark.parametrize("device", ["vulkan", "auto"])
def test_the_ggml_pair_counts_on_whisper_cpp_routes(
    device: str, test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch, _no_ct2_model: None
) -> None:
    monkeypatch.setattr(model_availability._engine, "whisper_cpp_available", lambda: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_ggml_downloaded", lambda m, r: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_vad_downloaded", lambda r: True)

    assert model_availability.usable_model_installed(replace(test_config, asr_device=device)) is True


def test_a_missing_vad_file_is_not_usable(
    test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch, _no_ct2_model: None
) -> None:
    """whisper.cpp needs both halves; the acoustic model alone will not run."""
    monkeypatch.setattr(model_availability._engine, "whisper_cpp_available", lambda: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_ggml_downloaded", lambda m, r: True)
    monkeypatch.setattr(model_availability.ggml_model_installer, "is_vad_downloaded", lambda r: False)

    assert model_availability.usable_model_installed(replace(test_config, asr_device="vulkan")) is False


def test_nothing_installed_is_not_usable(
    test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch, _no_ct2_model: None
) -> None:
    monkeypatch.setattr(model_availability._engine, "whisper_cpp_available", lambda: False)

    assert model_availability.usable_model_installed(replace(test_config, asr_device="auto")) is False


def test_a_probe_failure_reads_as_not_usable(
    test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch, _no_ct2_model: None
) -> None:
    """A surprise from the backend probe must not crash a pre-run gate."""

    def _boom() -> bool:
        raise OSError("backend probe exploded")

    monkeypatch.setattr(model_availability._engine, "whisper_cpp_available", _boom)

    assert model_availability.usable_model_installed(replace(test_config, asr_device="auto")) is False

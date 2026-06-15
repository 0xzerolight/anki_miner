"""Tests for the pure resource-setup helpers (no Qt)."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig, ChainEntry, create_default_config
from anki_miner.gui.utils.resource_setup import (
    apply_download_summary,
    should_offer_first_run_setup,
)
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
)


def _dict_result(dict_id: str = "jitendex", ok: bool = True) -> ResourceDownloadResult:
    return ResourceDownloadResult(
        spec_id="jitendex",
        kind="dict",
        display_name="Jitendex",
        url="https://example.com/jitendex.zip",
        ok=ok,
        detail="100 entries" if ok else "boom",
        dict_id=dict_id if ok else None,
    )


def _freq_result(ok: bool = True) -> ResourceDownloadResult:
    return ResourceDownloadResult(
        spec_id="jpdb-freq",
        kind="freq",
        display_name="JPDB Frequency",
        url="https://example.com/freq.zip",
        ok=ok,
        detail="100 entries" if ok else "boom",
    )


def _pitch_result(ok: bool = True) -> ResourceDownloadResult:
    return ResourceDownloadResult(
        spec_id="kanjium-pitch",
        kind="pitch",
        display_name="Kanjium Pitch",
        url="https://example.com/accents.txt",
        ok=ok,
        detail="downloaded" if ok else "boom",
    )


class TestApplyDownloadSummary:
    def test_empty_summary_returns_config_unchanged(self) -> None:
        config = create_default_config()
        result = apply_download_summary(config, ResourceDownloadSummary())
        assert result is config

    def test_all_failed_summary_returns_config_unchanged(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(
            results=[_dict_result(ok=False), _freq_result(ok=False), _pitch_result(ok=False)]
        )
        result = apply_download_summary(config, summary)
        assert result is config

    def test_dict_success_prepends_chain_entry(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(results=[_dict_result(dict_id="jitendex")])
        result = apply_download_summary(config, summary)

        assert result.dictionary_chain[0] == ChainEntry(kind="indexed", dict_id="jitendex", enabled=True)
        # Existing entries preserved after the new one.
        assert config.dictionary_chain[0] in result.dictionary_chain[1:]

    def test_dict_success_is_idempotent_no_duplicate(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(results=[_dict_result(dict_id="jitendex")])
        once = apply_download_summary(config, summary)
        twice = apply_download_summary(once, summary)

        jitendex_entries = [e for e in twice.dictionary_chain if e.dict_id == "jitendex"]
        assert len(jitendex_entries) == 1
        assert twice.dictionary_chain[0] == ChainEntry(kind="indexed", dict_id="jitendex", enabled=True)

    def test_dict_success_moves_existing_disabled_entry_to_front_enabled(self) -> None:
        config = replace(
            create_default_config(),
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
                ChainEntry(kind="indexed", dict_id="jitendex", enabled=False),
                ChainEntry(kind="jisho", dict_id=None, enabled=False),
            ),
        )
        summary = ResourceDownloadSummary(results=[_dict_result(dict_id="jitendex")])
        result = apply_download_summary(config, summary)

        assert result.dictionary_chain[0] == ChainEntry(kind="indexed", dict_id="jitendex", enabled=True)
        jitendex_entries = [e for e in result.dictionary_chain if e.dict_id == "jitendex"]
        assert len(jitendex_entries) == 1
        # Other entries still present.
        assert any(e.dict_id == "jmdict-english" for e in result.dictionary_chain)
        assert any(e.kind == "jisho" for e in result.dictionary_chain)

    def test_freq_success_sets_use_frequency_data(self) -> None:
        config = create_default_config()
        assert config.use_frequency_data is False
        result = apply_download_summary(config, ResourceDownloadSummary(results=[_freq_result()]))
        assert result.use_frequency_data is True
        # Path untouched.
        assert result.frequency_list_path == config.frequency_list_path

    def test_pitch_success_sets_use_pitch_accent(self) -> None:
        config = create_default_config()
        assert config.use_pitch_accent is False
        result = apply_download_summary(config, ResourceDownloadSummary(results=[_pitch_result()]))
        assert result.use_pitch_accent is True
        assert result.pitch_accent_path == config.pitch_accent_path

    def test_partial_summary_applies_only_succeeded(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(
            results=[
                _dict_result(dict_id="jitendex", ok=True),
                _freq_result(ok=False),
                _pitch_result(ok=True),
            ]
        )
        result = apply_download_summary(config, summary)

        assert result.dictionary_chain[0].dict_id == "jitendex"
        assert result.use_frequency_data is False  # freq failed
        assert result.use_pitch_accent is True

    def test_all_succeeded_applies_everything(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(results=[_dict_result(), _freq_result(), _pitch_result()])
        result = apply_download_summary(config, summary)

        assert result.dictionary_chain[0].dict_id == "jitendex"
        assert result.use_frequency_data is True
        assert result.use_pitch_accent is True


class TestShouldOfferFirstRunSetup:
    def test_true_when_both_files_missing(self, tmp_path) -> None:
        config = replace(
            create_default_config(),
            frequency_list_path=tmp_path / "frequency.csv",
            pitch_accent_path=tmp_path / "pitch.csv",
        )
        assert should_offer_first_run_setup(config) is True

    def test_true_when_only_freq_missing(self, tmp_path) -> None:
        pitch = tmp_path / "pitch.csv"
        pitch.write_text("data")
        config = replace(
            create_default_config(),
            frequency_list_path=tmp_path / "frequency.csv",
            pitch_accent_path=pitch,
        )
        assert should_offer_first_run_setup(config) is True

    def test_true_when_only_pitch_missing(self, tmp_path) -> None:
        freq = tmp_path / "frequency.csv"
        freq.write_text("data")
        config = replace(
            create_default_config(),
            frequency_list_path=freq,
            pitch_accent_path=tmp_path / "pitch.csv",
        )
        assert should_offer_first_run_setup(config) is True

    def test_false_when_both_present(self, tmp_path) -> None:
        freq = tmp_path / "frequency.csv"
        freq.write_text("data")
        pitch = tmp_path / "pitch.csv"
        pitch.write_text("data")
        config = replace(
            create_default_config(),
            frequency_list_path=freq,
            pitch_accent_path=pitch,
        )
        assert should_offer_first_run_setup(config) is False


class TestFirstRunSetupDoneRoundTrip:
    def test_flag_round_trips_through_config_manager(self, tmp_path, monkeypatch) -> None:
        from anki_miner.gui.utils import config_manager as cm

        config_file = tmp_path / "gui_config.json"
        monkeypatch.setattr(cm.GUIConfigManager, "CONFIG_FILE", config_file)

        config = replace(create_default_config(), first_run_setup_done=True)
        cm.GUIConfigManager.save_config(config)
        loaded = cm.GUIConfigManager.load_config()
        assert loaded.first_run_setup_done is True

    def test_absent_flag_defaults_false(self) -> None:
        config = AnkiMinerConfig()
        assert config.first_run_setup_done is False

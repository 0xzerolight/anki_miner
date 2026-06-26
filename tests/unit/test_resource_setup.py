"""Tests for the pure resource-setup helpers (no Qt)."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry, create_default_config
from anki_miner.gui.utils.resource_setup import apply_download_summary
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


def _freq_result(ok: bool = True, source_id: str = "jpdb") -> ResourceDownloadResult:
    return ResourceDownloadResult(
        spec_id="jpdb-freq",
        kind="freq",
        display_name="JPDB Frequency",
        url="https://example.com/freq.zip",
        ok=ok,
        detail="100 entries" if ok else "boom",
        source_id=source_id if ok else None,
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

    def test_freq_success_prepends_chain_entry_and_sets_flag(self) -> None:
        config = create_default_config()
        assert config.use_frequency_data is False
        assert config.frequency_chain == ()
        result = apply_download_summary(config, ResourceDownloadSummary(results=[_freq_result(source_id="jpdb")]))
        assert result.use_frequency_data is True
        # Chain entry prepended — flipping the flag alone would leave an empty
        # chain and zero providers in-session (the regression this fixes).
        assert result.frequency_chain[0] == FreqEntry(source_id="jpdb", enabled=True)

    def test_freq_success_is_idempotent_no_duplicate(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(results=[_freq_result(source_id="jpdb")])
        once = apply_download_summary(config, summary)
        twice = apply_download_summary(once, summary)

        jpdb_entries = [e for e in twice.frequency_chain if e.source_id == "jpdb"]
        assert len(jpdb_entries) == 1
        assert twice.frequency_chain[0] == FreqEntry(source_id="jpdb", enabled=True)

    def test_freq_success_moves_existing_disabled_entry_to_front_enabled(self) -> None:
        config = replace(
            create_default_config(),
            frequency_chain=(
                FreqEntry(source_id="other", enabled=True),
                FreqEntry(source_id="jpdb", enabled=False),
            ),
        )
        result = apply_download_summary(config, ResourceDownloadSummary(results=[_freq_result(source_id="jpdb")]))
        assert result.frequency_chain[0] == FreqEntry(source_id="jpdb", enabled=True)
        jpdb_entries = [e for e in result.frequency_chain if e.source_id == "jpdb"]
        assert len(jpdb_entries) == 1
        assert any(e.source_id == "other" for e in result.frequency_chain)

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
        assert result.frequency_chain == ()  # freq failed → no chain entry
        assert result.use_pitch_accent is True

    def test_all_succeeded_applies_everything(self) -> None:
        config = create_default_config()
        summary = ResourceDownloadSummary(results=[_dict_result(), _freq_result(), _pitch_result()])
        result = apply_download_summary(config, summary)

        assert result.dictionary_chain[0].dict_id == "jitendex"
        assert result.use_frequency_data is True
        assert result.frequency_chain[0] == FreqEntry(source_id="jpdb", enabled=True)
        assert result.use_pitch_accent is True


class TestFreqDownloadYieldsLiveServiceInSession:
    """End-to-end: a freq download summary -> service_factory builds a real,
    non-None MultiFrequencyService that resolves a term (regression guard)."""

    def test_applied_freq_summary_builds_live_frequency_service(self, tmp_path) -> None:
        import json
        import zipfile

        from anki_miner.gui.utils.service_factory import create_services
        from anki_miner.services.frequency.source_importer import import_frequency_source

        # Import a real freq source into freqs_root, exactly as the worker does,
        # then derive its source_id for the summary.
        zip_path = tmp_path / "jpdb.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps({"title": "JPDB", "format": 3, "revision": "r"}))
            zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 5]]))
        freqs_root = tmp_path / "freqs"
        import_result = import_frequency_source(zip_path, freqs_root)

        config = replace(create_default_config(), freqs_root=freqs_root)
        summary = ResourceDownloadSummary(results=[_freq_result(source_id=import_result.source_id)])
        applied = apply_download_summary(config, summary)

        services = create_services(applied)
        assert services.frequency_service is not None
        assert services.frequency_service.lookup_min("猫") == 5


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

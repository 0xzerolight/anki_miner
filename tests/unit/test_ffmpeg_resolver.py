"""Tests for the ffmpeg/ffprobe runtime resolver."""

import dataclasses
import logging

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils import ffmpeg_resolver
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe


@pytest.fixture
def base_config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "temp_media")


class TestResolveFfmpeg:
    def test_config_override_wins_when_file_exists(self, base_config, tmp_path):
        binary = tmp_path / "my-ffmpeg"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        config = dataclasses.replace(base_config, ffmpeg_location=binary)

        assert resolve_ffmpeg(config) == str(binary)

    def test_config_override_ignored_when_file_missing(self, base_config, tmp_path):
        missing = tmp_path / "does-not-exist"
        config = dataclasses.replace(base_config, ffmpeg_location=missing)

        assert resolve_ffmpeg(config) == "ffmpeg"

    @pytest.mark.parametrize(
        ("resolver", "field", "fallback"),
        [
            (resolve_ffmpeg, "ffmpeg_location", "ffmpeg"),
            (resolve_ffprobe, "ffprobe_location", "ffprobe"),
        ],
    )
    def test_0600_ffmpeg_override_rejected_pre_subprocess(self, base_config, tmp_path, resolver, field, fallback):
        binary = tmp_path / fallback
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o600)
        config = dataclasses.replace(base_config, **{field: binary})

        assert resolver(config) == fallback

    def test_bundled_used_when_frozen(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "ffmpeg"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)

        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        assert resolve_ffmpeg(base_config) == str(bundled)

    def test_bundled_non_executable_falls_through(self, base_config, tmp_path, monkeypatch):
        # A present-but-non-executable bundle must fall through to PATH rather
        # than be returned and fail later at subprocess time.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "ffmpeg"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o644)

        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        assert resolve_ffmpeg(base_config) == "ffmpeg"

    def test_bundled_windows_exe_name(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "ffmpeg.exe"
        bundled.write_text("binary")

        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "win32")

        assert resolve_ffmpeg(base_config) == str(bundled)

    def test_frozen_but_missing_bundle_falls_through(self, base_config, tmp_path, monkeypatch):
        # frozen, _MEIPASS set, but no bin/ffmpeg present
        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        assert resolve_ffmpeg(base_config) == "ffmpeg"

    def test_no_override_not_frozen_returns_literal(self, base_config, monkeypatch):
        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", False, raising=False)

        assert resolve_ffmpeg(base_config) == "ffmpeg"

    def test_config_override_beats_bundled(self, base_config, tmp_path, monkeypatch):
        # Both an override and a bundled binary exist; override wins.
        override = tmp_path / "override-ffmpeg"
        override.write_text("#!/bin/sh\n")
        override.chmod(0o755)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "ffmpeg").write_text("#!/bin/sh\n")

        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        config = dataclasses.replace(base_config, ffmpeg_location=override)
        assert resolve_ffmpeg(config) == str(override)


class TestResolveFfprobe:
    def test_config_override_wins_when_file_exists(self, base_config, tmp_path):
        binary = tmp_path / "my-ffprobe"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        config = dataclasses.replace(base_config, ffprobe_location=binary)

        assert resolve_ffprobe(config) == str(binary)

    def test_config_override_ignored_when_file_missing(self, base_config, tmp_path):
        config = dataclasses.replace(base_config, ffprobe_location=tmp_path / "nope")

        assert resolve_ffprobe(config) == "ffprobe"

    def test_bundled_used_when_frozen(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "ffprobe"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)

        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        assert resolve_ffprobe(base_config) == str(bundled)

    def test_no_override_not_frozen_returns_literal(self, base_config, monkeypatch):
        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", False, raising=False)

        assert resolve_ffprobe(base_config) == "ffprobe"


class TestCaching:
    def test_cache_does_not_mask_changed_override(self, base_config, tmp_path):
        first = tmp_path / "ffmpeg-a"
        first.write_text("#!/bin/sh\n")
        first.chmod(0o755)
        second = tmp_path / "ffmpeg-b"
        second.write_text("#!/bin/sh\n")
        second.chmod(0o755)

        cfg_a = dataclasses.replace(base_config, ffmpeg_location=first)
        cfg_b = dataclasses.replace(base_config, ffmpeg_location=second)

        assert resolve_ffmpeg(cfg_a) == str(first)
        # A different override must NOT return the stale first value.
        assert resolve_ffmpeg(cfg_b) == str(second)

    def test_repeated_call_hits_cache(self, base_config, tmp_path):
        binary = tmp_path / "ffmpeg-cached"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        config = dataclasses.replace(base_config, ffmpeg_location=binary)

        first = resolve_ffmpeg(config)
        # Delete the file: a cache hit returns the stored value without re-checking.
        binary.unlink()
        second = resolve_ffmpeg(config)

        assert first == second == str(binary)

    def test_cache_does_not_mask_frozen_state_change(self, base_config, tmp_path, monkeypatch):
        # Not frozen first.
        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", False, raising=False)
        assert resolve_ffmpeg(base_config) == "ffmpeg"

        # Now become frozen with a bundled binary; cache must not mask it.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "ffmpeg"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)
        monkeypatch.setattr(ffmpeg_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ffmpeg_resolver.sys, "platform", "linux")

        assert resolve_ffmpeg(base_config) == str(bundled)


class TestResolutionLogging:
    """Provenance receipts: which tier won, and which candidate was refused."""

    def test_stable_resolution_logs_one_receipt_across_two_calls(self, base_config, tmp_path, caplog):
        binary = tmp_path / "my-ffmpeg"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        config = dataclasses.replace(base_config, ffmpeg_location=binary)

        with caplog.at_level(logging.INFO, logger="anki_miner.utils.ffmpeg_resolver"):
            resolve_ffmpeg(config)
            ffmpeg_resolver._clear_cache()
            resolve_ffmpeg(config)

        receipts = [r for r in caplog.records if r.getMessage().startswith("ffmpeg resolved:")]
        assert len(receipts) == 1
        assert f"tier=override path={binary}" in receipts[0].getMessage()

    def test_changed_override_logs_a_second_receipt(self, base_config, tmp_path, caplog):
        first_binary = tmp_path / "first-ffmpeg"
        second_binary = tmp_path / "second-ffmpeg"
        for binary in (first_binary, second_binary):
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)

        with caplog.at_level(logging.INFO, logger="anki_miner.utils.ffmpeg_resolver"):
            resolve_ffmpeg(dataclasses.replace(base_config, ffmpeg_location=first_binary))
            resolve_ffmpeg(dataclasses.replace(base_config, ffmpeg_location=second_binary))

        receipts = [r for r in caplog.records if r.getMessage().startswith("ffmpeg resolved:")]
        assert len(receipts) == 2
        assert str(second_binary) in receipts[1].getMessage()

    def test_non_executable_override_is_refused_then_falls_back(self, base_config, tmp_path, caplog):
        binary = tmp_path / "ffmpeg"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o600)
        config = dataclasses.replace(base_config, ffmpeg_location=binary)

        with caplog.at_level(logging.INFO, logger="anki_miner.utils.ffmpeg_resolver"):
            assert resolve_ffmpeg(config) == "ffmpeg"

        refusals = [r for r in caplog.records if "ffmpeg resolution refused:" in r.getMessage()]
        assert len(refusals) == 1
        assert refusals[0].levelno == logging.WARNING
        assert f"reason=override_not_executable override={binary}" in refusals[0].getMessage()
        assert "ffmpeg resolved: tier=literal path=ffmpeg" in caplog.text

    def test_ffprobe_receipt_is_tracked_separately_from_ffmpeg(self, base_config, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="anki_miner.utils.ffmpeg_resolver"):
            resolve_ffmpeg(base_config)
            resolve_ffprobe(base_config)

        assert "ffmpeg resolved: tier=literal path=ffmpeg" in caplog.text
        assert "ffprobe resolved: tier=literal path=ffprobe" in caplog.text

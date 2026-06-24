"""Tests for the alass runtime resolver."""

import pytest

from anki_miner.utils import alass_resolver
from anki_miner.utils.alass_resolver import resolve_alass


class _Cfg:
    """Minimal config stub with an alass_location attribute."""

    def __init__(self, alass_location=None):
        self.alass_location = alass_location


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Ensure the module-level cache never leaks across tests."""
    alass_resolver._clear_cache()
    yield
    alass_resolver._clear_cache()


class TestResolveAlass:
    def test_config_override_wins_when_file_exists(self, tmp_path):
        binary = tmp_path / "my-alass"
        binary.write_text("#!/bin/sh\n")
        config = _Cfg(alass_location=binary)

        assert resolve_alass(config) == str(binary)

    def test_config_override_ignored_when_file_missing(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        config = _Cfg(alass_location=missing)

        assert resolve_alass(config) == "alass"

    def test_bundled_used_when_frozen(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "alass"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)

        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "linux")

        assert resolve_alass(_Cfg()) == str(bundled)

    def test_bundled_non_executable_falls_through(self, tmp_path, monkeypatch):
        # A present-but-non-executable bundle must fall through to PATH rather
        # than be returned and fail later at subprocess time.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "alass"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o644)

        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "linux")

        assert resolve_alass(_Cfg()) == "alass"

    def test_bundled_windows_exe_name(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "alass.exe"
        bundled.write_text("binary")

        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "win32")

        assert resolve_alass(_Cfg()) == str(bundled)

    def test_frozen_but_missing_bundle_falls_through(self, tmp_path, monkeypatch):
        # frozen, _MEIPASS set, but no bin/alass present
        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "linux")

        assert resolve_alass(_Cfg()) == "alass"

    def test_no_override_not_frozen_returns_literal(self, monkeypatch):
        monkeypatch.setattr(alass_resolver.sys, "frozen", False, raising=False)

        assert resolve_alass(_Cfg()) == "alass"

    def test_config_override_beats_bundled(self, tmp_path, monkeypatch):
        # Both an override and a bundled binary exist; override wins.
        override = tmp_path / "override-alass"
        override.write_text("#!/bin/sh\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "alass").write_text("#!/bin/sh\n")

        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "linux")

        config = _Cfg(alass_location=override)
        assert resolve_alass(config) == str(override)

    def test_no_alass_location_attr_uses_path_fallback(self, monkeypatch):
        # Config object without alass_location attribute at all — getattr default kicks in.
        monkeypatch.setattr(alass_resolver.sys, "frozen", False, raising=False)

        class _BareConfig:
            pass

        assert resolve_alass(_BareConfig()) == "alass"


class TestCaching:
    def test_cache_does_not_mask_changed_override(self, tmp_path):
        first = tmp_path / "alass-a"
        first.write_text("#!/bin/sh\n")
        second = tmp_path / "alass-b"
        second.write_text("#!/bin/sh\n")

        cfg_a = _Cfg(alass_location=first)
        cfg_b = _Cfg(alass_location=second)

        assert resolve_alass(cfg_a) == str(first)
        # A different override must NOT return the stale first value.
        assert resolve_alass(cfg_b) == str(second)

    def test_repeated_call_hits_cache(self, tmp_path):
        binary = tmp_path / "alass-cached"
        binary.write_text("#!/bin/sh\n")
        config = _Cfg(alass_location=binary)

        first = resolve_alass(config)
        # Delete the file: a cache hit returns the stored value without re-checking.
        binary.unlink()
        second = resolve_alass(config)

        assert first == second == str(binary)

    def test_cache_does_not_mask_frozen_state_change(self, tmp_path, monkeypatch):
        # Not frozen first.
        monkeypatch.setattr(alass_resolver.sys, "frozen", False, raising=False)
        assert resolve_alass(_Cfg()) == "alass"

        # Now become frozen with a bundled binary; cache must not mask it.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "alass"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)
        monkeypatch.setattr(alass_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(alass_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(alass_resolver.sys, "platform", "linux")

        assert resolve_alass(_Cfg()) == str(bundled)

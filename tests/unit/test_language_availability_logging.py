"""A broken language install must not read like an absent one.

The support case: "it says the pack isn't installed, but I installed it". Both
outcomes reach the user through the same "needs kiwipiepy" / "No tokenizer
registered" sentence, so the log is the only place the two can be told apart.
A clean ``find_spec`` -> ``None`` is an absence and stays at DEBUG; a probe that
RAISES means the module is on disk and unimportable, which is a different
diagnosis and a different level.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

BROKEN = "libmecab.so.2: cannot open shared object file"


def _boom(*_args: object, **_kwargs: object) -> bool:
    raise ImportError(BROKEN)


class TestProbeFailures:
    def test_a_raising_zh_probe_logs_a_warning_naming_the_module(self, monkeypatch, caplog) -> None:
        from anki_miner.languages.zh import availability

        monkeypatch.setattr(availability, "find_spec", _boom)

        with caplog.at_level(logging.WARNING, logger="anki_miner.languages.zh.availability"):
            assert availability._installed("jieba") is False

        record = next(r for r in caplog.records if "Language module probe failed" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert "module=jieba" in record.getMessage()
        assert f"ImportError: {BROKEN}" in record.getMessage()

    def test_a_clean_zh_absence_stays_quiet_at_warning(self, monkeypatch, caplog) -> None:
        from anki_miner.languages.zh import availability

        monkeypatch.setattr(availability, "find_spec", lambda _name: None)

        with caplog.at_level(logging.WARNING, logger="anki_miner.languages.zh.availability"):
            assert availability._installed("jieba") is False

        assert caplog.records == []

    def test_a_raising_ko_probe_logs_a_warning_naming_the_module(self, monkeypatch, caplog) -> None:
        from anki_miner.languages.ko import availability

        monkeypatch.setattr(availability, "find_spec", _boom)

        with caplog.at_level(logging.WARNING, logger="anki_miner.languages.ko.availability"):
            assert availability._installed("kiwipiepy") is False

        record = next(r for r in caplog.records if "Language module probe failed" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert "module=kiwipiepy" in record.getMessage()
        assert f"ImportError: {BROKEN}" in record.getMessage()

    def test_a_raising_model_probe_logs_a_warning(self, monkeypatch, caplog, tmp_path) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", _boom)
        monkeypatch.setattr(
            "anki_miner.services.language_pack_installer.component_path",
            lambda _code, _comp: tmp_path,
        )

        with caplog.at_level(logging.WARNING, logger="anki_miner.languages.ko.tokenizer"):
            assert tokenizer.resolve_model_path() == str(tmp_path)

        record = next(r for r in caplog.records if "Language module probe failed" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert "module=kiwipiepy_model" in record.getMessage()
        assert f"ImportError: {BROKEN}" in record.getMessage()


class TestTaggerProvider:
    def test_the_value_error_carries_the_broken_import_text(self, monkeypatch, caplog) -> None:
        from anki_miner.languages import tagger_provider

        real_import = importlib.import_module

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name.endswith(".zz.tokenizer"):
                raise ImportError(BROKEN)
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(importlib, "import_module", fake_import)

        with (
            caplog.at_level(logging.WARNING, logger="anki_miner.languages.tagger_provider"),
            pytest.raises(ValueError) as excinfo,
        ):
            tagger_provider._build("zz")

        # The flat sentence stays - every caller greps/handles it - but the
        # reason the import failed now travels with it.
        assert "No tokenizer registered" in str(excinfo.value)
        assert f"ImportError: {BROKEN}" in str(excinfo.value)
        record = next(r for r in caplog.records if "Language module probe failed" in r.getMessage())
        assert "module=anki_miner.languages.zz.tokenizer" in record.getMessage()


class TestPackManifest:
    def test_a_broken_manifest_logs_a_warning(self, monkeypatch, caplog) -> None:
        from anki_miner.services import language_pack_installer as installer

        installer.load_pack.cache_clear()
        monkeypatch.setattr(installer, "find_spec", lambda _name: object())
        monkeypatch.setattr(installer.importlib, "import_module", _boom)

        try:
            with caplog.at_level(logging.WARNING, logger="anki_miner.services.language_pack_installer"):
                assert installer.load_pack("zz") is None
        finally:
            installer.load_pack.cache_clear()

        record = next(r for r in caplog.records if "Language module probe failed" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert "module=anki_miner.languages.zz.pack" in record.getMessage()
        assert f"ImportError: {BROKEN}" in record.getMessage()

    def test_a_language_with_no_manifest_stays_quiet_at_warning(self, monkeypatch, caplog) -> None:
        from anki_miner.services import language_pack_installer as installer

        installer.load_pack.cache_clear()
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)

        try:
            with caplog.at_level(logging.WARNING, logger="anki_miner.services.language_pack_installer"):
                assert installer.load_pack("zz") is None
        finally:
            installer.load_pack.cache_clear()

        assert caplog.records == []


class TestSyspathInjection:
    def test_an_injection_failure_logs_a_warning_naming_the_root(self, monkeypatch, caplog) -> None:
        from anki_miner.services import language_pack_installer as installer

        root = Path("/nonexistent/language_packs/zz")

        class _Pack:
            components = (object(),)

        def explode(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("pack root is not a directory")

        monkeypatch.setattr(installer, "AVAILABLE_LANGUAGES", ("zz",))
        monkeypatch.setattr(installer, "load_pack", lambda _code: _Pack())
        monkeypatch.setattr(installer, "pack_supported", lambda _code: True)
        monkeypatch.setattr(installer, "language_pack_root", lambda _code: root)
        monkeypatch.setattr(installer, "_component_complete", explode)

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.language_pack_installer"):
            installer.ensure_language_packs_on_syspath()

        record = next(r for r in caplog.records if "Language pack syspath injection failed" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert f"root={root}" in record.getMessage()
        assert "RuntimeError: pack root is not a directory" in record.getMessage()

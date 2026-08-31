"""The release seed script: fail-open on an outage, fail-closed on bad bytes.

``scripts/fetch_language_pack_seeds.py`` fills a scratch directory with the
language packs the release bundle smokes need. A PyPI outage must not red a
release (the smoke skips that language instead), but a checksum mismatch must,
because it means the pinned bytes are not the bytes that arrived.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from anki_miner.exceptions import DownloadFailed, SetupError

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_language_pack_seeds.py"
_spec = importlib.util.spec_from_file_location("fetch_language_pack_seeds", _SCRIPT)
assert _spec is not None and _spec.loader is not None
seeds = importlib.util.module_from_spec(_spec)
sys.modules["fetch_language_pack_seeds"] = seeds
_spec.loader.exec_module(seeds)


def _recorder(calls: list[tuple[str, Path]], fail: dict[str, Exception] | None = None):
    """Return a stand-in installer that records calls and raises on demand."""

    def _install(code: str, root: Path, **_kwargs: object) -> Path:
        if fail and code in fail:
            raise fail[code]
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{code}_engine").mkdir()
        (root / f"{code}_engine" / "__init__.py").write_text("", encoding="utf-8")
        calls.append((code, root))
        return root

    return _install


def test_each_code_is_seeded_into_its_own_directory(tmp_path, monkeypatch, capsys) -> None:
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(seeds, "install_language_pack", _recorder(calls))

    assert seeds.main([str(tmp_path), "zh", "ko"]) == 0

    assert calls == [("zh", tmp_path / "zh"), ("ko", tmp_path / "ko")]
    assert "::warning::" not in capsys.readouterr().out


def test_a_download_failure_warns_skips_that_language_and_keeps_the_release_green(
    tmp_path, monkeypatch, capsys
) -> None:
    calls: list[tuple[str, Path]] = []
    failure = DownloadFailed("Failed to download https://example.invalid/jieba.tar.gz: boom")
    monkeypatch.setattr(seeds, "install_language_pack", _recorder(calls, {"zh": failure}))

    assert seeds.main([str(tmp_path), "zh", "ko"]) == 0

    out = capsys.readouterr().out
    assert "::warning::zh language pack seed failed" in out
    assert "the zh bundle smoke will be skipped. NOT failing the release." in out
    # The outage is per-language: every other code is still seeded.
    assert calls == [("ko", tmp_path / "ko")]


def test_a_checksum_mismatch_fails_closed(tmp_path, monkeypatch, capsys) -> None:
    calls: list[tuple[str, Path]] = []
    failure = SetupError("kiwipiepy download checksum mismatch: expected aa, got bb")
    monkeypatch.setattr(seeds, "install_language_pack", _recorder(calls, {"ko": failure}))

    assert seeds.main([str(tmp_path), "ko", "zh"]) == 1

    out = capsys.readouterr().out
    assert "::error::ko language pack seed failed" in out
    assert "checksum mismatch" in out
    assert "NOT failing the release" not in out
    # Refuses the whole run rather than seeding on past bytes that did not add up.
    assert calls == []


def test_a_language_without_a_pack_fails_closed(tmp_path, capsys) -> None:
    """Not monkeypatched: a bad code in the workflow is a bug, not an outage."""
    assert seeds.main([str(tmp_path), "ja"]) == 1
    assert "::error::ja language pack seed failed" in capsys.readouterr().out


def test_print_manifest_resolves_every_artifact_without_downloading(tmp_path, monkeypatch, capsys) -> None:
    def _never(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("--print-manifest must not download anything")

    monkeypatch.setattr(seeds, "install_language_pack", _never)

    assert seeds.main([str(tmp_path), "zh", "ko", "--print-manifest"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["platform"]["sys_platform"] == sys.platform
    assert data["platform"]["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"

    packs = {entry["code"]: entry for entry in data["packs"]}
    assert set(packs) == {"zh", "ko"}
    assert packs["zh"]["dest"] == str(tmp_path / "zh")
    assert packs["zh"]["approx_download_mb"] > 0

    zh = {comp["import_name"]: comp for comp in packs["zh"]["components"]}
    assert zh["jieba"]["required"] is True
    assert zh["jieba"]["artifact"]["kind"] == "sdist"
    assert zh["jieba"]["artifact"]["url"].endswith("jieba-0.42.1.tar.gz")
    assert len(zh["jieba"]["artifact"]["sha256"]) == 64
    assert zh["pypinyin"]["artifact"]["kind"] == "wheel"
    # opencc pins a cp312 ABI, so its artifact is null on any other interpreter —
    # and it is optional precisely so that stays a seedable pack.
    assert zh["opencc"]["required"] is False

    ko = {comp["import_name"]: comp for comp in packs["ko"]["components"]}
    assert ko["kiwipiepy_model"]["artifact"]["url"].endswith("kiwipiepy_model-0.23.0.tar.gz")
    # Per-platform wheel: resolved for the four release platforms, null elsewhere.
    assert "artifact" in ko["kiwipiepy"]


def test_print_manifest_reports_a_language_that_has_no_pack(tmp_path, capsys) -> None:
    assert seeds.main([str(tmp_path), "ja", "--print-manifest"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["packs"] == [{"code": "ja", "dest": str(tmp_path / "ja"), "supported": False, "components": []}]


def test_the_cli_requires_a_destination_and_at_least_one_code(capsys) -> None:
    with pytest.raises(SystemExit):
        seeds.main([])

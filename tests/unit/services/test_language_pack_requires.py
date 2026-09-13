"""S18: a language pack that requires a shared engine pack installs it first."""

from __future__ import annotations

import logging
import sys

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages import SHARED_PACK_CODES
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent
from anki_miner.services import language_pack_installer as installer


def _component(import_name: str, *, abi=None) -> PackComponent:
    return PackComponent(
        import_name=import_name,
        required=True,
        sentinels=("__init__.py",),
        universal=ArtifactSpec(
            url=f"https://example.invalid/{import_name}.whl",
            sha256="0" * 64,
            kind="wheel",
            member_prefix=f"{import_name}/",
        ),
        abi=abi,
    )


ENGINE = LanguagePack(code="_spacy", approx_download_mb=30, components=(_component("zzengine"),))
MODEL = LanguagePack(code="zz", approx_download_mb=12, components=(_component("zzmodel"),), requires=("_spacy",))


@pytest.fixture
def installs(monkeypatch, tmp_path):
    packs = {"_spacy": ENGINE, "zz": MODEL}
    monkeypatch.setattr(installer, "load_pack", lambda code: packs.get(code))
    monkeypatch.setattr(installer.paths, "ANKI_MINER_HOME", tmp_path)
    calls: list[tuple[str, object]] = []
    skip: set[str] = set()

    def fake_install(label, components, root, *, display_noun, satisfied, progress=None, cancelled_check=None):
        calls.append((label, root))
        if label in skip:
            return root
        for comp in components:
            if satisfied(comp):
                continue
            package = root / comp.import_name
            package.mkdir(parents=True, exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            if progress is not None:
                progress(1, 1, f"{label.upper()} pack (1/1): downloading")
        return root

    monkeypatch.setattr(installer, "install_components", fake_install)
    return calls, skip, packs


def _write_package(directory) -> None:
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")


def test_shared_pack_codes_name_the_spacy_engine():
    assert SHARED_PACK_CODES == ("_spacy",)
    assert LanguagePack(code="x", approx_download_mb=1).requires == ()


def test_the_prerequisite_installs_first_into_its_own_root(installs):
    calls, _skip, _packs = installs
    root = installer.language_pack_root("zz")

    installer.install_language_pack("zz", root)

    assert calls == [("_spacy", installer.language_pack_root("_spacy")), ("zz", root)]
    assert installer.is_installed("zz")


def test_a_satisfied_prerequisite_is_not_reinstalled(installs):
    calls, _skip, _packs = installs
    _write_package(installer.language_pack_root("_spacy") / "zzengine")

    installer.install_language_pack("zz", installer.language_pack_root("zz"))

    assert [label for label, _root in calls] == ["zz"]


def test_a_seed_root_puts_the_prerequisite_beside_it(installs, tmp_path):
    calls, _skip, _packs = installs
    seed = tmp_path / "seeds" / "zz"

    installer.install_language_pack("zz", seed)

    assert calls == [("_spacy", tmp_path / "seeds" / "_spacy"), ("zz", seed)]


def test_an_unsatisfied_prerequisite_fails_closed(installs):
    calls, skip, _packs = installs
    skip.add("_spacy")

    with pytest.raises(SetupError, match="zz language pack needs the _spacy pack"):
        installer.install_language_pack("zz", installer.language_pack_root("zz"))

    assert [label for label, _root in calls] == ["_spacy"]


def test_prerequisite_progress_is_reported_under_the_requesting_pack(installs):
    lines: list[str] = []

    installer.install_language_pack("zz", installer.language_pack_root("zz"), progress=lambda d, t, m: lines.append(m))

    assert lines == ["ZZ pack (1/1): downloading", "ZZ pack (1/1): downloading"]


def test_installed_needs_both_and_the_size_counts_what_is_missing(installs):
    assert installer.combined_download_mb("zz") == 42
    assert not installer.is_installed("zz")

    _write_package(installer.language_pack_root("zz") / "zzmodel")
    assert not installer.is_installed("zz")

    _write_package(installer.language_pack_root("_spacy") / "zzengine")
    assert installer.is_installed("zz")
    assert installer.combined_download_mb("zz") == 12


def test_an_importable_engine_satisfies_the_requirement_on_any_interpreter(installs):
    """B.7: a pip user off the engine's pinned ABI still sees the model row."""
    _calls, _skip, packs = installs
    other_abi = (3, 0) if sys.version_info[:2] != (3, 0) else (3, 1)
    packs["_spacy"] = LanguagePack(
        code="_spacy", approx_download_mb=30, components=(_component("json", abi=other_abi),)
    )

    assert installer.pack_supported("zz")
    assert installer.combined_download_mb("zz") == 12

    packs["_spacy"] = LanguagePack(
        code="_spacy", approx_download_mb=30, components=(_component("zzengine", abi=other_abi),)
    )
    assert not installer.pack_supported("zz")


def test_boot_injection_walks_the_shared_packs(installs, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    _write_package(installer.language_pack_root("_spacy") / "zzengine")

    installer.ensure_language_packs_on_syspath()

    assert str(installer.language_pack_root("_spacy")) in sys.path
    assert installer.pack_codes()[-len(SHARED_PACK_CODES) :] == SHARED_PACK_CODES


def test_a_boot_without_the_shared_package_logs_no_warning(caplog, monkeypatch, tmp_path):
    monkeypatch.setattr(installer.paths, "ANKI_MINER_HOME", tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    installer.load_pack.cache_clear()
    try:
        with caplog.at_level(logging.DEBUG, logger="anki_miner.services.language_pack_installer"):
            installer.ensure_language_packs_on_syspath()
            assert installer.load_pack("_absent_shared_pack") is None
    finally:
        installer.load_pack.cache_clear()

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

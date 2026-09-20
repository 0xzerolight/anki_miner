"""The Persian data pack: the availability probe over it.

The manifest itself (``languages/fa/pack.py``) lands with registration, because
``test_pack_manifests.py::test_every_pack_is_well_formed`` derives the expected
set of packs from ``AVAILABLE_LANGUAGES`` and a ``pack.py`` that exists before
the registry row does turns it red.
"""

from __future__ import annotations

from anki_miner.languages.fa import availability

COMPONENT_PATH = "anki_miner.services.language_pack_installer.component_path"


def test_the_component_is_a_directory_name_not_a_module():
    # find_spec can never answer for it, which is what stops the installer
    # calling the pack satisfied by something pip put on sys.path.
    from importlib.util import find_spec

    assert find_spec(availability.FA_DATA_COMPONENT) is None


def test_without_the_pack_the_reason_names_the_download(monkeypatch):
    monkeypatch.setattr(COMPONENT_PATH, lambda _code, _comp: None)
    assert availability.data_root() is None
    reason = availability.fa_missing_reason()
    assert reason is not None
    assert "Settings -> Mining Language" in reason


def test_with_the_pack_nothing_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(COMPONENT_PATH, lambda _code, _comp: tmp_path)
    assert availability.data_root() == tmp_path
    assert availability.fa_missing_reason() is None


def test_the_probe_asks_for_the_persian_pack(monkeypatch):
    seen: list[tuple[str, str]] = []

    def record(code: str, component: str):
        seen.append((code, component))
        return None

    monkeypatch.setattr(COMPONENT_PATH, record)
    availability.data_root()
    assert seen == [("fa", "hazm_data")]

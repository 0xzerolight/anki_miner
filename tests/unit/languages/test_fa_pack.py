"""The Persian data pack: its manifest and the availability probe over it.

The manifest (``languages/fa/pack.py``) landed WITH registration, not before it:
``test_pack_manifests.py::test_every_pack_is_well_formed`` derives the expected
set of packs from ``AVAILABLE_LANGUAGES``, so a ``pack.py`` that exists before
the registry row does turns it red.
"""

from __future__ import annotations

from anki_miner.languages.fa import availability
from anki_miner.languages.fa.pack import PACK

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


def test_the_pack_is_the_pinned_hazm_wheel_data_directory():
    assert PACK.code == "fa"
    assert PACK.requires == ()
    assert PACK.approx_download_mb > 0
    (component,) = PACK.components
    assert component.import_name == availability.FA_DATA_COMPONENT
    assert component.required is True
    # Data only: the wheel's hazm/ package is NOT extracted. hazm declares
    # Requires-Python >=3.12,<3.14 and its __init__ imports nltk/flashtext/tqdm,
    # so the code could not be imported on CI's 3.11 leg even if it shipped.
    assert component.universal is not None
    assert component.universal.member_prefix == "hazm/data/"
    assert component.universal.kind == "wheel"
    assert component.universal.url.endswith("/hazm-0.12.1-py3-none-any.whl")
    assert component.universal.sha256 == "91507896f5b77dcfe26c710b457801e1204ab0d86dcb756a7ab5912719108db0"
    # Every table the lexicon loads is a sentinel, so a half-extracted pack is
    # reported missing rather than parsing Persian with three of five tables.
    assert set(component.sentinels) == {
        "words.dat",
        "verbs.dat",
        "iverbs.dat",
        "iwords.dat",
        "stopwords.dat",
    }

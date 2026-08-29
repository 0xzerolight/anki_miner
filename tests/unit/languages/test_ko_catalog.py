"""``KO_CATALOG`` ships empty, and says why.

No Yomitan-shaped Korean resource with a stated licence exists to pin, so the
catalogue holds nothing and ``ko/catalog.py`` documents the two manual imports
instead. That documentation IS the deliverable here, so it is what these tests
pin — an empty tuple with no explanation reads as an unfinished placeholder.
"""

import re
from pathlib import Path

from anki_miner.languages import ko
from anki_miner.languages.ko import catalog as ko_catalog
from anki_miner.languages.ko.catalog import KO_CATALOG
from anki_miner.languages.registry import get_profile
from anki_miner.services.resource_catalog import RESOURCE_KINDS

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "anki_miner" / "languages" / "ko" / "catalog.py"


def _doc() -> str:
    assert ko_catalog.__doc__ is not None
    return ko_catalog.__doc__


def test_the_catalog_is_an_empty_tuple():
    assert KO_CATALOG == ()
    assert isinstance(KO_CATALOG, tuple)
    # Vacuous today, but it is the contract the download worker dispatches on:
    # a spec added later without a listed kind is a silent no-op download.
    assert {spec.kind for spec in KO_CATALOG} <= RESOURCE_KINDS


def test_the_ko_profile_carries_that_empty_catalog():
    assert get_profile("ko").catalog == ()
    assert ko.build_profile().catalog is KO_CATALOG


def test_the_emptiness_is_explained_not_left_as_a_placeholder():
    for path in (SOURCE, SOURCE.parent / "__init__.py"):
        source = path.read_text(encoding="utf-8")
        assert "task 3.11" not in source, f"{path.name}: the placeholder marker outlived the task"
        assert "TODO" not in source, path.name
    assert "empty" in _doc().lower()


def test_the_nikl_frequency_survey_is_documented_as_a_manual_import():
    doc = _doc()
    assert "NIKL" in doc
    assert "KOGL" in doc and "Type 1" in doc
    assert "manual" in doc.lower()


def test_the_documented_converter_path_is_real():
    paths = re.findall(r"scripts/[\w./-]+\.py", _doc())
    assert paths, "the docstring must name the converter that produces the CSV"
    for path in paths:
        assert (ROOT / path).is_file(), path


def test_a_user_supplied_yomitan_dictionary_is_documented():
    doc = _doc()
    assert "Yomitan" in doc
    assert "Settings" in doc, "the manual route is the Settings import flow"


def test_no_download_url_is_pinned():
    # The NIKL endpoint needs a matching Referer and rejects Range requests, so
    # anything URL-shaped here would be an automation promise the app cannot keep.
    assert "http" not in SOURCE.read_text(encoding="utf-8")

"""``KO_CATALOG`` pins the KRDICT dictionary, and documents the manual imports.

The catalogue shipped empty until the licence question was settled by opening
the artifact rather than reading the repo: every KRDICT build carries its own
``index.json`` ``attribution`` naming 국립국어원 and CC BY-SA 2.0 KR, so the
licence is stated by the rights holder even though the GitHub repo hosting the
Yomitan conversion has no LICENSE file. These tests pin the spec AND the
docstring: the attribution is a licence condition, not a comment, and the
frequency survey is still a manual import that has to stay explained.
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


def test_the_catalog_pins_the_krdict_dictionary():
    assert KO_CATALOG
    assert isinstance(KO_CATALOG, tuple)
    # The kind is what the download worker dispatches on: a spec whose kind is
    # not listed is a silent no-op download.
    assert {spec.kind for spec in KO_CATALOG} <= RESOURCE_KINDS
    assert [spec.id for spec in KO_CATALOG if spec.kind == "dict"] == ["krdict-en"]


def test_ids_are_unique_and_urls_are_direct_downloads():
    assert len({spec.id for spec in KO_CATALOG}) == len(KO_CATALOG)
    for spec in KO_CATALOG:
        assert spec.url.startswith("https://"), spec.id
        assert spec.license_note, spec.id


def test_the_pinned_build_is_the_one_without_quoted_examples():
    """NIKL's licence excludes example sentences quoted from publications.

    The No.Examples build carries none, so it is the one variant whose whole
    payload is inside CC BY-SA 2.0 KR. Swapping to the full build would pull in
    material the licence does not cover.
    """
    (dictionary,) = [spec for spec in KO_CATALOG if spec.kind == "dict"]
    assert "No.Examples" in dictionary.url


def test_the_licence_note_credits_the_rights_holder():
    """CC BY-SA attribution runs to 국립국어원, not to the Yomitan converter."""
    (dictionary,) = [spec for spec in KO_CATALOG if spec.kind == "dict"]
    note = dictionary.license_note
    assert "국립국어원" in note
    assert "CC BY-SA 2.0 KR" in note


def test_the_ko_profile_carries_that_catalog():
    assert get_profile("ko").catalog == KO_CATALOG
    assert ko.build_profile().catalog is KO_CATALOG


def test_the_catalog_is_explained_not_left_as_a_placeholder():
    for path in (SOURCE, SOURCE.parent / "__init__.py"):
        source = path.read_text(encoding="utf-8")
        assert "task 3.11" not in source, f"{path.name}: the placeholder marker outlived the task"
        assert "TODO" not in source, path.name


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


def test_a_user_supplied_yomitan_dictionary_is_still_documented():
    doc = _doc()
    assert "Yomitan" in doc
    assert "Settings" in doc, "the manual route is the Settings import flow"

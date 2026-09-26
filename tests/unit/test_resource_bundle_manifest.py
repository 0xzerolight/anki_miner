"""Resource bundle manifest: round trip, and every way a foreign file is refused."""

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.services.resource_bundle.manifest import (
    BundleError,
    BundleItem,
    BundleManifest,
    manifest_to_json,
    member_name,
    parse_manifest,
    read_bundle_manifest,
)

_FREQ = BundleItem(
    kind="frequency",
    item_id="jpdb",
    name="JPDB",
    member="frequency/jpdb/source.csv",
    enabled=False,
    options=(("declared_mode", "occurrence"), ("lemmatised", "1")),
)
_KNOWN = BundleItem(kind="known_words", item_id="known_words", name="Known-words ignore list", member="known_words.txt")


def _raw(**overrides):
    raw = json.loads(manifest_to_json(BundleManifest(language="ja", app_version="9.9.9", items=(_FREQ, _KNOWN))))
    raw.update(overrides)
    return raw


def _zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


def test_member_names_are_fixed_per_kind():
    assert member_name("dictionary", "jitendex", ".zip") == "dictionary/jitendex/source.zip"
    assert member_name("blacklist", "blacklist", ".txt") == "blacklist.txt"


def test_manifest_round_trips():
    assert parse_manifest(_raw()) == BundleManifest(language="ja", app_version="9.9.9", items=(_FREQ, _KNOWN))


def test_a_file_without_the_marker_is_refused():
    raw = _raw()
    del raw["anki_miner_resources"]
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_a_newer_format_is_refused():
    with pytest.raises(BundleError, match="newer"):
        parse_manifest(_raw(anki_miner_resources=2))


def test_an_unknown_language_is_refused():
    with pytest.raises(BundleError):
        parse_manifest(_raw(language="xx"))


def test_an_unknown_kind_is_skipped_for_forward_compatibility():
    raw = _raw()
    raw["items"].append({"kind": "theme", "id": "neon", "name": "Neon", "member": "theme/neon.json"})
    assert parse_manifest(raw).items == (_FREQ, _KNOWN)


def test_a_traversing_slot_id_is_refused():
    raw = _raw()
    raw["items"][0]["id"] = ".."
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_a_member_path_that_does_not_match_the_item_is_refused():
    raw = _raw()
    raw["items"][0]["member"] = "frequency/other/source.csv"
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_a_duplicate_item_is_refused():
    raw = _raw()
    raw["items"].append(dict(raw["items"][0]))
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_reading_a_non_zip_is_refused(tmp_path):
    path = tmp_path / "notes.zip"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(BundleError):
        read_bundle_manifest(path)


def test_reading_a_zip_without_a_manifest_is_refused(tmp_path):
    with pytest.raises(BundleError):
        read_bundle_manifest(_zip(tmp_path / "b.zip", {"known_words.txt": "猫\n"}))


def test_reading_a_bundle_missing_a_named_member_is_refused(tmp_path):
    path = _zip(tmp_path / "b.zip", {"manifest.json": json.dumps(_raw()), "known_words.txt": "猫\n"})
    with pytest.raises(BundleError, match="frequency/jpdb/source.csv"):
        read_bundle_manifest(path)


def test_reading_a_complete_bundle_returns_its_manifest(tmp_path):
    path = _zip(
        tmp_path / "b.zip",
        {"manifest.json": json.dumps(_raw()), "known_words.txt": "猫\n", "frequency/jpdb/source.csv": "猫,1\n"},
    )
    assert read_bundle_manifest(path).items == (_FREQ, _KNOWN)

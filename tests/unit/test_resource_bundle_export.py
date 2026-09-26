"""Export side of resource bundles: what is offered, and what the zip holds."""

import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import FreqEntry
from anki_miner.exceptions import OperationCancelled
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.resource_bundle import collect_export_candidates, read_bundle_manifest, write_resource_bundle
from anki_miner.services.resource_bundle import export as export_module
from tests.fixtures.resource_bundle import FREQ_ID, PITCH_ID, build_resource_setup


@pytest.fixture
def sender(tmp_path, test_config):
    return build_resource_setup(tmp_path / "sender", test_config)


def _collect(config):
    return collect_export_candidates(config, known_words_db=config.known_words_db_path)


def _write(config, target: Path, cancel=lambda: False):
    return write_resource_bundle(
        target,
        _collect(config),
        language="ja",
        app_version="test",
        known_words_db=config.known_words_db_path,
        progress=lambda *_: None,
        cancel_check=cancel,
    )


def test_collects_the_chained_resources_in_chain_order(sender):
    dict_id = sender.dictionary_chain[0].dict_id
    assert [(c.item.kind, c.item.item_id, c.unavailable) for c in _collect(sender)] == [
        ("dictionary", dict_id, ""),
        ("frequency", FREQ_ID, ""),
        ("pitch", PITCH_ID, ""),
        ("known_words", "known_words", ""),
        ("blacklist", "blacklist", ""),
    ]


def test_the_ignore_list_counts_only_rows_the_user_added(sender):
    known = next(c for c in _collect(sender) if c.item.kind == "known_words")
    assert known.word_count == 1


def test_a_slot_without_its_source_file_is_offered_but_unavailable(sender):
    (sender.dicts_root / sender.dictionary_chain[0].dict_id / "source.zip").unlink()
    dictionary = next(c for c in _collect(sender) if c.item.kind == "dictionary")
    assert dictionary.unavailable == "no_source"


def test_a_slot_for_another_language_is_offered_but_unavailable(sender, tmp_path):
    csv = tmp_path / "ko.csv"
    csv.write_text("term,rank\n고양이,1\n", encoding="utf-8")
    import_frequency_source(csv, sender.freqs_root, source_id="ko-freq", language="ko")
    config = replace(sender, frequency_chain=(*sender.frequency_chain, FreqEntry("ko-freq")))
    ko = next(c for c in _collect(config) if c.item.item_id == "ko-freq")
    assert ko.unavailable == "other_language"


def test_a_chained_slot_missing_from_disk_is_left_out(sender):
    config = replace(sender, frequency_chain=(*sender.frequency_chain, FreqEntry("gone")))
    assert "gone" not in {c.item.item_id for c in _collect(config)}


def test_frequency_import_options_travel(sender, monkeypatch):
    monkeypatch.setattr(export_module, "slot_import_options", lambda _slot: ("occurrence", True))
    frequency = next(c for c in _collect(sender) if c.item.kind == "frequency")
    assert frequency.item.options == (("declared_mode", "occurrence"), ("lemmatised", "1"))


def test_the_written_bundle_holds_each_source_and_the_ignore_list(sender, tmp_path):
    target = tmp_path / "bundle.zip"
    result = _write(sender, target)
    manifest = read_bundle_manifest(target)
    assert result.item_count == len(manifest.items) == 5
    with zipfile.ZipFile(target) as zf:
        assert zf.read(f"frequency/{FREQ_ID}/source.csv") == (sender.freqs_root / FREQ_ID / "source.csv").read_bytes()
        assert zf.read("known_words.txt").decode("utf-8") == "猫\n"
        assert zf.read("blacklist.txt") == sender.blacklist_path.read_bytes()


def test_unavailable_candidates_are_not_written(sender, tmp_path):
    (sender.dicts_root / sender.dictionary_chain[0].dict_id / "source.zip").unlink()
    target = tmp_path / "bundle.zip"
    _write(sender, target)
    assert "dictionary" not in {item.kind for item in read_bundle_manifest(target).items}


def test_a_cancelled_export_leaves_no_file_behind(sender, tmp_path):
    target = tmp_path / "out" / "bundle.zip"
    target.parent.mkdir()
    with pytest.raises(OperationCancelled):
        _write(sender, target, cancel=lambda: True)
    assert list(target.parent.iterdir()) == []


def test_a_slot_chained_twice_is_offered_once_and_the_bundle_stays_readable(sender, tmp_path):
    config = replace(sender, frequency_chain=(*sender.frequency_chain, FreqEntry(FREQ_ID)))
    assert [c.item.item_id for c in _collect(config)].count(FREQ_ID) == 1
    target = tmp_path / "bundle.zip"
    _write(config, target)
    assert [i.item_id for i in read_bundle_manifest(target).items].count(FREQ_ID) == 1


def test_the_written_ignore_list_records_its_word_count(sender, tmp_path):
    target = tmp_path / "bundle.zip"
    _write(sender, target)
    known = next(i for i in read_bundle_manifest(target).items if i.kind == "known_words")
    assert dict(known.options)["word_count"] == "1"

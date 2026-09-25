"""Import side of resource bundles: planning, installing, and chaining what landed."""

import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
from anki_miner.services.resource_bundle import (
    BundleInstallResult,
    BundleItem,
    apply_install_to_config,
    install_resource_bundle,
    plan_import,
    read_bundle_manifest,
)
from anki_miner.services.resource_bundle import install as install_module
from tests.fixtures.resource_bundle import FREQ_ID, PITCH_ID, empty_receiver, write_sender_bundle


def _install(bundle: Path, receiver, tmp_path, *, selected=None, progress=lambda *_: None, cancel=lambda: False):
    manifest = read_bundle_manifest(bundle)
    return install_resource_bundle(
        bundle,
        manifest,
        list(manifest.items) if selected is None else selected,
        receiver,
        known_words_db=receiver.known_words_db_path,
        wordlists_root=tmp_path / "home" / "wordlists",
        progress=progress,
        cancel_check=cancel,
    )


def _roots(config):
    return {"dictionary": config.dicts_root, "frequency": config.freqs_root, "pitch": config.pitch_root}


@pytest.fixture
def bundle_and_receiver(tmp_path, test_config):
    sender, bundle = write_sender_bundle(tmp_path, test_config)
    return sender, bundle, empty_receiver(tmp_path / "receiver", test_config)


def test_a_round_trip_installs_every_item(bundle_and_receiver, tmp_path):
    sender, bundle, receiver = bundle_and_receiver
    dict_id = sender.dictionary_chain[0].dict_id
    result = _install(bundle, receiver, tmp_path)

    assert result.failures == () and not result.cancelled
    assert [i.kind for i in result.installed] == ["dictionary", "frequency", "pitch", "known_words", "blacklist"]
    assert result.roots == _roots(receiver)
    dicts = DictionaryRegistry(receiver.dicts_root)
    dicts.load()
    assert dicts.get(dict_id) is not None and dicts.get(dict_id).schema_ok
    freqs = FrequencySourceRegistry(receiver.freqs_root)
    freqs.load()
    assert freqs.get(FREQ_ID).schema_ok and freqs.get(FREQ_ID).source_name == "Test Freq"
    pitches = PitchSourceRegistry(receiver.pitch_root)
    pitches.load()
    assert pitches.get(PITCH_ID).schema_ok
    assert KnownWordDB(receiver.known_words_db_path).get_words_by_source("user") == {"猫"}
    assert KnownWordDB(receiver.known_words_db_path).get_words_by_source("anki") == set()
    assert result.wordlist_paths["blacklist"] == tmp_path / "home" / "wordlists" / "ja" / "blacklist.txt"
    assert result.wordlist_paths["blacklist"].read_bytes() == sender.blacklist_path.read_bytes()


def test_plan_blocks_existing_slots_and_configured_lists(bundle_and_receiver, tmp_path):
    _sender, bundle, receiver = bundle_and_receiver
    csv = tmp_path / "mine.csv"
    csv.write_text("term,rank\n犬,1\n", encoding="utf-8")
    import_frequency_source(csv, receiver.freqs_root, source_id=FREQ_ID)
    receiver = replace(receiver, blacklist_path=tmp_path / "mine.txt")
    blocked = {(c.item.kind, c.blocked) for c in plan_import(read_bundle_manifest(bundle), receiver)}
    assert ("frequency", "installed") in blocked
    assert ("blacklist", "configured") in blocked
    assert ("pitch", "") in blocked


def test_a_failed_item_does_not_stop_the_rest(bundle_and_receiver, tmp_path):
    sender, bundle, receiver = bundle_and_receiver
    dict_id = sender.dictionary_chain[0].dict_id
    with zipfile.ZipFile(bundle) as src:
        entries = {name: src.read(name) for name in src.namelist()}
    entries[f"dictionary/{dict_id}/source.zip"] = b"not a zip"
    with zipfile.ZipFile(bundle, "w") as dst:
        for name, payload in entries.items():
            dst.writestr(name, payload)

    result = _install(bundle, receiver, tmp_path)
    assert [name for name, _ in result.failures] == ["Test Dict"]
    assert "frequency" in {i.kind for i in result.installed}
    assert not (receiver.dicts_root / dict_id).exists()


def test_cancel_keeps_what_landed_and_leaves_no_partial_slot(bundle_and_receiver, tmp_path):
    _sender, bundle, receiver = bundle_and_receiver
    stop = {"now": False}

    def progress(_cur, _total, message):
        if message.startswith("(2/"):
            stop["now"] = True

    result = _install(bundle, receiver, tmp_path, progress=progress, cancel=lambda: stop["now"])
    kinds = [i.kind for i in result.installed]
    assert result.cancelled
    assert kinds[:1] == ["dictionary"] and "pitch" not in kinds
    # Whether the frequency importer polls cancel on a one-row CSV or not, a
    # slot exists exactly when it is reported installed -- never half-built.
    for kind, root, slot_id in (("frequency", receiver.freqs_root, FREQ_ID), ("pitch", receiver.pitch_root, PITCH_ID)):
        assert (root / slot_id).exists() == (kind in kinds)


def test_frequency_rebuild_replays_import_options(bundle_and_receiver, tmp_path, monkeypatch):
    _sender, bundle, receiver = bundle_and_receiver
    seen: dict = {}
    monkeypatch.setattr(install_module, "build_frequency_lemmatizer", lambda language: "LEMMATIZER")
    monkeypatch.setattr(install_module, "import_frequency_source", lambda source, root, **kwargs: seen.update(kwargs))
    freq = next(i for i in read_bundle_manifest(bundle).items if i.kind == "frequency")
    replayed = replace(freq, options=(("declared_mode", "occurrence"), ("lemmatised", "1")))
    _install(bundle, receiver, tmp_path, selected=[replayed])
    assert seen["source_id"] == FREQ_ID and seen["source_name"] == "Test Freq"
    assert seen["declared_mode"] == "occurrence" and seen["lemmatize"] == "LEMMATIZER"


def _item(kind, item_id, enabled=True):
    return BundleItem(kind=kind, item_id=item_id, name=item_id, member=f"{kind}/{item_id}/source.zip", enabled=enabled)


def _result(config, *installed, wordlist_paths=None):
    return BundleInstallResult(
        installed=installed,
        wordlist_paths=wordlist_paths or {},
        known_words_added=0,
        failures=(),
        cancelled=False,
        roots=_roots(config),
    )


def test_apply_puts_new_dictionaries_on_top_and_appends_new_sources(test_config, tmp_path):
    config = replace(
        test_config,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="mine"), ChainEntry(kind="jisho", enabled=False)),
        frequency_chain=(FreqEntry("a"),),
        pitch_chain=(PitchSourceEntry("p"),),
    )
    black = BundleItem(kind="blacklist", item_id="blacklist", name="Blacklist", member="blacklist.txt", enabled=False)
    result = _result(
        config,
        _item("dictionary", "theirs"),
        _item("frequency", "b", enabled=False),
        _item("pitch", "q"),
        black,
        wordlist_paths={"blacklist": tmp_path / "blacklist.txt"},
    )
    new = apply_install_to_config(config, result)
    assert new.dictionary_chain == (
        ChainEntry(kind="indexed", dict_id="theirs"),
        ChainEntry(kind="indexed", dict_id="mine"),
        ChainEntry(kind="jisho", enabled=False),
    )
    assert new.frequency_chain == (FreqEntry("a"), FreqEntry("b", enabled=False))
    assert new.pitch_chain == (PitchSourceEntry("p"), PitchSourceEntry("q"))
    assert new.blacklist_path == tmp_path / "blacklist.txt" and new.use_blacklist is False


def test_apply_replaces_a_dangling_entry_instead_of_duplicating_it(test_config):
    config = replace(test_config, frequency_chain=(FreqEntry("b"), FreqEntry("a")))
    assert apply_install_to_config(config, _result(config, _item("frequency", "b"))).frequency_chain == (
        FreqEntry("a"),
        FreqEntry("b"),
    )


def test_apply_refuses_when_a_folder_moved_mid_import(test_config, tmp_path):
    result = _result(test_config, _item("dictionary", "theirs"))
    moved = replace(test_config, dicts_root=tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="theirs"):
        apply_install_to_config(moved, result)

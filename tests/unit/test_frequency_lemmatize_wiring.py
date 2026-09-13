"""S17: which imports lemmatise, and with what."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from anki_miner.gui.workers import import_worker, resource_download_worker
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker
from anki_miner.languages import tagger_provider
from anki_miner.languages.token import LanguageToken
from anki_miner.services.frequency import mode_probe, source_importer
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer, manual_import_lemmatizer
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import import_frequency_source, repair_frequency_source
from anki_miner.services.resource_catalog import ResourceSpec
from tests.unit.languages.stub_registry import register_stub_profile


class _StubTagger:
    def __call__(self, text: str) -> list[LanguageToken]:
        lemmas = {"geht": "gehen", "häuser": "haus"}
        return [LanguageToken(part, "X", lemma=lemmas.get(part, part)) for part in text.replace("'", " '").split()]


def test_lemmatizer_maps_single_token_terms_and_keeps_the_rest(monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "zz", _StubTagger())
    lemmatize = build_frequency_lemmatizer("zz")
    assert lemmatize(["geht", "häuser", "don't"]) == ["gehen", "haus", "don't"]


def test_manual_imports_lemmatise_only_for_a_declaring_profile(monkeypatch):
    assert manual_import_lemmatizer("ja") is None
    profile = register_stub_profile(monkeypatch, "zh")
    register_stub_profile(monkeypatch, "zh", capabilities=profile.capabilities | {"lemmatised_frequency"})
    assert callable(manual_import_lemmatizer("zh"))


def test_resource_spec_defaults_to_no_lemmatisation():
    assert ResourceSpec(id="a", kind="freq", display_name="A", url="https://x", license_note="n").lemmatise is False


def test_a_lemmatise_spec_declares_occurrence_and_passes_a_lemmatizer(tmp_path: Path, monkeypatch):
    spec = ResourceSpec(
        id="opensubtitles-zz",
        kind="freq",
        display_name="OS",
        url="https://x/zz_50k.txt",
        license_note="n",
        lemmatise=True,
    )

    def fake_download(url, *, dest_dir, **_kwargs):
        part = Path(dest_dir) / "zz_50k.txt.part"
        part.write_text("der 3\n", encoding="utf-8")
        return part

    seen: dict = {}

    def fake_import(path, dest_root, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after the call")

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_import)
    worker = ResourceDownloadWorker(
        [spec],
        dicts_root=tmp_path / "d",
        freqs_root=tmp_path / "f",
        pitch_root=tmp_path / "p",
        download_dir=tmp_path / "dl",
        language="zh",
    )
    (tmp_path / "dl").mkdir()

    worker.run()

    assert seen["declared_mode"] == mode_probe.OCCURRENCE_BASED
    assert callable(seen["lemmatize"])


def test_a_plain_spec_keeps_the_pre_s17_call_shape(tmp_path: Path, monkeypatch):
    spec = ResourceSpec(id="jpdb", kind="freq", display_name="J", url="https://x/jpdb.zip", license_note="n")

    def fake_download(url, *, dest_dir, **_kwargs):
        part = Path(dest_dir) / "jpdb.zip.part"
        part.write_bytes(b"")
        return part

    seen: dict = {}

    def fake_import(path, dest_root, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after the call")

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_import)
    (tmp_path / "dl").mkdir()
    worker = ResourceDownloadWorker(
        [spec],
        dicts_root=tmp_path / "d",
        freqs_root=tmp_path / "f",
        pitch_root=tmp_path / "p",
        download_dir=tmp_path / "dl",
    )

    worker.run()

    assert seen and "declared_mode" not in seen and "lemmatize" not in seen


def test_the_manual_worker_forwards_a_lemmatizer_only_when_given(tmp_path: Path, monkeypatch):
    calls: list[dict] = []

    def fake_import(path, dest_root, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(import_worker, "import_frequency_source", fake_import)

    def lemmatize(words):
        return words

    for kwargs in ({}, {"lemmatize": lemmatize}):
        worker = ImportWorker.for_source(tmp_path / "x.txt", tmp_path, **kwargs)
        with contextlib.suppress(RuntimeError):
            worker._runner(lambda *a: None, lambda: False)

    assert "lemmatize" not in calls[0]
    assert calls[1]["lemmatize"] is lemmatize


def _lemmatised_slot(tmp_path: Path, monkeypatch) -> tuple[Path, source_importer.FreqSourceImportResult]:
    register_stub_profile(monkeypatch, "zh")
    monkeypatch.setitem(tagger_provider._TAGGERS, "zh", _StubTagger())
    source = tmp_path / "zh_50k.txt"
    source.write_text("geht 50\nhaus 40\ngehen 30\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    result = import_frequency_source(
        source,
        dest,
        language="zh",
        declared_mode=mode_probe.OCCURRENCE_BASED,
        lemmatize=build_frequency_lemmatizer("zh"),
    )
    return dest, result


def test_repair_rebuilds_a_lemmatised_list_the_same_way(tmp_path: Path, monkeypatch):
    dest, result = _lemmatised_slot(tmp_path, monkeypatch)
    slot = dest / result.source_id

    repaired = repair_frequency_source(
        slot / "source.txt", dest, source_id=result.source_id, source_name=result.source_name
    )

    provider = IndexedFreqProvider(repaired.source_id, slot / "index.sqlite", "x")
    assert provider.load()
    assert (repaired.entry_count, provider.lookup("gehen"), provider.lookup("haus")) == (2, 1, 2)


def test_import_options_come_from_the_sidecar_even_beside_a_corrupt_index(tmp_path: Path, monkeypatch):
    dest, result = _lemmatised_slot(tmp_path, monkeypatch)
    slot = dest / result.source_id
    db = slot / "index.sqlite"
    db.write_bytes(b"not a database")
    sidecar_ns = (slot / "meta.json").stat().st_mtime_ns
    os.utime(db, ns=(sidecar_ns + 10**9, sidecar_ns + 10**9))  # the sidecar is now stale

    assert source_importer._slot_import_options(slot) == (mode_probe.OCCURRENCE_BASED, True)


def test_import_options_fall_back_to_the_index_without_a_sidecar(tmp_path: Path, monkeypatch):
    dest, result = _lemmatised_slot(tmp_path, monkeypatch)
    slot = dest / result.source_id
    (slot / "meta.json").unlink()

    assert source_importer._slot_import_options(slot) == (mode_probe.OCCURRENCE_BASED, True)


def test_import_options_default_when_nothing_answers(tmp_path: Path):
    slot = tmp_path / "broken"
    slot.mkdir()
    (slot / "index.sqlite").write_bytes(b"not a database")

    assert source_importer._slot_import_options(slot) == ("", False)


def test_a_default_slot_beside_a_corrupt_index_needs_no_index_read(tmp_path: Path, caplog):
    source = tmp_path / "list.csv"
    source.write_text("der,1\n", encoding="utf-8")
    result = import_frequency_source(source, tmp_path / "freqs")
    slot = tmp_path / "freqs" / result.source_id
    (slot / "index.sqlite").write_bytes(b"not a database")

    with caplog.at_level("WARNING", logger=source_importer.logger.name):
        assert source_importer._slot_import_options(slot) == ("", False)

    assert not caplog.records

"""Integration regressions for kanji-variant spelling lookups.

The reported bug: unidic's canonical lemma collapses kanji variants
(殺る → 遣る, 懸ける/賭ける → 掛ける), and definition/frequency lookups were
keyed on the lemma — so 殺る's card carried 遣る's "to do" definition and every
かける spelling shared 掛ける's rank. These tests run the REAL SQLite pipeline:
the dictionary index is built through ``import_yomitan_zip`` (repo rule: never
seed rows directly — the importer is where past parsing bugs lived) and the
frequency index through ``import_frequency_source``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import import_frequency_source
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


def _dict_provider(tmp_path: Path, term_banks: list[list[Any]]) -> IndexedDictProvider:
    zip_path = build_yomitan_zip(tmp_path / "src" / "dict.zip", term_banks=term_banks)
    dest_root = tmp_path / "dicts"
    result = import_yomitan_zip(zip_path, dest_root)
    provider = IndexedDictProvider(result.dict_id, dest_root / result.dict_id / "index.sqlite")
    assert provider.load()
    return provider


def _freq_service(tmp_path: Path, banks: list[list[Any]]) -> MultiFrequencyService:
    zip_path = tmp_path / "src" / "freq.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "JPDB-like", "format": 3, "revision": "r1"}))
        zf.writestr("term_meta_bank_1.json", json.dumps(banks))
    dest_root = tmp_path / "freqs"
    result = import_frequency_source(zip_path, dest_root)
    provider = IndexedFreqProvider(result.source_id, dest_root / result.source_id / "index.sqlite", "JPDB-like")
    assert provider.load()
    return MultiFrequencyService([provider])


_YARU_BANKS = [
    [
        # Same reading やる, two spellings, distinct senses. 遣る gets the
        # higher score so score-ordering alone would (wrongly) lead with it.
        ["遣る", "やる", "v5r", "v5r", 10, ["to do", "to undertake"], 1, ""],
        ["殺る", "やる", "v5r", "v5r", 0, ["to do someone in", "to bump off"], 2, ""],
    ]
]


class TestVariantSpellingDefinitions:
    def test_mined_form_key_returns_variant_sense(self, tmp_path: Path, test_config):
        """殺る keyed by its own spelling leads with "to do someone in", not
        遣る's "to do" (the reported bug)."""
        provider = _dict_provider(tmp_path, _YARU_BANKS)
        service = DefinitionService(test_config, providers=[provider])

        definitions = service.get_definitions_batch([("殺る", "やる")], fallback_context={"殺る": ("遣る", None)})

        assert definitions[0] is not None
        assert "to do someone in" in definitions[0]
        # The homograph may survive below via the reading match, but the
        # exact-spelling sense must lead (ORDER BY term-match first).
        if "to undertake" in definitions[0]:
            assert definitions[0].index("to do someone in") < definitions[0].index("to undertake")

    def test_lemma_fallback_resolves_missing_variant(self, tmp_path: Path, test_config):
        """A variant spelling absent from every dictionary falls back to the
        canonical lemma entry instead of producing no card."""
        # Dictionary knows only the canonical spelling.
        banks = [[["請う", "こう", "v5u-s", "v5u-s", 0, ["to beg", "to request"], 1, ""]]]
        provider = _dict_provider(tmp_path, banks)
        service = DefinitionService(test_config, providers=[provider])

        definitions = service.get_definitions_batch([("乞う", "こう")], fallback_context={"乞う": ("請う", None)})

        assert definitions[0] is not None
        assert "to beg" in definitions[0]


class TestVariantSpellingFrequency:
    def test_each_spelling_gets_its_own_rank(self, tmp_path: Path):
        """掛ける / 懸ける / 賭ける carry distinct per-spelling ranks (JPDB-style
        per-orthography rows) instead of all inheriting 掛ける's."""
        banks = [
            ["掛ける", "freq", {"reading": "かける", "frequency": {"value": 1500}}],
            ["懸ける", "freq", {"reading": "かける", "frequency": {"value": 9000}}],
            ["賭ける", "freq", {"reading": "かける", "frequency": {"value": 12000}}],
        ]
        service = _freq_service(tmp_path, banks)

        ranks = {
            spelling: [rank for _n, rank, _d in service.lookup_all(spelling, "かける")]
            for spelling in ("掛ける", "懸ける", "賭ける")
        }

        assert ranks == {"掛ける": [1500], "懸ける": [9000], "賭ける": [12000]}

    def test_absent_spelling_misses_so_caller_can_fall_back(self, tmp_path: Path):
        """A spelling no source ranks returns an empty breakdown — the
        orchestration layer's whole-result fallback then retries the lemma."""
        banks = [["掛ける", "freq", {"reading": "かける", "frequency": {"value": 1500}}]]
        service = _freq_service(tmp_path, banks)

        assert service.lookup_all("架ける", "かける") == []
        # The caller-side retry (episode_processor._phase2_filter) then hits:
        assert [r for _n, r, _d in service.lookup_all("掛ける", "かける")] == [1500]

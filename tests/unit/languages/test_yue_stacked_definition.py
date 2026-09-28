"""The yue Definition carries every dictionary's hit in chain order (YUE-02).

After the setup wizard the yue chain is wty-yue-en, CC-CEDICT-Canto, CC-Canto.
First hit wins would hand 蚊 CEDICT's Mandarin "mosquito" and hide CC-Canto's
Cantonese "dollar"; reordering would cost other words their main sense (可以,
四, 病). So the profile stacks the Definition the way the Glossary is built,
CEDICT still ahead of CC-Canto.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, DictRow, bulk_insert, create_index, write_meta
from tests.conftest import build_processor


def _seed(root: Path, dict_id: str, rows: list[DictRow]) -> IndexedDictProvider:
    folder = root / dict_id
    folder.mkdir(parents=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, rows)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )
    provider = IndexedDictProvider(dict_id, db, display_name=dict_id, keys=get_profile("yue").dict_keys)
    provider.load()
    return provider


def _row(term: str, gloss: str) -> DictRow:
    return DictRow(term=term, reading=None, content=f'<li class="gloss-item">{gloss}</li>')


def _wizard_chain(root: Path) -> list[IndexedDictProvider]:
    """The chain order the setup wizard leaves: wty-yue-en, CC-CEDICT-Canto, CC-Canto."""
    return [
        _seed(root, "wty-yue-en", [_row("嘅", "possessive particle")]),
        _seed(root, "cc-cedict-canto", [_row("蚊", "mosquito")]),
        _seed(root, "cc-canto", [_row("蚊", "money; one dollar")]),
    ]


def _man() -> TokenizedWord:
    return TokenizedWord(
        surface="蚊",
        lemma="蚊",
        reading="",
        sentence="三百五十蚊，好抵㗎。",
        start_time=1.0,
        end_time=2.0,
        duration=1.0,
        mined_form_override="蚊",
    )


def _phase4(config, definition_service):
    processor = build_processor(config, definition_service=definition_service)
    return processor._phase4_lookup(None, [(_man(), MediaData())], None)


def test_only_yue_stacks_its_definition():
    assert {code for code in AVAILABLE_LANGUAGES if get_profile(code).stacked_definition} == {"yue"}


def test_the_cantonese_sense_sits_beside_the_mandarin_one(test_config, tmp_path):
    config = replace(test_config, language="yue")
    service = DefinitionService(config, _wizard_chain(tmp_path), lookup=get_profile("yue").lookup)

    (definition,), _glossaries, _pitch = _phase4(config, service)

    assert definition is not None
    assert definition.index("mosquito") < definition.index("dollar")


def test_a_mandarin_run_keeps_its_first_hit_definition(test_config, tmp_path):
    config = replace(test_config, language="zh")
    service = DefinitionService(config, _wizard_chain(tmp_path))

    (definition,), _glossaries, _pitch = _phase4(config, service)

    assert definition is not None and "mosquito" in definition
    assert "dollar" not in definition


def test_the_glossary_reuses_the_stacked_definition_and_its_ladder(test_config, mock_services):
    config = replace(test_config, language="yue", anki_fields={**test_config.anki_fields, "glossary": "Glossary"})
    service = mock_services["definition_service"]
    service.get_glossaries_batch.return_value = ["<CEDICT><CC-Canto>"]

    definitions, glossaries, _pitch = _phase4(config, service)

    assert definitions == glossaries == ["<CEDICT><CC-Canto>"]
    service.get_definitions_batch.assert_not_called()
    service.get_glossaries_batch.assert_called_once()
    # The Definition keeps its miss ladder: the fallback context rides along.
    assert service.get_glossaries_batch.call_args.args == ([("蚊", "")], None, {"蚊": ("蚊", None)})

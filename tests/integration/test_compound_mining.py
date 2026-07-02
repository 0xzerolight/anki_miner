"""Integration: dictionary-attested compound mining over a real chain + real fugashi.

Seeds a real ``index.sqlite`` (same storage primitives as production imports),
builds the provider chain via ``DictionaryRegistry``, wires
``DefinitionService.offline_terms_exist`` into a ``SubtitleParserService``, and
parses a real .srt — the full production wiring minus the GUI.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _seed(root: Path, dict_id: str, entries: list[DictRow]) -> None:
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, entries)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(entries)),
        },
    )


def _build_service(tmp_path: Path) -> tuple[SubtitleParserService, DefinitionService]:
    _seed(
        tmp_path / "dicts",
        "compound-dict",
        [
            DictRow(
                term="走り出す",
                reading="はしりだす",
                content='<li class="gloss-item">to start running</li>',
                sequence=1,
            ),
            DictRow(
                term="応急処置", reading="おうきゅうしょち", content='<li class="gloss-item">first aid</li>', sequence=2
            ),
        ],
    )
    config = replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="compound-dict", enabled=True),),
        media_temp_folder=tmp_path / "media",
    )
    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    definition_service = DefinitionService(config, providers=registry.build_provider_chain(config))
    parser = SubtitleParserService(config, term_lookup=definition_service.offline_terms_exist)
    return parser, definition_service


def _write_srt(tmp_path: Path) -> Path:
    srt_file = tmp_path / "episode.srt"
    srt_file.write_text(
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "彼は急に走り出した。\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "応急処置が必要だ。\n",
        encoding="utf-8",
    )
    return srt_file


def test_offline_terms_exist_through_real_chain(tmp_path: Path) -> None:
    _parser, definition_service = _build_service(tmp_path)
    found = definition_service.offline_terms_exist(["走り出す", "応急処置", "存在しない語", "はしりだす"])
    # Exact headwords only; the reading row does not attest.
    assert found == {"走り出す", "応急処置"}


def test_full_parse_mines_compounds_not_fragments(tmp_path: Path) -> None:
    parser, _definition_service = _build_service(tmp_path)
    srt_file = _write_srt(tmp_path)

    words = parser.parse_subtitle_file(srt_file)
    lemmas = {w.lemma for w in words}

    assert "走り出す" in lemmas
    assert "応急処置" in lemmas
    # The original fragment bugs: components must not surface as cards.
    assert "走る" not in lemmas
    assert "出す" not in lemmas
    assert "応急" not in lemmas
    assert "処置" not in lemmas

    by_lemma = {w.lemma: w for w in words}
    assert by_lemma["走り出す"].mined_form == "走り出す"
    assert by_lemma["応急処置"].mined_form == "応急処置"


def test_count_lemmas_agrees_with_parse(tmp_path: Path) -> None:
    """T-38 Deck Builder parity: the counting path sees the same compounds."""
    parser, _definition_service = _build_service(tmp_path)
    srt_file = _write_srt(tmp_path)

    counts = parser.count_lemmas(srt_file)
    words = parser.parse_subtitle_file(srt_file)

    assert counts["走り出す"] == 1
    assert counts["応急処置"] == 1
    assert "応急" not in counts
    mineable = {w.lemma for w in words}
    assert mineable <= set(counts)

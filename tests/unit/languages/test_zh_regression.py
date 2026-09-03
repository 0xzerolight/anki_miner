"""zh regressions: encoding ladder, deck isolation, key symmetry, rendered card."""

from __future__ import annotations

import dataclasses
import logging
import re
from unittest.mock import MagicMock

import pysubs2
import pytest

from anki_miner.config import ChainEntry
from anki_miner.exceptions import SetupError
from anki_miner.gui.utils.service_factory import _create_subtitle_parser, resolve_known_words_db_path
from anki_miner.gui.widgets.youtube_playlist_flow import _classify_probe_result
from anki_miner.languages import registry
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.card_payload import CardPayload
from anki_miner.models.media import MediaData
from anki_miner.models.youtube import VideoInfo
from anki_miner.services.anki_note_builder import build_note
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding
from tests.conftest import build_processor

_SRT = "1\n00:00:01,000 --> 00:00:03,000\n{}\n"

#: note id -> (deck, first-field expression). The JA deck's expressions are all
#: Han-only on purpose: a script gate cannot tell them from Chinese.
_COLLECTION = {
    1: ("JA Mining", "学生"),
    2: ("JA Mining", "世界"),
    3: ("ZH Mining", "银行"),
    4: ("ZH Mining", "电影"),
}


def _fake_ankiconnect(monkeypatch):
    """A fake collection that honours the query's -deck: negations."""

    def _post(url, action, params=None, timeout=30):
        params = params or {}
        if action == "findNotes":
            excluded = set(re.findall(r'-deck:"([^"]+)"', params["query"]))
            return [nid for nid, (deck, _) in _COLLECTION.items() if deck not in excluded]
        if action == "notesInfo":
            return [{"fields": {"Expression": {"value": _COLLECTION[nid][1], "order": 0}}} for nid in params["notes"]]
        raise AssertionError(f"unexpected AnkiConnect action: {action}")

    monkeypatch.setattr("anki_miner.services.anki_service.post_action", _post)


def _zh_config(test_config, tmp_path, excluded_decks):
    """A zh config with *excluded_decks* applied AFTER the language switch.

    ``excluded_decks`` is in ``LANGUAGE_SCOPED_FIELDS``, so setting it on the ja
    config first would let the zh profile's blank scoped default overwrite it —
    and the contamination test below would pass for the wrong reason.
    """
    base = dataclasses.replace(test_config, known_words_db_path=tmp_path / "known_words.db")
    return dataclasses.replace(switch_language(base, "zh"), excluded_decks=excluded_decks)


def _gb18030_srt(tmp_path):
    path = tmp_path / "sample_gb18030.srt"
    path.write_bytes(_SRT.format("我今天去银行取钱。").encode("gb18030"))
    return path


def test_gb18030_subtitle_decodes_through_the_zh_profile_ladder(tmp_path):
    path = _gb18030_srt(tmp_path)
    with pytest.raises(UnicodeDecodeError) as exc_info:
        pysubs2.load(str(path), encoding="utf-8")
    subs = load_with_fallback_encoding(path, exc_info.value, encodings=get_profile("zh").import_encodings)
    assert subs[0].text == "我今天去银行取钱。"


def test_the_none_sentinel_keeps_the_japanese_ladder(tmp_path):
    """`None`, never `()` — an empty ladder would stop cp932 decoding."""
    path = tmp_path / "cp932.srt"
    path.write_bytes(_SRT.format("日本語です。").encode("cp932"))
    with pytest.raises(UnicodeDecodeError) as exc_info:
        pysubs2.load(str(path), encoding="utf-8")
    assert load_with_fallback_encoding(path, exc_info.value)[0].text == "日本語です。"
    assert load_with_fallback_encoding(path, exc_info.value, encodings=None)[0].text == "日本語です。"


def test_the_zh_cache_is_a_different_file_from_the_ja_one(test_config, tmp_path):
    ja_config = dataclasses.replace(test_config, known_words_db_path=tmp_path / "known_words.db")
    zh_config = switch_language(ja_config, "zh")
    assert resolve_known_words_db_path(ja_config) == tmp_path / "known_words.db"
    assert resolve_known_words_db_path(zh_config) == tmp_path / "known_words.zh.db"


def test_an_excluded_ja_deck_never_reaches_the_zh_known_words_db(test_config, tmp_path, monkeypatch):
    _fake_ankiconnect(monkeypatch)
    config = _zh_config(test_config, tmp_path, excluded_decks=("JA Mining",))
    vocabulary = AnkiService(config).get_existing_vocabulary()
    db = KnownWordDB(resolve_known_words_db_path(config))
    db.initialize()
    db.sync_with_anki(vocabulary)
    known = db.get_known_words()
    assert {"银行", "电影"} <= known
    assert not {"学生", "世界"} & known


def test_without_the_exclusion_the_same_notes_do_reach_it(test_config, tmp_path, monkeypatch):
    """The companion that makes the test above able to fail."""
    _fake_ankiconnect(monkeypatch)
    config = _zh_config(test_config, tmp_path, excluded_decks=())
    vocabulary = AnkiService(config).get_existing_vocabulary()
    db = KnownWordDB(resolve_known_words_db_path(config))
    db.initialize()
    db.sync_with_anki(vocabulary)
    assert {"学生", "世界"} <= db.get_known_words()


def _seed_zh_dict(dicts_root, dict_id="cedict-zh"):
    """One zh index written through the REAL zh import-side folding."""
    db_path = dicts_root / dict_id / "index.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    create_index(db_path)
    bulk_insert(
        db_path,
        # Source case as a CC-CEDICT port ships it; the term is Han, so the row
        # is reachable ONLY through its folded reading key.
        [DictRow(term="銀行", reading="Yín Háng", content="<div>bank</div>", sequence=1)],
        keys=get_profile("zh").dict_keys,
    )
    write_meta(
        db_path,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": "CC-CEDICT",
            "format": "yomitan",
            "entry_count": "1",
            "language": "zh",
        },
    )
    return db_path


def test_a_zh_index_imported_with_the_real_folding_is_queryable(test_config, tmp_path):
    """fold_reading symmetry, end to end: a break here is a SILENT zero-row miss.

    Both halves share one ``ZhDictKeyFolding``: the importer folds the reading
    key before writing it and the provider the chain builder hands back folds
    the query the same way. A casefold on one side only returns no rows, no
    exception and no log line.
    """
    dicts_root = tmp_path / "dicts"
    db_path = _seed_zh_dict(dicts_root)
    config = dataclasses.replace(
        switch_language(dataclasses.replace(test_config, dicts_root=dicts_root), "zh"),
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="cedict-zh", enabled=True),),
    )
    registry = DictionaryRegistry(dicts_root)
    registry.load()
    chain = registry.build_provider_chain(config)

    assert len(chain) == 1
    provider = chain[0]
    assert isinstance(provider, IndexedDictProvider)
    assert provider._keys is get_profile("zh").dict_keys
    assert provider.load() is True
    # Query cased as the source ships it and as the engine generates it: both
    # resolve, because both sides casefold.
    assert provider.lookup("Yín Háng") is not None
    assert provider.lookup("yín háng") is not None

    # Asymmetry proof: the ja folding does not casefold, so the source-cased
    # query finds nothing in the same file.
    ja_side = IndexedDictProvider("cedict-zh", db_path, display_name="CC-CEDICT", keys=get_profile("ja").dict_keys)
    assert ja_side.load() is True
    assert ja_side.lookup("Yín Háng") is None


def test_a_zh_card_carries_its_hook_fields_end_to_end(test_config, tmp_path, make_tokenized_word, monkeypatch):
    monkeypatch.setattr("anki_miner.languages.zh.render.to_traditional", lambda text: {"银行": "銀行"}.get(text, text))
    monkeypatch.setattr("anki_miner.languages.zh.render.pinyin_syllables", lambda text: [("yín", 2), ("háng", 2)])
    profile = get_profile("zh")
    config = dataclasses.replace(
        switch_language(test_config, "zh"),
        anki_fields={
            **profile.card_field_defaults,
            "word": "Expression",
            "sentence": "Sentence",
            "definition": "MainDefinition",
            "measure_word": "MeasureWord",
            "expression_traditional": "Traditional",
            "expression_pinyin": "Pinyin",
        },
    )
    word = make_tokenized_word(surface="银行", lemma="银行", reading="", sentence="我今天去银行取钱。")
    word.definition_html = "bank; CL:家[jia1]"
    extra: dict[str, str] = {}
    for hook in profile.render_hooks:
        extra.update(hook.render(word, config=config))
    note = build_note(CardPayload(word=word, media=MediaData(), definition="bank", extra_fields=extra), config, set())
    fields = note.note["fields"]
    assert fields["Expression"] == "银行"
    assert fields["MeasureWord"] == "家"
    assert fields["Traditional"] == "銀行"
    assert fields["Pinyin"].startswith('<span style="color:')
    assert "ExpressionFurigana" not in fields  # ja-only key blanked by the zh defaults


def _srt_file(tmp_path, name, *lines):
    path = tmp_path / name
    cues = "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{line}\n\n" for i, line in enumerate(lines, 1))
    path.write_text(cues, encoding="utf-8")
    return path


def test_a_switched_zh_config_mines_words_from_a_simplified_srt(test_config, tmp_path, caplog):
    """The composition root's parser seam, on the profile's own POS defaults.

    The release-bundle smoke is the only other real zh parse and no pytest run
    sees it. A config built without ``switch_language`` keeps the JA POS names,
    which reject every jieba flag and mine exactly nothing - silently.
    """
    parser = _create_subtitle_parser(switch_language(test_config, "zh"))
    path = _srt_file(tmp_path, "zh.srt", "今天我们来学习中文", "我觉得这个视频非常好看")
    with caplog.at_level(logging.WARNING, logger="anki_miner.services.subtitle_parser"):
        words = parser.parse_subtitle_file(path)
    assert {"今天", "学习", "中文", "视频"} <= {w.mined_form for w in words}
    assert not [r for r in caplog.records if "mined no words" in r.getMessage()]


def test_non_han_cue_text_mines_nothing_and_the_log_names_why(test_config, tmp_path, caplog):
    """English cues under a Chinese caption code mine nothing - the shape of the
    first zh YouTube report - and the GUI's "No words found in subtitles" carries
    no cause. The parser's one WARNING has to carry what tells a wrong-language
    subtitle apart from a wrong-language POS whitelist: the mining language, the
    language whose tagger ran, that whitelist, and the tags the tagger emitted.
    """
    parser = _create_subtitle_parser(switch_language(test_config, "zh"))
    path = _srt_file(tmp_path, "en.srt", "Today we are going to learn Chinese", "He went to Beijing yesterday")
    with caplog.at_level(logging.WARNING, logger="anki_miner.services.subtitle_parser"):
        words = parser.parse_subtitle_file(path)
    assert words == []
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING and "mined no words" in r.getMessage()]
    message = record.getMessage()
    assert "language=zh" in message
    assert "tagger_language=zh" in message
    assert "allowed_pos=" in message
    assert "lines=2" in message
    assert "raw_tokens=0" not in message and "raw_tokens=" in message
    assert "top_pos=" in message


def test_a_config_whose_language_degraded_refuses_to_mine(test_config, tmp_path, monkeypatch):
    """``config_language`` maps a code with no registered profile to ja so that
    Settings still loads. Mining that config ran the Japanese tagger over
    Chinese text and reported "No words found in subtitles" - the zh POS
    whitelist rejects every unidic tag. The tokenizing entry points refuse
    instead, naming the language; ``parse_raw_entries`` previews keep working.
    """
    config = switch_language(test_config, "zh")
    monkeypatch.delitem(registry._BUILDERS, "zh")
    parser = _create_subtitle_parser(config)
    path = _srt_file(tmp_path, "zh.srt", "今天我们来学习中文")
    assert parser.parse_raw_entries(path)
    with pytest.raises(SetupError, match="'zh'"):
        parser.parse_subtitle_file(path)
    with pytest.raises(SetupError, match="'zh'"):
        parser.parse_subtitle_file_with_index(path)
    with pytest.raises(SetupError, match="'zh'"):
        parser.count_lemmas(path)


def _zero_word_run(test_config, tmp_path, language, cue_texts):
    """Drive process_episode with a parser that mines nothing; return the warnings."""
    presenter = MagicMock()
    parser = MagicMock()
    parser.parse_subtitle_file.return_value = []
    parser.parse_raw_entries.return_value = [(float(i), float(i) + 1.0, text) for i, text in enumerate(cue_texts)]
    config = test_config if language == "ja" else switch_language(test_config, language)
    processor = build_processor(config, presenter=presenter, subtitle_parser=parser)
    result = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.srt")
    assert result.total_words_found == 0
    return [call.args[0] for call in presenter.show_warning.call_args_list]


def test_a_subtitle_without_the_mining_script_is_named_as_such(test_config, tmp_path):
    """English cues under a Chinese caption code: the one zero-word case the
    user can act on, so the warning names the track, not "no words"."""
    warnings = _zero_word_run(test_config, tmp_path, "zh", ["Today we learn Chinese", "It is fun"])
    assert any(f"no {get_profile('zh').display_name} text" in w for w in warnings)
    assert not any("No words found" in w for w in warnings)


def test_a_subtitle_in_the_mining_script_keeps_the_generic_message(test_config, tmp_path):
    warnings = _zero_word_run(test_config, tmp_path, "zh", ["今天我们来学习中文"])
    assert any("No words found in subtitles" in w for w in warnings)


def test_the_probe_rejection_names_the_mining_language(test_config):
    """A zh run used to be refused with "No Japanese subtitles available"."""
    info = VideoInfo(
        video_id="x",
        title="t",
        duration_s=60,
        has_manual_ja_subs=False,
        has_auto_ja_subs=False,
        is_live=False,
        is_age_restricted=False,
    )
    mineable, message, mode = _classify_probe_result(info, switch_language(test_config, "zh"))
    assert (mineable, mode) == (False, None)
    assert message == "No Chinese subtitles available for this video."

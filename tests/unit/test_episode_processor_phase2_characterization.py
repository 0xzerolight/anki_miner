"""Characterization of ``EpisodeProcessor._phase2_filter``, the phase-2 word filter.

Which words survive phase 2, and in what order, is what a run mines. This file
pins everything the phase makes observable, per scenario:

* the kept words, in order, with the sentence each one ends on;
* every presenter call, in order (stage, info, success, warning);
* the progress stage the run's callback receives;
* every record the module logger emits, including the ``Phase 2 filter``
  summary with its counters in order;
* the ``_EpisodeContext`` fields the phase stamps.

Between them the five scenarios reach every branch of the phase:

* frequency attach;
* the include-everything path and the subtraction path;
* the user ignore list;
* the known-words DB sync (added, not added, failing) and its fallback;
* the whitelist coverage snapshot and the all-known warning;
* the offline definition probe: direct hit, deinflection hit, and a drop with
  and without the "+N more" overflow;
* the whitelist force-include partition and merge-back;
* the frequency band and its ignored-cutoff warning;
* the word-list, script-type and name-wordset filters;
* the reading occurrence floor, sentence dedup, i+1 and the sentence-length
  caps;
* the within-run duplicate collapse, on an exact and on an identity collision.

A changed expected value here means a change to what gets mined. Update one
only on purpose, never to make a refactor pass.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.orchestration.episode_processor import _EpisodeContext
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService
from tests.conftest import RecordingProgress, build_processor

_LOGGER = "anki_miner.orchestration.episode_processor"

# Every phase-2 knob, pinned so a changed config default cannot move a scenario.
_KNOBS = {
    "include_known_words": False,
    "use_known_words_db": False,
    "known_words_match_kana_variants": True,
    "use_whitelist": False,
    "min_frequency_rank": 0,
    "max_frequency_rank": 0,
    "frequency_keep_unranked": False,
    "exclude_hiragana_only_words": False,
    "exclude_katakana_only_words": False,
    "deduplicate_sentences": False,
    "use_i_plus_one_filter": False,
    "max_sentence_duration_seconds": 0.0,
    "max_sentence_chars": 0,
    "allow_duplicate_cards": False,
    "bypass_optional_filters": False,
}

# Terms the fake offline dictionary has no entry for.
_UNDEFINED = frozenset({f"欠{i}" for i in range(11)} | {"食べさせる", "空"})
# Frequency ranks by card front; every other front ranks 1000, these are unranked.
_RANKS = {"頻出": 50, "珍": 9000}
_UNRANKED = frozenset({"強制", "無順"})


def _noun(front: str, sentence: str | None = None, *, lemma: str | None = None, duration: float = 1.0):
    return TokenizedWord(
        surface=front,
        lemma=lemma or front,
        reading="ヨミ",
        sentence=sentence or f"{front}です。",
        start_time=0.0,
        end_time=duration,
        duration=duration,
        pos="名詞",
    )


def _verb(front: str):
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="ヨミ",
        sentence=f"{front}。",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="動詞",
        orth_base=front,
    )


def _line(text: str, *lemmas: str) -> LineLemmas:
    return LineLemmas(text, frozenset(lemmas), 10.0, 12.0, 2.0)


def _definition_service():
    service = MagicMock(name="definition_service")
    service.has_offline_definitions.side_effect = lambda terms: {t: t not in _UNDEFINED for t in terms}
    # 食べさせる has no entry of its own; its causative deinflection 食べる does.
    service.offline_deinflection_terms_exist.side_effect = lambda candidates: {
        term for term, _conditions in candidates if term == "食べる"
    }
    service.offline_term_identities.side_effect = lambda pairs: {
        pair: {("辞書", 7, "でる")} for pair in pairs if pair[0] in ("出る", "出でる")
    }
    return service


def _frequency_service(*, numeric: bool = True):
    service = MagicMock(name="frequency_service")
    service.is_available.return_value = True
    service.has_numeric_source.return_value = numeric
    service._providers = [SimpleNamespace(name="JLPT")]
    service.lookup_all_many.side_effect = lambda pairs: [
        [] if front in _UNRANKED else [("JPDB", _RANKS.get(front, 1000), None)] for front, _reading in pairs
    ]
    return service


def _known_word_db(*, user=frozenset(), known=frozenset(), sync=(0, 0), fail=False):
    db = MagicMock(name="known_word_db")
    db.is_available.return_value = True
    if fail:
        db.get_words_by_source.side_effect = sqlite3.OperationalError("database is locked")
        db.get_known_words.side_effect = sqlite3.OperationalError("database is locked")
    else:
        db.get_words_by_source.return_value = set(user)
        db.get_known_words.return_value = set(known)
        db.sync_with_anki.return_value = sync
    return db


def _anki_service(vocabulary):
    service = MagicMock(name="anki_service")
    service.get_existing_vocabulary.return_value = set(vocabulary)
    return service


def _word_lists(tmp_path, *, whitelist=(), blacklist=()):
    white = tmp_path / "whitelist.txt"
    black = tmp_path / "blacklist.txt"
    white.write_text("\n".join(whitelist) + "\n", encoding="utf-8")
    black.write_text("\n".join(blacklist) + "\n", encoding="utf-8")
    service = WordListService(blacklist_path=black, whitelist_path=white)
    service.load()
    return service


def _wordsets(*names):
    return SimpleNamespace(is_available=lambda: True, is_excluded=lambda term: term in names)


def _full(test_config, tmp_path):
    """Subtraction path with every optional filter on and each one biting."""
    config = replace(
        test_config,
        **{
            **_KNOBS,
            "use_known_words_db": True,
            "use_whitelist": True,
            "min_frequency_rank": 100,
            "max_frequency_rank": 5000,
            "exclude_hiragana_only_words": True,
            "exclude_katakana_only_words": True,
            "deduplicate_sentences": True,
            "max_sentence_duration_seconds": 5.0,
            "max_sentence_chars": 15,
        },
    )
    words = [
        _noun("既知"),
        _noun("無視"),
        _noun("同期"),
        _noun("既知W"),
        _noun("強制"),
        _noun("普通"),
        _noun("頻出"),
        _noun("珍"),
        _noun("無順"),
        _noun("黒"),
        _noun("ひらがな"),
        _noun("カタカナ"),
        _noun("田中"),
        _noun("稀"),
        _noun("文甲", "同じ文です。"),
        _noun("文乙", "同じ文です。"),
        _noun("長文", "とてもとても長い長い例文ですよね。"),
        _noun("長尺", duration=6.0),
        _noun("重複"),
        _noun("重複", "重複の別文。", lemma="重複語"),
        _verb("出る"),
        _verb("出でる"),
        _verb("食べさせる"),
        *(_noun(f"欠{i}") for i in range(11)),
    ]
    processor = build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=WordFilterService(config),
        definition_service=_definition_service(),
        anki_service=_anki_service({"既知", "同期", "既知W"}),
        known_word_db=_known_word_db(user={"無視"}, known={"既知"}, sync=(2, 3)),
        frequency_service=_frequency_service(),
        word_list_service=_word_lists(tmp_path, whitelist=("強制", "既知W"), blacklist=("黒",)),
        wordset_service=_wordsets("田中"),
    )
    occurrences = {w.lemma: 2 for w in words} | {"稀": 1}
    return processor, words, None, occurrences, 2


def _i_plus_one(test_config, tmp_path):
    """i+1 path: Anki-only known set; a blacklisted unknown still counts as unknown."""
    config = replace(
        test_config,
        **{
            **_KNOBS,
            "use_whitelist": True,
            "deduplicate_sentences": True,
            "use_i_plus_one_filter": True,
            "max_sentence_chars": 8,
        },
    )
    words = [_noun("既知"), _noun("強制"), _noun("的"), _noun("孤"), _noun("黒"), _noun("長"), _noun("空")]
    line_index = [
        _line("既知と的と孤と黒。", "既知", "的", "孤", "黒"),
        # 孤's only short line: i+1 for it alone unless 黒 (blacklisted, so no
        # longer a target) still counts as unknown, which it must (Issue #74).
        _line("孤と黒だ。", "孤", "黒"),
        _line("的だ。", "的"),
        _line("長い長い長い台詞だ。", "長"),
        _line("強制だ。", "強制"),
    ]
    processor = build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=WordFilterService(config),
        definition_service=_definition_service(),
        anki_service=_anki_service({"既知"}),
        known_word_db=_known_word_db(user=set()),
        word_list_service=_word_lists(tmp_path, whitelist=("強制",), blacklist=("黒",)),
    )
    return processor, words, line_index, None, 1


def _include_known(test_config, tmp_path):
    """Include-everything path; a max-rank cutoff with only a categorical source."""
    config = replace(
        test_config,
        **{**_KNOBS, "include_known_words": True, "max_frequency_rank": 5000, "allow_duplicate_cards": True},
    )
    words = [_noun("既知"), _noun("普通"), _noun("重複"), _noun("重複", lemma="重複語")]
    processor = build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=WordFilterService(config),
        definition_service=_definition_service(),
        anki_service=_anki_service({"既知"}),
        frequency_service=_frequency_service(numeric=False),
    )
    return processor, words, None, None, 1


def _db_failure(test_config, tmp_path):
    """Both known-words DB reads raise; every word is already in Anki."""
    config = replace(test_config, **{**_KNOBS, "use_known_words_db": True})
    processor = build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=WordFilterService(config),
        definition_service=_definition_service(),
        anki_service=_anki_service({"既知", "既知二"}),
        known_word_db=_known_word_db(fail=True),
    )
    return processor, [_noun("既知"), _noun("既知二")], None, None, 1


def _bypass(test_config, tmp_path):
    """bypass_optional_filters: every optional filter configured, only the floor and collapse apply."""
    config = replace(
        test_config,
        **{
            **_KNOBS,
            "use_known_words_db": True,
            "use_whitelist": True,
            "min_frequency_rank": 100,
            "max_frequency_rank": 5000,
            "exclude_hiragana_only_words": True,
            "deduplicate_sentences": True,
            "max_sentence_chars": 3,
            "bypass_optional_filters": True,
        },
    )
    words = [
        _noun("既知"),
        _noun("頻出"),
        _noun("ひらがな"),
        _noun("黒"),
        _noun("稀"),
        _noun("空"),
        _noun("重複"),
        _noun("重複", lemma="重複語"),
    ]
    processor = build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=WordFilterService(config),
        definition_service=_definition_service(),
        anki_service=_anki_service({"既知"}),
        known_word_db=_known_word_db(known={"既知"}, sync=(0, 1)),
        frequency_service=_frequency_service(),
        word_list_service=_word_lists(tmp_path, whitelist=("黒",), blacklist=("黒",)),
        wordset_service=_wordsets("稀"),
    )
    occurrences = {w.lemma: 2 for w in words} | {"稀": 1}
    return processor, words, None, occurrences, 2


_SCENARIOS = {
    "full": _full,
    "i_plus_one": _i_plus_one,
    "include_known": _include_known,
    "db_failure": _db_failure,
    "bypass": _bypass,
}

_STAGE = ("show_stage", (2, 5, "Filtering against known vocabulary"))

_EXPECTED = {
    "full": {
        "kept": [
            ("強制", "強制です。"),
            ("普通", "普通です。"),
            ("文甲", "同じ文です。"),
            ("重複", "重複です。"),
            ("出る", "出る。"),
            ("食べさせる", "食べさせる。"),
        ],
        "presenter": [
            ("show_info", ("Frequency data: 32/34 words ranked",)),
            _STAGE,
            ("show_info", ("Known word DB synced: 2 new words (3 total)",)),
            ("show_success", ("30 new word(s) to mine",)),
            ("show_info", ("Comprehension: 11.8% of words already known",)),
            (
                "show_warning",
                (
                    "Skipped 11 words missing from your offline dictionaries: "
                    "欠0, 欠1, 欠2, 欠3, 欠4, 欠5, 欠6, 欠7, 欠8, 欠9 (+1 more)",
                ),
            ),
            ("show_info", ("Frequency filter: removed 3 words outside ranks 100-5000",)),
            ("show_info", ("Word list filter: removed 1 words",)),
            ("show_info", ("Script-type filter: removed 2 hiragana-only/katakana-only words",)),
            ("show_info", ("Name wordset filter: removed 1 words",)),
            ("show_info", ("Sentence deduplication: removed 1 duplicate-sentence words",)),
            ("show_info", ("Sentence length filter: removed 2 words (cap: 5s, 15 chars)",)),
            ("show_info", ("Whitelist: force-included 1 word(s)",)),
            ("show_info", ("Collapsed 2 duplicate-expression word(s)",)),
        ],
        "log": [
            ("DEBUG", "Definitions missing: phase=2 count=11 words=欠0,欠1,欠2,欠3,欠4,欠5,欠6,欠7,欠8,欠9,欠10"),
            (
                "INFO",
                "Phase 2 filter: in=34 out=6 frequency_ranked=32 known_hits=4 known_db_added=2 known_db_total=3 "
                "frequency_rejects=3 word_list_rejects=1 script_rejects=2 wordset_rejects=1 episode_rejects=1 "
                "duplicate_sentence_rejects=1 i_plus_one_rejects=0 sentence_length_rejects=2 "
                "whitelist_force_includes=1 no_definition_rejects=11 duplicate_expression_rejects=2",
            ),
        ],
        "ctx": {
            "new_words_found": 6,
            "candidate_words_found": 30,
            "comprehension_percentage": 11.764706,
            "unknown_lemmas": (
                "ひらがな カタカナ 出でる 出る 強制 文乙 文甲 普通 欠0 欠1 欠10 欠2 欠3 欠4 欠5 欠6 欠7 欠8 欠9 "
                "無順 珍 田中 稀 重複 重複語 長尺 長文 頻出 食べさせる 黒"
            ),
            "difficulty_total_words": 34,
            "difficulty_unknown_words": 30,
            "whitelist_coverage": (["強制", "既知W"], ["既知W"]),
        },
    },
    "i_plus_one": {
        "kept": [("強制", "強制です。"), ("的", "的だ。")],
        "presenter": [
            _STAGE,
            ("show_success", ("6 new word(s) to mine",)),
            ("show_info", ("Comprehension: 14.3% of words already known",)),
            ("show_warning", ("Skipped 1 words missing from your offline dictionaries: 空",)),
            ("show_info", ("Word list filter: removed 1 words",)),
            ("show_info", ("i+1 filter: kept 2/3 words (67%)",)),
            ("show_info", ("Sentence length filter: removed 1 words (cap: 8 chars)",)),
            ("show_info", ("Whitelist: force-included 1 word(s)",)),
        ],
        "log": [
            ("DEBUG", "Definitions missing: phase=2 count=1 words=空"),
            (
                "INFO",
                "Phase 2 filter: in=7 out=2 frequency_ranked=0 known_hits=1 known_db_added=0 known_db_total=0 "
                "frequency_rejects=0 word_list_rejects=1 script_rejects=0 wordset_rejects=0 episode_rejects=0 "
                "duplicate_sentence_rejects=0 i_plus_one_rejects=1 sentence_length_rejects=1 "
                "whitelist_force_includes=1 no_definition_rejects=1 duplicate_expression_rejects=0",
            ),
        ],
        "ctx": {
            "new_words_found": 2,
            "candidate_words_found": 6,
            "comprehension_percentage": 14.285714,
            "unknown_lemmas": "孤 強制 的 空 長 黒",
            "difficulty_total_words": 7,
            "difficulty_unknown_words": 6,
            "whitelist_coverage": (["強制"], []),
        },
    },
    "include_known": {
        "kept": [("既知", "既知です。"), ("普通", "普通です。"), ("重複", "重複です。"), ("重複", "重複です。")],
        "presenter": [
            ("show_info", ("Frequency data: 4/4 words ranked",)),
            _STAGE,
            ("show_info", ("Including words already known",)),
            ("show_success", ("4 new word(s) to mine",)),
            ("show_info", ("Comprehension: 0.0% of words already known",)),
            (
                "show_warning",
                ("Frequency cutoff ignored — no ranked frequency source is loaded (Settings → Frequency).",),
            ),
        ],
        "log": [
            ("WARNING", "Frequency cutoff ignored: low=0 high=5000 sources=JLPT"),
            (
                "INFO",
                "Phase 2 filter: in=4 out=4 frequency_ranked=4 known_hits=0 known_db_added=0 known_db_total=0 "
                "frequency_rejects=0 word_list_rejects=0 script_rejects=0 wordset_rejects=0 episode_rejects=0 "
                "duplicate_sentence_rejects=0 i_plus_one_rejects=0 sentence_length_rejects=0 "
                "whitelist_force_includes=0 no_definition_rejects=0 duplicate_expression_rejects=0",
            ),
        ],
        "ctx": {
            "new_words_found": 4,
            "candidate_words_found": 4,
            "comprehension_percentage": 0.0,
            "unknown_lemmas": "既知 普通 重複 重複語",
            "difficulty_total_words": 4,
            "difficulty_unknown_words": 4,
            "whitelist_coverage": None,
        },
    },
    "db_failure": {
        "kept": [],
        "presenter": [
            _STAGE,
            ("show_success", ("0 new word(s) to mine",)),
            ("show_info", ("Comprehension: 100.0% of words already known",)),
            ("show_warning", ("All 2 word(s) from this run are already known — no new cards created",)),
        ],
        "log": [
            (
                "WARNING",
                "Could not read the user ignore list from known_words.db (database is locked); "
                "proceeding without it this run.",
            ),
            (
                "WARNING",
                "Could not access known_words.db (database is locked); "
                "falling back to Anki's existing vocabulary for this run.",
            ),
            (
                "INFO",
                "Phase 2 filter: in=2 out=0 frequency_ranked=0 known_hits=2 known_db_added=0 known_db_total=0 "
                "frequency_rejects=0 word_list_rejects=0 script_rejects=0 wordset_rejects=0 episode_rejects=0 "
                "duplicate_sentence_rejects=0 i_plus_one_rejects=0 sentence_length_rejects=0 "
                "whitelist_force_includes=0 no_definition_rejects=0 duplicate_expression_rejects=0",
            ),
        ],
        "ctx": {
            "new_words_found": 0,
            "candidate_words_found": 0,
            "comprehension_percentage": 100.0,
            "unknown_lemmas": "",
            "difficulty_total_words": 2,
            "difficulty_unknown_words": 0,
            "whitelist_coverage": None,
        },
    },
    "bypass": {
        "kept": [
            ("頻出", "頻出です。"),
            ("ひらがな", "ひらがなです。"),
            ("黒", "黒です。"),
            ("空", "空です。"),
            ("重複", "重複です。"),
        ],
        "presenter": [
            ("show_info", ("Frequency data: 8/8 words ranked",)),
            _STAGE,
            ("show_success", ("7 new word(s) to mine",)),
            ("show_info", ("Comprehension: 12.5% of words already known",)),
            ("show_info", ("Collapsed 1 duplicate-expression word(s)",)),
        ],
        "log": [
            (
                "INFO",
                "Phase 2 filter: in=8 out=5 frequency_ranked=8 known_hits=1 known_db_added=0 known_db_total=1 "
                "frequency_rejects=0 word_list_rejects=0 script_rejects=0 wordset_rejects=0 episode_rejects=1 "
                "duplicate_sentence_rejects=0 i_plus_one_rejects=0 sentence_length_rejects=0 "
                "whitelist_force_includes=0 no_definition_rejects=0 duplicate_expression_rejects=1",
            ),
        ],
        "ctx": {
            "new_words_found": 5,
            "candidate_words_found": 7,
            "comprehension_percentage": 12.5,
            "unknown_lemmas": "ひらがな 稀 空 重複 重複語 頻出 黒",
            "difficulty_total_words": 8,
            "difficulty_unknown_words": 7,
            "whitelist_coverage": None,
        },
    },
}


def _observe(processor, words, line_index, occurrences, min_occurrence, caplog):
    ctx = _EpisodeContext(0.0, "", "", "episode", "series", "")
    progress = RecordingProgress()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        kept = processor._phase2_filter(ctx, words, line_index, progress, occurrences, min_occurrence)
    assert progress.stages == [_STAGE[1]]
    coverage = ctx.whitelist_coverage
    return {
        "kept": [(w.mined_form, w.sentence) for w in kept],
        "presenter": [(name, args) for name, args, _kwargs in processor.presenter.mock_calls],
        "log": [(r.levelname, r.getMessage()) for r in caplog.records if r.name == _LOGGER],
        "ctx": {
            "new_words_found": ctx.new_words_found,
            "candidate_words_found": ctx.candidate_words_found,
            "comprehension_percentage": round(ctx.comprehension_percentage, 6),
            "unknown_lemmas": " ".join(sorted(ctx.unknown_lemmas)),
            "difficulty_total_words": ctx.difficulty_total_words,
            "difficulty_unknown_words": ctx.difficulty_unknown_words,
            "whitelist_coverage": None if coverage is None else (sorted(coverage.entries), sorted(coverage.known)),
        },
    }


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_phase2_filter_output_is_pinned(scenario, test_config, tmp_path, caplog):
    processor, words, line_index, occurrences, min_occurrence = _SCENARIOS[scenario](test_config, tmp_path)

    observed = _observe(processor, words, line_index, occurrences, min_occurrence, caplog)

    assert observed["kept"] == _EXPECTED[scenario]["kept"]
    assert observed["presenter"] == _EXPECTED[scenario]["presenter"]
    assert observed["log"] == _EXPECTED[scenario]["log"]
    assert observed["ctx"] == _EXPECTED[scenario]["ctx"]

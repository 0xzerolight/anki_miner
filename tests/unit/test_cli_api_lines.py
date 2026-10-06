"""--api lines: the mine run's curation callback and its result rows."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

import pytest

from anki_miner.cli.api import files, lines
from anki_miner.models.word import TokenizedWord
from anki_miner.services.subtitle_parser import SubtitleParserService

ENTRIES = [
    (12.48, 14.90, "約束したでしょう"),
    (15.02, 17.60, "今日こそは言うよ"),
    (17.64, 20.10, "ちゃんと"),
    (30.0, 32.0, "約束だよ"),
]


def _word(front: str, line: int, *, lemma: str | None = None, entries=ENTRIES, **kw) -> TokenizedWord:
    start, end, text = entries[line]
    return TokenizedWord(
        surface=front,
        lemma=lemma or front,
        reading="",
        sentence=text,
        start_time=start,
        end_time=end,
        duration=end - start,
        mined_form_override=front,
        **kw,
    )


def test_cue_index_needs_matching_text() -> None:
    assert lines.cue_index(ENTRIES, _word("約束", 3)) == 3
    assert lines.cue_index(ENTRIES, replace(_word("約束", 3), sentence="other")) is None


R = files.WordRequest
MERGES = [(0, 0), (0, 1), (1, 0), (0, 0)]
KEYS = {"word", "mined_form", "status", "note_id", "media_missing", "line_start", "sentence", "start", "end", "filter"}


def _on_lines(front: str, *on: int, lemma: str | None = None, entries=ENTRIES) -> TokenizedWord:
    """A word on its first line with a leaf variant per line, its own included, as attach_sentence_candidates builds."""
    word = _word(front, on[0], lemma=lemma, entries=entries)
    if len(on) > 1:
        word.sentence_candidates = [_word(front, n, lemma=lemma, entries=entries) for n in on]
    return word


def _selection(requests, *, entries=ENTRIES, raw=ENTRIES, merges=MERGES, budget=30.0) -> lines.WordSelection:
    return lines.WordSelection(requests, entries, raw, merges, budget)


def test_word_keeps_its_own_line_with_that_lines_merge() -> None:
    selection = _selection([R("今日")])
    [chosen] = selection([_word("今日", 1)])
    assert chosen.line_expansion == (0, 1)
    [row] = selection.report(lines.Fates(created={"今日": 7}))
    assert row == {
        "word": "今日",
        "mined_form": "今日",
        "status": "created",
        "note_id": 7,
        "media_missing": [],
        "line_start": 15.02,
        "sentence": "今日こそは言うよ ちゃんと",
        "start": 15.02,
        "end": 20.10,
        "filter": None,
    }


def test_line_start_takes_the_nearest_of_the_words_lines() -> None:
    selection = _selection([R("約束", line_start=29.0)])
    [chosen] = selection([_on_lines("約束", 0, 3)])
    assert chosen.start_time == 30.0 and chosen.sentence == "約束だよ"


def test_line_start_tie_goes_to_the_earlier_line() -> None:
    entries = [(10.0, 11.0, "約束だ"), (20.0, 21.0, "約束よ")]
    selection = _selection([R("約束", line_start=15.0)], entries=entries, raw=entries, merges=[(0, 0)] * 2)
    [chosen] = selection([_on_lines("約束", 0, 1, entries=entries)])
    assert chosen.start_time == 10.0


def test_line_start_is_compared_in_file_time_not_after_the_offset() -> None:
    # offset -16 clamps lines 0 and 1 to 0.0: only the times the file holds tell them apart
    shifted = [(max(0.0, s - 16.0), max(0.0, s - 16.0, e - 16.0), t) for s, e, t in ENTRIES]
    selection = _selection([R("約束", line_start=15.02)], entries=shifted, raw=ENTRIES)
    [chosen] = selection([_on_lines("約束", 0, 1, entries=shifted)])
    assert chosen.sentence == "今日こそは言うよ"
    [row] = selection.report(lines.Fates())
    assert row["line_start"] == 15.02 and row["start"] == 0.0


def test_line_text_takes_the_first_line_containing_it_ignoring_width_and_spaces() -> None:
    entries = [(1.0, 2.0, "約束したでしょう"), (3.0, 4.0, "OK 約束だよ"), (5.0, 6.0, "OK 約束だよ!")]
    selection = _selection([R("約束", line_text="ＯＫ約束")], entries=entries, raw=entries, merges=[(0, 0)] * 3)
    [chosen] = selection([_on_lines("約束", 0, 1, 2, entries=entries)])
    assert chosen.start_time == 3.0


def test_line_text_matching_no_line_keeps_the_words_own_line() -> None:
    selection = _selection([R("約束", line_text="nothing like it")])
    [chosen] = selection([_on_lines("約束", 0, 3)])
    assert chosen.start_time == 12.48


def test_line_start_wins_over_line_text() -> None:
    selection = _selection([R("約束", line_start=12.48, line_text="約束だよ")])
    [chosen] = selection([_on_lines("約束", 0, 3)])
    assert chosen.start_time == 12.48


def test_lemma_fallback_takes_the_word_nearest_line_start() -> None:
    selection = _selection([R("言う", line_start=30.0), R("言う")])
    chosen = selection([_word("いう", 1, lemma="言う"), _word("云う", 3, lemma="言う")])
    assert [w.mined_form for w in chosen] == ["云う", "いう"]  # the second, with nothing to narrow it, takes the first
    rows = selection.report(lines.Fates())
    assert [(r["word"], r["mined_form"]) for r in rows] == [("言う", "云う"), ("言う", "いう")]


def test_card_front_beats_dictionary_form() -> None:
    selection = _selection([R("言う", line_start=30.0)])
    [chosen] = selection([_word("言う", 1), _word("いう", 3, lemma="言う")])
    assert chosen.mined_form == "言う" and chosen.start_time == 15.02


def test_word_is_compared_after_nfc() -> None:
    decomposed, composed = chr(0x304B) + chr(0x3099), chr(0x304C)  # か + combining dakuten, and が
    assert decomposed != composed and unicodedata.normalize("NFC", decomposed) == composed
    selection = _selection([R(decomposed)])
    assert [w.mined_form for w in selection([_word(composed, 1)])] == [composed]


def test_a_second_request_for_the_same_word_is_duplicate() -> None:
    selection = _selection([R("今日"), R("今日", line_start=15.02)])
    assert len(selection([_word("今日", 1)])) == 1
    first, second = selection.report(lines.Fates(created={"今日": 7}))
    assert first["status"] == "created"
    assert (second["mined_form"], second["status"], second["note_id"], second["line_start"]) == (
        "今日",
        "duplicate",
        None,
        None,
    )


def test_explicit_expansion_stops_at_the_file_ends_and_the_budget() -> None:
    wide = _selection([R("今日", line_expansion=(5, 5))])
    assert wide([_word("今日", 1)])[0].line_expansion == (1, 2)  # all four lines: 12.48..32.0 fits 30 s
    tight = _selection([R("今日", line_expansion=(5, 5))], budget=6.0)
    assert tight([_word("今日", 1)])[0].line_expansion == (0, 1)  # after first: 15.02..20.10; one more breaks 6 s
    none = _selection([R("今日", line_expansion=(0, 0))])
    assert none([_word("今日", 1)])[0].line_expansion == (0, 0)


def test_report_statuses() -> None:
    selection = _selection([R("約束"), R("今日"), R("別"), R("学校"), R("走る"), R("無い")])
    selection([_word("約束", 0), _word("今日", 1), _word("別", 2), _word("学校", 3)])
    rows = selection.report(
        lines.Fates(
            created={"約束": 11},
            not_created={"今日": "duplicate"},
            dropped={"別": "media_failed"},
            rejected=[_word("走る", 3)],
            media_missing={"別": ["picture", "audio"], "学校": ["picture", "audio"]},
        )
    )
    assert [(r["mined_form"], r["status"]) for r in rows] == [
        ("約束", "created"),
        ("今日", "duplicate"),
        ("別", "media_failed"),
        ("学校", "not_attempted"),
        ("走る", "no_definition"),
        (None, "not_found"),
    ]
    assert rows[0]["note_id"] == 11 and rows[1]["note_id"] is None
    assert rows[2]["media_missing"] == ["picture", "audio"]
    assert rows[3]["media_missing"] == []  # nothing failed for a word the run never reached
    assert all(set(r) == KEYS for r in rows)


def test_a_run_that_stopped_before_curation_attempted_nothing() -> None:
    selection = _selection([R("約束"), R("走る")])
    rows = selection.report(lines.Fates(rejected=[_word("走る", 3)], stopped=True))
    assert [r["status"] for r in rows] == ["not_attempted", "no_definition"]


def test_a_run_that_finished_without_curation_found_nothing() -> None:
    # e.g. the subtitle yields no words, or none survives phase 2 (no named word was produced): re-mining will not help
    selection = _selection([R("約束")])
    [row] = selection.report(lines.Fates(stopped=False))
    assert row["status"] == "not_found"


def test_a_word_off_every_line_keeps_its_own_sentence_and_times() -> None:
    word = replace(_word("今日", 1), sentence="not in the file")
    selection = _selection([R("今日", line_start=15.02)])
    [chosen] = selection([word])
    assert chosen.sentence == "not in the file"
    [row] = selection.report(lines.Fates())
    assert row["line_start"] is None and row["sentence"] == "not in the file" and row["start"] == 15.02


@pytest.mark.parametrize(
    "raw", ["（男性）約束だよ", "約束(やくそく)だよ", "{\\an8}約束だよ", "約束\\Nだよ", "<i>約束だよ</i>"]
)
def test_line_text_is_cleaned_the_way_the_lines_were(test_config, raw) -> None:
    # a whole line as the subtitle file holds it: speaker tag, furigana, override tag, hard break, markup
    clean = SubtitleParserService(test_config)._clean_line_text
    entries = [(1.0, 2.0, "約束したでしょう"), (3.0, 4.0, clean(raw))]
    selection = lines.WordSelection([R("約束", line_text=raw)], entries, entries, [(0, 0)] * 2, 30.0, clean=clean)
    [chosen] = selection([_on_lines("約束", 0, 1, entries=entries)])
    assert chosen.start_time == 3.0


def test_line_text_that_cleans_to_nothing_is_matched_as_written() -> None:
    selection = lines.WordSelection(
        [R("約束", line_text="(やくそく)")], ENTRIES, ENTRIES, MERGES, 30.0, clean=lambda text: ""
    )
    [chosen] = selection([_on_lines("約束", 3, 0)])
    assert chosen.start_time == 30.0  # its own line, not the first line an empty needle would match


def test_a_word_is_matched_through_the_languages_fold_when_neither_form_equals_it() -> None:
    fold = {"头发": "头发", "頭髮": "头发"}.get
    selection = lines.WordSelection([R("头发")], ENTRIES, ENTRIES, MERGES, 30.0, fold=lambda s: fold(s, s))
    [chosen] = selection([_word("頭髮", 1)])
    assert chosen.mined_form == "頭髮"  # the card keeps the subtitle's spelling


def test_a_merged_word_is_not_found_with_the_merge_named() -> None:
    selection = _selection([R("わかる")])
    selection([_word("分かる", 1)])
    [row] = selection.report(lines.Fates(collapsed=[(_word("わかる", 3, lemma="分かる"), "分かる")]))
    assert (row["status"], row["mined_form"], row["filter"]) == ("not_found", "分かる", "duplicate-expression")


def test_fronts_that_fold_together_are_one_word_while_the_merge_is_on() -> None:
    selection = lines.WordSelection([R("May"), R("may")], ENTRIES, ENTRIES, MERGES, 30.0, fold=str.casefold)
    assert [w.mined_form for w in selection([_word("May", 1), _word("may", 3)])] == ["May"]
    assert [r["status"] for r in selection.report(lines.Fates())] == ["not_attempted", "duplicate"]


def test_with_duplicate_cards_allowed_fronts_that_fold_together_stay_apart() -> None:
    # phase 2 keeps fold-equal fronts apart then, so each named one is its own word
    selection = lines.WordSelection(
        [R("May"), R("may")], ENTRIES, ENTRIES, MERGES, 30.0, fold=str.casefold, allow_duplicates=True
    )
    assert [w.mined_form for w in selection([_word("May", 1), _word("may", 3)])] == ["May", "may"]

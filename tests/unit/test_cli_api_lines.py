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
KEYS = {
    "word",
    "mined_form",
    "status",
    "note_id",
    "from_line",
    "media_missing",
    "line_start",
    "sentence",
    "start",
    "end",
    "filter",
}


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
        "from_line": False,
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


def test_the_front_beats_the_dictionary_form_on_the_named_line() -> None:
    selection = _selection([R("言う", line_start=30.0)])
    [chosen] = selection([_word("言う", 3), _word("いう", 3, lemma="言う")])
    assert chosen.mined_form == "言う"


def test_the_named_line_beats_the_front_on_another_line() -> None:
    selection = _selection([R("言う", line_start=30.0)])
    [chosen] = selection([_word("言う", 1), _word("いう", 3, lemma="言う")])
    assert chosen.mined_form == "いう" and chosen.start_time == 30.0


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
    selection = _selection([R("今日")])
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


def _line_words(*, tokens=(), readings=None, removed=()) -> lines.LineWords:
    """LineWords over fakes: the parse yields the tokens spelling a text; word_on_line moves a word onto the line."""

    def word_on_line(word, line, span, *, reading=None):
        start, end, text = line
        moved = replace(
            word,
            sentence=text,
            start_time=start,
            end_time=end,
            duration=end - start,
            surface=text[span[0] : span[1]],
            surface_start=span[0],
            surface_end=span[1],
            sentence_candidates=[],
            line_expansion=(0, 0),
        )
        return replace(moved, reading=reading, expression_reading=reading) if reading else moved

    return lines.LineWords(
        parse_line=lambda text: [
            replace(t, surface_start=0, surface_end=len(text)) for t in tokens if t.surface == text
        ],
        word_on_line=word_on_line,
        with_reading=lambda word, reading: replace(word, reading=reading, expression_reading=reading),
        readings=lambda terms: {t: (readings or {}).get(t, []) for t in terms},
        removed=lambda name: name in removed,
    )


def _made(requests, words, *, entries=ENTRIES, **line_words) -> tuple[lines.WordSelection, list[TokenizedWord]]:
    selection = lines.WordSelection(
        requests, entries, entries, [(0, 0)] * len(entries), 30.0, line_words=_line_words(**line_words)
    )
    return selection, selection(words)


def test_a_word_the_episode_never_produced_is_made_from_its_line() -> None:
    selection, [chosen] = _made([R("言う", line_start=15.02)], [], readings={"言う": ["いう"]})
    assert (chosen.mined_form, chosen.sentence, chosen.start_time) == ("言う", "今日こそは言うよ", 15.02)
    assert (chosen.surface_start, chosen.surface_end, chosen.reading) == (5, 7, "いう")
    [row] = selection.report(lines.Fates(created={"言う": 9}))
    assert (row["status"], row["from_line"], row["line_start"], row["note_id"]) == ("created", True, 15.02, 9)


def test_a_word_produced_only_on_other_lines_is_made_from_the_named_line_from_that_word() -> None:
    entries = [(1.0, 2.0, "約束だ"), (3.0, 4.0, "約束事が多い")]
    produced = replace(_word("約束", 0, entries=entries), reading="やくそく")
    _selection, [chosen] = _made([R("約束", line_start=3.0)], [produced], entries=entries)
    assert (chosen.sentence, chosen.reading, chosen.surface_start) == ("約束事が多い", "やくそく", 0)


def test_surface_and_reading_name_the_written_form() -> None:
    entries = [(5.0, 6.0, "急に走り出した")]
    request = R("走り出す", line_start=5.0, surface="走り出した", reading="はしりだす")
    _selection, [chosen] = _made([request], [], entries=entries)
    assert (chosen.mined_form, chosen.surface, chosen.reading) == ("走り出す", "走り出した", "はしりだす")


def test_the_parsed_token_is_the_template_when_one_token_spells_the_match() -> None:
    entries = [(5.0, 6.0, "おかわりください")]
    token = TokenizedWord(
        surface="おかわり",
        lemma="お代わり",
        reading="おかわり",
        sentence="おかわり",
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        mined_form_override="おかわり",
    )
    _selection, [chosen] = _made([R("おかわり", line_start=5.0)], [], entries=entries, tokens=[token])
    assert (chosen.lemma, chosen.mined_form) == ("お代わり", "おかわり")


def test_a_word_not_on_the_named_line_is_not_found() -> None:
    selection, chosen = _made([R("約束", line_start=15.02)], [_on_lines("約束", 0, 3)])
    assert chosen == []
    [row] = selection.report(lines.Fates())
    assert (row["status"], row["from_line"]) == ("not_found", False)


def test_the_first_match_on_the_line_is_used_even_inside_a_longer_word() -> None:
    """Review Focus 5: the caller named this line; its own reading of it decides."""
    entries = [(1.0, 2.0, "本屋の本")]
    _selection, [chosen] = _made([R("本", line_start=1.0)], [], entries=entries)
    assert (chosen.surface_start, chosen.surface_end) == (0, 1)


def test_a_word_the_dictionary_check_removed_is_not_made_from_its_line() -> None:
    entries = [(1.0, 2.0, "走るよ")]  # the line holds the word: only `removed` can stop it being made
    selection, chosen = _made([R("走る", line_start=1.0)], [], entries=entries, removed={"走る"})
    assert chosen == []
    [row] = selection.report(lines.Fates(rejected=[_word("走る", 0, entries=entries)]))
    assert (row["status"], row["from_line"]) == ("no_definition", False)


def test_a_fold_match_made_from_its_line_keeps_the_subtitles_spelling() -> None:
    """Z-12 with Z-2: 头发 named; 頭髮 produced on another line and written on the named one."""
    entries = [(1.0, 2.0, "頭髮很長"), (3.0, 4.0, "他的頭髮")]
    fold = {"头发": "头发", "頭髮": "头发"}
    selection = lines.WordSelection(
        [R("头发", line_start=3.0)],
        entries,
        entries,
        [(0, 0)] * 2,
        30.0,
        fold=lambda s: fold.get(s, s),
        line_words=_line_words(),
    )
    [chosen] = selection([replace(_word("頭髮", 0, entries=entries), surface_start=0, surface_end=2)])
    assert (chosen.mined_form, chosen.sentence, chosen.surface_start) == ("頭髮", "他的頭髮", 2)


def test_a_fold_match_through_the_episodes_surface_keeps_its_card_front() -> None:
    """An inflected surface is never a card front: Häuser on the named line still makes a Haus card."""
    entries = [(1.0, 2.0, "Das Haus"), (3.0, 4.0, "Die Häuser")]
    produced = replace(_word("Haus", 0, entries=entries), surface="Häuser")
    selection = lines.WordSelection(
        [R("haus", line_start=3.0)], entries, entries, [(0, 0)] * 2, 30.0, fold=str.casefold, line_words=_line_words()
    )
    [chosen] = selection([produced])
    assert (chosen.mined_form, chosen.sentence) == ("Haus", "Die Häuser")


def test_a_reading_also_applies_to_a_word_the_parse_produced_on_the_line() -> None:
    selection = lines.WordSelection(
        [R("今日", line_start=15.02, reading="こんにち")], ENTRIES, ENTRIES, MERGES, 30.0, line_words=_line_words()
    )
    [chosen] = selection([_word("今日", 1)])
    assert chosen.reading == "こんにち"


def test_fronts_that_fold_together_make_one_card() -> None:
    """Review Focus 1: two notes with one first field would fail Anki's whole batch."""
    entries = [(1.0, 2.0, "Essen ist gut"), (3.0, 4.0, "wir essen")]
    selection = lines.WordSelection(
        [R("Essen", line_start=1.0), R("essen")],
        entries,
        entries,
        [(0, 0)] * 2,
        30.0,
        fold=str.casefold,
        line_words=_line_words(),
    )
    chosen = selection([_word("essen", 1, entries=entries)])
    assert [w.mined_form for w in chosen] == ["Essen"]
    first, second = selection.report(lines.Fates())
    assert first["from_line"] is True and second["status"] == "duplicate"


def test_a_subtitle_without_lines_names_no_line() -> None:
    selection = lines.WordSelection([R("約束", line_start=1.0)], [], [], [], 30.0, line_words=_line_words())
    [chosen] = selection([replace(_word("約束", 0), sentence="off the file")])
    assert chosen.sentence == "off the file"


def test_find_folded_maps_back_to_the_lines_own_offsets() -> None:
    assert lines.find_folded("ＯＫ 約束だよ", "約束") == (3, 5)
    assert lines.find_folded("約 束だよ", "約束") == (0, 3)
    assert lines.find_folded("約束だよ", "走る") is None

"""--api lines: candidates.json and the prepare/commit curation callbacks."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.cli.api import lines
from anki_miner.cli.api.files import WordPick
from anki_miner.models.word import TokenizedWord

ENTRIES = [
    (12.48, 14.90, "約束したでしょう"),
    (15.02, 17.60, "今日こそは言うよ"),
    (17.64, 20.10, "ちゃんと"),
    (30.0, 32.0, "約束だよ"),
]


def _word(front: str, line: int, **kw) -> TokenizedWord:
    start, end, text = ENTRIES[line]
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="",
        sentence=text,
        start_time=start,
        end_time=end,
        duration=end - start,
        mined_form_override=front,
        **kw,
    )


def _with_variants(front: str, line: int, other: int) -> TokenizedWord:
    word = _word(front, line)
    word.sentence_candidates = [
        replace(_word(front, line), sentence_candidates=[]),
        replace(_word(front, other), sentence_candidates=[]),
    ]
    return word


def test_cue_index_needs_matching_text() -> None:
    assert lines.cue_index(ENTRIES, _word("約束", 3)) == 3
    assert lines.cue_index(ENTRIES, replace(_word("約束", 3), sentence="other")) is None


def test_candidates_file_shape() -> None:
    merges = [(0, 0), (0, 1), (1, 0), (0, 0)]
    doc = lines.candidates_file("ep", ENTRIES, merges, [_with_variants("約束", 0, 3), _word("今日", 1)])
    assert doc["schema"] == 1 and doc["run_id"] == "ep" and doc["dropped"] is None
    assert doc["lines"][1] == [1, 15.02, 17.60, "今日こそは言うよ", [0, 1]]
    first = doc["candidates"][0]
    assert first == {
        "mined_form": "約束",
        "lemma": "約束",
        "orth_base": "",
        "surface": "約束",
        "expression_reading": "",
        "line": 0,
        "sentence_candidates": [0, 3],
    }
    assert doc["candidates"][1]["sentence_candidates"] == [1]  # always includes its own line


def test_capture_returns_nothing_to_mine() -> None:
    capture = lines.CandidateCapture()
    words = [_word("今日", 1)]
    assert capture(words) == [] and capture.words == words
    assert capture.suppress_curation_messages is True


def test_validate_picks() -> None:
    cands = [{"mined_form": "約束", "sentence_candidates": [0, 3]}]
    assert lines.validate_picks([WordPick("約束", 3)], cands) is None
    assert "line 2" in lines.validate_picks([WordPick("約束", 2)], cands)
    assert "negative" in lines.validate_picks([WordPick("約束", None, (-1, 0))], cands)
    assert lines.validate_picks([WordPick("unknown", 9)], cands) is None  # becomes not_found


def test_selection_moves_word_to_chosen_line_with_its_merge() -> None:
    merges = [(0, 0), (0, 1), (1, 0), (0, 0)]
    selection = lines.PickSelection([WordPick("約束", 3), WordPick("今日"), WordPick("無い")], ENTRIES, merges)
    chosen = selection([_with_variants("約束", 0, 3), _word("今日", 1), _word("別", 2)])
    assert [w.mined_form for w in chosen] == ["約束", "今日"]
    assert chosen[0].start_time == 30.0 and chosen[0].line_expansion == (0, 0)
    assert chosen[1].line_expansion == (0, 1)  # the default line keeps its automatic merge
    report = selection.report({"今日": 1727000000001}, {"約束": ["picture", "audio"], "今日": ["picture"]})
    assert report[0] == {
        "mined_form": "約束",
        "status": "not_created",
        "note_id": None,
        "media_missing": ["picture", "audio"],
        "line_range": [3, 3],
        "sentence": "約束だよ",
        "start": 30.0,
        "end": 32.0,
    }
    assert report[1]["status"] == "created" and report[1]["note_id"] == 1727000000001
    assert report[1]["media_missing"] == ["picture"]
    assert report[1]["line_range"] == [1, 2] and report[1]["sentence"] == "今日こそは言うよ ちゃんと"
    assert report[2] == {
        "mined_form": "無い",
        "status": "not_found",
        "note_id": None,
        "media_missing": [],
        "line_range": None,
        "sentence": None,
        "start": None,
        "end": None,
    }


def test_selection_explicit_zero_expansion_means_no_merge() -> None:
    merges = [(0, 0), (0, 1), (1, 0), (0, 0)]
    selection = lines.PickSelection([WordPick("今日", None, (0, 0))], ENTRIES, merges)
    [word] = selection([_word("今日", 1)])
    assert word.line_expansion == (0, 0)

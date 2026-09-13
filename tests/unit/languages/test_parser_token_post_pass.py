"""The parser post-pass seam (§4.3 item 2(b), R36 three-argument shape)."""

from __future__ import annotations

from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingUnit

LINE = "cats sat"


def _attest_nothing(surfaces: list[str]) -> set[str]:
    return set()


def test_omitted_post_pass_leaves_the_tagger_output_untouched(make_eu_parser):
    parser = make_eu_parser()
    expected = [(token.surface, token.feature.lemma) for token in parser.tagger(LINE)]

    state = parser._build_line_state(LINE, 0.0, 1.0)

    assert parser._token_post_pass is None
    assert [(token.surface, token.feature.lemma) for token in state[1]] == expected


def test_post_pass_output_is_what_the_line_mines(make_eu_parser):
    calls: list[tuple[int, object, object]] = []

    def post_pass(tokens, attest, forms):
        calls.append((len(tokens), attest, forms))
        return [LanguageToken(t.surface, t.feature.pos1, lemma="x" + t.feature.lemma) for t in tokens]

    parser = make_eu_parser(token_post_pass=post_pass)
    words, _index, counts = parser.parse_text_units(
        [ReadingUnit(text=LINE, index=0, location_label="p.1")], want_line_index=False
    )

    assert {word.lemma for word in words} == {"xcats", "xsat"}
    assert set(counts) == {"xcats", "xsat"}
    assert calls == [(2, None, None)]


def test_post_pass_receives_the_memoised_attestation_probe(make_eu_parser):
    seen: list[object] = []

    def post_pass(tokens, attest, forms):
        seen.append(attest)
        assert forms is None
        return tokens

    parser = make_eu_parser(token_post_pass=post_pass, term_lookup=_attest_nothing)
    parser._build_line_state(LINE, 0.0, 1.0)

    assert seen == [parser._attest] and parser._attest is not None

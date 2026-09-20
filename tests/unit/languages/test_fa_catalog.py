"""The recommended Persian resources and the frequency-direction probe terms (spec C.2).

Both probe lists were measured against the real ``hermitdave/FrequencyWords`` ``content/2018/fa/
fa_50k.txt`` while this task was written; the ranks are in the comments beside them. No test
downloads that file: ``network`` is NOT excluded by ``scripts/health.sh``'s marker string, so a
downloading test would make the Definition-of-Done gate depend on GitHub being up (the id/tr
precedent -- ``test_id_frequency_probe.py`` pins measured ranks instead).

Every Persian character is a \\N{NAME} escape (LEAD-BRIEF section 3); the readable spellings are in
the comments.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from anki_miner.languages.fa.catalog import FA_CATALOG
from anki_miner.languages.fa.script import fa_fold
from anki_miner.services.frequency import mode_probe, source_importer
from anki_miner.services.resource_catalog import RESOURCE_KINDS

_ALEF = "\N{ARABIC LETTER ALEF}"
_ALEF_MADDA = "\N{ARABIC LETTER ALEF WITH MADDA ABOVE}"
_BEH = "\N{ARABIC LETTER BEH}"
_PEH = "\N{ARABIC LETTER PEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_JEEM = "\N{ARABIC LETTER JEEM}"
_TCHEH = "\N{ARABIC LETTER TCHEH}"
_KHAH = "\N{ARABIC LETTER KHAH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_ZAIN = "\N{ARABIC LETTER ZAIN}"
_SEEN = "\N{ARABIC LETTER SEEN}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_AIN = "\N{ARABIC LETTER AIN}"
_FEH = "\N{ARABIC LETTER FEH}"
_QAF = "\N{ARABIC LETTER QAF}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_GAF = "\N{ARABIC LETTER GAF}"
_LAM = "\N{ARABIC LETTER LAM}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"

#: fa_50k ranks 2-115: va, dar, be, az, ke, in, ra, ba, ast, baraye.
COMMON = [
    _WAW,  # 6
    _DAL + _REH,  # 14
    _BEH + _HEH,  # 5
    _ALEF + _ZAIN,  # 8
    _KEHEH + _HEH,  # 2
    _ALEF + _YEH + _NOON,  # 29
    _REH + _ALEF,  # 98
    _BEH + _ALEF,  # 13
    _ALEF + _SEEN + _TEH,  # 115
    _BEH + _REH + _ALEF + _YEH,  # 95
]

#: fa_50k ranks 3,079-14,102: fanus, senjab, qayeq, atashfeshan, docharxe,
#: kabutar, malax, nardeban, chakosh, parvane. The spec's docharxe-savari
#: ("cycling") replaces none of these -- it is ABSENT from fa_50k and it carries
#: a ZWNJ, which fa_fold strips, so the probe term and the index key would
#: differ. The bare noun docharxe ("bicycle") is the same concept, present, and
#: fold-stable.
RARE = [
    _FEH + _ALEF + _NOON + _WAW + _SEEN,  # 8,920
    _SEEN + _NOON + _JEEM + _ALEF + _BEH,  # 8,667
    _QAF + _ALEF + _YEH + _QAF,  # 3,079
    _ALEF_MADDA + _TEH + _SHEEN + _FEH + _SHEEN + _ALEF + _NOON,  # 12,114
    _DAL + _WAW + _TCHEH + _REH + _KHAH + _HEH,  # 3,353
    _KEHEH + _BEH + _WAW + _TEH + _REH,  # 9,901
    _MEEM + _LAM + _KHAH,  # 14,102
    _NOON + _REH + _DAL + _BEH + _ALEF + _NOON,  # 13,090
    _TCHEH + _KEHEH + _SHEEN,  # 4,662
    _PEH + _REH + _WAW + _ALEF + _NOON + _HEH,  # 4,542
]


class TestCatalog:
    def test_two_rows_a_dictionary_and_a_frequency_list(self):
        assert [(spec.id, spec.kind) for spec in FA_CATALOG] == [
            ("wty-fa-en", "dict"),
            ("opensubtitles-fa", "freq"),
        ]
        assert {spec.kind for spec in FA_CATALOG} <= RESOURCE_KINDS
        assert len({spec.id for spec in FA_CATALOG}) == len(FA_CATALOG)

    def test_the_frequency_list_is_lemmatised_in_app(self):
        # R21: hermitdave lists are surface-keyed word counts, and Persian verbs
        # spread a lemma over hundreds of rows.
        freq = next(spec for spec in FA_CATALOG if spec.kind == "freq")
        assert freq.lemmatise is True
        assert next(spec for spec in FA_CATALOG if spec.kind == "dict").lemmatise is False

    def test_every_url_is_https_on_a_host_the_project_already_uses(self):
        hosts = {urlsplit(spec.url).netloc for spec in FA_CATALOG}
        assert hosts <= {"huggingface.co", "raw.githubusercontent.com"}
        assert all(urlsplit(spec.url).scheme == "https" for spec in FA_CATALOG)

    def test_every_row_carries_its_licence_line(self):
        for spec in FA_CATALOG:
            assert "CC BY-SA 4.0" in spec.license_note, spec.id
            assert spec.display_name and spec.variant == ""


class TestProbeTerms:
    def test_the_persian_tables(self):
        assert mode_probe.MORE_COMMON_TERMS["fa"] == COMMON
        assert mode_probe.LESS_COMMON_TERMS["fa"] == RARE
        assert len(set(COMMON)) == len(set(RARE)) == 10
        assert not set(COMMON) & set(RARE)

    def test_every_probe_term_survives_the_fold(self):
        # The probe looks a term up in an index whose keys are folded, so a term
        # the fold rewrites could never vote.
        for term in COMMON + RARE:
            assert fa_fold(term) == term, term

    def test_an_occurrence_list_is_detected_from_persian_terms(self):
        counts = {(term, None): 2_000_000 - index for index, term in enumerate(COMMON)}
        counts.update({(term, None): 10 + index for index, term in enumerate(RARE)})
        _rows, converted = source_importer._iter_rank_rows(counts, "", "fa")
        assert converted is True

    def test_a_rank_list_is_detected_from_persian_terms(self):
        ranks = {(term, None): 1 + index for index, term in enumerate(COMMON)}
        ranks.update({(term, None): 30_000 + index for index, term in enumerate(RARE)})
        _rows, converted = source_importer._iter_rank_rows(ranks, "", "fa")
        assert converted is False

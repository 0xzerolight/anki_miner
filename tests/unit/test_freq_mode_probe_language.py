"""The direction probe uses the language the import stamps, not always ja."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

# Mining languages that plausibly gain a probe table later; the ones still
# without one are the real-code cases ruling R6a is about. "vi" is never a
# mining language, so this list can never filter down to empty.
_CANDIDATE_LANGUAGES = ("ko", "zh", "vi")


def _ranks() -> dict[tuple[str, str | None], int]:
    return {("AAA", None): 900, ("BBB", None): 5}


def _ja_shaped_ranks() -> dict[tuple[str, str | None], int]:
    """Real ja probe terms in occurrence shape (common term holds the big number)."""
    return {
        (mode_probe.MORE_COMMON_TERMS["ja"][0], None): 900,
        (mode_probe.LESS_COMMON_TERMS["ja"][0], None): 5,
    }


def test_probe_terms_follow_the_source_language(monkeypatch):
    monkeypatch.setitem(mode_probe.MORE_COMMON_TERMS, "zz", ["AAA"])
    monkeypatch.setitem(mode_probe.LESS_COMMON_TERMS, "zz", ["BBB"])

    _rows, converted = source_importer._iter_rank_rows(_ranks(), "", "zz")
    assert converted is True  # AAA (900) > BBB (5) => occurrence-based


def test_declared_mode_still_wins(monkeypatch):
    monkeypatch.setitem(mode_probe.MORE_COMMON_TERMS, "zz", ["AAA"])
    monkeypatch.setitem(mode_probe.LESS_COMMON_TERMS, "zz", ["BBB"])
    _rows, converted = source_importer._iter_rank_rows(_ranks(), mode_probe.RANK_BASED, "zz")
    assert converted is False


def test_another_languages_terms_do_not_decide(monkeypatch):
    # The pooled probe set was the bug: a zz-only term must not steer a ja source.
    monkeypatch.setitem(mode_probe.MORE_COMMON_TERMS, "zz", ["AAA"])
    monkeypatch.setitem(mode_probe.LESS_COMMON_TERMS, "zz", ["BBB"])
    _rows, converted = source_importer._iter_rank_rows(_ranks(), "", "ja")
    assert converted is False  # no ja probe term present => tie => rank-based


def test_default_language_is_ja():
    _rows, converted = source_importer._iter_rank_rows(_ranks(), "")
    assert converted is False


def test_import_forwards_its_stamped_language(tmp_path, monkeypatch):
    seen: list[str] = []
    real = mode_probe.resolve_is_occurrence

    def _spy(declared_mode, term_values, source_language="ja"):
        seen.append(source_language)
        return real(declared_mode, term_values, source_language)

    monkeypatch.setattr(mode_probe, "resolve_is_occurrence", _spy)
    csv_path = tmp_path / "list.csv"
    csv_path.write_text("AAA,1\nBBB,2\n", encoding="utf-8")
    source_importer.import_frequency_source(csv_path, tmp_path / "out", language="ko")
    assert seen == ["ko"]


def test_language_with_no_probe_table_is_not_steered_by_ja() -> None:
    """Ruling R6a: an absent language probes NEUTRAL, never with pooled ja terms.

    Uses the real tables (no monkeypatch): ja is currently the only language with
    a probe list, so pooling would hand ko/zh sources the Japanese vote.
    """
    absent = [code for code in _CANDIDATE_LANGUAGES if code not in mode_probe.MORE_COMMON_TERMS]
    assert absent, "at least one candidate language must still lack a probe table"
    for code in absent:
        _rows, converted = source_importer._iter_rank_rows(_ja_shaped_ranks(), "", code)
        assert converted is False, f"{code} was steered by ja probe terms"


def test_the_same_rows_still_steer_a_ja_source() -> None:
    """Control for the test above: the fixture really does vote occurrence for ja."""
    _rows, converted = source_importer._iter_rank_rows(_ja_shaped_ranks(), "", "ja")
    assert converted is True

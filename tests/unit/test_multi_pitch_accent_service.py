"""Tests for the first-hit-wins multi-source pitch chain.

Core semantics: chain order IS priority; later sources only fill words earlier
sources miss. Includes the accepted-shadowing pin (tier-2 single-candidate
cross-source shadowing) so the behavior stays a documented decision, not an
accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.multi_pitch_service import MultiPitchAccentService
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent_service import (
    PitchMapsStore,
    build_pitch_maps,
    iter_pitch_csv_rows,
)


def _provider(tmp_path: Path, source_id: str, rows: list[storage.PitchStorageRow]) -> IndexedPitchProvider:
    db = tmp_path / source_id / "index.sqlite"
    storage.build_index(
        db,
        rows,
        {
            "schema_version": str(storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": source_id,
            "source_revision": "",
            "import_date": "2026-01-01T00:00:00+00:00",
            "entry_count": str(len(rows)),
        },
    )
    provider = IndexedPitchProvider(source_id, db, source_id)
    assert provider.load() is True
    return provider


@pytest.fixture
def chain(tmp_path: Path) -> MultiPitchAccentService:
    top = _provider(
        tmp_path,
        "top",
        [
            ("たべる", "食べる", "2", "", ""),
            ("はじく", "弾く", "2", "", ""),  # 弾く under ONE reading only
        ],
    )
    bottom = _provider(
        tmp_path,
        "bottom",
        [
            ("たべる", "食べる", "0", "", ""),  # overlaps top — must lose
            ("ひく", "弾く", "0", "", ""),
            ("がっこう", "学校", "0", "", ""),  # only in bottom — must fill
        ],
    )
    return MultiPitchAccentService([top, bottom])


class TestFirstHitWins:
    def test_top_source_wins_on_overlap(self, chain: MultiPitchAccentService) -> None:
        entry = chain.lookup_entry("食べる", "たべる")
        assert entry is not None and entry.pattern == "2"

    def test_later_source_fills_earlier_miss(self, chain: MultiPitchAccentService) -> None:
        entry = chain.lookup_entry("学校", "がっこう")
        assert entry is not None and entry.pattern == "0"

    def test_no_source_has_word(self, chain: MultiPitchAccentService) -> None:
        assert chain.lookup_entry("存在しない", "そんざいしない") is None

    def test_reorder_flips_winner(self, tmp_path: Path) -> None:
        a = _provider(tmp_path, "a", [("ねこ", "猫", "1", "", "")])
        b = _provider(tmp_path, "b", [("ねこ", "猫", "0", "", "")])
        assert MultiPitchAccentService([a, b]).lookup_entry("猫", "ねこ").pattern == "1"
        assert MultiPitchAccentService([b, a]).lookup_entry("猫", "ねこ").pattern == "0"

    def test_tier2_cross_source_shadowing_is_accepted(self, chain: MultiPitchAccentService) -> None:
        """ACCEPTED tradeoff (see MultiPitchAccentService docstring): the top
        source holds 弾く under はじく only, so its single-candidate tier-2
        fallback answers a ひく query and the bottom source's exact
        (弾く, ひく) is never consulted. Pinned so a future change here is a
        deliberate decision, not an accident."""
        entry = chain.lookup_entry("弾く", "ひく")
        assert entry is not None and entry.pattern == "2"  # top's はじく entry

    def test_cross_headword_reading_match_cannot_shadow_lower_exact_pair(self, tmp_path: Path) -> None:
        top = _provider(tmp_path, "top", [("かいじゅ", "槐樹", "1", "", "")])
        bottom = _provider(tmp_path, "bottom", [("かいじゅ", "解呪", "2", "", "")])

        entry = MultiPitchAccentService([top, bottom]).lookup_entry("解呪", "かいじゅ")

        assert entry is not None and entry.pattern == "2"


class TestAvailabilityAndCounts:
    def test_is_available_any_provider(self, chain: MultiPitchAccentService) -> None:
        assert chain.is_available() is True

    def test_empty_chain_unavailable(self) -> None:
        svc = MultiPitchAccentService([])
        assert svc.is_available() is False
        assert svc.lookup_entry("猫", "ねこ") is None
        assert svc.entry_count == 0

    def test_entry_count_sums_providers(self, chain: MultiPitchAccentService) -> None:
        assert chain.entry_count == 5

    def test_close_is_noop(self, chain: MultiPitchAccentService) -> None:
        chain.close()
        assert chain.lookup_entry("食べる", "たべる") is not None


class TestSingleSourceParity:
    """A one-provider chain behaves identically to the flat CSV store."""

    def test_lookup_parity_with_csv_store(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "ひく,弾く,0\nはじく,弾く,2\nねこ,猫,1\nありがとう,,2\n",
            encoding="utf-8",
        )
        flat = PitchMapsStore()
        flat._set_maps(build_pitch_maps(iter_pitch_csv_rows(csv_file)))

        rows = [
            ("ひく", "弾く", "0", "", ""),
            ("はじく", "弾く", "2", "", ""),
            ("ねこ", "猫", "1", "", ""),
            ("ありがとう", "", "2", "", ""),
        ]
        chain = MultiPitchAccentService([_provider(tmp_path, "only", rows)])

        cases = [
            ("弾く", "ひく"),
            ("弾く", "はじく"),
            ("弾く", ""),  # homograph without reading — refuse to guess
            ("弾く", "みすまっち"),  # multi-reading + no exact → refuse-to-guess tiers
            ("猫", "ねこ"),
            ("ねこ", ""),  # no cross-headword reading-only lookup
            ("ありがとう", "ありがとう"),
            ("未知", "みち"),
        ]
        for word, reading in cases:
            flat_entry = flat.lookup_entry(word, reading)
            chain_entry = chain.lookup_entry(word, reading)
            assert (flat_entry is None) == (chain_entry is None), (word, reading)
            if flat_entry is not None:
                assert flat_entry.pattern == chain_entry.pattern, (word, reading)

    def test_batch_detailed_parity(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,2\nねこ,猫,1\n", encoding="utf-8")
        flat = PitchMapsStore()
        flat._set_maps(build_pitch_maps(iter_pitch_csv_rows(csv_file)))
        chain = MultiPitchAccentService(
            [_provider(tmp_path, "only", [("たべる", "食べる", "2", "", ""), ("ねこ", "猫", "1", "", "")])]
        )
        batch = [("食べる", "たべる", "動詞"), ("猫", "ねこ", "名詞"), ("無い", "ない", None)]
        assert flat.lookup_batch_detailed(batch) == chain.lookup_batch_detailed(batch)
        assert flat.lookup_batch_detailed(batch, fmt="romaji") == chain.lookup_batch_detailed(batch, fmt="romaji")


class TestDisabledSkipViaRegistry:
    def test_registry_build_sources_skips_disabled(self, tmp_path: Path) -> None:
        from dataclasses import replace

        from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
        from anki_miner.services.pitch_accent.registry import PitchSourceRegistry

        _provider(tmp_path, "top", [("ねこ", "猫", "1", "", "")])
        _provider(tmp_path, "bottom", [("ねこ", "猫", "0", "", "")])
        cfg = replace(
            AnkiMinerConfig(),
            pitch_root=tmp_path,
            pitch_chain=(
                PitchSourceEntry("top", enabled=False),
                PitchSourceEntry("bottom"),
            ),
        )
        registry = PitchSourceRegistry(tmp_path)
        registry.load()
        providers = [p for p in registry.build_sources(cfg) if p.load()]
        assert [p.source_id for p in providers] == ["bottom"]
        assert MultiPitchAccentService(providers).lookup_entry("猫", "ねこ").pattern == "0"

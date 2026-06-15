"""Tests for the recommended-resource catalog (pure data)."""

import dataclasses

import pytest

from anki_miner.services.resource_catalog import (
    RECOMMENDED_DEFAULT_SET,
    RESOURCE_KINDS,
    ResourceSpec,
)


class TestRecommendedDefaultSet:
    def test_has_exactly_three_entries(self):
        assert len(RECOMMENDED_DEFAULT_SET) == 3

    def test_ids_are_unique(self):
        ids = [spec.id for spec in RECOMMENDED_DEFAULT_SET]
        assert len(ids) == len(set(ids))

    def test_kinds_within_allowed_set(self):
        for spec in RECOMMENDED_DEFAULT_SET:
            assert spec.kind in RESOURCE_KINDS

    def test_allowed_kinds_value(self):
        assert frozenset({"dict", "freq", "pitch"}) == RESOURCE_KINDS

    def test_jitendex_entry(self):
        spec = _by_id("jitendex")
        assert spec.kind == "dict"
        assert spec.display_name == "Jitendex"
        assert spec.url == (
            "https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip"
        )
        assert spec.license_note

    def test_jpdb_freq_entry(self):
        spec = _by_id("jpdb-freq")
        assert spec.kind == "freq"
        assert spec.display_name == "JPDB v2.2 Kana Frequency"
        assert spec.url == (
            "https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/"
            "JPDB_v2.2_Frequency_Kana_2024-10-13.zip"
        )
        assert spec.license_note

    def test_kanjium_pitch_entry(self):
        spec = _by_id("kanjium-pitch")
        assert spec.kind == "pitch"
        assert spec.display_name == "Kanjium Pitch Accent"
        assert spec.url == (
            "https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt"
        )
        assert spec.license_note


class TestResourceSpec:
    def test_is_frozen(self):
        spec = RECOMMENDED_DEFAULT_SET[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.id = "mutated"  # type: ignore[misc]

    def test_fields_present(self):
        field_names = {f.name for f in dataclasses.fields(ResourceSpec)}
        assert field_names == {"id", "kind", "display_name", "url", "license_note"}


def _by_id(spec_id: str) -> ResourceSpec:
    for spec in RECOMMENDED_DEFAULT_SET:
        if spec.id == spec_id:
            return spec
    raise AssertionError(f"no spec with id {spec_id!r}")

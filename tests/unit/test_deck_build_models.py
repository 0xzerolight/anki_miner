"""Tests for deck-builder data models."""

from dataclasses import FrozenInstanceError

import pytest

from anki_miner.models.deck_build import (
    DeckBuildPreview,
    DeckBuildRequest,
    DeckCorpus,
    DeckSelectionMode,
)


class TestDeckSelectionMode:
    """Tests for DeckSelectionMode enum."""

    def test_all_member_exists_with_string_value(self):
        assert DeckSelectionMode.ALL.value == "all"

    def test_top_n_member_exists_with_string_value(self):
        assert DeckSelectionMode.TOP_N.value == "top_n"

    def test_coverage_pct_member_exists_with_string_value(self):
        assert DeckSelectionMode.COVERAGE_PCT.value == "coverage_pct"

    def test_all_members_are_present(self):
        members = {m.value for m in DeckSelectionMode}
        assert members == {"all", "top_n", "coverage_pct"}


class TestDeckBuildRequest:
    """Tests for DeckBuildRequest dataclass."""

    def test_basic_construction(self, tmp_path):
        request = DeckBuildRequest(
            video_folder=tmp_path / "video",
            subtitle_folder=tmp_path / "subs",
            deck_name="My Deck",
            skip_known=False,
            review=True,
        )
        assert request.video_folder == tmp_path / "video"
        assert request.subtitle_folder == tmp_path / "subs"
        assert request.deck_name == "My Deck"
        assert request.skip_known is False
        assert request.review is True

    def test_defaults(self, tmp_path):
        request = DeckBuildRequest(
            video_folder=tmp_path / "video",
            subtitle_folder=tmp_path / "subs",
            deck_name="Defaults",
            skip_known=False,
            review=False,
        )
        assert request.subtitle_offset == 0.0
        assert request.secondary_folder is None
        assert request.secondary_offset == 0.0

    def test_is_frozen(self, tmp_path):
        request = DeckBuildRequest(
            video_folder=tmp_path / "video",
            subtitle_folder=tmp_path / "subs",
            deck_name="Test",
            skip_known=False,
            review=False,
        )
        with pytest.raises(FrozenInstanceError):
            request.deck_name = "Changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            request.skip_known = True  # type: ignore[misc]

    def test_secondary_folder_and_offsets(self, tmp_path):
        request = DeckBuildRequest(
            video_folder=tmp_path / "video",
            subtitle_folder=tmp_path / "subs",
            deck_name="Dual",
            skip_known=True,
            review=False,
            subtitle_offset=0.5,
            secondary_folder=tmp_path / "subs2",
            secondary_offset=-0.25,
        )
        assert request.subtitle_offset == 0.5
        assert request.secondary_folder == tmp_path / "subs2"
        assert request.secondary_offset == -0.25


class TestDeckCorpus:
    """Tests for DeckCorpus dataclass."""

    def test_basic_construction(self):
        corpus = DeckCorpus(
            counts={"a": 5, "b": 3},
            row_lemmas=(frozenset({"a"}), frozenset({"b"})),
            episodes=2,
        )
        assert corpus.counts == {"a": 5, "b": 3}
        assert corpus.row_lemmas == (frozenset({"a"}), frozenset({"b"}))
        assert corpus.episodes == 2

    def test_is_frozen(self):
        corpus = DeckCorpus(counts={}, row_lemmas=(), episodes=0)
        with pytest.raises(FrozenInstanceError):
            corpus.episodes = 1  # type: ignore[misc]

    def test_empty_corpus(self):
        corpus = DeckCorpus(counts={}, row_lemmas=(), episodes=0)
        assert corpus.counts == {}
        assert corpus.row_lemmas == ()
        assert corpus.episodes == 0


class TestDeckBuildPreview:
    """Tests for DeckBuildPreview dataclass."""

    def test_basic_construction(self):
        preview = DeckBuildPreview(
            total_tokens=10000,
            unique_lemmas=2500,
            candidate_count=500,
            projected_coverage_pct=75.5,
            known_skipped=50,
            card_count=450,
        )
        assert preview.total_tokens == 10000
        assert preview.unique_lemmas == 2500
        assert preview.candidate_count == 500
        assert preview.projected_coverage_pct == 75.5
        assert preview.known_skipped == 50
        assert preview.card_count == 450

    def test_is_frozen(self):
        preview = DeckBuildPreview(
            total_tokens=1000,
            unique_lemmas=200,
            candidate_count=100,
            projected_coverage_pct=80.0,
            known_skipped=10,
            card_count=90,
        )
        with pytest.raises(FrozenInstanceError):
            preview.total_tokens = 2000  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            preview.card_count = 95  # type: ignore[misc]

    def test_zero_values(self):
        preview = DeckBuildPreview(0, 0, 0, 0.0, 0, 0)
        assert preview.total_tokens == 0
        assert preview.card_count == 0

    def test_fields_round_trip(self):
        data = {
            "total_tokens": 5000,
            "unique_lemmas": 1000,
            "candidate_count": 250,
            "projected_coverage_pct": 65.5,
            "known_skipped": 25,
            "card_count": 225,
        }
        preview = DeckBuildPreview(**data)
        assert preview.total_tokens == data["total_tokens"]
        assert preview.unique_lemmas == data["unique_lemmas"]
        assert preview.candidate_count == data["candidate_count"]
        assert preview.projected_coverage_pct == data["projected_coverage_pct"]
        assert preview.known_skipped == data["known_skipped"]
        assert preview.card_count == data["card_count"]

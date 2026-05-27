"""Tests for deck-builder data models."""

from dataclasses import FrozenInstanceError

import pytest

from anki_miner.models.deck_build import (
    DeckBuildPreview,
    DeckBuildRequest,
    DeckSelectionMode,
)
from anki_miner.utils.file_pairing import FilePair


class TestDeckSelectionMode:
    """Tests for DeckSelectionMode enum."""

    def test_all_member_exists_with_string_value(self):
        """Test that ALL member exists with expected string value."""
        assert DeckSelectionMode.ALL.value == "all"

    def test_top_n_member_exists_with_string_value(self):
        """Test that TOP_N member exists with expected string value."""
        assert DeckSelectionMode.TOP_N.value == "top_n"

    def test_coverage_pct_member_exists_with_string_value(self):
        """Test that COVERAGE_PCT member exists with expected string value."""
        assert DeckSelectionMode.COVERAGE_PCT.value == "coverage_pct"

    def test_all_members_are_present(self):
        """Test that all expected members are present."""
        members = {m.value for m in DeckSelectionMode}
        assert members == {"all", "top_n", "coverage_pct"}


class TestDeckBuildRequest:
    """Tests for DeckBuildRequest dataclass."""

    def _make_file_pair(self, tmp_path):
        """Create a test FilePair with dummy Path objects."""
        video = tmp_path / "episode_01.mp4"
        subtitle = tmp_path / "episode_01.ass"
        return FilePair(video=video, subtitle=subtitle)

    def test_basic_construction(self, tmp_path):
        """Test basic construction with all fields."""
        pair = self._make_file_pair(tmp_path)
        request = DeckBuildRequest(
            pairs=[pair],
            deck_name="My Deck",
            mode=DeckSelectionMode.ALL,
            value=0.0,
            collection_filter=False,
        )
        assert request.pairs == [pair]
        assert request.deck_name == "My Deck"
        assert request.mode == DeckSelectionMode.ALL
        assert request.value == 0.0
        assert request.collection_filter is False

    def test_multiple_pairs(self, tmp_path):
        """Test construction with multiple file pairs."""
        pair1 = FilePair(video=tmp_path / "ep01.mp4", subtitle=tmp_path / "ep01.ass")
        pair2 = FilePair(video=tmp_path / "ep02.mp4", subtitle=tmp_path / "ep02.ass")
        request = DeckBuildRequest(
            pairs=[pair1, pair2],
            deck_name="Series",
            mode=DeckSelectionMode.TOP_N,
            value=500.0,
            collection_filter=True,
        )
        assert len(request.pairs) == 2
        assert request.pairs[0] == pair1
        assert request.pairs[1] == pair2

    def test_is_frozen(self, tmp_path):
        """Test that request is frozen and cannot be mutated."""
        pair = self._make_file_pair(tmp_path)
        request = DeckBuildRequest(
            pairs=[pair],
            deck_name="Test",
            mode=DeckSelectionMode.ALL,
            value=0.0,
            collection_filter=False,
        )
        with pytest.raises(FrozenInstanceError):
            request.deck_name = "Changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            request.mode = DeckSelectionMode.TOP_N  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            request.collection_filter = True  # type: ignore[misc]

    def test_top_n_mode_with_value(self, tmp_path):
        """Test TOP_N mode with numeric value."""
        pair = self._make_file_pair(tmp_path)
        request = DeckBuildRequest(
            pairs=[pair],
            deck_name="Top 1000",
            mode=DeckSelectionMode.TOP_N,
            value=1000.0,
            collection_filter=False,
        )
        assert request.mode == DeckSelectionMode.TOP_N
        assert request.value == 1000.0

    def test_coverage_pct_mode_with_value(self, tmp_path):
        """Test COVERAGE_PCT mode with percentage value."""
        pair = self._make_file_pair(tmp_path)
        request = DeckBuildRequest(
            pairs=[pair],
            deck_name="80% Coverage",
            mode=DeckSelectionMode.COVERAGE_PCT,
            value=80.0,
            collection_filter=False,
        )
        assert request.mode == DeckSelectionMode.COVERAGE_PCT
        assert request.value == 80.0

    def test_all_mode_ignores_value(self, tmp_path):
        """Test that ALL mode still accepts value field (even if ignored)."""
        pair = self._make_file_pair(tmp_path)
        request = DeckBuildRequest(
            pairs=[pair],
            deck_name="Everything",
            mode=DeckSelectionMode.ALL,
            value=999.0,  # Ignored for ALL mode
            collection_filter=False,
        )
        assert request.mode == DeckSelectionMode.ALL


class TestDeckBuildPreview:
    """Tests for DeckBuildPreview dataclass."""

    def test_basic_construction(self):
        """Test basic construction with all fields."""
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
        """Test that preview is frozen and cannot be mutated."""
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
        with pytest.raises(FrozenInstanceError):
            preview.projected_coverage_pct = 85.0  # type: ignore[misc]

    def test_zero_values(self):
        """Test preview with zero values."""
        preview = DeckBuildPreview(
            total_tokens=0,
            unique_lemmas=0,
            candidate_count=0,
            projected_coverage_pct=0.0,
            known_skipped=0,
            card_count=0,
        )
        assert preview.total_tokens == 0
        assert preview.card_count == 0

    def test_large_values(self):
        """Test preview with large values."""
        preview = DeckBuildPreview(
            total_tokens=1000000,
            unique_lemmas=50000,
            candidate_count=10000,
            projected_coverage_pct=99.9,
            known_skipped=5000,
            card_count=5000,
        )
        assert preview.total_tokens == 1000000
        assert preview.unique_lemmas == 50000
        assert preview.candidate_count == 10000
        assert preview.projected_coverage_pct == 99.9

    def test_fields_round_trip(self):
        """Test that fields maintain their values."""
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

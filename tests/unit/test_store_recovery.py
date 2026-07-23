from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.store_recovery import (
    RecoveryArtifact,
    decide_slot_recovery,
    make_tombstone_path,
)


def _artifact(
    name: str,
    *,
    kind: str = "backup",
    generation: int,
    valid: bool = True,
    owned: bool = True,
) -> RecoveryArtifact:
    return RecoveryArtifact(
        path=Path(name),
        kind=kind,
        generation=generation,
        valid=valid,
        owned=owned,
    )


@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("canonical", ["absent", "invalid", "valid"])
def test_recovery_matrix_covers_every_canonical_and_listing_state(
    canonical: str,
    listed: bool,
) -> None:
    older = _artifact("slot.bak-10-a", generation=10)
    newer = _artifact("slot.tomb-20-b", kind="tombstone", generation=20)

    decision = decide_slot_recovery(
        canonical=canonical,
        listed=listed,
        candidates=(older, newer),
    )

    if canonical == "valid" or not listed:
        assert decision.restore is None
        assert decision.quarantine_canonical is False
        assert decision.collect == (newer, older)
    else:
        assert decision.restore is newer
        assert decision.quarantine_canonical is (canonical == "invalid")
        assert decision.collect == (older,)


def test_newest_valid_owned_candidate_wins_across_backup_and_tombstone() -> None:
    newer_tombstone = _artifact(
        "slot.tomb-30-new",
        kind="tombstone",
        generation=30,
    )
    decision = decide_slot_recovery(
        canonical="absent",
        listed=True,
        candidates=(
            _artifact("slot.bak-20-old", generation=20),
            newer_tombstone,
            _artifact("slot.bak-40-invalid", generation=40, valid=False),
            _artifact("slot.bak-50-unowned", generation=50, owned=False),
        ),
    )

    assert decision.restore is newer_tombstone
    assert tuple(item.path.name for item in decision.collect) == (
        "slot.bak-40-invalid",
        "slot.bak-20-old",
    )


def test_newer_tombstone_beats_older_tombstone() -> None:
    newer = _artifact(
        "slot.tomb-200-new",
        kind="tombstone",
        generation=200,
    )
    decision = decide_slot_recovery(
        canonical="absent",
        listed=True,
        candidates=(
            _artifact("slot.tomb-100-old", kind="tombstone", generation=100),
            newer,
        ),
    )

    assert decision.restore is newer
    assert tuple(item.path.name for item in decision.collect) == ("slot.tomb-100-old",)


def test_unlisted_slot_never_restores_mixed_residue() -> None:
    decision = decide_slot_recovery(
        canonical="absent",
        listed=False,
        candidates=(
            _artifact("slot.bak-20", generation=20),
            _artifact("slot.tomb-30", kind="tombstone", generation=30),
        ),
    )

    assert decision.restore is None
    assert tuple(item.path.name for item in decision.collect) == (
        "slot.tomb-30",
        "slot.bak-20",
    )


def test_unowned_candidates_are_never_restored_or_collected() -> None:
    unowned = _artifact("slot.tomb-30", kind="tombstone", generation=30, owned=False)

    decision = decide_slot_recovery(
        canonical="absent",
        listed=True,
        candidates=(unowned,),
    )

    assert decision.restore is None
    assert decision.collect == ()
    assert decision.quarantine_canonical is False


def test_tombstone_name_matches_generation_format() -> None:
    canonical = Path("/managed/slot")

    tombstone = make_tombstone_path(canonical, generation=123, nonce="abc")

    assert tombstone == Path("/managed/slot.tomb-123-abc")

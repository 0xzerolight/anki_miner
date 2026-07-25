"""Pure per-slot recovery decisions shared by remove and startup repair."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

CanonicalState: TypeAlias = Literal["absent", "invalid", "valid"]
ArtifactKind: TypeAlias = Literal["backup", "tombstone", "quarantine"]


@dataclass(frozen=True)
class RecoveryArtifact:
    """One owned-or-ambiguous recovery candidate beside a canonical slot."""

    path: Path
    kind: ArtifactKind
    generation: int
    valid: bool
    owned: bool


@dataclass(frozen=True)
class RecoveryDecision:
    """Filesystem-neutral actions for one canonical slot and its residue."""

    restore: RecoveryArtifact | None
    collect: tuple[RecoveryArtifact, ...]
    quarantine_canonical: bool


def decide_slot_recovery(
    *,
    canonical: CanonicalState,
    listed: bool,
    candidates: tuple[RecoveryArtifact, ...],
) -> RecoveryDecision:
    """Choose restore/collect actions under the unified J29 matrix."""
    owned = tuple(
        sorted(
            (candidate for candidate in candidates if candidate.owned),
            key=lambda candidate: (candidate.generation, candidate.path.name),
            reverse=True,
        )
    )
    if canonical == "valid" or not listed:
        return RecoveryDecision(
            restore=None,
            collect=owned,
            quarantine_canonical=False,
        )

    restore = next(
        (
            candidate
            for candidate in owned
            if candidate.valid or (canonical == "absent" and candidate.kind == "quarantine")
        ),
        None,
    )
    return RecoveryDecision(
        restore=restore,
        collect=tuple(candidate for candidate in owned if candidate is not restore),
        quarantine_canonical=canonical == "invalid" and restore is not None,
    )


def make_tombstone_path(
    canonical: Path,
    *,
    generation: int | None = None,
    nonce: str | None = None,
) -> Path:
    """Return a unique ``<slot>.tomb-<generation>-<nonce>`` sibling."""
    while True:
        value = generation if generation is not None else time.time_ns()
        suffix = nonce if nonce is not None else uuid.uuid4().hex
        tombstone = canonical.with_name(f"{canonical.name}.tomb-{value}-{suffix}")
        if generation is not None or nonce is not None or not os.path.lexists(tombstone):
            return tombstone

"""One lock-gated startup recovery and garbage-collection pass."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.services._sqlite_index import (
    StoreFamily,
    is_generated_store_artifact,
    prove_owned_generation,
    prove_owned_slot,
    read_ownership_marker,
    validate_index_schema,
    validate_index_schema_cached,
    validate_store_id,
)
from anki_miner.services.store_recovery import (
    ArtifactKind,
    CanonicalState,
    RecoveryArtifact,
    decide_slot_recovery,
)
from anki_miner.utils.robust_fs import robust_rmtree

logger = logging.getLogger(__name__)

_DELETE_RETRY_BUDGET_S = 2.0
_STAGING_MIN_AGE_NS = 24 * 60 * 60 * 1_000_000_000
_RETAINED_TOMBSTONE_MARKER = ".anki-miner-retained"
_RETAINED_DELETION_PREFIX = ".anki-miner-retained-deletion-"


@dataclass(frozen=True)
class _FamilySpec:
    root: Path
    family: StoreFamily
    listed_ids: frozenset[str]


class _DeleteBudget:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._deadline = clock() + _DELETE_RETRY_BUDGET_S

    def delete(self, path: Path, action: str) -> bool:
        remaining = self._deadline - self._clock()
        if remaining <= 0.0:
            logger.warning("Startup store recovery retained %s; cleanup budget exhausted", path)
            return False
        deleted, error = robust_rmtree(
            path,
            mode="outcome",
            deadline_s=remaining,
            clock=self._clock,
        )
        if deleted:
            logger.info("Startup store recovery %s: %s", action, path)
        else:
            logger.warning(
                "Startup store recovery failed to %s %s: %s",
                action,
                path,
                error,
            )
        return deleted


def _family_specs(config: AnkiMinerConfig) -> tuple[_FamilySpec, ...]:
    return (
        _FamilySpec(
            config.dicts_root,
            "dictionary",
            frozenset(
                entry.dict_id
                for entry in config.dictionary_chain
                if entry.kind == "indexed" and entry.dict_id is not None
            ),
        ),
        _FamilySpec(
            config.freqs_root,
            "frequency",
            frozenset(entry.source_id for entry in config.frequency_chain),
        ),
        _FamilySpec(
            config.audio_packs_root,
            "audio",
            frozenset(
                entry.pack_id
                for entry in config.expression_audio_chain
                if entry.kind == "pack" and entry.pack_id is not None
            ),
        ),
        _FamilySpec(
            config.pitch_root,
            "pitch",
            frozenset(entry.source_id for entry in config.pitch_chain),
        ),
    )


def _recovery_artifact_name(
    name: str,
    listed_ids: frozenset[str],
) -> tuple[str, ArtifactKind] | None:
    markers: tuple[tuple[str, ArtifactKind], ...] = (
        (".bak-", "backup"),
        (".tomb-", "tombstone"),
        (".corrupt-", "quarantine"),
    )
    for marker, kind in markers:
        slot_id, found, suffix = name.rpartition(marker)
        if (
            not found
            or not slot_id
            or not suffix
            or (is_generated_store_artifact(slot_id) and slot_id not in listed_ids)
        ):
            continue
        try:
            validate_store_id(slot_id)
        except ValueError:
            continue
        return slot_id, kind
    return None


def _is_staging_name(name: str) -> bool:
    return name.startswith(".staging-") and len(name) > len(".staging-")


def _unique_quarantine_path(canonical: Path) -> Path:
    while True:
        quarantine = canonical.with_name(f"{canonical.name}.corrupt-{time.time_ns()}-{uuid.uuid4().hex}")
        if not os.path.lexists(quarantine):
            return quarantine


def _candidate(
    root: Path,
    slot_id: str,
    family: StoreFamily,
    path: Path,
    kind: ArtifactKind,
) -> RecoveryArtifact:
    owned = prove_owned_generation(root, slot_id, family, path)
    valid = owned and validate_index_schema(path / "index.sqlite", family)
    marker = {
        "backup": ".bak-",
        "tombstone": ".tomb-",
        "quarantine": ".corrupt-",
    }[kind]
    timestamp_text = path.name.rpartition(marker)[2].partition("-")[0]
    if timestamp_text.isdigit():
        generation = int(timestamp_text)
    else:
        try:
            generation = path.stat().st_mtime_ns
        except OSError:
            generation = 0
    if not owned:
        logger.warning("Startup store recovery retained unowned artifact: %s", path)
    return RecoveryArtifact(
        path=path,
        kind=kind,
        generation=generation,
        valid=valid,
        owned=owned,
    )


def _collect_artifact(
    spec: _FamilySpec,
    slot_id: str,
    artifact: RecoveryArtifact,
    budget: _DeleteBudget,
) -> None:
    if not prove_owned_generation(spec.root, slot_id, spec.family, artifact.path):
        logger.warning("Startup store recovery retained artifact whose ownership changed: %s", artifact.path)
        return
    budget.delete(artifact.path, f"removed obsolete {artifact.kind}")


def _retained_deletion_marker(spec: _FamilySpec, slot_id: str) -> Path:
    identity = f"{spec.family}\0{slot_id}".encode()
    return spec.root / f"{_RETAINED_DELETION_PREFIX}{hashlib.sha256(identity).hexdigest()}"


def backup_config_repair_is_safe(config: AnkiMinerConfig) -> bool:
    """Return whether a recovered backup may become the primary config."""
    for spec in _family_specs(config):
        try:
            if not spec.root.is_dir():
                continue
            children = {child.name: child for child in spec.root.iterdir()}
        except OSError:
            logger.warning(
                "Config backup repair deferred because the %s root could not be checked: %s",
                spec.family,
                spec.root,
                exc_info=True,
            )
            return False

        for slot_id in spec.listed_ids:
            try:
                validate_store_id(slot_id)
            except ValueError:
                continue
            canonical = spec.root / slot_id
            if validate_index_schema(canonical / "index.sqlite", spec.family) and prove_owned_slot(
                spec.root,
                slot_id,
                spec.family,
            ):
                continue
            deletion_marker = _retained_deletion_marker(spec, slot_id)
            if deletion_marker.is_file():
                continue
            tombstones = tuple(
                child
                for name, child in children.items()
                if (parsed := _recovery_artifact_name(name, spec.listed_ids)) is not None
                if parsed == (slot_id, "tombstone")
            )
            if tombstones and not any((tombstone / _RETAINED_TOMBSTONE_MARKER).is_file() for tombstone in tombstones):
                logger.warning(
                    "Config backup repair deferred until deletion intent is durable: %s/%s",
                    spec.family,
                    slot_id,
                )
                return False
    return True


def _recover_slot(
    spec: _FamilySpec,
    slot_id: str,
    children: dict[str, Path],
    budget: _DeleteBudget,
    *,
    allow_collection: bool,
) -> None:
    canonical = spec.root / slot_id
    if not os.path.lexists(canonical):
        canonical_state: CanonicalState = "absent"
    # The one check that runs for every configured slot on a healthy boot, and
    # the only one here worth serving from the meta sidecar: it runs before
    # compose_main_window, so its SQLite opens are paid before the first paint.
    # The sidecar returns the verdict validate_index_schema would, or defers to
    # it — the recovery decision below is identical either way. The recovery
    # artifacts scanned further down stay on the full check: they exist only on
    # an already-unhealthy slot, where the boot cost is not what matters.
    elif validate_index_schema_cached(canonical / "index.sqlite", spec.family) and prove_owned_slot(
        spec.root,
        slot_id,
        spec.family,
    ):
        canonical_state = "valid"
    else:
        canonical_state = "invalid"

    candidates = tuple(
        _candidate(spec.root, slot_id, spec.family, child, kind)
        for name, child in children.items()
        if name not in spec.listed_ids
        if (parsed := _recovery_artifact_name(name, spec.listed_ids)) is not None
        for candidate_slot, kind in (parsed,)
        if candidate_slot == slot_id
    )
    deletion_marker = _retained_deletion_marker(spec, slot_id)
    eligible_candidates = candidates
    if canonical_state != "valid" and slot_id in spec.listed_ids:
        retained_deletion = deletion_marker.is_file() or any(
            candidate.kind == "tombstone" and (candidate.path / _RETAINED_TOMBSTONE_MARKER).is_file()
            for candidate in candidates
        )
        if retained_deletion:
            eligible_candidates = ()
        elif not allow_collection and any(candidate.kind == "tombstone" for candidate in candidates):
            try:
                deletion_marker.touch(exist_ok=True)
            except OSError:
                logger.warning(
                    "Startup store recovery could not retain deletion intent durably: %s",
                    deletion_marker,
                    exc_info=True,
                )
            logger.warning(
                "Startup store recovery retained tombstone because config provenance is not authoritative: %s",
                slot_id,
            )
            return
    decision = decide_slot_recovery(
        canonical=canonical_state,
        listed=slot_id in spec.listed_ids,
        candidates=eligible_candidates,
    )

    quarantine: Path | None = None
    if decision.quarantine_canonical:
        if not prove_owned_slot(spec.root, slot_id, spec.family):
            logger.warning(
                "Startup store recovery retained invalid unowned canonical and its recovery candidates: %s",
                canonical,
            )
            return
        quarantine = _unique_quarantine_path(canonical)
        try:
            os.replace(canonical, quarantine)
        except OSError:
            logger.warning(
                "Startup store recovery failed to quarantine invalid canonical %s",
                canonical,
                exc_info=True,
            )
            return
        logger.info("Startup store recovery quarantined invalid canonical: %s -> %s", canonical, quarantine)

    if decision.restore is not None:
        restore = decision.restore
        try:
            if not prove_owned_generation(spec.root, slot_id, spec.family, restore.path):
                logger.warning(
                    "Startup store recovery retained recovery candidate whose ownership changed: %s", restore.path
                )
                return
            try:
                os.replace(restore.path, canonical)
            except OSError:
                logger.warning(
                    "Startup store recovery failed to restore %s from %s",
                    canonical,
                    restore.path,
                    exc_info=True,
                )
                return
            logger.info("Startup store recovery restored canonical: %s <- %s", canonical, restore.path)
        finally:
            if quarantine is not None and not os.path.lexists(canonical):
                try:
                    os.replace(quarantine, canonical)
                except OSError:
                    logger.warning(
                        "Startup store recovery failed to roll back quarantine %s",
                        quarantine,
                        exc_info=True,
                    )

    if allow_collection:
        for artifact in decision.collect:
            _collect_artifact(spec, slot_id, artifact, budget)
        if deletion_marker.is_file() and not any(
            candidate.kind == "tombstone" and os.path.lexists(candidate.path) for candidate in candidates
        ):
            try:
                deletion_marker.unlink()
            except OSError:
                logger.warning(
                    "Startup store recovery could not clear resolved deletion intent: %s",
                    deletion_marker,
                    exc_info=True,
                )


def _sweep_staging(
    spec: _FamilySpec,
    children: dict[str, Path],
    budget: _DeleteBudget,
    now_ns: int,
) -> None:
    for name, staging in children.items():
        if not _is_staging_name(name):
            continue
        marker = read_ownership_marker(staging)
        if marker is None or marker[0] != spec.family:
            logger.warning("Startup store recovery retained unowned staging: %s", staging)
            continue
        if not prove_owned_generation(spec.root, marker[1], spec.family, staging):
            logger.warning("Startup store recovery retained staging whose ownership changed: %s", staging)
            continue
        try:
            old_enough = now_ns - staging.stat().st_mtime_ns >= _STAGING_MIN_AGE_NS
        except OSError:
            logger.warning("Startup store recovery could not age staging: %s", staging, exc_info=True)
            continue
        if old_enough:
            budget.delete(staging, "removed aged staging")


def _recover_family(
    spec: _FamilySpec,
    budget: _DeleteBudget,
    now_ns: int,
    *,
    allow_collection: bool,
) -> None:
    try:
        if not spec.root.is_dir():
            return
        children = {child.name: child for child in spec.root.iterdir()}
    except OSError:
        logger.warning("Startup store recovery could not scan %s root %s", spec.family, spec.root, exc_info=True)
        return

    slot_ids = set(spec.listed_ids)
    for name in children:
        if name in spec.listed_ids:
            continue
        parsed = _recovery_artifact_name(name, spec.listed_ids)
        if parsed is not None:
            slot_ids.add(parsed[0])
        elif not is_generated_store_artifact(name):
            try:
                validate_store_id(name)
            except ValueError:
                continue
            slot_ids.add(name)

    for slot_id in sorted(slot_ids):
        try:
            validate_store_id(slot_id)
        except ValueError:
            logger.warning("Startup store recovery skipped invalid configured %s id: %r", spec.family, slot_id)
            continue
        _recover_slot(
            spec,
            slot_id,
            children,
            budget,
            allow_collection=allow_collection,
        )
    if allow_collection:
        _sweep_staging(spec, children, budget, now_ns)


def run_startup_store_recovery(
    config: AnkiMinerConfig,
    *,
    allow_collection: bool = True,
    now_ns: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Recover and collect managed SQLite generations once before composition."""
    budget = _DeleteBudget(clock)
    recovery_now_ns = time.time_ns() if now_ns is None else now_ns
    for spec in _family_specs(config):
        try:
            _recover_family(
                spec,
                budget,
                recovery_now_ns,
                allow_collection=allow_collection,
            )
        except Exception:
            logger.exception(
                "Startup store recovery failed for %s root %s",
                spec.family,
                spec.root,
            )

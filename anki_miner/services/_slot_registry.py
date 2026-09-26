"""Chain policy shared by the four indexed-resource registries.

Dictionaries, frequency sources, pitch sources and audio packs each scan
``<root>/<slot_id>/index.sqlite`` folders in their own ``load()``. What they
then answer about their config chain, and the gates their chain build applies
(:meth:`IndexedSlotRegistry._walk_enabled`), is one policy, kept here so the
families cannot drift apart. ``__init__`` does no I/O.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Generic, Protocol, TypeAlias, TypeVar

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps services import-free of gui
    from anki_miner.gui.utils.service_factory import ServiceLoadResult

#: What every ``build_*`` method's ``load_result`` sink is. Duck typed in
#: practice: anything with a ``warnings`` list.
LoadResultSink: TypeAlias = "ServiceLoadResult"


class SlotMeta(Protocol):
    """What the chain policy reads off every family's meta dataclass."""

    @property
    def schema_ok(self) -> bool: ...

    @property
    def entry_count(self) -> int: ...

    @property
    def language(self) -> str: ...


class ChainSlotEntry(Protocol):
    """What the chain policy reads off every family's chain entry."""

    @property
    def enabled(self) -> bool: ...


MetaT = TypeVar("MetaT", bound=SlotMeta)
EntryT = TypeVar("EntryT", bound=ChainSlotEntry)


class IndexedSlotRegistry(ABC, Generic[MetaT, EntryT]):
    """One family's scanned slots plus the chain questions asked of them."""

    #: Opens every chain-build log line ("Dictionary", "Audio pack", ...).
    _noun: ClassVar[str]
    #: The subclass module's logger, so chain-build records keep their name.
    _logger: ClassVar[logging.Logger]
    #: Level of the "referenced in config but not found" line.
    _missing_level: ClassVar[int] = logging.WARNING

    def __init__(self, root: Path) -> None:
        self._root = root
        self._slots: dict[str, MetaT] = {}

    @abstractmethod
    def _chain(self, config: AnkiMinerConfig) -> Sequence[EntryT]:
        """This family's chain in *config*."""

    @abstractmethod
    def _slot_id(self, entry: EntryT) -> str | None:
        """The slot *entry* names; ``None`` for a kind with no slot or a null id."""

    @abstractmethod
    def _meta_id(self, meta: MetaT) -> str:
        """The id *meta* reports for itself: the chain-match and sort key."""

    def _slot_available(self, meta: MetaT) -> bool:
        """Whether what *meta* indexes is reachable; side-effect free.

        Only audio packs keep their files outside the index, so only they can
        lose them (a moved folder, an unplugged drive).
        """
        return True

    @abstractmethod
    def _skipped_language_notice(self, meta: MetaT) -> str:
        """The user warning for a slot indexed for another mining language.

        Each subclass keeps its literal ``QCoreApplication.translate(
        "ResourceChain", ...)`` call so pylupdate6 extracts it unchanged.
        """

    def _stale_detail(self, meta: MetaT) -> str:
        """What the schema-stale log line says is wrong with *meta*."""
        return "wrong schema_version"

    def _extra_gate(self, slot_id: str, meta: MetaT) -> bool:
        """A family gate run after the schema gate; log and return False to skip."""
        return True

    def get(self, slot_id: str) -> MetaT | None:
        return self._slots.get(slot_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[MetaT]:
        """Return on-disk slots not referenced by any chain entry.

        Only slots with schema_ok=True are returned: a stale or future slot
        cannot be loaded and the chain build would drop it anyway. A slot
        referenced by a *disabled* chain entry is still considered listed (it
        has a visible, unchecked row the user can re-enable), so it is excluded
        — unlisted() surfaces only slots with no chain row at all. Results are
        sorted by id for deterministic ordering.

        Does NOT call load(); callers control when the scan happens.
        """
        chained_ids = {slot_id for entry in self._chain(config) if (slot_id := self._slot_id(entry)) is not None}
        return sorted(
            (meta for meta in self._slots.values() if self._meta_id(meta) not in chained_ids and meta.schema_ok),
            key=self._meta_id,
        )

    def stale_enabled(self, config: AnkiMinerConfig) -> list[MetaT]:
        """Enabled chain slots present on disk but schema-mismatched.

        The single source of truth for every reimport surface (settings row
        button, startup prompt, pre-run gate, health check): a slot the user
        upgraded past without reimporting, which the chain build silently
        drops. A slot missing on disk is NOT reported — the user may have
        deleted it deliberately, and there is nothing left to rebuild from. An
        entry of a kind with no slot (jisho, the online audio sources) cannot be
        stale. Sorted by id for deterministic messaging.

        Does NOT call load(); callers control when the scan happens.
        """
        stale: list[MetaT] = []
        for entry in self._chain(config):
            slot_id = self._slot_id(entry)
            if not entry.enabled or not slot_id:
                continue
            meta = self._slots.get(slot_id)
            if meta is not None and not meta.schema_ok:
                stale.append(meta)
        return sorted(stale, key=self._meta_id)

    def usable_enabled(self, config: AnkiMinerConfig) -> list[MetaT]:
        """Enabled chain slots that can actually answer a lookup.

        Read off this snapshot: present on disk, schema-current, holding at
        least one entry, stamped for the run's mining language, and with its
        source reachable (``_slot_available``; audio packs only). For
        dictionaries these are the conditions
        :meth:`DefinitionService.has_usable_offline_provider` applies after
        building and loading the chain. Answering them opens no SQLite
        connection, so a readiness check can call this without file-locking an
        index Reimport All is about to replace (Windows).

        "An ``index.sqlite`` exists" was never the question worth asking: a
        schema-stale index is dropped from the chain, a zero-entry index opens
        perfectly and returns nothing, and a slot indexed for another language
        is skipped. All three mine cards without the resource.

        Does NOT call load(); callers control when the scan happens.
        """
        language = config_language(config)
        usable: list[MetaT] = []
        for entry in self._chain(config):
            slot_id = self._slot_id(entry)
            if not entry.enabled or not slot_id:
                continue
            meta = self._slots.get(slot_id)
            if (
                meta is not None
                and meta.schema_ok
                and meta.entry_count > 0
                and meta.language == language
                and self._slot_available(meta)
            ):
                usable.append(meta)
        return sorted(usable, key=self._meta_id)

    def _walk_enabled(
        self,
        config: AnkiMinerConfig,
        language: str,
        load_result: LoadResultSink | None,
    ) -> Iterator[tuple[EntryT, MetaT | None]]:
        """Walk the enabled chain entries in order through the slot gates.

        Yields ``(entry, meta)`` for each slot that passes every gate, and
        ``(entry, None)`` for each enabled entry that names no slot (another
        kind, or a null id) so the subclass can handle it in chain order. A
        slot that fails a gate is logged and not yielded: missing on disk (at
        ``_missing_level``), schema-stale, refused by ``_extra_gate``, or
        stamped for another mining language, which also appends
        ``_skipped_language_notice`` to *load_result* when one is given.

        Every record here passes ``stacklevel=2``: the log format is
        ``%(name)s:%(lineno)d``, and a generator's caller is the ``build_*``
        that iterates it, so each line points at the registry that built it.
        """
        for entry in self._chain(config):
            if not entry.enabled:
                continue
            slot_id = self._slot_id(entry)
            if slot_id is None:
                yield entry, None
                continue
            meta = self._slots.get(slot_id)
            if meta is None:
                self._logger.log(
                    self._missing_level,
                    "%s '%s' referenced in config but not found in %s",
                    self._noun,
                    slot_id,
                    self._root,
                    stacklevel=2,
                )
                continue
            # A stale index must never reach the runtime chain.
            if not meta.schema_ok:
                self._logger.warning(
                    "%s '%s' has %s; needs reimport",
                    self._noun,
                    slot_id,
                    self._stale_detail(meta),
                    stacklevel=2,
                )
                continue
            if not self._extra_gate(slot_id, meta):
                continue
            if meta.language != language:
                # A ko index answering a zh run returns confident nonsense;
                # skipping is the only safe read of a cross-language slot.
                self._logger.warning(
                    "%s '%s' is indexed for '%s'; skipped for '%s'",
                    self._noun,
                    slot_id,
                    meta.language,
                    language,
                    stacklevel=2,
                )
                if load_result is not None:
                    load_result.warnings.append(self._skipped_language_notice(meta))
                continue
            yield entry, meta

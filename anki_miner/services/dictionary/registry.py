"""Discovery + provider-chain assembly for installed dictionaries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.dictionary_provider import DictionaryProvider
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services._sqlite_index import (
    is_generated_store_artifact,
    meta_language,
    read_ownership_marker,
    scan_index_root,
)
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.providers.jisho_provider import JishoProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps services import-free of gui
    from anki_miner.gui.utils.service_factory import ServiceLoadResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DictMeta:
    dict_id: str
    source_name: str
    format: str
    entry_count: int
    schema_ok: bool
    db_path: Path
    #: Mining language this dictionary was imported for. Absent from every
    #: pre-transition meta.json, hence the tolerant "ja" default.
    language: str = "ja"


class DictionaryRegistry:
    """Scans the dictionaries folder and builds runtime provider chains."""

    def __init__(self, dicts_root: Path):
        self._root = dicts_root
        self._dicts: dict[str, DictMeta] = {}

    def load(self) -> None:
        self._dicts = scan_index_root(
            self._root,
            self._parse_meta,
            child_prefilter=lambda child: (
                not is_generated_store_artifact(child.name)
                or read_ownership_marker(child) == ("dictionary", child.name)
            ),
            warn_label="dictionary",
        )

    def _parse_meta(self, child: Path, db: Path, meta: dict[str, str]) -> DictMeta:
        source_name = meta.get("source_name")
        format_name = meta.get("format")
        raw_version = meta.get("schema_version")
        raw_count = meta.get("entry_count")
        try:
            version = int(raw_version) if isinstance(raw_version, str) else 0
        except (TypeError, ValueError):
            version = 0
        try:
            count = int(raw_count) if isinstance(raw_count, str) else 0
        except (TypeError, ValueError):
            count = 0
        # schema_ok policy: dictionaries require an exact-version match — a
        # mismatch is dropped from the chain and gated for reimport.
        return DictMeta(
            dict_id=child.name,
            source_name=source_name if isinstance(source_name, str) else child.name,
            format=format_name if isinstance(format_name, str) else "unknown",
            entry_count=count,
            schema_ok=(version == SCHEMA_VERSION),
            db_path=db,
            language=meta_language(meta),
        )

    def get(self, dict_id: str) -> DictMeta | None:
        return self._dicts.get(dict_id)

    def unlisted(self, config: AnkiMinerConfig) -> list[DictMeta]:
        """Return on-disk dicts not referenced by any entry in the config chain.

        Only dicts with schema_ok=True are returned — schema-mismatched dicts
        cannot be loaded and would be dropped by build_provider_chain anyway.
        Results are sorted by dict_id for deterministic ordering.

        A dict referenced by a *disabled* chain entry is still considered
        listed (it has a visible, unchecked row the user can re-enable), so it
        is excluded — unlisted() surfaces only dicts with no chain row at all.

        Does NOT call load(); callers control when the scan happens.
        """
        chained_ids: set[str] = {
            entry.dict_id for entry in config.dictionary_chain if entry.kind == "indexed" and entry.dict_id is not None
        }
        return sorted(
            (meta for meta in self._dicts.values() if meta.dict_id not in chained_ids and meta.schema_ok),
            key=lambda m: m.dict_id,
        )

    def stale_enabled(self, config: AnkiMinerConfig) -> list[DictMeta]:
        """Enabled indexed chain slots present on disk but schema-mismatched.

        The migration gate's single source of truth (4.0): iterate the *enabled*
        indexed ``dictionary_chain`` entries and return those whose resolved
        ``DictMeta.schema_ok`` is False — a dict the user upgraded past without
        reimporting, which ``build_provider_chain`` silently drops (reopening the
        zero-definition window this gate exists to close). A slot missing on disk
        (``meta is None``) is a *different* failure handled elsewhere and is not
        reported here. Sorted by dict_id for deterministic messaging.

        Does NOT call load(); callers control when the scan happens.
        """
        stale: list[DictMeta] = []
        for entry in config.dictionary_chain:
            if entry.kind != "indexed" or not entry.enabled or entry.dict_id is None:
                continue
            meta = self._dicts.get(entry.dict_id)
            if meta is not None and not meta.schema_ok:
                stale.append(meta)
        return sorted(stale, key=lambda m: m.dict_id)

    def usable_enabled(self, config: AnkiMinerConfig) -> list[DictMeta]:
        """Enabled indexed chain slots that can actually answer a lookup.

        Three conditions, all read off this snapshot: present on disk,
        schema-current, and holding at least one entry. They are the same three
        :meth:`DefinitionService.has_usable_offline_provider` applies to the
        registry after building and loading the chain — answered here without
        opening a single SQLite connection, which is what makes this callable
        from a readiness check that must not take a file lock on the very
        indexes the user may be about to reimport.

        "An ``index.sqlite`` exists" was never the question worth asking: a
        schema-stale index is dropped from the chain, and a zero-entry index
        opens perfectly and returns nothing. Both mine cards with no definition.

        Does NOT call load(); callers control when the scan happens.
        """
        usable: list[DictMeta] = []
        for entry in config.dictionary_chain:
            if entry.kind != "indexed" or not entry.enabled or entry.dict_id is None:
                continue
            meta = self._dicts.get(entry.dict_id)
            if meta is not None and meta.schema_ok and meta.entry_count > 0:
                usable.append(meta)
        return sorted(usable, key=lambda m: m.dict_id)

    def build_provider_chain(
        self,
        config: AnkiMinerConfig,
        *,
        load_result: ServiceLoadResult | None = None,
    ) -> list[DictionaryProvider]:
        """Build the ordered provider chain from config + disk state.

        Entries with enabled=False are skipped. Indexed entries whose dict_id
        is missing on disk are dropped with a warning. Indexed entries stamped
        for another mining language are dropped the same way. Jisho is included
        if its ChainEntry is enabled. Providers are returned in chain order.

        ``load_result`` is an optional sink for the user-facing warnings (duck
        typed: anything with a ``warnings`` list). ``None`` keeps them in the
        log only.

        Caller is responsible for invoking provider.load() on each.
        """
        language = config_language(config)
        chain: list[DictionaryProvider] = []
        for entry in config.dictionary_chain:
            if not entry.enabled:
                continue
            if entry.kind == "indexed":
                if entry.dict_id is None:
                    logger.warning("Skipping indexed ChainEntry with null dict_id")
                    continue
                meta = self._dicts.get(entry.dict_id)
                if meta is None:
                    # Debug, not warning: this chain is rebuilt on every episode,
                    # and a missing on-disk dict (e.g. the legacy 'jmdict-english'
                    # default slot a user never migrated, or a transiently
                    # unreachable dicts_root) is skip-and-continue by design. A
                    # genuinely empty chain is still surfaced at WARNING by
                    # build_definition_service ("No offline dictionary index").
                    logger.debug(
                        "Dictionary '%s' referenced in config but not found in %s",
                        entry.dict_id,
                        self._root,
                    )
                    continue
                if not meta.schema_ok:
                    logger.warning(
                        "Dictionary '%s' has wrong schema_version; needs reimport",
                        entry.dict_id,
                    )
                    continue
                if meta.language != language:
                    # A ko index answering a zh run returns confident nonsense;
                    # skipping is the only safe read of a cross-language slot.
                    logger.warning(
                        "Dictionary '%s' is indexed for '%s'; skipped for '%s'",
                        entry.dict_id,
                        meta.language,
                        language,
                    )
                    if load_result is not None:
                        load_result.warnings.append(
                            tr_format(
                                QCoreApplication.translate("ResourceChain", "Dictionary '%1' is for %2; skipped"),
                                entry.dict_id,
                                meta.language,
                            )
                        )
                    continue
                chain.append(
                    IndexedDictProvider(
                        dict_id=meta.dict_id,
                        db_path=meta.db_path,
                        display_name=meta.source_name,
                        keys=get_profile(language).dict_keys,
                    )
                )
            elif entry.kind == "jisho":
                chain.append(JishoProvider(config.jisho_api_url, config.jisho_delay))
        return chain


def stale_enabled_dicts(config: AnkiMinerConfig) -> list[DictMeta]:
    """Build+scan a fresh registry and return enabled slots needing reimport.

    Convenience wrapper used by the startup migration prompt and the queue
    workers' pre-loop gate: it builds a :class:`DictionaryRegistry` from
    ``config.dicts_root``, loads it, and delegates to :meth:`stale_enabled`.
    ``load()`` swallows scan OSErrors internally, so this never raises for a
    missing / unreadable dicts folder (it simply reports no staleness).
    """
    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    return registry.stale_enabled(config)


def format_stale_reimport_message(metas: list[DictMeta]) -> str:
    """Actionable one-line error naming the schema-stale dictionaries.

    Points the user at the one-click fix (Settings → Dictionaries → Reimport
    All). Shared by the processor backstop and the queue-worker pre-loop gate so
    every entry point speaks with one voice.
    """
    names = ", ".join(f"'{m.source_name}'" for m in metas)
    verb = "need" if len(metas) != 1 else "needs"
    noun = "Dictionaries" if len(metas) != 1 else "Dictionary"
    return f"{noun} {names} {verb} reimport (schema upgrade) — Settings → Dictionaries → Reimport All"


def stale_dict_reimport_error(config: AnkiMinerConfig) -> str | None:
    """Return the actionable reimport message if any enabled slot is stale.

    ``None`` when the chain is clean. The queue workers call this once before
    their per-item loop so a stale slot aborts the whole run with a single error
    instead of emitting one soft-failure row per queued item.
    """
    stale = stale_enabled_dicts(config)
    if not stale:
        return None
    return format_stale_reimport_message(stale)

"""One pre-run gate for every schema-stale indexed resource.

A schema bump makes existing indexes unusable, and each family's ``build_*``
silently drops the stale slot. For dictionaries that means cards with no
definition; for frequency it means the card loses its rank *and*
``max_frequency_rank`` stops filtering, so the run floods the deck with rare
words; for pitch it means the pitch field goes blank; for audio packs it means
the card falls through to the online sources, or gets no audio at all. All four
are silent, and all four are fixed the same way — reimport, which is the
migration.

This module aggregates the four families' ``stale_enabled_*`` helpers into the
single message the queue workers, the episode processor and the backfill worker
abort with, so a user upgrading past two bumps gets one error naming everything
rather than one error per run.

**Only stale is reported, never absent.** A family with no chain entries, or
none enabled, contributes nothing; so does an enabled entry whose slot is gone
from disk (``meta is None``). Frequency, pitch and audio packs are optional by
design — activation is derived from an enabled source existing — so a user who
never configured them must never be gated. The trigger is narrow on purpose:
the user asked for the source, it is on disk, and an app upgrade broke it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
    from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
    from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
    from anki_miner.services.pitch_accent.registry import PitchSourceMeta, PitchSourceRegistry

#: Family key -> (plural noun, singular noun, Settings path). The Settings path
#: is the one-click fix each family's message points at.
_FAMILY_LABELS: dict[str, tuple[str, str, str]] = {
    "dictionary": ("Dictionaries", "Dictionary", "Settings → Dictionaries → Reimport All"),
    "frequency": ("Frequency sources", "Frequency source", "Settings → Frequency → Reimport All"),
    "pitch": ("Pitch sources", "Pitch source", "Settings → Pitch Accent → Reimport All"),
    "audio": ("Audio packs", "Audio pack", "Settings → Audio → Reimport All"),
}


def format_stale_family_message(family: str, names: list[str]) -> str:
    """Actionable one-line message naming one family's schema-stale sources."""
    plural, singular, fix = _FAMILY_LABELS[family]
    joined = ", ".join(f"'{name}'" for name in names)
    verb = "need" if len(names) != 1 else "needs"
    noun = plural if len(names) != 1 else singular
    return f"{noun} {joined} {verb} reimport (schema upgrade) — {fix}"


def stale_resource_reimport_error(
    config: AnkiMinerConfig,
    *,
    families: frozenset[str] | None = None,
    dictionary_registry: DictionaryRegistry | None = None,
    frequency_registry: FrequencySourceRegistry | None = None,
    pitch_registry: PitchSourceRegistry | None = None,
    audio_registry: AudioPackRegistry | None = None,
) -> str | None:
    """Return the actionable reimport message if any enabled slot is stale.

    ``None`` when nothing is stale, which is also the answer for every family
    the user has not configured. Callers invoke this once before their per-item
    loop so a stale slot aborts the whole run with a single error instead of
    emitting one soft-failure row per queued item.

    Args:
        config: Live config; supplies the chains and the resource roots.
        families: Restrict the check to these family keys (``"dictionary"``,
            ``"frequency"``, ``"pitch"``, ``"audio"``). Used by the backfill
            worker, which gates per requested field — a definition-only
            backfill must not abort over a stale pitch index. ``None`` checks
            all four.
        dictionary_registry: Already-scanned registry to read instead of
            rescanning. The episode processor passes the same handle that built
            its provider chain, so the per-episode gate costs no disk I/O.
        frequency_registry: As above, for frequency.
        pitch_registry: As above, for pitch.
        audio_registry: As above, for audio packs.

    Returns:
        A newline-joined message covering every stale family, or ``None``.
    """
    from anki_miner.services.audio_packs.registry import stale_enabled_audio_packs
    from anki_miner.services.dictionary.registry import stale_enabled_dicts
    from anki_miner.services.frequency.registry import stale_enabled_freq_sources
    from anki_miner.services.pitch_accent.registry import stale_enabled_pitch_sources

    wanted = families if families is not None else frozenset(_FAMILY_LABELS)
    messages: list[str] = []

    if "dictionary" in wanted:
        dict_stale: list[DictMeta] = (
            dictionary_registry.stale_enabled(config)
            if dictionary_registry is not None
            else stale_enabled_dicts(config)
        )
        if dict_stale:
            messages.append(format_stale_family_message("dictionary", [m.source_name for m in dict_stale]))

    if "frequency" in wanted:
        freq_stale: list[FreqSourceMeta] = (
            frequency_registry.stale_enabled(config)
            if frequency_registry is not None
            else stale_enabled_freq_sources(config)
        )
        if freq_stale:
            messages.append(format_stale_family_message("frequency", [m.source_name for m in freq_stale]))

    if "pitch" in wanted:
        pitch_stale: list[PitchSourceMeta] = (
            pitch_registry.stale_enabled(config) if pitch_registry is not None else stale_enabled_pitch_sources(config)
        )
        if pitch_stale:
            messages.append(format_stale_family_message("pitch", [m.source_name for m in pitch_stale]))

    if "audio" in wanted and config.anki_fields.get("expression_audio"):
        # AudioPackMeta's display field is ``source``, not ``source_name``.
        audio_stale: list[AudioPackMeta] = (
            audio_registry.stale_enabled(config) if audio_registry is not None else stale_enabled_audio_packs(config)
        )
        if audio_stale:
            messages.append(format_stale_family_message("audio", [m.source for m in audio_stale]))

    if not messages:
        return None
    return "\n".join(messages)

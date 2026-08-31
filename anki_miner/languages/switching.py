"""Language-scoped config fields and the switch that swaps them."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig

logger = logging.getLogger(__name__)

#: Config fields whose value belongs to the ACTIVE language. Every profile's
#: scoped_defaults is derived by iterating this tuple, never hand-written.
#: Stage 2A task 2A.11 appended "script_variant" and "reading_tone_color".
LANGUAGE_SCOPED_FIELDS: tuple[str, ...] = (
    "dictionary_chain",
    "frequency_chain",
    "pitch_chain",
    "expression_audio_chain",
    "allowed_pos",
    "excluded_subtypes",
    "excluded_wordsets",
    "exclude_hiragana_only_words",
    "exclude_katakana_only_words",
    "known_words_match_kana_variants",
    "anki_fields",
    "anki_deck_name",
    "anki_note_type",
    "card_type",
    "blacklist_path",
    "whitelist_path",
    "use_blacklist",
    "use_whitelist",
    "downloader_subtitle_langs",
    "excluded_decks",
    "script_variant",
    "reading_tone_color",
)


def blank_scoped_defaults() -> dict[str, object]:
    """Type-derived blank value for every ``LANGUAGE_SCOPED_FIELDS`` name.

    Shared by every language's own ``_scoped_defaults()``: tuple fields blank
    to ``()``, bool fields to ``False``, str fields to ``""``, and everything
    else (``anki_fields``, ``blacklist_path``, ``whitelist_path`` today) to
    ``None`` — ``anki_fields`` is always overridden by the caller, and
    ``blacklist_path``/``whitelist_path`` are already ``None`` on a blank
    ``AnkiMinerConfig()``. Never hand-written: a field appended to
    ``LANGUAGE_SCOPED_FIELDS`` lands here automatically, typed from whatever
    the config dataclass gives it as a default.
    """
    from anki_miner.config.config import AnkiMinerConfig

    blank = AnkiMinerConfig()
    defaults: dict[str, object] = {}
    for name in LANGUAGE_SCOPED_FIELDS:
        current = getattr(blank, name)
        if isinstance(current, tuple):
            defaults[name] = ()
        elif isinstance(current, bool):
            defaults[name] = False
        elif isinstance(current, str):
            defaults[name] = ""
        else:
            defaults[name] = None
    return defaults


def switch_language(config: AnkiMinerConfig, new_code: str) -> AnkiMinerConfig:
    """Return a new config with ``new_code`` active and the scoped fields swapped.

    The outgoing language's LANGUAGE_SCOPED_FIELDS values are parked in
    ``language_stash[old]``; the incoming language's parked snapshot is popped
    back into the live fields, or — on a first visit — the profile's
    ``scoped_defaults``, which covers EVERY scoped field so no JA-shaped
    dataclass default can leak into a zh/ko session (spec 4).

    ``language_stash`` holds a snapshot for every language that is NOT active,
    so an entry for the incoming code is always removed, and an entry for the
    OUTGOING code is stale by construction and is discarded rather than kept:
    ``language`` is portable through a settings import while ``language_stash``
    is machine-specific and stripped from it, so an import can land a config
    whose active language already has a local parked snapshot. ``new_code`` is
    folded the same way ``AnkiMinerConfig.__post_init__`` folds ``language``
    and the stash keys, because keying the stash off an unnormalized code would
    leave the now-active language parked.

    Never mutates: the result is a ``dataclasses.replace``, and the profile's
    ``scoped_defaults`` values (the config's own default objects) are copied,
    never edited. ``get_profile`` is imported inside the function because
    ``registry`` builds the ja profile from ``languages.ja``, which imports
    this module.
    """
    code = str(new_code).strip().lower()

    if code == config.language:
        if code in config.language_stash:
            logger.debug("Dropping the stale language_stash entry for the active language %r", code)
            kept = {c: dict(v) for c, v in config.language_stash.items() if c != code}
            return dataclasses.replace(config, language_stash=kept)
        return config

    from anki_miner.languages.registry import get_profile

    profile = get_profile(code)
    missing = [name for name in LANGUAGE_SCOPED_FIELDS if name not in profile.scoped_defaults]
    if missing:
        raise ValueError(f"Profile {code!r} scoped_defaults is missing scoped field(s): {', '.join(missing)}")

    # Any, not object: the values are heterogeneous config-field values, and
    # dataclasses.replace type-checks the **kwargs against each field.
    stash: dict[str, dict[str, Any]] = {c: dict(v) for c, v in config.language_stash.items()}
    if config.language in stash:
        logger.debug("Overwriting the stale %r language_stash entry with its live values", config.language)
    stash[config.language] = {name: getattr(config, name) for name in LANGUAGE_SCOPED_FIELDS}
    incoming = stash.pop(code, None)
    # The stash is a snapshot, not a schema: it is layered OVER the incoming
    # profile's defaults and filtered to the scoped names rather than trusted
    # as a complete key set. A field the snapshot omits would otherwise keep the
    # OUTGOING language's live value, and every pre-2A.11 stash is partial by
    # construction — 2A.11 appends "script_variant" and "reading_tone_color" to
    # LANGUAGE_SCOPED_FIELDS with no CONFIG_SCHEMA_VERSION bump. The filter also
    # keeps a hand-edited gui_config.json from reaching dataclasses.replace with
    # a name that is not a config field (TypeError).
    values: dict[str, Any] = {
        **dict(profile.scoped_defaults),
        **{k: v for k, v in dict(incoming or {}).items() if k in LANGUAGE_SCOPED_FIELDS},
    }
    return dataclasses.replace(config, language=code, language_stash=stash, **values)

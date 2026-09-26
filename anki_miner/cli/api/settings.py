"""Which settings an API call runs with: a profile, a mining language, a one-run overlay.

Read-only by construction: gui_config.json and profile files are parsed with
``_parse_and_migrate(archive_future=False)`` — never ``load_config``, which can
repair or archive files — and nothing is ever saved.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace

from anki_miner.cli.api.contract import BAD_RUN_FILE, PROFILE_UNREADABLE, ApiError
from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.switching import switch_language

logger = logging.getLogger(__name__)

#: The run file's ``config`` keys (proposal, "Run file"). Anything else is BAD_RUN_FILE.
ALLOWED_OVERLAY_KEYS = frozenset(
    {
        "anki_deck_name",
        "anki_note_type",
        "anki_fields",
        "card_type",
        "card_type_marker_fields",
        "allow_duplicate_cards",
        "merge_incomplete_cues",
        "max_parallel_workers",
        "min_frequency_rank",
        "max_frequency_rank",
        "use_blacklist",
        "use_whitelist",
        "deduplicate_sentences",
        "use_i_plus_one_filter",
        "max_sentence_duration_seconds",
        "max_sentence_chars",
        "exclude_hiragana_only_words",
        "exclude_katakana_only_words",
    }
)

#: Settings a video episode run never reads. A window session between prepare
#: and commit touches them as a matter of course (config_version moves on
#: every save; a tab's run options persist when ticked), so they stay out of
#: the RUN_STALE comparison. Everything else is compared: a field added later
#: errs toward RUN_STALE (the caller re-prepares), never toward commit mining
#: from lines prepare did not see.
_STALENESS_EXEMPT = frozenset(
    {
        # bookkeeping and window chrome
        "config_version",
        "last_known_version",
        "skipped_update_version",
        "legacy_pitch_migrated",
        "first_run_shortcut_done",
        "first_run_setup_done",
        "check_for_updates",
        "theme",
        "theme_favorites",
        "themes_root",
        "ui_zoom",
        "ui_language",
        "hidden_utilities",
        "key_bindings",
        # other languages' stashed settings; other tabs' run options
        "language_stash",
        "review_words_before_mining",
        "secondary_subtitle_enabled",
        "backfill_field_groups",
        "reading_min_occurrence",
        # tools a video run does not use, and paths the API sets or never touches
        "auto_update_ytdlp",
        "ytdlp_prerelease",
        "ytdlp_location",
        "alass_location",
        "mokuro_location",
        "media_temp_folder",
        "log_path",
        "stats_db_path",
        "known_words_db_path",
    }
)
_STALENESS_EXEMPT_PREFIXES = (
    "condenser_",
    "downloader_",
    "deck_builder_",
    "youtube_",
    "asr_",
    "mokuro_",
    "reading_tts_",
)

#: Before any profile exists the window adopts gui_config.json as "default"
#: (profile_controller); until then the API reports that implicit profile.
_IMPLICIT = Profile(id="default", name="Default")


def profiles() -> list[dict[str, object]]:
    """Every profile with its ``id``, ``name`` and whether it is the active one."""
    listed = ProfileStore.list_profiles() or (_IMPLICIT,)
    active = _active_id(listed)
    return [{"id": p.id, "name": p.name, "active": p.id == active} for p in listed]


def _active_id(listed: tuple[Profile, ...]) -> str | None:
    if listed == (_IMPLICIT,):
        return _IMPLICIT.id
    marker = GUIConfigManager.read_active_profile_id()
    return marker if any(p.id == marker for p in listed) else None


def load_profile_config(profile_id: str | None) -> AnkiMinerConfig:
    """The saved settings of *profile_id*; None or the active id reads gui_config.json."""
    listed = ProfileStore.list_profiles() or (_IMPLICIT,)
    if profile_id is None or profile_id == _active_id(listed):
        return _live_config()
    try:
        return ProfileStore.read_profile(profile_id)
    except (OSError, ValueError, TypeError) as exc:
        raise ApiError(PROFILE_UNREADABLE, f"Profile {profile_id!r} cannot be read: {exc}") from exc


def _live_config() -> AnkiMinerConfig:
    """gui_config.json, then its .bak, read-only; the defaults when neither exists, as the app does."""
    primary = GUIConfigManager.CONFIG_FILE
    present = [p for p in (primary, primary.with_name(primary.name + ".bak")) if p.exists()]
    if not present:
        return create_default_config()
    for path in present:
        try:
            return GUIConfigManager._parse_and_migrate(path, archive_future=False)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("API: settings file unreadable: path=%s exc=%s", path, exc)
    raise ApiError(PROFILE_UNREADABLE, "Anki Miner's settings file cannot be read.")


def with_language(config: AnkiMinerConfig, language: object, *, code: str) -> AnkiMinerConfig:
    """*config* switched to *language* the way the window switches (stash or defaults)."""
    if not isinstance(language, str) or language not in AVAILABLE_LANGUAGES:
        raise ApiError(code, f"Unknown mining language: {language!r}")
    return switch_language(config, language)


def apply_overlay(config: AnkiMinerConfig, overlay: Mapping[str, object]) -> AnkiMinerConfig:
    """*overlay* applied the way Settings -> Import applies a file: typed, maps merged per key."""
    unknown = sorted(set(overlay) - ALLOWED_OVERLAY_KEYS)
    if unknown:
        raise ApiError(BAD_RUN_FILE, f"These config keys are not allowed: {', '.join(unknown)}")
    incoming, invalid = GUIConfigManager._validate_incoming(dict(overlay))
    if invalid:
        raise ApiError(BAD_RUN_FILE, f"These config values have the wrong type: {', '.join(sorted(invalid))}")
    GUIConfigManager._overlay_mapping_fields(incoming, config)
    try:
        return replace(config, **incoming)
    except (ValueError, TypeError) as exc:
        raise ApiError(BAD_RUN_FILE, f"The config was refused: {exc}") from exc


def resolve_run_config(profile_id: str | None, language: object, overlay: Mapping[str, object]) -> AnkiMinerConfig:
    """Profile, then language, then overlay — for this run only, never saved."""
    config = apply_overlay(with_language(load_profile_config(profile_id), language, code=BAD_RUN_FILE), overlay)
    # The caller keeps its own record of what the user knows and mined
    # (proposal, "Calling it"): no known-words subtraction, and no
    # known_words.db file created for it (use_known_words_db initializes one).
    return replace(config, include_known_words=True, use_known_words_db=False)


def staleness_view(config: AnkiMinerConfig) -> dict[str, object]:
    """The JSON-safe settings a saved run is compared on."""
    data = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(config))
    return {
        key: value
        for key, value in data.items()
        if key not in _STALENESS_EXEMPT and not key.startswith(_STALENESS_EXEMPT_PREFIXES)
    }

"""The API's settings writes: settings-import and setup (API.md).

settings.py stays read-only; every write lives here and goes through the
window's own storage: ``ProfileStore`` for a profile file,
``GUIConfigManager.save_config`` for gui_config.json. Callers hold the run lock
with no window open (cli.entry.acquire_run_lock), because a window writes its
settings back from memory and would undo these.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import BAD_ARGUMENTS, PROFILE_UNREADABLE, ApiError
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import MAX_PROFILES, Profile, ProfileStore
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _active_marker(profile_id: str | None) -> Iterator[None]:
    """``save_config`` stamps ``ACTIVE_PROFILE_ID``, which only the window's boot seeds.

    Unseeded, a save drops the marker from gui_config.json, and the window's
    next boot saves the live settings again as "Recovered settings"
    (profile_controller._recover_unidentified_config).
    """
    previous = GUIConfigManager.ACTIVE_PROFILE_ID
    GUIConfigManager.ACTIVE_PROFILE_ID = profile_id
    try:
        yield
    finally:
        GUIConfigManager.ACTIVE_PROFILE_ID = previous


def save_profile(profile_id: str | None, config: AnkiMinerConfig) -> str | None:
    """Write *config* as *profile_id*'s settings (None: the active profile's); return the id written.

    The active profile's settings are gui_config.json, saved the way the window
    saves it, its marker kept as it is on disk. Any other profile's are its
    file, keeping its display name. *profile_id* must be the active profile or
    an existing one: an unknown id would create a profile named after it,
    past ProfileStore.create's checks.
    """
    active = settings.active_profile_id()
    if profile_id is None or profile_id == active:
        with _active_marker(GUIConfigManager.read_active_profile_id()):
            GUIConfigManager.save_config(config)
        return active
    name = next((p.name for p in ProfileStore.list_profiles() if p.id == profile_id), profile_id)
    ProfileStore.write_profile(profile_id, config, name=name)
    return profile_id


def adopt_default(live: AnkiMinerConfig) -> None:
    """The window's first boot under profiles: the live settings saved as ``default``, marked active.

    profile_controller._reconcile writes default.json and points the marker at
    it; the window stamps the marker on its next save. This process stamps it
    at once: a boot that finds profiles and no marker saves the live settings a
    second time, as "Recovered settings". The marker goes first, so a failed
    default.json leaves no profiles, which the window's boot adopts cleanly.
    """
    default = settings.IMPLICIT_PROFILE
    with _active_marker(default.id):
        GUIConfigManager.save_config(live)
    ProfileStore.write_profile(default.id, live, name=default.name)


def import_settings(path: Path, language: str, *, name: str | None, profile_id: str | None) -> dict[str, object]:
    """settings-import: *path* applied to a new profile called *name*, or to *profile_id*, never the active one."""
    existing = ProfileStore.scan_profiles()
    if existing is None:
        raise ApiError(PROFILE_UNREADABLE, f"The profiles folder cannot be read: {ProfileStore.profiles_dir()}")
    if profile_id is not None:
        if profile_id == settings.active_profile_id():
            raise ApiError(
                BAD_ARGUMENTS,
                f"{profile_id!r} is the active profile; settings-import never changes the settings the window uses.",
            )
        config, invalid = _imported(path, settings.load_profile_config(profile_id), language)
        save_profile(profile_id, config)
        return {"profile": profile_id, "created": False, "invalid_fields": invalid}
    if name is None:  # argparse requires --name or --profile
        raise ApiError(BAD_ARGUMENTS, "Give --name or --profile.")
    _check_new_name(name, existing or (settings.IMPLICIT_PROFILE,))
    live = settings.load_profile_config(None)
    config, invalid = _imported(path, live, language)
    if not existing:
        adopt_default(live)
    try:
        created = ProfileStore.create(name, config)
    except ValueError as exc:
        raise ApiError(BAD_ARGUMENTS, str(exc)) from exc
    return {"profile": created.id, "created": True, "invalid_fields": invalid}


def _check_new_name(name: str, existing: tuple[Profile, ...]) -> None:
    """ProfileStore.create's refusals, checked before anything (default.json included) is written."""
    try:
        ProfileStore._validate_name(name, existing)
    except ValueError as exc:
        taken = next((p for p in existing if p.name.casefold() == name.strip().casefold()), None)
        hint = f"; use --profile {taken.id} to update it" if taken is not None else ""
        raise ApiError(BAD_ARGUMENTS, f"{exc}{hint}.") from exc
    if len(existing) >= MAX_PROFILES:
        raise ApiError(BAD_ARGUMENTS, f"There are already {MAX_PROFILES} profiles, the most Anki Miner keeps.")


def _imported(path: Path, base: AnkiMinerConfig, language: str) -> tuple[AnkiMinerConfig, list[str]]:
    """*base* switched to *language*, then *path* applied as Import from file… applies it."""
    target = settings.with_language(base, language, code=BAD_ARGUMENTS)
    try:
        result = GUIConfigManager.import_config(path, target)
    except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
        raise ApiError(BAD_ARGUMENTS, f"{path} is not a settings file Anki Miner can import: {exc}") from exc
    config = result.config
    if config.language != language:
        # The deck, note type, fields and word lists in it belong to that language.
        raise ApiError(
            BAD_ARGUMENTS,
            f"{path} holds settings for {config.language!r}, not {language!r}. "
            "Export them for that language, or leave 'language' out of the file.",
        )
    invalid = list(result.invalid_fields)
    if config.subtitle_regex_filter or config.subtitle_regex_replacement:
        # import_config checks the types only; the Settings tab compiles the pattern too (settings_tab.py).
        try:
            compile_subtitle_regex_filter(config.subtitle_regex_filter, config.subtitle_regex_replacement)
        except ValueError:
            config = replace(
                config,
                subtitle_regex_filter=target.subtitle_regex_filter,
                subtitle_regex_replacement=target.subtitle_regex_replacement,
                use_subtitle_regex_filter=target.use_subtitle_regex_filter,
            )
            invalid.append("subtitle_regex_filter")
    for notice in result.notices:
        logger.info("API settings-import: %s", notice)
    return config, sorted(set(invalid))

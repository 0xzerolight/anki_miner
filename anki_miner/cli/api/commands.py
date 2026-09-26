"""``profiles``, ``check`` and ``settings-export`` (proposal, "Operational" and "Settings")."""

from __future__ import annotations

from pathlib import Path

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import BAD_ARGUMENTS, ApiError
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages.registry import get_profile
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.services.validation_service import ValidationService
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg, resolve_ffprobe

_ANKI_DOWN = "Not checked: Anki is not reachable."
_NO_NOTE_TYPE = "Not checked: the note type is not in Anki."
_NO_TOOL = "{name} was not found. Install ffmpeg, or set its location in Anki Miner's settings."


def profiles_result() -> dict[str, object]:
    return {"profiles": settings.profiles()}


def check_result(profile_id: str | None, language: str) -> dict[str, object]:
    """Readiness for *language* under *profile_id*: one item per check, in the setup wizard's order."""
    config = settings.with_language(settings.load_profile_config(profile_id), language, code=BAD_ARGUMENTS)
    validation = ValidationService(config)
    anki_ok, anki_message = validation.check_ankiconnect()
    items = [_item("anki", anki_ok, anki_message)]
    if anki_ok:
        items.append(_item("deck", *validation.check_deck_exists()))
        note_ok, note_message = validation.check_note_type_exists()
        items.append(_item("note_type", note_ok, note_message))
        items.append(
            _item("fields", *validation.check_field_names()) if note_ok else _item("fields", False, _NO_NOTE_TYPE)
        )
    else:
        items += [_item(name, False, _ANKI_DOWN) for name in ("deck", "note_type", "fields")]
    items.append(_item("dictionary", *validation.check_offline_dictionary()))
    stale = stale_resource_reimport_error(config)
    items.append(_item("resources", stale is None, stale))
    probe = get_profile(language).unavailable_reason
    reason = probe() if probe is not None else None
    items.append(_item("language_pack", not reason, reason))
    for name, resolved in (("ffmpeg", resolve_ffmpeg(config)), ("ffprobe", resolve_ffprobe(config))):
        items.append(_item(name, binary_available(resolved), _NO_TOOL.format(name=name)))
    return {"ready": all(item["ok"] for item in items), "items": items}


def _item(name: str, ok: bool, message: str | None) -> dict[str, object]:
    return {"name": name, "ok": bool(ok), "message": None if ok else (message or None)}


def settings_export(profile_id: str | None, language: str, out: Path) -> None:
    """What Settings -> Export writes, for *language* (stashed settings, or defaults if never used)."""
    if not out.parent.is_dir():
        raise ApiError(BAD_ARGUMENTS, f"The folder for --out does not exist: {out.parent}")
    config = settings.load_profile_config(profile_id)
    exported = settings.with_language(config, language, code=BAD_ARGUMENTS)
    configured = language == config.language or language in config.language_stash
    try:
        GUIConfigManager.export_config(exported, out, extra={"configured": configured})
    except OSError as exc:
        raise ApiError(BAD_ARGUMENTS, f"--out cannot be written: {exc}") from exc

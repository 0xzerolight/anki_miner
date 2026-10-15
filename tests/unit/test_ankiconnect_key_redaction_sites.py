"""AI-02: every place outside the transport that writes the AnkiConnect URL masks the key."""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.exceptions import AnkiConnectionError

_KEYED = "http://127.0.0.1:8765/?key=SECRET"


def test_the_boot_config_line_masks_the_key(caplog):
    from anki_miner.gui import app as app_module

    config = replace(create_default_config(), ankiconnect_url=_KEYED)
    with caplog.at_level(logging.INFO, logger=app_module.logger.name):
        app_module._log_effective_config(config)

    message = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Config effective:"))
    assert "ankiconnect=http://127.0.0.1:8765/?key=***" in message
    assert "SECRET" not in message


def test_the_health_check_receipt_masks_the_key(caplog):
    from anki_miner.services.validation_service import ValidationService

    service = ValidationService(replace(AnkiMinerConfig(), ankiconnect_url=_KEYED))
    with (
        patch(
            "anki_miner.services.validation_service.post_action",
            side_effect=AnkiConnectionError("AnkiConnect error in 'version': valid api key must be provided"),
        ),
        caplog.at_level(logging.DEBUG),
    ):
        ok, _message = service._check_ankiconnect()

    assert ok is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("key=***" in m for m in messages)
    assert not [m for m in messages if "SECRET" in m]


def test_the_diagnostics_bundle_masks_the_key():
    from anki_miner.diagnostics.environment import _loggable_ankiconnect_url

    assert _loggable_ankiconnect_url(SimpleNamespace(ankiconnect_url=_KEYED)) == "http://127.0.0.1:8765/?key=***"
    assert _loggable_ankiconnect_url(SimpleNamespace(ankiconnect_url="http://127.0.0.1:8765")) == (
        "http://127.0.0.1:8765"
    )


def test_the_probe_controller_logs_only_the_masked_url():
    from anki_miner.gui.controllers import anki_probe_controller

    source = inspect.getsource(anki_probe_controller)

    assert source.count("url=redact_ankiconnect_url(ankiconnect_url)") == 2
    # `\b` skips the probe_config `ankiconnect_url=ankiconnect_url,` keyword, which is not a log field.
    assert not re.search(r"\burl=ankiconnect_url,", source)


def test_no_diagnostics_bundle_member_carries_the_key(tmp_path, monkeypatch):
    """The ZIP is attached to bug reports: settings.json and the on-disk config copies mask the key too."""
    from anki_miner.diagnostics import bundle
    from anki_miner.diagnostics.environment import collect_environment

    home = tmp_path / "home"
    (home / "profiles").mkdir(parents=True)
    on_disk = json.dumps({"ankiconnect_url": _KEYED, "theme": "dark"})
    (home / "gui_config.json").write_text(on_disk, encoding="utf-8")
    (home / "gui_config.json.bak").write_text(on_disk, encoding="utf-8")
    (home / "profiles" / "work.json").write_text(on_disk, encoding="utf-8")
    monkeypatch.setattr(bundle.paths, "ANKI_MINER_HOME", home)
    monkeypatch.setattr(bundle, "_early_crash_path", lambda: tmp_path / "absent-early-crash.log")
    config = replace(create_default_config(), ankiconnect_url=_KEYED, log_path=home / "anki_miner.log")
    target = tmp_path / "diagnostics.zip"

    bundle.write_diagnostics_bundle(target, config=config, snapshot=collect_environment(config), health_lines=[])

    with ZipFile(target) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    assert {"settings.json", "environment.txt", "config/gui_config.json", "config/profiles/work.json"} <= set(members)
    assert [name for name, content in members.items() if b"SECRET" in content] == []
    masked = "http://127.0.0.1:8765/?key=***"
    assert json.loads(members["settings.json"])["ankiconnect_url"] == masked
    assert json.loads(members["config/gui_config.json"])["ankiconnect_url"] == masked
    assert json.loads(members["config/profiles/work.json"])["theme"] == "dark"


def test_a_keyless_on_disk_config_is_copied_byte_for_byte():
    from anki_miner.diagnostics.bundle import _redact_config_json

    raw = b'{"ankiconnect_url": "http://127.0.0.1:8765"}'

    assert _redact_config_json(raw) == raw

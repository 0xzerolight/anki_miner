"""Tests for eager default dicts_root creation at app startup (Issue #100).

A clean install has no ``~/.anki_miner/dicts`` until the first dictionary
import, so the Settings → Dictionaries storage-folder selector rendered a red
"Folder not found" border on first boot. ``_ensure_default_dicts_root`` creates
the DEFAULT root only; a user-relocated (possibly unmounted) ``dicts_root``
must stay visibly invalid.

The isolated-home autouse fixture patches ``anki_miner.gui.app.ANKI_MINER_HOME``
(``HOME_CONSUMERS``) and ``create_default_config()`` resolves its
``dicts_root`` default through the same isolated home, so the default-equality
guard matches without extra patching. The ``test_config`` fixture instead
hardcodes ``dicts_root=temp_dir/"dicts"`` — a NON-default path — which is what
the skip case relies on.
"""

from dataclasses import replace
from pathlib import Path

from anki_miner.config import create_default_config
from anki_miner.gui import app as app_module
from anki_miner.gui.app import _ensure_default_dicts_root


def test_creates_default_dicts_root():
    cfg = create_default_config()
    assert not cfg.dicts_root.exists()

    _ensure_default_dicts_root(cfg)

    assert cfg.dicts_root.is_dir()


def test_idempotent_when_root_already_exists():
    cfg = create_default_config()
    cfg.dicts_root.mkdir(parents=True)

    _ensure_default_dicts_root(cfg)

    assert cfg.dicts_root.is_dir()


def test_skips_non_default_dicts_root(tmp_path):
    custom = tmp_path / "elsewhere" / "dicts"
    cfg = replace(create_default_config(), dicts_root=custom)

    _ensure_default_dicts_root(cfg)

    # A relocated root is the user's business — never eagerly created (it may
    # be an unmounted external drive; a phantom dir would mask that).
    assert not custom.exists()


def test_none_config_is_tolerated():
    # Startup config load can fail; the helper must skip, not crash boot.
    _ensure_default_dicts_root(None)


def test_oserror_is_swallowed_and_warned(monkeypatch, caplog):
    cfg = create_default_config()

    def boom(path: Path) -> Path:
        raise OSError("disk says no")

    monkeypatch.setattr(app_module, "ensure_directory", boom)

    with caplog.at_level("WARNING", logger="anki_miner.gui.app"):
        _ensure_default_dicts_root(cfg)  # must not raise

    assert any("dicts_root" in rec.message for rec in caplog.records)

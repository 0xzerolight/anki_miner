"""Tests for ANKI_MINER_HOME env var override in anki_miner.config.paths."""

import importlib
from pathlib import Path

import anki_miner.config.paths as paths


def _reload() -> object:
    return importlib.reload(paths)


def test_env_var_set_overrides_default(monkeypatch, tmp_path):
    """A non-empty ANKI_MINER_HOME env var wins over the default location."""
    try:
        monkeypatch.setenv("ANKI_MINER_HOME", str(tmp_path))
        mod = _reload()
        assert Path(tmp_path) == mod.ANKI_MINER_HOME
    finally:
        monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
        importlib.reload(paths)


def test_env_var_unset_uses_default(monkeypatch):
    """With no env var, the default ~/.anki_miner is used."""
    try:
        monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
        mod = _reload()
        assert Path.home() / ".anki_miner" == mod.ANKI_MINER_HOME
    finally:
        monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
        importlib.reload(paths)


def test_env_var_empty_string_falls_back_to_default(monkeypatch):
    """An empty-string env var must fall back to the default location."""
    try:
        monkeypatch.setenv("ANKI_MINER_HOME", "")
        mod = _reload()
        assert Path.home() / ".anki_miner" == mod.ANKI_MINER_HOME
    finally:
        monkeypatch.delenv("ANKI_MINER_HOME", raising=False)
        importlib.reload(paths)

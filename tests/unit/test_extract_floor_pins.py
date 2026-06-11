"""Tests for scripts/extract_floor_pins.py.

The script lives outside the importable package, so load it by file path. The
key behavior under test is fail-closed: a FRAGILE dep whose specifier stops
matching ``>=`` (e.g. switched to ``~=``) must cause a nonzero exit rather than
silently dropping the pin (which would make smoke-min-deps test latest, not the
floor).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "extract_floor_pins.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extract_floor_pins", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_emits_pins_for_all_fragile_deps() -> None:
    mod = _load()
    deps = ["yt-dlp>=2026.3.3,<2027.0.0", "psutil>=5.9.0", "requests>=2.28.0"]
    assert mod.floor_pins(deps) == ["yt-dlp==2026.3.3", "psutil==5.9.0"]


def test_preserves_fragile_order_regardless_of_dep_order() -> None:
    mod = _load()
    deps = ["psutil>=5.9.0", "yt-dlp>=2026.3.3"]
    # Output order follows FRAGILE, not the dependency list.
    assert mod.floor_pins(deps) == ["yt-dlp==2026.3.3", "psutil==5.9.0"]


def test_fails_closed_when_fragile_dep_uses_compatible_release_specifier() -> None:
    mod = _load()
    # yt-dlp switched >= to ~=: the regex no longer matches, so the pin is
    # dropped. Must raise rather than silently emit only psutil.
    deps = ["yt-dlp~=2026.3.3", "psutil>=5.9.0"]
    with pytest.raises(SystemExit) as exc:
        mod.floor_pins(deps)
    assert "yt-dlp" in str(exc.value)


def test_fails_closed_when_fragile_dep_is_renamed_or_removed() -> None:
    mod = _load()
    deps = ["psutil>=5.9.0", "requests>=2.28.0"]  # yt-dlp absent entirely
    with pytest.raises(SystemExit) as exc:
        mod.floor_pins(deps)
    assert "yt-dlp" in str(exc.value)


def test_real_pyproject_yields_both_fragile_pins() -> None:
    """Guard against the live pyproject silently dropping a FRAGILE floor."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 lacks stdlib tomllib
        import tomli as tomllib

    pyproject = _SCRIPT.resolve().parents[1] / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    mod = _load()
    pins = mod.floor_pins(deps)
    assert len(pins) == len(mod.FRAGILE)
    assert all("==" in p for p in pins)

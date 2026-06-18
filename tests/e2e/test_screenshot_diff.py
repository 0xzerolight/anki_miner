"""Unit tests for tests/e2e/screenshot_diff.py.

Covers:
* identical images → ~0.0
* clearly different image → above SCREENSHOT_DIFF_WARN_THRESHOLD
* size mismatch → 1.0
* missing file(s) → None
* PIL unavailable (simulated via monkeypatch) → None
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.e2e.screenshot_diff import SCREENSHOT_DIFF_WARN_THRESHOLD, screenshot_diff

pytest.importorskip("PIL", reason="Pillow not installed — visual-diff tests skipped")


# ---------------------------------------------------------------------------
# Helpers: create minimal PNG images without Qt
# ---------------------------------------------------------------------------


def _write_solid_png(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> None:
    """Write a solid-colour PNG to *path* using Pillow."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    img.save(path, "PNG")


# ---------------------------------------------------------------------------
# Correctness tests
# ---------------------------------------------------------------------------


def test_identical_images_returns_zero(tmp_path: Path) -> None:
    """Two byte-identical files → diff = 0.0."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (100, 150, 200))
    b = tmp_path / "b.png"
    _write_solid_png(b, (100, 150, 200))

    result = screenshot_diff(a, b)

    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-6)


def test_maximally_different_images_returns_one(tmp_path: Path) -> None:
    """Black vs. white solid image → diff = 1.0 (max)."""
    black = tmp_path / "black.png"
    _write_solid_png(black, (0, 0, 0))
    white = tmp_path / "white.png"
    _write_solid_png(white, (255, 255, 255))

    result = screenshot_diff(black, white)

    assert result is not None
    assert result == pytest.approx(1.0, abs=1e-6)


def test_clearly_different_image_above_threshold(tmp_path: Path) -> None:
    """A large colour change is well above SCREENSHOT_DIFF_WARN_THRESHOLD."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (0, 0, 0))
    b = tmp_path / "b.png"
    _write_solid_png(b, (200, 200, 200))

    result = screenshot_diff(a, b)

    assert result is not None
    assert result > SCREENSHOT_DIFF_WARN_THRESHOLD, f"Expected diff > {SCREENSHOT_DIFF_WARN_THRESHOLD}, got {result}"


def test_slightly_different_image_below_threshold(tmp_path: Path) -> None:
    """A tiny 1-unit colour nudge is well below the warn threshold."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (128, 128, 128))
    b = tmp_path / "b.png"
    _write_solid_png(b, (129, 128, 128))  # only R channel differs by 1

    result = screenshot_diff(a, b)

    assert result is not None
    # Expected: 1/255 / 3 channels ≈ 0.0013 — well below 0.02.
    assert result < SCREENSHOT_DIFF_WARN_THRESHOLD, f"Expected diff < {SCREENSHOT_DIFF_WARN_THRESHOLD}, got {result}"


def test_size_mismatch_returns_one(tmp_path: Path) -> None:
    """Images with different dimensions → 1.0 (layout changed)."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (0, 0, 0), size=(64, 64))
    b = tmp_path / "b.png"
    _write_solid_png(b, (0, 0, 0), size=(128, 64))

    result = screenshot_diff(a, b)

    assert result == pytest.approx(1.0, abs=1e-6)


def test_same_file_returns_zero(tmp_path: Path) -> None:
    """Diffing a file against itself yields exactly 0.0."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (50, 100, 150))

    result = screenshot_diff(a, a)

    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Graceful-degradation tests
# ---------------------------------------------------------------------------


def test_missing_baseline_returns_none(tmp_path: Path) -> None:
    """Baseline file missing → None (no crash)."""
    missing = tmp_path / "baseline.png"
    current = tmp_path / "current.png"
    _write_solid_png(current, (0, 0, 0))

    result = screenshot_diff(missing, current)

    assert result is None


def test_missing_current_returns_none(tmp_path: Path) -> None:
    """Current screenshot missing → None (no crash)."""
    baseline = tmp_path / "baseline.png"
    _write_solid_png(baseline, (0, 0, 0))
    missing = tmp_path / "current.png"

    result = screenshot_diff(baseline, missing)

    assert result is None


def test_both_missing_returns_none(tmp_path: Path) -> None:
    """Both files missing → None (no crash)."""
    result = screenshot_diff(tmp_path / "a.png", tmp_path / "b.png")
    assert result is None


def test_pil_unavailable_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When PIL is not importable, screenshot_diff returns None without crashing.

    Simulates the "Pillow not installed" path by removing PIL from sys.modules
    and blocking its import, then reloading the screenshot_diff module so the
    soft-import guard is re-executed in the degraded state.
    """
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_solid_png(a, (0, 0, 0))
    _write_solid_png(b, (255, 255, 255))

    # Block PIL imports.
    original_modules = {k: v for k, v in sys.modules.items() if "PIL" in k}
    for key in list(sys.modules.keys()):
        if "PIL" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)

    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name.startswith("PIL"):
            raise ImportError(f"Mocked: {name} not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    # Reload the module so the soft PIL import runs against the blocked state.
    import tests.e2e.screenshot_diff as sd_mod

    importlib.reload(sd_mod)
    try:
        result = sd_mod.screenshot_diff(a, b)
    finally:
        # Restore: reload with real PIL back in place.
        for key in list(sys.modules.keys()):
            if "PIL" in key:
                monkeypatch.delitem(sys.modules, key, raising=False)
        for k, v in original_modules.items():
            sys.modules[k] = v
        importlib.reload(sd_mod)

    assert result is None


# ---------------------------------------------------------------------------
# Return value type and range
# ---------------------------------------------------------------------------


def test_return_value_in_range(tmp_path: Path) -> None:
    """screenshot_diff always returns a value in [0.0, 1.0] for valid inputs."""
    a = tmp_path / "a.png"
    _write_solid_png(a, (30, 60, 90))
    b = tmp_path / "b.png"
    _write_solid_png(b, (120, 80, 40))

    result = screenshot_diff(a, b)

    assert result is not None
    assert 0.0 <= result <= 1.0

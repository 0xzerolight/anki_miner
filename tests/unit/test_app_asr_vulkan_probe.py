"""Tests for the ASR Vulkan-probe early-exit branch in gui.app.main().

When ``ANKI_MINER_ASR_VULKAN_PROBE`` is set, ``main()`` must route into the
standalone probe and exit with its return code BEFORE any Qt/QApplication
construction (mirrors the existing ANKI_MINER_SMOKE env-var branches). The
parent ``_engine.vulkan_device_count`` spawns this child specifically so a
broken Vulkan driver aborts only the child.
"""

import pytest

from anki_miner.gui import app


def test_probe_env_routes_into_probe_and_exits(monkeypatch):
    """Setting the probe env var exits with the probe's return code."""
    monkeypatch.setenv("ANKI_MINER_ASR_VULKAN_PROBE", "1")
    monkeypatch.delenv("ANKI_MINER_SMOKE", raising=False)

    from anki_miner.services.asr import _vulkan_probe

    monkeypatch.setattr(_vulkan_probe, "main", lambda: 0)

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 0


def test_probe_branch_runs_before_any_qt_construction(monkeypatch):
    """The probe branch must fire before QApplication is ever touched."""
    monkeypatch.setenv("ANKI_MINER_ASR_VULKAN_PROBE", "1")
    monkeypatch.delenv("ANKI_MINER_SMOKE", raising=False)

    from anki_miner.services.asr import _vulkan_probe

    monkeypatch.setattr(_vulkan_probe, "main", lambda: 7)

    # Any Qt construction would explode this test before the probe exits.
    def _no_qt(*args, **kwargs):
        raise AssertionError("QApplication must not be constructed in the probe branch")

    monkeypatch.setattr(app, "QApplication", _no_qt)

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 7


def test_whispercpp_smoke_env_routes_into_smoke_and_exits(monkeypatch):
    """ANKI_MINER_SMOKE=whispercpp exits with the smoke's return code, before Qt."""
    monkeypatch.delenv("ANKI_MINER_ASR_VULKAN_PROBE", raising=False)
    monkeypatch.setenv("ANKI_MINER_SMOKE", "whispercpp")

    monkeypatch.setattr(app, "_run_whispercpp_bundled_smoke", lambda: 3)

    def _no_qt(*args, **kwargs):
        raise AssertionError("QApplication must not be constructed in the smoke branch")

    monkeypatch.setattr(app, "QApplication", _no_qt)

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 3


def test_no_probe_env_does_not_route_into_probe(monkeypatch):
    """Without the env var the probe branch is skipped (main proceeds past it)."""
    monkeypatch.delenv("ANKI_MINER_ASR_VULKAN_PROBE", raising=False)
    monkeypatch.delenv("ANKI_MINER_SMOKE", raising=False)

    from anki_miner.services.asr import _vulkan_probe

    called = {"n": 0}

    def _probe():
        called["n"] += 1
        return 0

    monkeypatch.setattr(_vulkan_probe, "main", _probe)

    # Stop main() right after the early-exit guards so we don't build the GUI:
    # _configure_logging runs just after the smoke/probe branches.
    sentinel = RuntimeError("stop after early-exit guards")

    def _stop(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(app, "_configure_logging", _stop)

    with pytest.raises(RuntimeError) as exc_info:
        app.main()

    assert exc_info.value is sentinel
    assert called["n"] == 0

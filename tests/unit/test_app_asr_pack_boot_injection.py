"""The ASR engine pack must be on ``sys.path`` before anything probes for it.

Mirrors ``test_app_language_pack_boot_injection.py``: ``_engine.available()``
is a ``find_spec`` probe, and the smoke dispatch, Settings construction (the
Transcription & Alignment panel probes off-thread on build) and the Generate
tab all run it. The one call site sits right after the language packs, so the
two roots join ``sys.path`` in one place and one order.
"""

from __future__ import annotations

import inspect

import pytest

from anki_miner.gui import app as app_module


def _main_source() -> str:
    return inspect.getsource(app_module.main)


def test_injection_follows_the_language_packs_and_precedes_the_smokes_and_config_load() -> None:
    source = _main_source()

    language_pos = source.index("ensure_language_packs_on_syspath()")
    asr_pos = source.index("ensure_asr_pack_on_syspath()")
    absent_smoke_pos = source.index("_run_asr_pack_absent_bundled_smoke()")
    asr_smoke_pos = source.index("_run_asr_bundled_smoke()")
    config_pos = source.index("GUIConfigManager.load_config_with_provenance()")

    assert language_pos < asr_pos < min(absent_smoke_pos, asr_smoke_pos) < config_pos


def test_injection_appears_exactly_once() -> None:
    assert _main_source().count("ensure_asr_pack_on_syspath()") == 1


def test_injection_runs_before_the_asr_smoke_dispatch_at_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[object] = []
    monkeypatch.setattr(app_module, "ensure_language_packs_on_syspath", lambda: order.append("inject-languages"))
    monkeypatch.setattr(app_module, "ensure_asr_pack_on_syspath", lambda: order.append("inject-asr"))
    monkeypatch.setattr(app_module, "_run_asr_pack_absent_bundled_smoke", lambda: order.append("smoke") or 0)
    monkeypatch.setenv("ANKI_MINER_SMOKE", "asr-absent")

    with pytest.raises(SystemExit) as excinfo:
        app_module.main()

    assert excinfo.value.code == 0
    assert order == ["inject-languages", "inject-asr", "smoke"]

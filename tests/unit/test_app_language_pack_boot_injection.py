"""Language packs must be on ``sys.path`` before anything can probe for them.

``ensure_language_packs_on_syspath`` is idempotent and best-effort (Tasks 1-2),
but until something calls it at boot a downloaded pack sits on disk unseen by
every ``find_spec`` probe in the process - the smoke dispatch, config load,
window composition (which builds the Mining Language panel) and the prewarm
all run ``find_spec`` against the packages a pack provides. This module pins
the one call site and its position: first thing in ``main()``, ahead of every
one of those.
"""

from __future__ import annotations

import inspect

import pytest

from anki_miner.gui import app as app_module


def _main_source() -> str:
    return inspect.getsource(app_module.main)


def test_injection_precedes_the_smoke_dispatch_and_config_load_in_source() -> None:
    source = _main_source()

    scrub_pos = source.index("_scrub_pyinstaller_env()")
    injection_pos = source.index("ensure_language_packs_on_syspath()")
    smoke_pos = source.index("_run_language_bundled_smoke(smoke_language)")
    config_pos = source.index("GUIConfigManager.load_config_with_provenance()")

    assert scrub_pos < injection_pos < smoke_pos < config_pos


def test_injection_appears_exactly_once() -> None:
    assert _main_source().count("ensure_language_packs_on_syspath()") == 1


def test_injection_runs_before_the_language_smoke_dispatch_at_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[object] = []
    monkeypatch.setattr(app_module, "ensure_language_packs_on_syspath", lambda: order.append("inject"))
    monkeypatch.setattr(app_module, "_run_language_bundled_smoke", lambda code: order.append(("smoke", code)) or 0)
    monkeypatch.setenv("ANKI_MINER_SMOKE", "ja")

    with pytest.raises(SystemExit) as excinfo:
        app_module.main()

    assert excinfo.value.code == 0
    assert order == ["inject", ("smoke", "ja")]

"""Boot-time signal for a config language whose engine pack is missing.

Task 6 can strip a language's engines from a bundle upgrade; a user whose
``config.language`` still names that language would otherwise learn about it
only mid-run. ``_warn_if_active_language_unavailable`` closes that gap: it
reads the profile's own ``unavailable_reason`` probe right after boot and, if
it names a reason, reports it on the window's screen-issue banner (D24 - never
a modal) with an action that jumps straight to the Mining Language selector,
reusing the header chip's route (``main_window.py:995``).
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui import app as app_module
from tests.unit.languages.stub_registry import register_stub_profile


def _settings_tab(window):
    index = window._settings_tab_index()
    assert index >= 0, "the composition has no Settings tab"
    return window.tabs.widget(index)


def test_unavailable_active_language_shows_banner_with_open_settings_action(wired_window, monkeypatch):
    window, _titles, _tabs = wired_window
    # Deliberately no window.show() / event-loop pump here: pumping lets the
    # window's own startup QTimer.singleShot(0, ...) callbacks fire (deck/
    # note-type AnkiConnect refreshes among them), which the network tripwire
    # rejects. isHidden() reads the banner's own explicit show()/hide() state
    # without needing the ancestor chain actually painted.
    window.config = dataclasses.replace(window.config, language="zh")
    register_stub_profile(
        monkeypatch,
        "zh",
        unavailable_reason=lambda: "Chinese mining needs the Chinese language pack.",
    )
    settings = _settings_tab(window)
    jumps: list[str] = []
    monkeypatch.setattr(settings, "jump_to_setting", jumps.append)
    window.tabs.setCurrentIndex(0)

    app_module._warn_if_active_language_unavailable(window)

    banner = window.issue_banner()
    assert banner is not None
    issue = banner.current_issue()
    assert issue is not None
    assert issue.summary == "Chinese mining needs the Chinese language pack."
    assert not banner.isHidden()

    banner.action_button.click()

    assert window.tabs.currentWidget() is settings
    assert jumps == ["mining_language.mining_language_combo"]


def test_ja_active_language_never_probes_and_shows_no_banner(wired_window, monkeypatch):
    window, _titles, _tabs = wired_window
    window.config = dataclasses.replace(window.config, language="ja")

    def _fail(code):
        raise AssertionError(f"get_profile({code!r}) must not be called for ja")

    monkeypatch.setattr(app_module, "get_profile", _fail)

    app_module._warn_if_active_language_unavailable(window)

    banner = window.issue_banner()
    assert banner is not None
    assert banner.current_issue() is None
    assert banner.isHidden()


def test_no_reason_shows_no_banner(wired_window, monkeypatch):
    window, _titles, _tabs = wired_window
    window.config = dataclasses.replace(window.config, language="zh")
    register_stub_profile(monkeypatch, "zh", unavailable_reason=lambda: None)

    app_module._warn_if_active_language_unavailable(window)

    banner = window.issue_banner()
    assert banner is not None
    assert banner.current_issue() is None
    assert banner.isHidden()

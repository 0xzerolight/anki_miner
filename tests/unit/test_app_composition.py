"""Tests for the production main-window composition seam."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")


def test_compose_main_window_returns_tabs_without_committing_boot(
    qtbot,
    patch_heavy_init,
    test_config,
):
    patch_heavy_init(test_config)

    from anki_miner.gui.app import ComposedApp, compose_main_window

    composed = compose_main_window(test_config)
    qtbot.addWidget(composed.window)

    assert isinstance(composed, ComposedApp)
    assert composed.window._boot_committed is False
    assert composed.analytics_tab.stats_service is composed.stats_service
    assert composed.window.tabs.widget(3) is composed.analytics_tab
    assert [composed.window.tabs.tabText(index) for index in range(composed.window.tabs.count())] == [
        "Video",
        "Audiobooks",
        "Reading",
        "Analytics",
        "Utilities",
        "Settings",
    ]


def test_backfill_restyle_button_reaches_the_restyle_entry_point(
    qtbot,
    patch_heavy_init,
    test_config,
    monkeypatch,
):
    """Restyle moved off the Tools menu onto Card Backfill (Task 14): the
    button's ``restyle_requested`` signal must actually be wired to
    ``MainWindow.restyle_mined_cards``, not just fire into the void."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.app import compose_main_window

    patch_heavy_init(test_config)
    composed = compose_main_window(test_config)
    window = composed.window
    qtbot.addWidget(window)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    started: list = []
    monkeypatch.setattr(window.background_tasks, "start_restyle_cards", lambda *a, **k: started.append(a))

    utilities_index = next(i for i in range(window.tabs.count()) if window.tabs.tabText(i) == "Utilities")
    backfill_tab = window.tabs.widget(utilities_index).backfill_tab
    backfill_tab.restyle_button.click()

    assert started, "clicking Restyle cards… did not reach the restyle entry point"


def _no_extra_checks(window, args, kwargs):
    pass


def _check_asr_model_args(window, args, kwargs):
    # app.py's _connect_asr_download._start passes the emitted model name
    # through verbatim, plus the configured models root.
    assert args[0] == "tiny"
    assert args[1] == window.get_config().asr_models_root


def _check_vulkan_model_args(window, args, kwargs):
    # app.py's _connect_vulkan_download._start: same model/root pass-through.
    assert args[0] == "ggml-base"
    assert args[1] == window.get_config().asr_models_root


def _check_language_pack_args(window, args, kwargs):
    # app.py's _connect_language_pack_download._on_requested passes the
    # emitted language code through verbatim, plus its pack root.
    from anki_miner.services.language_pack_installer import language_pack_root

    assert args[0] == "ko"
    assert args[1] == language_pack_root("ko")


def _check_ytdlp_force_true(window, args, kwargs):
    # Every ytdlp update call site passes force=True (bypasses the 24h
    # throttle), which is what makes a first install work from a click.
    assert kwargs.get("force") is True


@pytest.mark.parametrize(
    ("main_tab", "sub_attr", "signal_name", "emit_args", "start_method", "assert_args"),
    [
        ("Settings", None, "asr_download_requested", ("tiny",), "start_asr_model_download", _check_asr_model_args),
        ("Settings", None, "alass_download_requested", (), "start_alass_download", _no_extra_checks),
        ("Settings", None, "cuda_pack_download_requested", (), "start_cuda_pack_download", _no_extra_checks),
        ("Settings", None, "vad_pack_download_requested", (), "start_vad_pack_download", _no_extra_checks),
        ("Settings", None, "asr_pack_download_requested", (), "start_asr_pack_download", _no_extra_checks),
        (
            "Settings",
            None,
            "vulkan_model_download_requested",
            ("ggml-base",),
            "start_vulkan_download",
            _check_vulkan_model_args,
        ),
        (
            "Settings",
            None,
            "language_pack_download_requested",
            ("ko",),
            "start_language_pack_download",
            _check_language_pack_args,
        ),
        ("Utilities", "download_tab", "ytdlp_download_requested", (), "start_ytdlp_update", _check_ytdlp_force_true),
        ("Video", "youtube_tab", "ytdlp_download_requested", (), "start_ytdlp_update", _check_ytdlp_force_true),
        ("Utilities", "mokuro_tab", "mokuro_install_requested", (), "start_mokuro_install", _no_extra_checks),
        # The Settings "Update yt-dlp now" button (the actual Manga-OCR-incident
        # seam this test class regression-tests): wired inline in
        # compose_main_window, not through a _connect_* helper.
        ("Settings", None, "ytdlp_update_requested", (), "start_ytdlp_update", _check_ytdlp_force_true),
    ],
    ids=[
        "settings-asr_download_requested",
        "settings-alass_download_requested",
        "settings-cuda_pack_download_requested",
        "settings-vad_pack_download_requested",
        "settings-asr_pack_download_requested",
        "settings-vulkan_model_download_requested",
        "settings-language_pack_download_requested",
        "utilities.download_tab-ytdlp_download_requested",
        "video.youtube_tab-ytdlp_download_requested",
        "utilities.mokuro_tab-mokuro_install_requested",
        "settings-ytdlp_update_requested",
    ],
)
def test_download_requested_signal_reaches_background_tasks(
    main_tab,
    sub_attr,
    signal_name,
    emit_args,
    start_method,
    assert_args,
    wired_window,
    monkeypatch,
):
    """Every resource-download button must be wired all the way from its tab's
    signal, through the real ``compose_main_window`` wiring, to the
    ``BackgroundTaskController`` method that actually starts the worker --
    with the arguments production actually passes, where it passes any.

    Regression test for tests-01: deleting a ``_connect_*`` entry from the
    loop in ``compose_main_window``, the ``_connect_ytdlp_download`` call, or
    the inline ``_connect_mokuro_install``/settings ``ytdlp_update_requested``
    wiring used to fail no test, because the per-helper wiring files call the
    ``_connect_*`` helper directly on a bare ``MainWindow`` instead of going
    through composition.
    """
    window, _titles, tabs = wired_window
    emitter = tabs[main_tab] if sub_attr is None else getattr(tabs[main_tab], sub_attr)
    signal = getattr(emitter, signal_name)

    reached: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        window.background_tasks,
        start_method,
        lambda *args, **kwargs: reached.append((args, kwargs)),
    )

    signal.emit(*emit_args)

    assert reached, f"{signal_name} on {main_tab} never reached background_tasks.{start_method}"
    assert_args(window, *reached[0])

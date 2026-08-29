"""Every ``KnownWordDB`` call site opens the per-language known-words DB.

Task 1A.10. ``resolve_known_words_db_path`` is the sole derivation site: ``"ja"``
keeps ``config.known_words_db_path`` verbatim, every other language gets the
``<stem>.<lang><suffix>`` sibling. The five sites outside ``create_services``
used to read the raw config field, so on a zh config Deck Filter, the curator's
known-list commit, the two Settings known-words tools and the main window's undo
would have opened ``known_words.db`` while mining wrote ``known_words.zh.db``.

Each site is driven through its own module binding: ``main_window`` and
``_mining_tab_base`` import from ``services.known_word_db`` inside the function
body, while ``settings_tab`` and ``deck_filter_worker`` hold module-level names.
One central monkeypatch would therefore observe only half the sites, so there is
one driver (and one patch target) per site.
"""

from __future__ import annotations

import contextlib
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import anki_miner
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path

REPO_ROOT = Path(anki_miner.__file__).resolve().parent.parent

# Every site that must resolve to one path for one config.
SITES = (
    "anki_miner/gui/main_window.py",
    "anki_miner/gui/widgets/_mining_tab_base.py",
    "anki_miner/gui/widgets/settings_tab.py",
    "anki_miner/gui/workers/deck_filter_worker.py",
    "anki_miner/gui/utils/service_factory.py",
)

# The field itself may only be named by the dataclass that declares it, the
# resolver, and the known-words service's own docstring.
ALLOWED_TO_NAME_THE_FIELD = {
    "anki_miner/config/config.py",
    "anki_miner/gui/utils/service_factory.py",
    "anki_miner/services/known_word_db.py",
}


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# Static guards
# ----------------------------------------------------------------------


def test_no_site_reads_the_raw_config_field():
    """Only config.py and the resolver may name known_words_db_path."""
    hits = [
        path
        for path in SITES
        if path not in ALLOWED_TO_NAME_THE_FIELD and "config.known_words_db_path" in _source(path)
    ]
    assert hits == []


def test_service_factory_constructs_through_the_resolver():
    """The sixth site (Stage 0's) stays on the resolver.

    ``create_services`` cannot be driven for a non-ja config until the zh
    profile lands in Stage 2A, so this site is pinned at the source instead of
    by execution; the other five are driven for real below.
    """
    assert "KnownWordDB(resolve_known_words_db_path(config))" in _source("anki_miner/gui/utils/service_factory.py")


def test_ja_path_is_byte_identical(test_config):
    cfg = dataclasses.replace(test_config, known_words_db_path=test_config.known_words_db_path)
    assert resolve_known_words_db_path(cfg) == test_config.known_words_db_path


# ----------------------------------------------------------------------
# Per-site drivers
# ----------------------------------------------------------------------


@pytest.fixture
def ctx(monkeypatch, qtbot, test_config, patch_heavy_init):
    """Shared driver context.

    ``refs`` keeps a Python reference to every top-level widget a driver builds:
    ``qtbot.addWidget`` holds only a weakref, so an inline-built screen would
    otherwise be collected mid-test.
    """
    refs: list = []
    context = SimpleNamespace(
        monkeypatch=monkeypatch,
        qtbot=qtbot,
        refs=refs,
        base_config=test_config,
        patch_heavy_init=patch_heavy_init,
    )
    yield context
    for widget in refs:
        shutdown = getattr(widget, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                shutdown()
        for worker in getattr(widget, "iter_close_workers", list)():
            if worker is not None:
                worker.wait(3000)
    qtbot.wait(10)
    for widget in refs:
        with contextlib.suppress(RuntimeError):
            widget.deleteLater()


def _recorder(seen: list[Path]):
    """Stand-in for ``KnownWordDB`` that records the path it was handed."""

    def _build(path):
        seen.append(Path(path))
        db = MagicMock()
        db.is_available.return_value = False
        return db

    return _build


def _drive_main_window_undo(cfg, ctx) -> Path:
    """MainWindow's undo callback reverts source='mined' rows."""
    from anki_miner.gui import main_window as mw_module
    from anki_miner.models import ProcessingResult
    from anki_miner.services import known_word_db as kw_module

    seen: list[Path] = []
    # The site imports KnownWordDB inside undo_callback, so the binding that
    # matters is the one on the service module, not on main_window.
    ctx.monkeypatch.setattr(kw_module, "KnownWordDB", _recorder(seen))

    captured: dict = {}

    class _FakeResultsDialog:
        def __init__(self, result, parent, undo_callback=None, on_undo_committed=None):
            captured["cb"] = undo_callback

        def exec(self):
            return 0

    ctx.monkeypatch.setattr(mw_module, "ResultsDialog", _FakeResultsDialog)

    ctx.patch_heavy_init(ctx.base_config)
    window = mw_module.MainWindow()
    ctx.qtbot.addWidget(window)
    ctx.refs.append(window)
    window.config = cfg
    ctx.monkeypatch.setattr(window._anki_service, "delete_notes", lambda note_ids: len(note_ids))

    window._on_run_details(
        ProcessingResult(
            total_words_found=1,
            new_words_found=1,
            cards_created=1,
            card_ids=[101],
            mined_forms=["猫"],
        )
    )
    captured["cb"]([101])
    return seen[0]


def _drive_curator_commit(cfg, ctx) -> Path:
    """MiningTabBase._commit_known_words — the curator's Confirm path."""
    from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
    from anki_miner.services import known_word_db as kw_module

    seen: list[Path] = []
    # Function-local import again: patch the helper on its owning module.
    ctx.monkeypatch.setattr(kw_module, "add_user_known_words", lambda path, forms: seen.append(Path(path)) or 0)

    class _KnownWordsTab(MiningTabBase):
        pass

    tab = _KnownWordsTab()
    ctx.qtbot.addWidget(tab)
    ctx.refs.append(tab)
    tab.config = cfg
    assert tab._commit_known_words({"猫"}) == 0
    return seen[0]


def _settings_tab(ctx):
    from anki_miner.gui.widgets import settings_tab as st_module

    tab = st_module.SettingsTab(ctx.base_config)
    ctx.qtbot.addWidget(tab)
    ctx.refs.append(tab)
    return tab


def _drive_settings_rebuild(cfg, ctx) -> Path:
    """Settings -> Filtering -> Rebuild Known Words DB."""
    from anki_miner.gui.widgets import settings_tab as st_module

    seen: list[Path] = []
    ctx.monkeypatch.setattr(st_module, "KnownWordDB", _recorder(seen))
    ctx.monkeypatch.setattr(
        st_module.QMessageBox,
        "question",
        lambda *args, **kwargs: st_module.QMessageBox.StandardButton.Yes,
    )
    ctx.monkeypatch.setattr(st_module, "run_off_thread", lambda *args, **kwargs: None)

    tab = _settings_tab(ctx)
    tab.config = cfg
    tab._on_rebuild_known_words()
    return seen[0]


def _drive_settings_manage(cfg, ctx) -> Path:
    """Settings -> Filtering -> Manage Known Words."""
    from anki_miner.gui.widgets import settings_tab as st_module
    from anki_miner.gui.widgets.dialogs import known_words_dialog as kwd_module

    seen: list[Path] = []
    ctx.monkeypatch.setattr(st_module, "KnownWordDB", _recorder(seen))
    ctx.monkeypatch.setattr(kwd_module, "KnownWordsManagerDialog", lambda db, parent: MagicMock())

    tab = _settings_tab(ctx)
    tab.config = cfg
    tab._on_manage_known_words()
    return seen[0]


def _drive_deck_filter_worker(cfg, ctx) -> Path:
    """Utilities -> Deck Filter builds its own known-words service."""
    from anki_miner.languages.registry import get_profile
    from anki_miner.services import tagger as tagger_module

    from anki_miner.gui.workers import deck_filter_worker  # isort: skip - kept beside its patches

    seen: list[Path] = []
    ctx.monkeypatch.setattr(deck_filter_worker, "KnownWordDB", _recorder(seen))
    # The zh LanguageProfile lands in Stage 2A; the bundle's word_filter needs
    # one, and it is not what this test is about.
    ctx.monkeypatch.setattr(deck_filter_worker, "get_profile", lambda code: get_profile("ja"))
    ctx.monkeypatch.setattr(tagger_module, "get_shared_tagger", lambda: MagicMock())

    deck_filter_worker._build_filter_bundle(cfg, None)
    return seen[0]


SITE_DRIVERS = {
    "main_window/undo": _drive_main_window_undo,
    "mining_tab_base/commit_known_words": _drive_curator_commit,
    "settings_tab/manage_known_words": _drive_settings_manage,
    "settings_tab/rebuild_known_words": _drive_settings_rebuild,
    "workers/deck_filter": _drive_deck_filter_worker,
}


# ----------------------------------------------------------------------
# Behavioural guards, one test per site
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "expected_name"),
    [("ja", "known_words.db"), ("zh", "known_words.zh.db")],
)
@pytest.mark.parametrize("site", sorted(SITE_DRIVERS))
def test_site_opens_the_db_for_the_active_language(site, language, expected_name, ctx):
    cfg = dataclasses.replace(ctx.base_config, language=language)
    expected = ctx.base_config.known_words_db_path.with_name(expected_name)

    assert SITE_DRIVERS[site](cfg, ctx) == expected


def test_zh_config_resolves_every_site_to_one_sibling(ctx):
    cfg = dataclasses.replace(ctx.base_config, language="zh")
    expected = resolve_known_words_db_path(cfg)
    assert expected == ctx.base_config.known_words_db_path.with_name("known_words.zh.db")

    seen = {name: drive(cfg, ctx) for name, drive in sorted(SITE_DRIVERS.items())}

    assert set(seen.values()) == {expected}, seen

"""The layout cells and the screen list, kept free of any import side effect.

Split out of ``atlas.py`` on purpose: the driver modules call
``isolation.bootstrap()`` at import time, which sets ``ANKI_MINER_HOME`` for the
whole process. That is correct for a driver — nothing may import ``anki_miner``
before the home is redirected — and completely wrong for anything that merely
wants to read the cell table, such as ``tests/unit/test_ui_atlas_harness.py``.
Everything importable without consequence lives here.
"""

from __future__ import annotations

#: name -> (width, height, font_scale, locale, first_run, lengthen_strings)
#:
#: ``reference`` and ``hostile`` are the two cells the owner's decision plan
#: names. ``hostile_pseudo`` is the honest stand-in for a fully translated
#: German catalog (see ``atlas.py``); ``firstrun`` covers the setup path.
CELLS: dict[str, tuple[int, int, float, str, bool, bool]] = {
    "reference": (1280, 800, 1.0, "en", False, False),
    "hostile": (1024, 768, 1.5, "de", False, False),
    "hostile_pseudo": (1024, 768, 1.5, "de", False, True),
    "firstrun": (1280, 800, 1.0, "en", True, False),
}

#: How many characters the pseudo-locale adds to every string.
PSEUDO_PADDING = 25


def screens_to_visit() -> list[tuple[str, str, str | None]]:
    """``(label, main_tab_key, subtab_key)`` from the app's OWN registries.

    Reading ``MAIN_TABS``/``SUBTAB_KEYS`` rather than a list in this file is what
    makes a newly added screen automatically part of the atlas.
    """
    from anki_miner.gui.capabilities import MAIN_TABS, SUBTAB_KEYS

    out: list[tuple[str, str, str | None]] = []
    for main in sorted(MAIN_TABS):
        subs = SUBTAB_KEYS.get(main)
        if subs:
            for sub in sorted(subs):
                out.append((f"{main}.{sub}", main, sub))
        else:
            out.append((main, main, None))
    return out


def reveal(window, main_key: str, sub_key: str | None) -> None:
    """Navigate by stable key via the app's own registry, never a tab index."""
    from anki_miner.gui.capabilities import CapabilityTarget

    window.reveal_capability(CapabilityTarget(main_key, sub_key))


def find_main_window():
    """The composed ``MainWindow``, found by class name among top-level widgets."""
    from PyQt6.QtWidgets import QApplication

    return next((w for w in QApplication.topLevelWidgets() if type(w).__name__ == "MainWindow"), None)

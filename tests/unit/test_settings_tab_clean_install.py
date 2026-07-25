"""Clean-install regression net for Settings → Dictionaries (Issue #100).

Reproduces the reporter's state: fresh home, ``dicts_root`` existing (the
post-boot state ``_ensure_default_dicts_root`` produces) and no
``pitch_accent.csv``. ``SettingsTab.__init__`` → ``_load_config`` is the layer
that ``set_path``s BOTH selectors (``set_dicts_root`` for storage, the direct
``pitch_accent_selector.set_path`` for pitch), so this is the test that
actually guards the ``optional=True`` wiring in the panel — a bare
``DictionarySettingsPanel`` never touches the pitch selector.
"""

from __future__ import annotations

import contextlib

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


def test_no_red_border_in_dictionaries_panel_on_clean_install(test_config: AnkiMinerConfig, qtbot):
    # Post-boot clean install: dicts_root exists (change 1), no pitch sources.
    test_config.dicts_root.mkdir(parents=True, exist_ok=True)
    assert test_config.pitch_chain == ()

    tab = SettingsTab(test_config)
    qtbot.addWidget(tab)
    try:
        panel = tab.dictionary_panel

        assert panel.dicts_root_selector.input.property("error") is False
    finally:
        tab.shutdown()
        for w in tab.iter_close_workers():
            if w is not None:
                w.wait(3000)
        qtbot.wait(10)
        with contextlib.suppress(RuntimeError):
            tab.deleteLater()

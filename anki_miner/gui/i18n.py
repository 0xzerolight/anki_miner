"""UI translation setup (Discussion #76).

Installs a QTranslator for the app's own strings plus Qt's bundled
``qtbase_<lang>.qm`` (standard dialog buttons, file picker) at startup, before
any widget is constructed. "en" is the source language and installs nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.resources import get_resource_dir

logger = logging.getLogger(__name__)

# Code → endonym (the language's own name). Adding a language: drop a
# ``anki_miner_<code>.ts`` next to the English one, translate + compile it, and
# add one entry here. Filesystem discovery is deliberately NOT used so a
# half-finished .ts never auto-appears and every entry gets a human-readable name.
_LANGUAGES: dict[str, str] = {
    "en": "English",
    "ja": "日本語",
    "ru": "Русский",
}


def translations_dir() -> Path:
    """Directory holding the bundled .ts/.qm catalogs (frozen-safe)."""
    return get_resource_dir() / "translations"


def available_languages() -> dict[str, str]:
    """Map of selectable language code → display name."""
    return dict(_LANGUAGES)


def install_translators(app: QApplication, language: str) -> list[QTranslator]:
    """Install translators for ``language`` and return them (keep a strong ref).

    QCoreApplication.installTranslator does not take ownership; the returned
    list must outlive the app or translations silently revert.
    """
    installed: list[QTranslator] = []
    if not language or language == "en":
        return installed

    app_translator = QTranslator()
    if app_translator.load(f"anki_miner_{language}", str(translations_dir())):
        app.installTranslator(app_translator)
        installed.append(app_translator)
    else:
        logger.warning("No UI translation catalog for language %r; using English", language)

    # Qt's own widget strings (OK/Cancel, file dialog). Best-effort: may be
    # absent under PyInstaller. Failure leaves Qt's built-ins in English.
    qt_translator = QTranslator()
    qt_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load(QLocale(language), "qtbase", "_", qt_path):
        app.installTranslator(qt_translator)
        installed.append(qt_translator)

    return installed

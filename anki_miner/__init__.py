"""
Anki Miner - Automated Vocabulary Mining Tool

Mines vocabulary from video, audio, manga, books and text into Anki
flashcards with audio, screenshots, and definitions.
"""

# Single source of truth. Bump this on release; pyproject.toml reads it via
# `[tool.setuptools.dynamic] version = {attr = "anki_miner.__version__"}`.
# Do NOT switch back to importlib.metadata.version() — frozen builds can pick
# up orphan dist-info dirs from prior installs and report the wrong version
# (Issue #10).
__version__ = "3.4.0"
__author__ = "Anki Miner Contributors"

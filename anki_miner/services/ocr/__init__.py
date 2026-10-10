"""OCR pieces shared by Utilities → Manga OCR and Utilities → Video OCR.

``paddle_rec`` and ``mokuro_driver`` are standalone files: the managed mokuro
environment runs them from disk, so they import nothing from ``anki_miner``, and
the frozen app ships them as data files next to this package (anki_miner.spec).
"""

from __future__ import annotations

from pathlib import Path

#: Run with the managed mokuro Python for a language whose profile names a recogniser.
MOKURO_DRIVER = Path(__file__).with_name("mokuro_driver.py")

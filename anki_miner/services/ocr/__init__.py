"""OCR pieces shared by Utilities → Manga OCR and Utilities → Video OCR.

``paddle_rec`` and ``mokuro_driver`` are standalone files: the managed mokuro
environment runs them from disk, so they import nothing from ``anki_miner``, and
the frozen app ships them as data files next to this package (anki_miner.spec).
"""

from __future__ import annotations

from pathlib import Path

#: Run with the managed mokuro Python for a language whose profile names a recogniser.
MOKURO_DRIVER = Path(__file__).with_name("mokuro_driver.py")

#: About the size of the onnxruntime wheel either tool downloads when it lacks one
#: (Video OCR's onnx pack, the managed mokuro venv's onnxruntime); setup-card
#: tooltips only. Video OCR's offer for Japanese read 50 MB with meikiocr's 33 MB.
ONNXRUNTIME_WHEEL_MB = 17

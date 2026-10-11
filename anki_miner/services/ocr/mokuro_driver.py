"""Manga OCR for the mining languages manga-ocr cannot read: mokuro with a PP-OCR line recogniser.

The app runs this file with the managed mokuro environment's Python and never imports it
at run time:

    <uv_root>/mokuro/bin/python -P mokuro_driver.py <volume> --disable_confirmation=True ...

``ANKI_MINER_OCR_MODEL`` names the recogniser .onnx and ``ANKI_MINER_OCR_REVERSE`` is "1"
for a right-to-left model. mokuro's detector, page loop, .mokuro output and log lines
are untouched (the page cache only moves to a folder per recogniser), so
MokuroRunnerService reads this run like the ``mokuro`` console script.
Standalone: mokuro's own dependencies and ``paddle_rec`` (this folder, which ``main``
appends to sys.path after mokuro's imports) only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: mokuro cuts a long line into chunks and glues their text with "", which loses a
#: spaced script's word gaps; a ratio no line reaches sends every line whole.
_WHOLE_LINE = 10_000


class _KeepVerticalLinesTurned:
    """comic-text-detector hands vertical lines over already turned counter-clockwise
    (textblock.get_transformed_region), the way PaddleOCR feeds vertical text
    (``np.rot90``); mokuro turns them back clockwise only for manga-ocr. Skip that turn."""

    def __init__(self, cv2: Any) -> None:
        self._cv2 = cv2

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cv2, name)

    def rotate(self, src: Any, code: int) -> Any:
        if code == self._cv2.ROTATE_90_CLOCKWISE:
            return src
        return self._cv2.rotate(src, code)


def _narrow_line_margin(textblock_cls: Any) -> None:
    """Halve the margin comic-text-detector adds around a horizontal line crop.

    ``TextBlock.get_transformed_region`` widens each horizontal ``eng``/``unknown`` line by
    ``font_size / 3`` on every side, and reads ``font_size`` for nothing else. PP-OCR reads
    tighter crops than manga-ocr, and that margin pulled slices of the neighbouring lines
    into Arabic crops. ``font_size / 6`` took the 2026-10-10 spike's Arabic line CER from
    10.7% to 4.8%; no other script lost more than 0.7 points, and all still pass.
    """
    original = textblock_cls.get_transformed_region

    def get_transformed_region(self: Any, *args: Any, **kwargs: Any) -> Any:
        font_size = self.font_size
        self.font_size = font_size / 2
        try:
            return original(self, *args, **kwargs)
        finally:
            self.font_size = font_size

    textblock_cls.get_transformed_region = get_transformed_region


def _cache_per_recognizer(volume_cls: Any, cache_tag: str) -> None:
    """Keep this recogniser's page cache apart from mokuro's own and other recognisers'.

    mokuro caches each page's OCR under ``Volume.path_ocr_cache`` (``<parent>/_ocr/<stem>/``)
    and reuses any cached page, so a run cancelled in one language and resumed in another
    would mix two engines' text in one .mokuro. The folder is a sibling,
    ``<parent>/_ocr/<stem>.<tag>``: a subfolder of ``<stem>/`` would land in a Japanese
    run's ``**/*.json`` glob over it.
    """

    def path_ocr_cache(self: Any) -> Any:
        return self.path_mokuro.parent / "_ocr" / f"{self.path_mokuro.stem}.{cache_tag}"

    volume_cls.path_ocr_cache = property(path_ocr_cache)


def install(
    recognizer: Any,
    manga_page_ocr: Any,
    mokuro_generator: Any,
    textblock_cls: Any,
    volume_cls: Any,
    cache_tag: str,
) -> None:
    """Swap manga-ocr for ``recognizer`` in mokuro's modules, narrow the detector's line
    margin and give the page cache a ``cache_tag`` folder of its own, in place."""
    import numpy as np

    class LineReader:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass  # stands in for MangaOcr(pretrained_model_name_or_path, force_cpu)

        def __call__(self, image: Any) -> str:
            # mokuro reads pages as BGR and wraps each crop with Image.fromarray: still BGR.
            return str(recognizer.recognize([np.ascontiguousarray(np.asarray(image))])[0][0])

    class WholeLinePageOcr(manga_page_ocr.MangaPageOcr):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("max_ratio_hor", _WHOLE_LINE)
            kwargs.setdefault("max_ratio_vert", _WHOLE_LINE)
            super().__init__(*args, **kwargs)

    manga_page_ocr.MangaOcr = LineReader
    manga_page_ocr.cv2 = _KeepVerticalLinesTurned(manga_page_ocr.cv2)
    mokuro_generator.MangaPageOcr = WholeLinePageOcr
    _narrow_line_margin(textblock_cls)
    _cache_per_recognizer(volume_cls, cache_tag)


def main() -> None:
    import sys

    import fire
    from comic_text_detector.utils.textblock import TextBlock
    from mokuro import manga_page_ocr, mokuro_generator
    from mokuro.run import run
    from mokuro.volume import Volume

    # paddle_rec sits in the app's own folder: never write its __pycache__ there (a
    # signed macOS .app must not be modified). mokuro's own imports above still cache.
    sys.dont_write_bytecode = True
    # The app runs this file with -P: its folder joins sys.path only now, last, so no
    # file in it can shadow one of mokuro's imports above.
    sys.path.append(str(Path(__file__).resolve().parent))
    from paddle_rec import load_recognizer

    model = Path(os.environ["ANKI_MINER_OCR_MODEL"])
    recognizer = load_recognizer(model, reverse=os.environ.get("ANKI_MINER_OCR_REVERSE") == "1")
    install(recognizer, manga_page_ocr, mokuro_generator, TextBlock, Volume, model.stem)
    fire.Fire(run)


if __name__ == "__main__":
    main()

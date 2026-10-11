"""The driver swaps manga-ocr for a PP-OCR recogniser inside mokuro, and nothing else."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from anki_miner.services.ocr import mokuro_driver

pytestmark = pytest.mark.asr  # numpy-backed (the gate in test_asr_marker_gating.py)


class _PageOcr:
    def __init__(self, pretrained="kha-white/manga-ocr-base", force_cpu=False, max_ratio_vert=16, max_ratio_hor=8, **_):
        self.max_ratio_vert, self.max_ratio_hor = max_ratio_vert, max_ratio_hor


class _Cv2:
    ROTATE_90_CLOCKWISE, ROTATE_90_COUNTERCLOCKWISE = 0, 2
    IMREAD_COLOR = 1

    def __init__(self):
        self.calls = []

    def rotate(self, src, code):
        self.calls.append(code)
        return src


def _text_block_cls():
    """A fresh stand-in for comic-text-detector's TextBlock (install patches the class in place)."""

    class TextBlock:
        def __init__(self, font_size):
            self.font_size = font_size
            self.seen = []
            self.fail = False

        def get_transformed_region(self, img, idx, textheight):
            self.seen.append(self.font_size)
            if self.fail:
                raise RuntimeError("homography failed")
            return img

    return TextBlock


def _volume_cls():
    """A fresh stand-in for mokuro's Volume, with mokuro 0.2.5's page cache folder."""

    class Volume:
        def __init__(self, path_mokuro):
            self.path_mokuro = path_mokuro

        @property
        def path_ocr_cache(self):
            return self.path_mokuro.parent / "_ocr" / self.path_mokuro.stem

    return Volume


def _modules():
    cv2 = _Cv2()
    page = SimpleNamespace(MangaPageOcr=_PageOcr, MangaOcr=object, cv2=cv2)
    return page, SimpleNamespace(MangaPageOcr=_PageOcr), cv2, _text_block_cls()


def _install(rec, page, generator, text_block, volume_cls=None, cache_tag="rec_model"):
    mokuro_driver.install(rec, page, generator, text_block, volume_cls or _volume_cls(), cache_tag)


class _Recognizer:
    def __init__(self):
        self.seen = []

    def recognize(self, images):
        self.seen.extend(images)
        return [("¿Dónde está?", 0.9)]


def test_lines_go_whole_to_the_recogniser():
    page, generator, _, text_block = _modules()
    _install(_Recognizer(), page, generator, text_block)
    ocr = generator.MangaPageOcr("kha-white/manga-ocr-base", force_cpu=True)
    assert ocr.max_ratio_hor >= 10_000 and ocr.max_ratio_vert >= 10_000


def test_manga_ocr_is_replaced_by_the_recogniser_on_the_same_bgr_pixels():
    page, generator, _, text_block = _modules()
    rec = _Recognizer()
    _install(rec, page, generator, text_block)
    reader = page.MangaOcr("kha-white/manga-ocr-base", False)
    crop = np.zeros((20, 60, 3), dtype=np.uint8)
    crop[..., 0] = 255  # blue in BGR
    assert reader(Image.fromarray(crop)) == "¿Dónde está?"
    assert rec.seen[0][0, 0].tolist() == [255, 0, 0]


def test_vertical_lines_stay_as_the_detector_turned_them_and_other_calls_pass_through():
    page, generator, cv2, text_block = _modules()
    _install(_Recognizer(), page, generator, text_block)
    assert page.cv2.rotate("wide crop", cv2.ROTATE_90_CLOCKWISE) == "wide crop"  # mokuro's turn is skipped
    assert cv2.calls == []
    page.cv2.rotate("img", cv2.ROTATE_90_COUNTERCLOCKWISE)
    assert cv2.calls == [cv2.ROTATE_90_COUNTERCLOCKWISE]
    assert page.cv2.IMREAD_COLOR == 1


def test_line_crops_widen_by_half_the_detector_margin_and_the_font_size_is_restored():
    page, generator, _, text_block = _modules()
    _install(_Recognizer(), page, generator, text_block)
    blk = text_block(font_size=30)
    assert blk.get_transformed_region("img", 0, 64) == "img"
    assert blk.seen == [15]
    assert blk.font_size == 30
    blk.fail = True
    with pytest.raises(RuntimeError, match="homography failed"):
        blk.get_transformed_region("img", 0, 64)
    assert blk.seen == [15, 15]
    assert blk.font_size == 30


def test_each_recogniser_caches_its_pages_beside_mokuros_own_cache():
    # A run resumed in another language must not reuse pages another engine read; a
    # sibling of <stem>/ also stays out of a Japanese run's **/*.json glob over <stem>/.
    page, generator, _, text_block = _modules()
    volume_cls = _volume_cls()
    _install(_Recognizer(), page, generator, text_block, volume_cls, "korean_PP-OCRv5_rec_mobile")
    volume = volume_cls(Path("/manga/series/vol 1.mokuro"))
    assert volume.path_ocr_cache == Path("/manga/series/_ocr/vol 1.korean_PP-OCRv5_rec_mobile")

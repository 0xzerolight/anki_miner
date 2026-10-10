"""Pinned PP-OCR recognition models (PaddleOCR, Apache-2.0, converted to ONNX by RapidOCR v3.10.0).

A language reaches one through ``LanguageProfile.ocr_recognizer``. Each file embeds its
character list (ONNX metadata ``character``), so a recogniser is one download. Bumping
one means url, sha256 and size together, and re-running the opt-in
test_ocr_recognizers_real.py. Chosen per language by the 2026-10-10 spike
(docs/mining-languages/specs/2026-10-10-multilingual-ocr-spike.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from anki_miner.services.ocr.model_installer import ModelSpec

_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx"


@dataclass(frozen=True)
class RecognizerSpec:
    model: ModelSpec
    #: The model emits right-to-left text in visual order (PaddleOCR's Arabic-script models).
    reverse: bool = False


def _rec(path: str, sha256: str, size_bytes: int, *, reverse: bool = False) -> RecognizerSpec:
    filename = path.rsplit("/", 1)[1]
    return RecognizerSpec(ModelSpec(filename, f"{_BASE}/{path}", sha256, size_bytes), reverse)


RECOGNIZERS: dict[str, RecognizerSpec] = {
    "ppocrv6-small": _rec(
        "PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
        21234383,
    ),
    "korean": _rec(
        "PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx",
        "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
        13488748,
    ),
    "eslav": _rec(
        "PP-OCRv5/rec/eslav_PP-OCRv5_rec_mobile.onnx",
        "08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab",
        7911802,
    ),
    "el": _rec(
        "PP-OCRv5/rec/el_PP-OCRv5_rec_mobile.onnx",
        "b4368bccd557123c702b7549fee6cd1e94b581337d1c9b65310f109131542b7f",
        7832350,
    ),
    "th": _rec(
        "PP-OCRv5/rec/th_PP-OCRv5_rec_mobile.onnx",
        "de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e",
        7915294,
    ),
    "arabic": _rec(
        "PP-OCRv5/rec/arabic_PP-OCRv5_rec_mobile.onnx",
        "c1192e632d0baa9146ae5b756a0e635e3dc63c1733737ebfd1629e87144e9295",
        8023828,
        reverse=True,  # the spike's clean ar lines: CER 0.73% with reverse, 85.9% without
    ),
}

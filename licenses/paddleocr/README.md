# PaddleOCR

The text recognition models that Manga OCR and Video OCR run for languages other
than Japanese are PaddleOCR's PP-OCR models (https://github.com/PaddlePaddle/PaddleOCR),
Apache License 2.0 — the text beside this file. The app uses RapidOCR's ONNX
conversion of them (https://github.com/RapidAI/RapidOCR, Apache-2.0), tag `v3.10.0`:

- `PP-OCRv6/rec/PP-OCRv6_rec_small.onnx`
- `PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx`
- `PP-OCRv5/rec/eslav_PP-OCRv5_rec_mobile.onnx`
- `PP-OCRv5/rec/el_PP-OCRv5_rec_mobile.onnx`
- `PP-OCRv5/rec/th_PP-OCRv5_rec_mobile.onnx`
- `PP-OCRv5/rec/arabic_PP-OCRv5_rec_mobile.onnx`

They are not shipped: the app downloads them from ModelScope (`RapidAI/RapidOCR`)
at that tag on first use, pinned by sha256 in
`anki_miner/services/ocr/recognizers.py`.

`anki_miner/services/ocr/paddle_rec.py` follows RapidOCR's `ch_ppocr_rec` pre- and
post-processing, and its right-to-left reordering is modelled on PaddleOCR's
`BaseRecLabelDecode.pred_reverse`.

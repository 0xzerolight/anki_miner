# meikiocr

`anki_miner/services/video_ocr/meiki_engine.py` is a modified copy of
`meikiocr/ocr.py` from meikiocr (https://github.com/rtr46/meikiocr), commit
`52aa607ecdc1038c7bf390f37cd9a8b306e8a08a` (version 0.3.5), Apache License 2.0 —
the text beside this file. The changes are listed in that file's header: PIL
replaces opencv, models load from a directory instead of huggingface_hub, every
box goes to the horizontal recogniser, and the vertical path is removed.

The two model files the engine runs, `meiki.text.detect.v0.1.960x544.onnx`
(rtr46/meiki.text.detect.v0) and `meiki.text.rec.v0.960x32.onnx`
(rtr46/meiki.txt.recognition.v0), are LGPL-3.0. They are not shipped: the app
downloads them from Hugging Face at pinned commits on first use.

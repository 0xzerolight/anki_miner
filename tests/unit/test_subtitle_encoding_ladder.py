"""The decode ladder is the caller's, and a wrong ladder is observable."""

import pysubs2
import pytest

from anki_miner.utils import subtitle_encoding as enc_mod
from anki_miner.utils.subtitle_encoding import detect_subtitle_encoding, load_with_fallback_encoding

_SRT = "1\r\n00:00:01,000 --> 00:00:03,000\r\n{}\r\n\r\n"
ZH_LADDER = ("utf-8-sig", "gb18030", "big5")


def _write(tmp_path, text, encoding):
    data = _SRT.format(text).encode(encoding)
    path = tmp_path / "a.srt"
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError) as exc:
        data.decode("utf-8")
    return path, exc.value


def _spy(monkeypatch):
    seen: list[str | None] = []
    real = pysubs2.load

    def _load(p, **kwargs):
        seen.append(kwargs.get("encoding"))
        return real(p, **kwargs)

    monkeypatch.setattr(enc_mod.pysubs2, "load", _load)
    return seen


def test_zh_ladder_decodes_gb18030(tmp_path, monkeypatch):
    path, err = _write(tmp_path, "你好世界", "gb18030")
    seen = _spy(monkeypatch)
    subs = load_with_fallback_encoding(path, err, encodings=ZH_LADDER)
    assert subs[0].text == "你好世界"
    assert seen == ["utf-8-sig", "gb18030"]


def test_japanese_ladder_would_mangle_the_same_file(tmp_path, monkeypatch):
    """Regression precondition: gb18030 bytes fail cp932 but decode as EUC-JP
    into plausible kanji, so the ja ladder wins with mojibake and never raises."""
    path, err = _write(tmp_path, "你好世界", "gb18030")
    seen = _spy(monkeypatch)
    subs = load_with_fallback_encoding(path, err)
    assert subs[0].text == "低挫弊順"
    assert seen == ["cp932", "euc_jp"]


def test_default_ladder_is_unchanged_for_japanese(tmp_path, monkeypatch):
    path, err = _write(tmp_path, "猫が走る", "euc_jp")
    seen = _spy(monkeypatch)
    subs = load_with_fallback_encoding(path, err)
    assert subs[0].text == "猫が走る"
    assert seen == ["cp932", "euc_jp"]


def test_detect_names_the_zh_ladder_encoding(tmp_path):
    path, _ = _write(tmp_path, "你好世界", "gb18030")
    assert detect_subtitle_encoding(path, encodings=ZH_LADDER) == "gb18030"
    assert detect_subtitle_encoding(path) == "euc-jp"


def test_ja_profile_pins_the_ladder():
    from anki_miner.languages.registry import get_profile

    assert get_profile("ja").import_encodings == ("utf-8-sig", "cp932", "euc_jp")

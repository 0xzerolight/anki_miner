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


#: Traditional-Chinese line whose Big5 bytes gb18030 swallows whole.
_BIG5_LINE = "他喜歡看電影和學習中文。"
#: Its simplified counterpart, written in GB18030 — the file that must NEVER flip.
_GB_LINE = "他喜欢看电影和学习中文。"


def test_zh_ladder_rescues_a_big5_subtitle(tmp_path, monkeypatch):
    """gb18030 decodes every valid Big5 sequence, so the big5 leg was dead.

    Not a reorder: gb18030 stays first and only steps aside when its own result
    carries the private-use-area mojibake signature.
    """
    path, err = _write(tmp_path, _BIG5_LINE, "big5")
    seen = _spy(monkeypatch)
    subs = load_with_fallback_encoding(path, err, encodings=ZH_LADDER)
    assert subs[0].text == _BIG5_LINE
    assert seen == ["utf-8-sig", "big5"]


def test_a_gb18030_subtitle_never_flips_to_big5(tmp_path, monkeypatch):
    """The whole risk of the guard: GB majority content must keep gb18030."""
    path, err = _write(tmp_path, _GB_LINE, "gb18030")
    seen = _spy(monkeypatch)
    subs = load_with_fallback_encoding(path, err, encodings=ZH_LADDER)
    assert subs[0].text == _GB_LINE
    assert seen == ["utf-8-sig", "gb18030"]


def test_detect_names_big5_for_a_big5_subtitle(tmp_path):
    path, _ = _write(tmp_path, _BIG5_LINE, "big5")
    assert detect_subtitle_encoding(path, encodings=ZH_LADDER) == "big5"


def test_detect_still_names_gb18030_for_a_gb18030_subtitle(tmp_path):
    path, _ = _write(tmp_path, _GB_LINE, "gb18030")
    assert detect_subtitle_encoding(path, encodings=ZH_LADDER) == "gb18030"


def test_a_bounded_head_cut_mid_character_still_names_big5(tmp_path, monkeypatch):
    """The sniff bound cuts a double-byte sequence; that is not a wrong codec."""
    path = tmp_path / "big5.srt"
    path.write_bytes(_SRT.format(_BIG5_LINE * 40).encode("big5"))
    monkeypatch.setattr(enc_mod, "_MAX_SNIFF_BYTES", 41)
    assert detect_subtitle_encoding(path, encodings=ZH_LADDER) == "big5"


def test_the_guard_is_inert_without_a_big5_leg(tmp_path, monkeypatch):
    """A ladder with no big5 entry keeps gb18030 even for Big5 bytes."""
    path, err = _write(tmp_path, _BIG5_LINE, "big5")
    seen = _spy(monkeypatch)
    load_with_fallback_encoding(path, err, encodings=("utf-8-sig", "gb18030"))
    assert seen == ["utf-8-sig", "gb18030"]


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


JA_LADDER = ("utf-8-sig", "cp932", "euc_jp")
_BOM = b"\xef\xbb\xbf"

#: A truncated multi-byte tail is what the bounded head read produces on a real
#: file, and what a genuinely cut-short one holds. The BOM cases are the
#: regression: ``utf_8_sig`` strips the BOM before delegating, so its
#: UnicodeDecodeError offsets are relative to the BOM-stripped bytes and never
#: reach ``len(head)``. Every case's expected value is the PRE-LADDER module's
#: result, which used a plain "utf-8" leg with absolute offsets.
_TRUNCATED_CASES = {
    "bom_latin_trunc": _BOM + _SRT.format("Hello there").encode("utf-8") + b"\xc3",
    "bom_ja_trunc": _BOM + _SRT.format("猫が走る").encode("utf-8") + "日本".encode()[:-1],
    "bom_2byte_trunc": _BOM + _SRT.format("café").encode("utf-8") + b"\xc3",
    "bom_emoji_trunc": _BOM + _SRT.format("hello 🐱").encode("utf-8") + "🐱".encode()[:-1],
    "utf8_nobom_truncated_tail": _SRT.format("猫が走る").encode("utf-8") + "日本".encode()[:-1],
}


@pytest.mark.parametrize("case", sorted(_TRUNCATED_CASES))
def test_a_truncated_multibyte_tail_still_names_utf8(tmp_path, case):
    """A BOM'd UTF-8 subtitle cut mid-character is utf-8, not mojibake.

    alass gets this label via --encoding-inc; naming cp932 or windows-1251 for
    a UTF-8 file produces a mojibake retimed subtitle.
    """
    path = tmp_path / f"{case}.srt"
    path.write_bytes(_TRUNCATED_CASES[case])
    assert detect_subtitle_encoding(path, encodings=JA_LADDER) == "utf-8"


def test_the_bounded_head_cut_is_the_same_case(tmp_path, monkeypatch):
    """The truncation the sniff bound itself creates, not a truncated file."""
    path = tmp_path / "big.srt"
    path.write_bytes(_BOM + _SRT.format("猫が走る" * 200).encode("utf-8"))
    monkeypatch.setattr(enc_mod, "_MAX_SNIFF_BYTES", 60)
    assert detect_subtitle_encoding(path, encodings=JA_LADDER) == "utf-8"


def test_a_genuine_invalid_byte_still_falls_through(tmp_path):
    """The retry must not swallow a real cp932 file: its invalid byte is mid-head."""
    path = tmp_path / "cp932.srt"
    path.write_bytes(_SRT.format("猫が走る").encode("cp932"))
    assert detect_subtitle_encoding(path, encodings=JA_LADDER) == "shift_jis"

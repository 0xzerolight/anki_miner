"""The reading sources sniff their encoding with a ladder the profile supplies.

Two of the five reading sources guess an encoding: the Aozora/plain-text novel
loader and the subtitle loader. Both went through ``_util._decode``, whose ladder
is hard Japanese — cp932 and EUC-JP, tie-broken by a Japanese-character ratio.
A Chinese ``.txt`` novel in GB18030 decodes cleanly as cp932 into mojibake, so it
was never going to arrive as text.

``encodings=None`` still selects that built-in Japanese path, byte for byte, and
it is what Japanese keeps passing. That is deliberate and is pinned below: the
built-in path is strictly more than an ordered list of encodings — it carries a
UTF-16 BOM branch and the cp932-versus-EUC-JP tiebreak, and EUC-JP bytes usually
*do* decode as cp932, so a first-success ladder built from the ja profile's
``import_encodings`` would silently mojibake exactly the files ``_jp_ratio``
exists to rescue. Handing Japanese its own profile ladder would change Japanese
output; the sentinel is what keeps it identical.

The other three sources take no ladder because they never guess: mokuro reads a
UTF-8 JSON sidecar, EPUB gets its encoding from the archive's own XML
declaration, and the Text sub-tab hands over an already-decoded string.
"""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import detector
from anki_miner.services.reading._util import _decode

#: Every byte shape the built-in Japanese path is documented to handle. Each
#: decodes to its own text, so a ladder that mishandles one is visible.
JA_CORPUS: tuple[tuple[str, bytes], ...] = (
    ("utf-8", "日本語のテスト".encode()),
    ("utf-8-bom", "﻿日本語".encode()),
    ("utf-16-le", "日本語".encode("utf-16")),
    ("cp932", "日本語".encode("cp932")),
    ("cp932-halfwidth", "ｶﾅ".encode("cp932")),
    ("euc-jp", "かな".encode("euc_jp")),
    ("ascii", b"plain ascii"),
    ("empty", b""),
)


@pytest.mark.parametrize(("name", "raw"), JA_CORPUS, ids=[n for n, _ in JA_CORPUS])
def test_the_none_sentinel_is_the_pre_change_japanese_path(name: str, raw: bytes) -> None:
    """Explicit ``None`` and the old no-argument call are the same decode."""
    assert _decode(raw, encodings=None) == _decode(raw)


def test_the_ja_profile_ladder_is_not_a_substitute_for_the_sentinel() -> None:
    """Why Japanese call sites keep passing ``None`` instead of their ladder.

    Not a wish: EUC-JP hiragana decodes without error as cp932, so a
    first-success ladder returns the mojibake and never reaches the EUC-JP leg.
    This is the regression guard against "simplifying" ja onto its own ladder.
    """
    euc = "かな".encode("euc_jp")

    assert _decode(euc) == "かな"
    assert _decode(euc, encodings=get_profile("ja").import_encodings) == "､ｫ､ﾊ"


def test_a_supplied_ladder_decodes_chinese_bytes() -> None:
    """The point of the seam: GB18030 arrives as text, not as cp932 mojibake."""
    raw = "这是一个测试".encode("gb18030")
    zh_ladder = ("utf-8-sig", "gb18030", "big5")

    assert _decode(raw, encodings=zh_ladder) == "这是一个测试"
    assert _decode(raw) != "这是一个测试"


def test_a_supplied_ladder_rescues_big5_from_the_total_gb18030_decode() -> None:
    """gb18030 decodes every valid Big5 sequence, so first-success never got there.

    The rescue is signature-driven, not an ordering change: gb18030 is only
    stepped over when its own output is private-use-area mojibake.
    """
    traditional = "他喜歡看電影和學習中文。"
    zh_ladder = ("utf-8-sig", "gb18030", "big5")

    assert _decode(traditional.encode("big5"), encodings=zh_ladder) == traditional


def test_gb18030_bytes_never_flip_to_big5() -> None:
    """They decode cleanly under big5 too, so only the signature keeps them apart."""
    simplified = "他喜欢看电影和学习中文。"
    zh_ladder = ("utf-8-sig", "gb18030", "big5")

    assert _decode(simplified.encode("gb18030"), encodings=zh_ladder) == simplified


def test_the_big5_rescue_is_inert_without_a_big5_leg() -> None:
    raw = "他喜歡看電影和學習中文。".encode("big5")

    assert _decode(raw, encodings=("utf-8-sig", "gb18030")) == raw.decode("gb18030")


def test_a_supplied_ladder_is_ordered_first_success() -> None:
    utf8 = "这是".encode()

    assert _decode(utf8, encodings=("utf-8-sig", "gb18030")) == "这是"
    # Later legs are never consulted once one succeeds.
    assert _decode(utf8, encodings=("gb18030", "utf-8-sig")) != "这是"


def test_a_supplied_ladder_handles_empty_and_unknown_names() -> None:
    """Empty input still decodes; an unusable ladder raises rather than lying."""
    assert _decode(b"", encodings=("gb18030",)) == ""
    with pytest.raises(SetupError):
        _decode("かな".encode("euc_jp"), encodings=("ascii",))
    with pytest.raises(SetupError):
        _decode(b"x", encodings=())


# ---------------------------------------------------------------------------
# Dispatcher: only the two sniffing loaders receive the ladder
# ---------------------------------------------------------------------------

_LOADER_MODULES = {
    "mokuro": "anki_miner.services.reading.mokuro_source",
    "epub": "anki_miner.services.reading.epub_source",
    "txt": "anki_miner.services.reading.aozora_source",
    "subtitle": "anki_miner.services.reading.subtitle_source",
    "text": "anki_miner.services.reading.text_source",
}


def _ref(kind: str) -> ReadingSourceRef:
    if kind == "text":
        return ReadingSourceRef(kind="text", title="Text", text="x")
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path("whatever"),
        image_root=None,
        title="T",
        volume=None,
    )


def _patched_loader(kind: str, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    module = importlib.import_module(_LOADER_MODULES[kind])
    fake = MagicMock(return_value=object())
    monkeypatch.setattr(module, "load", fake)
    return fake


@pytest.mark.parametrize("kind", ["txt", "subtitle"])
def test_the_ladder_reaches_the_two_sniffing_loaders(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patched_loader(kind, monkeypatch)
    ladder = ("utf-8-sig", "gb18030", "big5")

    detector.load(_ref(kind), encodings=ladder)

    assert fake.call_args.kwargs["encodings"] == ladder


@pytest.mark.parametrize("kind", ["mokuro", "epub", "text"])
def test_the_non_sniffing_loaders_are_never_handed_a_ladder(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """They would raise TypeError: their formats declare their own encoding."""
    fake = _patched_loader(kind, monkeypatch)

    detector.load(_ref(kind), encodings=("gb18030",))

    fake.assert_called_once_with(_ref(kind))


@pytest.mark.parametrize("kind", ["mokuro", "epub", "txt", "subtitle", "text"])
def test_the_sentinel_is_omitted_from_the_loader_call(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``None`` keeps the pre-change call shape, which the dispatcher tests pin."""
    fake = _patched_loader(kind, monkeypatch)

    detector.load(_ref(kind), encodings=None)

    fake.assert_called_once_with(_ref(kind))


# ---------------------------------------------------------------------------
# The one call site that owns a config
# ---------------------------------------------------------------------------


def test_the_worker_resolves_the_sentinel_for_japanese() -> None:
    from anki_miner.gui.workers.reading_queue_worker import reading_decode_ladder

    assert reading_decode_ladder(AnkiMinerConfig()) is None


def test_the_worker_resolves_a_profile_ladder_for_another_language(monkeypatch) -> None:
    from anki_miner.gui.workers.reading_queue_worker import reading_decode_ladder
    from tests.unit.languages.stub_registry import register_stub_profile

    profile = register_stub_profile(monkeypatch, "zh", import_encodings=("utf-8-sig", "gb18030", "big5"))
    zh_config = dataclasses.replace(AnkiMinerConfig(), language="zh")

    assert reading_decode_ladder(zh_config) == profile.import_encodings

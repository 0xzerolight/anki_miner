"""The th pack pins the pythainlp wheel and the tzdata wheel Windows needs."""

from anki_miner.languages.pack_spec import LanguagePack
from anki_miner.languages.th.pack import PACK

PYTHAINLP_SHA = "625b32cd42320dc6e359315108c58eb62480804b16ae843ae3249d925f4f2cf9"


def test_pack_is_a_language_pack_for_th():
    assert isinstance(PACK, LanguagePack)
    assert PACK.code == "th" and PACK.approx_download_mb == 21
    assert PACK.requires == ()


def test_pythainlp_is_the_one_required_component():
    required = [c for c in PACK.components if c.required]
    assert [c.import_name for c in required] == ["pythainlp"]
    spec = required[0].universal
    assert spec is not None and spec.sha256 == PYTHAINLP_SHA and spec.kind == "wheel"
    assert spec.member_prefix == "pythainlp/"
    assert spec.url.startswith("https://files.pythonhosted.org/")
    assert spec.url.endswith("/pythainlp-5.3.7-py3-none-any.whl")


def test_sentinels_are_the_files_the_newmm_perceptron_path_opens():
    comp = next(c for c in PACK.components if c.import_name == "pythainlp")
    assert comp.sentinels == (
        "__init__.py",
        "corpus/words_th.txt",
        "corpus/pos_tud_perceptron.json",
        "corpus/tnc_freq.txt",
    )


def test_excluded_data_is_never_a_sentinel_and_never_a_prefix_of_one():
    comp = next(c for c in PACK.components if c.import_name == "pythainlp")
    assert comp.universal is not None
    excluded = comp.universal.exclude
    assert excluded  # the spec's list, minus corpus/deepcut.onnx (absent from 5.3.7)
    assert "corpus/deepcut.onnx" not in excluded
    for sentinel in comp.sentinels:
        assert sentinel not in excluded
        assert not any(sentinel.startswith(entry) for entry in excluded if entry.endswith("/"))


def test_tzdata_is_optional_and_universal():
    # Universal, NOT per_platform: test_pack_manifests
    # ::test_per_platform_tables_cover_the_release_matrix asserts that any
    # per_platform table covers all four release platforms, and the wheel is a
    # 348 KB pure py2.py3-none-any artifact, so there is nothing to vary.
    tz = next(c for c in PACK.components if c.import_name == "tzdata")
    assert tz.required is False
    assert tz.per_platform is None
    spec = tz.universal
    assert spec is not None
    assert spec.sha256 == "dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931"
    assert spec.member_prefix == "tzdata/"
    assert spec.url.endswith("/tzdata-2026.3-py2.py3-none-any.whl")
    assert tz.sentinels == ("__init__.py", "zoneinfo/Asia/Bangkok")

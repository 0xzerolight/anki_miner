"""Dependency-pack manifest for Thai mining.

Pins the same pythainlp version ``pyproject.toml``'s ``[th]`` extra installs, so
a pack user and a pip user run byte-identical engines.

``exclude`` drops the corpora the newmm + perceptron path never opens. That the
path never opens them is measured, not assumed: an ``sys.addaudithook`` trace of
``import pythainlp`` -> ``word_tokenize(engine="newmm")`` ->
``pos_tag(engine="perceptron", corpus="tud")`` -> the stopword/name corpora opens
exactly the four sentinels below plus ``corpus/stopwords_th.txt`` and the three
name lists. 42 MB of the wheel's 64 MB uncompressed payload goes. The spec also
lists ``corpus/deepcut.onnx``; 5.3.7 does not ship it, so pinning it would be a
line that protects nothing.

``tzdata`` is OPTIONAL in the manifest and universal in artifact, but it is not
optional in effect on Windows: ``pythainlp/util/date.py`` evaluates
``ZoneInfo("Asia/Bangkok")`` at import, and with no tz database
``import pythainlp`` raises ``ZoneInfoNotFoundError``. Windows has no system tz
database and no frozen bundle carries one (nothing in ``anki_miner`` imports
``zoneinfo``, so PyInstaller collects no ``tzdata``). It stays ``required=False``
because a Linux or macOS pack must not fail over a component whose only job is
on Windows.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_PYTHAINLP = PackComponent(
    import_name="pythainlp",
    required=True,
    sentinels=("__init__.py", "corpus/words_th.txt", "corpus/pos_tud_perceptron.json", "corpus/tnc_freq.txt"),
    universal=ArtifactSpec(
        # pythainlp-5.3.7-py3-none-any.whl (19,849,878 B)
        url=(
            "https://files.pythonhosted.org/packages/bd/d3/"
            "d81ee1eea09f195e4243400c6b7090bae4cf40d6a7cde4d9ebd4b7f42c96/"
            "pythainlp-5.3.7-py3-none-any.whl"
        ),
        sha256="625b32cd42320dc6e359315108c58eb62480804b16ae843ae3249d925f4f2cf9",
        kind="wheel",
        member_prefix="pythainlp/",
        exclude=(
            "corpus/wikipedia_titles_th.txt",  # 12.5 MB, pythainlp.corpus.wikipedia only
            "corpus/wordnet_th.db",  # 11.2 MB, pythainlp.corpus.wordnet only
            "corpus/pos_orchid_perceptron.json",  # 5.0 MB, corpus="orchid"
            "corpus/sentenceseg_crfcut.model",  # 4.1 MB, crfcut (needs pycrfsuite; never called)
            "corpus/tdtb-pt_tagger.json",  # 3.6 MB, corpus="tdtb"
            "corpus/thai2rom_decoder.onnx",  # 3.0 MB, romanisation (rejected, C.3)
            "corpus/thainer_crf_1_5_1.model",  # 1.6 MB, named-entity tagger
            "corpus/thai2rom_encoder.onnx",  # 1.1 MB, romanisation (rejected, C.3)
        ),
    ),
)

_TZDATA = PackComponent(
    import_name="tzdata",
    required=False,
    sentinels=("__init__.py", "zoneinfo/Asia/Bangkok"),
    # Universal rather than a Windows-only per_platform entry. It is Windows
    # that NEEDS it, but the artifact is a 348 KB pure py2.py3-none-any wheel
    # with nothing to vary, and tests/unit/languages/test_pack_manifests.py
    # ::test_per_platform_tables_cover_the_release_matrix requires any
    # per_platform table to cover all four release platforms. On Linux and macOS
    # the extracted copy is inert: zoneinfo reads the system database first and
    # only falls back to this package.
    universal=ArtifactSpec(
        # tzdata-2026.3-py2.py3-none-any.whl (348,168 B)
        url=(
            "https://files.pythonhosted.org/packages/e5/6d/"
            "b53b99a9f2766d095985947a5782f1702cabb129a34f7a802d7197af832f/"
            "tzdata-2026.3-py2.py3-none-any.whl"
        ),
        sha256="dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931",
        kind="wheel",
        member_prefix="tzdata/",
    ),
)

PACK = LanguagePack(code="th", approx_download_mb=21, components=(_PYTHAINLP, _TZDATA))

__all__ = ["PACK"]

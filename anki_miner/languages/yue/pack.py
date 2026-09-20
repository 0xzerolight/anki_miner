"""Dependency-pack manifest for Cantonese mining.

pycantonese and rustling both ship cp310-abi3 wheels (built once against the
stable ABI, so one per-platform pin covers every CPython minor -- ``abi=None``),
and PyPI publishes ``manylinux_2_17_aarch64`` for both, so ``("linux","aarch64")``
pins like ko with no gap. Both compiled cores sit INSIDE their package
directories (``pycantonese/_rust.abi3.so``, ``rustling/_lib_name.abi3.so``), so
``member_prefix`` captures them and there are no root members.

``exclude`` drops what the runtime path never opens: the GPL-3 CantoMap data
(which therefore never lands on disk and owes no ``licenses/`` notice), the CTCPC
and Common Voice corpora, and the two training scripts. 8,712,833 B removed,
measured against the installed tree. ``data/hkcancor/LICENSE.txt`` and
``data/rime_cantonese/LICENSE.txt`` stay, so the CC BY 4.0 attribution travels
with the data it covers.

One consequence worth stating: ``scripts/build_yue_frequency.py`` reads
``data/ctcpc/sents.json``, which this ``exclude`` removes -- the converter runs
against a plain ``pip install pycantonese`` at build time, never against a pack.

macOS x86_64's floor is 10.12, read off the platform tag by
``macos_floor_from_url``. Both packages are MIT.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_PYCANTONESE_EXCLUDE = (
    "data/ctcpc/",  # 6.4 MB, the parallel corpus the segmenter was TRAINED on
    "data/cantomap/",  # 1.9 MB, GPL-3: never lands on a user's disk
    "data/common_voice/",  # 389 KB, training data
    "word_segmentation/train_segmenter.py",
    "pos_tagging/train_tagger.py",
)

#: Platform-neutral: the extension module's name differs per OS, so no sentinel
#: may name one. The three data files are what segment(), pos_tag() and
#: characters_to_jyutping() actually open.
_PYCANTONESE_SENTINELS = (
    "__init__.py",
    "word_segmentation/segmenter.fb.zst",
    "pos_tagging/tagger.fb.zst",
    "data/rime_cantonese/chars_to_jyutping.json",
    "data/hkcancor/FC-020_v.cha",
)

_PYCANTONESE = PackComponent(
    import_name="pycantonese",
    required=True,
    sentinels=_PYCANTONESE_SENTINELS,
    abi=None,
    per_platform={
        ("linux", "x86_64"): ArtifactSpec(
            # pycantonese-5.0.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (42,676,646 B)
            url=(
                "https://files.pythonhosted.org/packages/ee/fe/"
                "ee179a7336935d71d1db34183008a5362457d657e59c7a4c63e20c0b7852/"
                "pycantonese-5.0.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
            ),
            sha256="4a010973683fb8d14313ef44cdf7a4ded1adbe1a1bc64c93d0f75e65f141dd1a",
            kind="wheel",
            member_prefix="pycantonese/",
            exclude=_PYCANTONESE_EXCLUDE,
        ),
        ("linux", "aarch64"): ArtifactSpec(
            # pycantonese-5.0.0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl (42,602,997 B)
            url=(
                "https://files.pythonhosted.org/packages/56/70/"
                "702c2edf2bd1dac56aef6236e30d977b83efe313e4a6b1ef9fa23004341a/"
                "pycantonese-5.0.0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
            ),
            sha256="609d14a588cb0b39fe1644347609677dbda77e25978dfd34f1f9de455f88637b",
            kind="wheel",
            member_prefix="pycantonese/",
            exclude=_PYCANTONESE_EXCLUDE,
        ),
        ("win32", "AMD64"): ArtifactSpec(
            # pycantonese-5.0.0-cp310-abi3-win_amd64.whl (42,123,344 B)
            url=(
                "https://files.pythonhosted.org/packages/f3/fd/"
                "c13baa1362c377552644f3558759fc4b87557a9d9e3f7a030b25b897bfe3/"
                "pycantonese-5.0.0-cp310-abi3-win_amd64.whl"
            ),
            sha256="8f7bdc1e9a37b5e164cd57f22aecae8532d4d0b74ff5b6f5e28480a657fa53f9",
            kind="wheel",
            member_prefix="pycantonese/",
            exclude=_PYCANTONESE_EXCLUDE,
        ),
        ("darwin", "arm64"): ArtifactSpec(
            # pycantonese-5.0.0-cp310-abi3-macosx_11_0_arm64.whl (42,276,871 B)
            url=(
                "https://files.pythonhosted.org/packages/d8/a9/"
                "cbc19623761136d7c8f7230d633bc4735acf685f12da78662f4c24206bbf/"
                "pycantonese-5.0.0-cp310-abi3-macosx_11_0_arm64.whl"
            ),
            sha256="b33d794ea43c8bb7e18699e561fa51a0f48dfb5a033d569b99b733b6ffc30917",
            kind="wheel",
            member_prefix="pycantonese/",
            exclude=_PYCANTONESE_EXCLUDE,
        ),
        ("darwin", "x86_64"): ArtifactSpec(
            # pycantonese-5.0.0-cp310-abi3-macosx_10_12_x86_64.whl (42,475,866 B)
            url=(
                "https://files.pythonhosted.org/packages/1e/80/"
                "9b7163e41a460c7455026b7ff000ac8c75469ce8992a1f711bccf5759fbe/"
                "pycantonese-5.0.0-cp310-abi3-macosx_10_12_x86_64.whl"
            ),
            sha256="587387ffe61969d95a5ee5f3f47eb74712c50e76dbc00d578dfb59b45934e3f5",
            kind="wheel",
            member_prefix="pycantonese/",
            exclude=_PYCANTONESE_EXCLUDE,
        ),
    },
)

_RUSTLING = PackComponent(
    import_name="rustling",
    required=True,
    sentinels=("__init__.py", "wordseg/__init__.py", "perceptron_pos_tagger/__init__.py"),
    abi=None,
    per_platform={
        ("linux", "x86_64"): ArtifactSpec(
            # rustling-0.9.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (3,068,174 B)
            url=(
                "https://files.pythonhosted.org/packages/c8/e5/"
                "6a7a3bb9d7c7df38897e263fb35234e44415e6db3339aea1b29326c4ed87/"
                "rustling-0.9.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
            ),
            sha256="91b3bddab02d7ed48ff6adc5829cbb8675509f1b9166223b2cd592c0a1f24335",
            kind="wheel",
            member_prefix="rustling/",
        ),
        ("linux", "aarch64"): ArtifactSpec(
            # rustling-0.9.0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl (3,028,200 B)
            url=(
                "https://files.pythonhosted.org/packages/1f/50/"
                "e71de3a4f09ccf198f71cfa82b9acc5b2ed8e4d7e9d648739fd56ddb0bc1/"
                "rustling-0.9.0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
            ),
            sha256="2467d93f82c00e2eb2d11efe14be42aa71cabb4f7d60ef554e113e2efc11ebc5",
            kind="wheel",
            member_prefix="rustling/",
        ),
        ("win32", "AMD64"): ArtifactSpec(
            # rustling-0.9.0-cp310-abi3-win_amd64.whl (2,543,968 B)
            url=(
                "https://files.pythonhosted.org/packages/f4/7d/"
                "0a60cfb46024dd662dca71bd653b86c7a4b07945782ff6c777c800b67dfb/"
                "rustling-0.9.0-cp310-abi3-win_amd64.whl"
            ),
            sha256="e78ce1bc7b9167d251d53541ffe8c978836bff2c0a6af992974f039eb2ad5c30",
            kind="wheel",
            member_prefix="rustling/",
        ),
        ("darwin", "arm64"): ArtifactSpec(
            # rustling-0.9.0-cp310-abi3-macosx_11_0_arm64.whl (2,690,618 B)
            url=(
                "https://files.pythonhosted.org/packages/56/76/"
                "72946585e18d5b3d8193126ba4e0e0843a0bc6fe479b6617d981876634d6/"
                "rustling-0.9.0-cp310-abi3-macosx_11_0_arm64.whl"
            ),
            sha256="94d0938e910df2f41aa5bb8d69ce71262c9e17eff04824e660c49bd3808825ab",
            kind="wheel",
            member_prefix="rustling/",
        ),
        ("darwin", "x86_64"): ArtifactSpec(
            # rustling-0.9.0-cp310-abi3-macosx_10_12_x86_64.whl (2,824,392 B)
            url=(
                "https://files.pythonhosted.org/packages/99/38/"
                "0e5af9217867b0884964549517e86330c9a5ab9705b78969696ade70ef5e/"
                "rustling-0.9.0-cp310-abi3-macosx_10_12_x86_64.whl"
            ),
            sha256="44c99629f7b251a740920837fea12653537e1b97281cdcfe8993ac93588576b3",
            kind="wheel",
            member_prefix="rustling/",
        ),
    },
)

#: 42.7 MB + 3.1 MB on the largest platform. The download is the whole wheel;
#: ``exclude`` only trims what lands on disk.
PACK = LanguagePack(code="yue", approx_download_mb=46, components=(_PYCANTONESE, _RUSTLING))

__all__ = ["PACK"]

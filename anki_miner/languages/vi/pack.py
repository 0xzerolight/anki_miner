"""Dependency-pack manifest for Vietnamese mining.

Hand-written (the ko/zh shape), not generated: ``pin_language_pack.py runtime`` would resolve
underthesea's whole Requires-Dist (Click, tqdm, requests, PyYAML, huggingface-hub), while the
tokenize + tag path imports exactly four distributions. Proven with ``python3.12 -I -S`` and only
a pack root on ``sys.path`` (plan decision 5, pinned by ``test_vi_pack.py``): underthesea alone
dies on joblib, joblib 1.6 on cloudpickle (it no longer vendors it), and the four together
segment and tag with no other third-party import. Versions match the ``[vi]`` extra and the dev
venv, so a pack user and a pip user run the same engine.

underthesea's CRF models live inside its wheel (no HuggingFace download on this path). The
excludes drop what the path never imports: the torch/jax subtrees, the LLM-agent HTTP clients,
4.9 MB of address-mapping data and ``utils/`` (whose ``__init__`` reconfigures logging). Each
optional import underthesea probes at import time then resolves to None instead of loading.
underthesea_core is a Rust extension with per-CPython wheels, pinned at the bundle's ABI like
opencc; its extension module sits inside its own package dir.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

#: Package-relative paths never extracted from the underthesea wheel (verified against 9.5.0).
UNDERTHESEA_EXCLUDES: tuple[str, ...] = (
    "address/",
    "agent/",
    "models/dependency_parser.py",
    "modules/",
    "pipeline/dependency_parse/",
    "pipeline/translate/",
    "pipeline/tts/",
    "trainers/",
    "utils/",
)

_UNDERTHESEA = PackComponent(
    import_name="underthesea",
    required=True,
    sentinels=(
        "__init__.py",
        "pipeline/word_tokenize/models/ws_crf_vlsp2013_20230727/models.bin",
        "pipeline/pos_tag/models/pos_crf_vlsp2013_20230303/models.bin",
    ),
    universal=ArtifactSpec(
        # underthesea-9.5.0-py3-none-any.whl (7,273,326 B)
        url=(
            "https://files.pythonhosted.org/packages/ef/d5/"
            "9d81c3d04ad8aac071115bbc8e1870741308d6dadffd201be7a6fde0329f/"
            "underthesea-9.5.0-py3-none-any.whl"
        ),
        sha256="81400f41b75ceff6f80c52b4ad043806c80f5899ec555d8e6374d468a59bce56",
        kind="wheel",
        member_prefix="underthesea/",
        exclude=UNDERTHESEA_EXCLUDES,
    ),
)

_UNDERTHESEA_CORE = PackComponent(
    import_name="underthesea_core",
    required=True,
    sentinels=("__init__.py",),
    abi=(3, 12),
    per_platform={
        ("linux", "x86_64"): ArtifactSpec(
            # underthesea_core-3.3.2-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/f2/2b/"
                "160f57cf05774636d08f06895a7c761515f6245df20fae5d2954e30ae5ab/"
                "underthesea_core-3.3.2-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
            ),
            sha256="a80ddbedbb3e2092ea9a5c3fba09fb1c66c5ebbc7695c42a4b789933ef58f99b",
            kind="wheel",
            member_prefix="underthesea_core/",
        ),
        ("linux", "aarch64"): ArtifactSpec(
            # underthesea_core-3.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
            url=(
                "https://files.pythonhosted.org/packages/e0/79/"
                "9e9c3605970c1d74bff3d07d7f2fcf7ee3aa035abc88c3a045f0a0760eb0/"
                "underthesea_core-3.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
            ),
            sha256="18b0b73872d3f848e93087eeefec5e79d2bc1e5b9173d5603b2ddd2ec2971916",
            kind="wheel",
            member_prefix="underthesea_core/",
        ),
        ("win32", "AMD64"): ArtifactSpec(
            # underthesea_core-3.3.2-cp312-cp312-win_amd64.whl
            url=(
                "https://files.pythonhosted.org/packages/2d/27/"
                "01ea9e5955771b6a57a57d94a4fe426912710883a7b9bf3b9e4c71a9d7ed/"
                "underthesea_core-3.3.2-cp312-cp312-win_amd64.whl"
            ),
            sha256="c003ea761dddaf8cb502fdd4cace58a53797083cfaac8fce2b5b2877a479c4e5",
            kind="wheel",
            member_prefix="underthesea_core/",
        ),
        ("darwin", "arm64"): ArtifactSpec(
            # underthesea_core-3.3.2-cp312-cp312-macosx_11_0_arm64.whl
            url=(
                "https://files.pythonhosted.org/packages/67/64/"
                "9f79208b2f9b317f2ff9d5cf4a7364b1579731aefa1eb816da232323322d/"
                "underthesea_core-3.3.2-cp312-cp312-macosx_11_0_arm64.whl"
            ),
            sha256="94cbf2375aed5b7585a5afb668a7b64cd7f82e5079442a603964200455ea74a4",
            kind="wheel",
            member_prefix="underthesea_core/",
        ),
        ("darwin", "x86_64"): ArtifactSpec(
            # underthesea_core-3.3.2-cp312-cp312-macosx_10_12_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/8c/de/"
                "b19289d9081acf3b7037239153badf669bd33ed6ff17969bf6f1e5fb74ab/"
                "underthesea_core-3.3.2-cp312-cp312-macosx_10_12_x86_64.whl"
            ),
            sha256="2492b6c8fbc4988c792e43de610b9dbb0435978122f75554c7ec0aa3f1a25ec3",
            kind="wheel",
            member_prefix="underthesea_core/",
        ),
    },
)

_JOBLIB = PackComponent(
    import_name="joblib",
    required=True,
    sentinels=("__init__.py",),
    universal=ArtifactSpec(
        # joblib-1.6.0-py3-none-any.whl
        url=(
            "https://files.pythonhosted.org/packages/18/53/"
            "84099323c2ec4be98d935f63c033ac4151ee83836ca1050ede3b3aadf155/"
            "joblib-1.6.0-py3-none-any.whl"
        ),
        sha256="3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba",
        kind="wheel",
        member_prefix="joblib/",
    ),
)

_CLOUDPICKLE = PackComponent(
    import_name="cloudpickle",
    required=True,
    sentinels=("__init__.py",),
    universal=ArtifactSpec(
        # cloudpickle-3.1.2-py3-none-any.whl
        url=(
            "https://files.pythonhosted.org/packages/88/39/"
            "799be3f2f0f38cc727ee3b4f1445fe6d5e4133064ec2e4115069418a5bb6/"
            "cloudpickle-3.1.2-py3-none-any.whl"
        ),
        sha256="9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a",
        kind="wheel",
        member_prefix="cloudpickle/",
    ),
)

#: ceil((7,273,326 + 1,525,120 + 306,115 + 22,228) B / 1e6) = 10 (the largest core wheel).
PACK = LanguagePack(
    code="vi",
    approx_download_mb=10,
    components=(_UNDERTHESEA, _UNDERTHESEA_CORE, _JOBLIB, _CLOUDPICKLE),
)

__all__ = ["PACK", "UNDERTHESEA_EXCLUDES"]

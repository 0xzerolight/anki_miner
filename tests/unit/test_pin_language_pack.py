"""scripts/pin_language_pack.py: manifests are generated, never hand-written."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "pin_language_pack.py"
_spec = importlib.util.spec_from_file_location("pin_language_pack", _SCRIPT)
assert _spec is not None and _spec.loader is not None
pin = importlib.util.module_from_spec(_spec)
sys.modules["pin_language_pack"] = pin
_spec.loader.exec_module(pin)

_MODEL_URL = "https://github.com/explosion/spacy-models/releases/download/x/xx_model-1.0-py3-none-any.whl"


def _exec_manifest(text: str) -> LanguagePack:
    namespace: dict[str, object] = {}
    exec(compile(text, "pack.py", "exec"), namespace)  # noqa: S102
    return namespace["PACK"]  # type: ignore[return-value]


def _release(digest: str | None = None, size: int = 1) -> dict[str, object]:
    return {
        "assets": [
            {
                "name": "xx_model-1.0-py3-none-any.whl",
                "browser_download_url": _MODEL_URL,
                "digest": digest,
                "size": size,
            }
        ]
    }


def _project_black_mode():
    """The mode ``black --check .`` runs with, read by black's own pyproject parser."""
    import black

    config = black.parse_pyproject_toml(str(_ROOT / "pyproject.toml"))
    return config, black.Mode(
        line_length=config["line_length"],
        target_versions={black.TargetVersion[version.upper()] for version in config["target_version"]},
    )


def test_a_model_pack_renders_to_the_expected_manifest():
    release = {
        "assets": [
            {
                "name": "de_core_news_sm-3.8.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl",
                "digest": None,  # the real spaCy model assets report none
                "size": 14_000_000,
            }
        ]
    }
    resolved = pin.resolve_model(
        "de",
        "de_core_news_sm",
        "3.8.0",
        ("_spacy",),
        fetch_json=lambda url: release,
        hash_download=lambda url: ("a" * 64, 14_000_000),
    )

    pack = _exec_manifest(pin.render_manifest(resolved))

    assert pack == LanguagePack(
        code="de",
        approx_download_mb=14,
        requires=("_spacy",),
        components=(
            PackComponent(
                import_name="de_core_news_sm",
                required=True,
                sentinels=("__init__.py", "de_core_news_sm-3.8.0/config.cfg", "de_core_news_sm-3.8.0/meta.json"),
                universal=ArtifactSpec(
                    url=release["assets"][0]["browser_download_url"],
                    sha256="a" * 64,
                    kind="wheel",
                    member_prefix="de_core_news_sm/",
                ),
            ),
        ),
    )


def test_a_runtime_pack_resolves_per_platform_and_subtracts_the_lock(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("# pins\nnumpy==2.5.0\n", encoding="utf-8")
    compiled: list[list[str]] = []

    def run_compile(args, stdin):
        compiled.append(args)
        return "blis==1.3.0\nnumpy==2.5.0\nwasabi==1.1.3\n"

    def wheel(name, version, tag, sha):
        filename = f"{name}-{version}-{tag}.whl"
        return {
            "filename": filename,
            "url": f"https://files.pythonhosted.org/packages/xx/{filename}",
            "digests": {"sha256": sha * 64},
            "size": 1_048_576,
            "packagetype": "bdist_wheel",
        }

    index = {
        "blis": {
            "urls": [
                wheel("blis", "1.3.0", "cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64", "1"),
                wheel("blis", "1.3.0", "cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64", "2"),
                wheel("blis", "1.3.0", "cp312-cp312-win_amd64", "3"),
                wheel("blis", "1.3.0", "cp312-cp312-macosx_11_0_arm64", "4"),
                wheel("blis", "1.3.0", "cp312-cp312-macosx_10_13_x86_64", "5"),
            ]
        },
        "wasabi": {"urls": [wheel("wasabi", "1.1.3", "py3-none-any", "6")]},
    }

    resolved = pin.resolve_runtime(
        "_spacy",
        ["spacy>=3.8,<3.8.15"],
        "3.12",
        lock,
        run_compile=run_compile,
        fetch_json=lambda url: index[url.split("/pypi/")[1].split("/")[0]],
        read_top_level=lambda artifact: [artifact["filename"].split("-")[0]],
    )
    pack = _exec_manifest(pin.render_manifest(resolved))

    assert len(compiled) == len(pin.PLATFORMS) and "--constraint" in compiled[0]
    assert [comp.import_name for comp in pack.components] == ["blis", "wasabi"]
    blis, wasabi = pack.components
    assert blis.abi == (3, 12) and set(blis.per_platform) == set(pin.PLATFORMS)
    assert blis.per_platform[("win32", "AMD64")].sha256 == "3" * 64
    assert wasabi.universal is not None and wasabi.abi is None
    # The committed fixture is written with sorted keys; re-rendering it must not
    # reorder the per-platform table, or the regeneration test below would fail.
    assert pin.render_manifest(json.loads(json.dumps(resolved, sort_keys=True))) == pin.render_manifest(resolved)


def test_a_reported_digest_must_agree_with_the_download():
    with pytest.raises(SystemExit, match="disagrees"):
        pin.resolve_model(
            "xx",
            "xx_model",
            "1.0",
            (),
            fetch_json=lambda url: _release(digest="sha256:" + "b" * 64),
            hash_download=lambda url: ("a" * 64, 1),
        )


def test_a_download_of_the_wrong_size_is_refused():
    with pytest.raises(SystemExit, match="bytes"):
        pin.resolve_model(
            "xx",
            "xx_model",
            "1.0",
            (),
            fetch_json=lambda url: _release(size=5),
            hash_download=lambda url: ("a" * 64, 4),
        )


def test_an_interrupted_download_is_never_cached_or_hashed(tmp_path, monkeypatch):
    def interrupted(url, filename):
        Path(filename).write_bytes(b"par")
        raise OSError("connection reset")

    monkeypatch.setattr(pin.urllib.request, "urlretrieve", interrupted)
    with pytest.raises(OSError, match="connection reset"):
        pin.sha256_of_download(_MODEL_URL, cache=tmp_path)
    assert not (tmp_path / "xx_model-1.0-py3-none-any.whl").exists()

    def complete(url, filename):
        Path(filename).write_bytes(b"whole")

    monkeypatch.setattr(pin.urllib.request, "urlretrieve", complete)
    assert pin.sha256_of_download(_MODEL_URL, cache=tmp_path) == (hashlib.sha256(b"whole").hexdigest(), 5)
    assert not list(tmp_path.glob("*.part"))


def test_read_top_level_caches_only_a_complete_wheel(tmp_path, monkeypatch):
    artifact = {
        "filename": "zz-1.0-py3-none-any.whl",
        "url": "https://files.pythonhosted.org/p/zz-1.0-py3-none-any.whl",
    }

    def interrupted(url, filename):
        Path(filename).write_bytes(b"PK")
        raise OSError("timed out")

    monkeypatch.setattr(pin.urllib.request, "urlretrieve", interrupted)
    with pytest.raises(OSError, match="timed out"):
        pin.read_top_level(artifact, cache=tmp_path)
    assert not (tmp_path / artifact["filename"]).exists()

    def complete(url, filename):
        with zipfile.ZipFile(filename, "w") as zf:
            zf.writestr("zz/__init__.py", "")
            zf.writestr("zz-1.0.dist-info/RECORD", "zz/__init__.py,,\nzz-1.0.dist-info/RECORD,,\n")

    monkeypatch.setattr(pin.urllib.request, "urlretrieve", complete)
    assert pin.read_top_level(artifact, cache=tmp_path) == ["zz"]


def test_the_black_mode_is_the_projects_own():
    config, mode = _project_black_mode()

    assert pin._black_mode() == mode
    # A formatting key the generator does not read would let a committed
    # manifest drift from `black --check .`; only file-selection keys may exist.
    assert set(config) <= {"line_length", "target_version", "include", "exclude", "extend_exclude", "force_exclude"}


def test_the_rendered_manifest_is_black_stable():
    import black

    resolved = pin.resolve_model(
        "xx",
        "xx_model",
        "1.0",
        ("_spacy",),
        fetch_json=lambda url: _release(size=5),
        hash_download=lambda url: ("c" * 64, 5),
    )
    text = pin.render_manifest(resolved)

    _config, mode = _project_black_mode()
    assert black.format_str(text, mode=mode) == text
    assert "'" not in text.split('"""', 2)[2]  # every string literal is double-quoted


def test_a_distribution_without_a_package_directory_is_refused(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("", encoding="utf-8")
    index = {
        "urls": [
            {
                "filename": "six-1.0-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/p/six-1.0-py3-none-any.whl",
                "digests": {"sha256": "0" * 64},
                "size": 1,
                "packagetype": "bdist_wheel",
            }
        ]
    }

    with pytest.raises(SystemExit, match="six"):
        pin.resolve_runtime(
            "_x",
            ["six"],
            "3.12",
            lock,
            run_compile=lambda args, stdin: "six==1.0\n",
            fetch_json=lambda url: index,
            read_top_level=lambda artifact: [],
        )


@pytest.mark.parametrize(
    "fixture", sorted((_ROOT / "tests" / "fixtures" / "language_packs").glob("*.resolved.json")), ids=lambda p: p.stem
)
def test_every_committed_manifest_is_the_generator_output(fixture):
    resolved = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = _ROOT / "anki_miner" / "languages" / resolved["code"] / "pack.py"
    assert manifest.read_text(encoding="utf-8") == pin.render_manifest(resolved)

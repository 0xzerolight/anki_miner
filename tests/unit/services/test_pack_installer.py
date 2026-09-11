"""Tests for the generic dependency-pack core the language and ASR packs share.

Only what the language-pack suite (tests/unit/services/test_language_pack_installer.py)
does not already exercise through its adapter: the pack-agnostic entry points,
the ``label`` in resume keys and progress lines, and DIRECTORY root members
(auditwheel/delvewheel ``<pkg>.libs/`` trees promoted beside the package dir).
Nothing hits the network: ``download_to_temp`` is replaced by an in-memory stub.
"""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages.pack_spec import ArtifactSpec, DependencyPack, PackComponent
from anki_miner.services import pack_installer as core
from tests.unit._resume_key_assert import assert_stable_resume_key


def _wheel(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


_LIBS_WHEEL = _wheel(
    {
        "xxnative/__init__.py": b"import _xxnative_ext\n",
        "xxnative/_ext.abi3.so": b"\x7fELF",
        "xxnative.libs/libxx-abc123.so.1": b"lib",
        "xxnative.libs/libgomp-def456.so.1": b"lib",
        "xxnative-1.0.dist-info/METADATA": b"Name: xxnative\n",
    }
)
_LIBS_SPEC = ArtifactSpec(
    url="https://files.pythonhosted.org/packages/xx/xxnative-1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
    sha256=hashlib.sha256(_LIBS_WHEEL).hexdigest(),
    kind="wheel",
    member_prefix="xxnative/",
    root_members=("xxnative.libs/",),
)
_LIBS_COMPONENT = PackComponent(import_name="xxnative", required=True, sentinels=("__init__.py",), universal=_LIBS_SPEC)

_PURE_WHEEL = _wheel({"xxpure/__init__.py": b"", "xxpure-1.0.dist-info/METADATA": b"Name: xxpure\n"})
_PURE_SPEC = ArtifactSpec(
    url="https://files.pythonhosted.org/packages/xx/xxpure-1.0-py3-none-any.whl",
    sha256=hashlib.sha256(_PURE_WHEEL).hexdigest(),
    kind="wheel",
    member_prefix="xxpure/",
)
_PURE_COMPONENT = PackComponent(import_name="xxpure", required=True, sentinels=("__init__.py",), universal=_PURE_SPEC)

_PACK = DependencyPack(name="xxpack", approx_download_mb=1, components=(_LIBS_COMPONENT, _PURE_COMPONENT))


class _Downloader:
    def __init__(self) -> None:
        self.payloads = {_LIBS_SPEC.url: _LIBS_WHEEL, _PURE_SPEC.url: _PURE_WHEEL}
        self.urls: list[str] = []
        self.resume_keys: list[str | None] = []

    def __call__(
        self, url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None, resume_key=None, resume_root=None
    ):
        assert_stable_resume_key(resume_key)
        assert max_bytes == core.MAX_ARTIFACT_BYTES
        self.urls.append(url)
        self.resume_keys.append(resume_key)
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / f"artifact-{len(self.urls)}.part"
        part.write_bytes(self.payloads[url])
        if progress is not None:
            progress(0, len(self.payloads[url]), "downloading")
        return part


@pytest.fixture
def downloader(monkeypatch) -> _Downloader:
    fake = _Downloader()
    monkeypatch.setattr(core, "download_to_temp", fake)
    return fake


@pytest.fixture
def clean_syspath():
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def _never_satisfied(_comp: PackComponent) -> bool:
    return False


def _install(label: str, components, root: Path, **kwargs) -> Path:
    kwargs.setdefault("display_noun", f"{label} pack")
    kwargs.setdefault("satisfied", _never_satisfied)
    return core.install_components(label, components, root, **kwargs)


class TestDependencyPack:
    def test_is_frozen_named_data(self) -> None:
        assert _PACK.name == "xxpack"
        assert [c.import_name for c in _PACK.components] == ["xxnative", "xxpure"]
        with pytest.raises(AttributeError):
            _PACK.name = "other"  # type: ignore[misc]


class TestDirectoryRootMembers:
    def test_the_libs_tree_is_promoted_beside_the_package(self, tmp_path, downloader) -> None:
        root = tmp_path / "root"

        _install("xxpack", _PACK.components, root)

        assert (root / "xxnative" / "_ext.abi3.so").is_file()
        assert (root / "xxnative.libs" / "libxx-abc123.so.1").is_file()
        assert (root / "xxnative.libs" / "libgomp-def456.so.1").is_file()
        assert not (root / "xxnative-1.0.dist-info").exists()
        assert not any(path.name.startswith(".staging-") for path in root.iterdir())

    def test_root_member_present_accepts_a_directory_prefix(self, tmp_path) -> None:
        assert not core.root_member_present(tmp_path, "xxnative.libs/")
        (tmp_path / "xxnative.libs").mkdir()
        assert not core.root_member_present(tmp_path, "xxnative.libs/")  # empty dir is not a payload
        (tmp_path / "xxnative.libs" / "libxx.so").write_bytes(b"x")
        assert core.root_member_present(tmp_path, "xxnative.libs/")

    def test_root_member_present_keeps_the_file_prefix_form(self, tmp_path) -> None:
        assert not core.root_member_present(tmp_path, "_xxroot.")
        (tmp_path / "_xxroot.abi3.so").write_bytes(b"x")
        assert core.root_member_present(tmp_path, "_xxroot.")

    def test_a_component_without_its_libs_tree_is_incomplete(self, tmp_path) -> None:
        (tmp_path / "xxnative").mkdir()
        (tmp_path / "xxnative" / "__init__.py").write_bytes(b"")
        assert not core.component_complete(tmp_path, _LIBS_COMPONENT)
        (tmp_path / "xxnative.libs").mkdir()
        (tmp_path / "xxnative.libs" / "libxx.so").write_bytes(b"x")
        assert core.component_complete(tmp_path, _LIBS_COMPONENT)

    def test_a_wheel_missing_the_declared_libs_tree_is_refused(self, tmp_path, downloader) -> None:
        downloader.payloads[_LIBS_SPEC.url] = _wheel({"xxnative/__init__.py": b""})
        spec = ArtifactSpec(
            url=_LIBS_SPEC.url,
            sha256=hashlib.sha256(downloader.payloads[_LIBS_SPEC.url]).hexdigest(),
            kind="wheel",
            member_prefix="xxnative/",
            root_members=("xxnative.libs/",),
        )
        comp = PackComponent(import_name="xxnative", required=True, sentinels=("__init__.py",), universal=spec)

        with pytest.raises(SetupError, match=r"xxnative\.libs/\*"):
            _install("xxpack", (comp,), tmp_path / "root")
        assert not (tmp_path / "root" / "xxnative").exists()


class TestInstallComponents:
    def test_the_label_names_the_resume_key_and_the_progress_line(self, tmp_path, downloader) -> None:
        lines: list[str] = []

        _install(
            "asr", _PACK.components, tmp_path / "root", progress=lambda _done, _total, message: lines.append(message)
        )

        assert downloader.resume_keys == [
            f"pack-asr-xxnative-{_LIBS_SPEC.sha256[:16]}",
            f"pack-asr-xxpure-{_PURE_SPEC.sha256[:16]}",
        ]
        assert lines == ["ASR pack (1/2): downloading", "ASR pack (2/2): downloading"]

    def test_satisfied_components_are_skipped(self, tmp_path, downloader) -> None:
        _install("xxpack", _PACK.components, tmp_path / "root", satisfied=lambda comp: comp.import_name == "xxnative")

        assert downloader.urls == [_PURE_SPEC.url]

    def test_the_display_noun_names_the_user_facing_errors(self, tmp_path, downloader) -> None:
        comp = PackComponent(
            import_name="xxplat",
            required=True,
            sentinels=("__init__.py",),
            per_platform={("noplat", "noarch"): _PURE_SPEC},
        )

        with pytest.raises(SetupError, match=r"^The xx language pack is not supported on this platform/Python"):
            _install("xx", (_PURE_COMPONENT, comp), tmp_path / "root", display_noun="xx language pack")
        assert downloader.urls == []

        from anki_miner.exceptions import OperationCancelled

        with pytest.raises(OperationCancelled, match=r"^xx language pack installation cancelled$"):
            _install(
                "xx",
                (_PURE_COMPONENT,),
                tmp_path / "root",
                display_noun="xx language pack",
                cancelled_check=lambda: True,
            )

    def test_components_supported_ignores_optional_gaps(self) -> None:
        optional = PackComponent(
            import_name="xxopt",
            required=False,
            sentinels=("__init__.py",),
            per_platform={("noplat", "noarch"): _PURE_SPEC},
        )
        required = PackComponent(
            import_name="xxreq",
            required=True,
            sentinels=("__init__.py",),
            per_platform={("noplat", "noarch"): _PURE_SPEC},
        )
        assert core.components_supported((_PURE_COMPONENT, optional))
        assert not core.components_supported((_PURE_COMPONENT, required))


class TestSyspath:
    def test_root_has_complete_component_reads_the_disk(self, tmp_path) -> None:
        assert not core.root_has_complete_component(tmp_path, _PACK.components)
        (tmp_path / "xxpure").mkdir()
        (tmp_path / "xxpure" / "__init__.py").write_bytes(b"")
        assert core.root_has_complete_component(tmp_path, _PACK.components)

    def test_append_to_syspath_appends_once(self, tmp_path, clean_syspath) -> None:
        assert core.append_to_syspath(tmp_path)
        assert not core.append_to_syspath(tmp_path)
        assert sys.path[-1] == str(tmp_path)
        assert sys.path.count(str(tmp_path)) == 1

    def test_importable_outside_ignores_a_resolution_inside_a_root(self, tmp_path, clean_syspath) -> None:
        (tmp_path / "xxpure").mkdir()
        (tmp_path / "xxpure" / "__init__.py").write_bytes(b"")
        core.append_to_syspath(tmp_path)
        import importlib

        importlib.invalidate_caches()

        assert not core.importable_outside("xxpure", (tmp_path,))
        assert core.importable_outside("xxpure", (tmp_path / "elsewhere",))
        assert not core.importable_outside("xxnever_installed_anywhere", (tmp_path,))

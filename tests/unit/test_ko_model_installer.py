"""Tests for the in-app Korean (kiwipiepy) model pack installer.

Tests NEVER hit the network: ``download_to_temp`` is monkeypatched to write a
fixture sdist (a real ``.tar.gz`` built in memory) into the staging dir, and the
pinned sha256 is swapped for the fixture's own digest.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import threading
from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services import ko_model_installer
from tests.unit._resume_key_assert import assert_stable_resume_key as _assert_stable_resume_key


def _make_sdist(members: dict[str, bytes]) -> bytes:
    """Return gzipped tar bytes containing *members* (arcname -> contents)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for arcname, data in members.items():
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_PREFIX = ko_model_installer._MEMBER_PREFIX

#: A fake kiwipiepy_model sdist: the model payload plus sdist metadata that must
#: not be extracted.
_MODEL_SDIST = _make_sdist(
    {
        f"{_PREFIX}__init__.py": b"# model package",
        f"{_PREFIX}sj.morph": b"morphemes",
        f"{_PREFIX}default.dict": b"default",
        f"{_PREFIX}combiningRule.txt": b"rules",
        f"{_PREFIX}cong.mdl": b"language-model",
        "kiwipiepy_model-0.23.0/PKG-INFO": b"meta",  # must NOT be extracted
        "kiwipiepy_model-0.23.0/MANIFEST.in": b"manifest",
    }
)


def _pin_fixture_sha(monkeypatch, payload: bytes = _MODEL_SDIST) -> None:
    """Repin the module sha256 onto the fixture sdist's own digest."""
    monkeypatch.setattr(ko_model_installer, "KO_MODEL_SHA256", hashlib.sha256(payload).hexdigest())


def _patch_download(monkeypatch, payload: bytes = _MODEL_SDIST):
    """Patch download_to_temp to write *payload* as the downloaded .part sdist."""

    def fake_download(
        url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None, resume_key=None, resume_root=None
    ):
        _assert_stable_resume_key(resume_key)
        assert url == ko_model_installer.KO_MODEL_URL
        if progress is not None:
            progress(0, len(payload), "downloading")
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / "fake.part"
        part.write_bytes(payload)
        return part

    monkeypatch.setattr(ko_model_installer, "download_to_temp", fake_download)


class TestModelRoot:
    def test_root_sits_beside_the_other_managed_dirs_in_the_app_home(self, tmp_path) -> None:
        assert ko_model_installer.ko_model_root(tmp_path) == tmp_path / "ko_model"

    def test_root_defaults_to_the_app_home_read_at_call_time(self, monkeypatch, tmp_path) -> None:
        # Read at call time (never snapshotted at import) so the test-home
        # isolation fixtures redirect it like every other managed directory.
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        assert ko_model_installer.ko_model_root() == tmp_path / "ko_model"


class TestIsInstalled:
    def test_false_on_empty_dir(self, tmp_path) -> None:
        assert ko_model_installer.is_installed(tmp_path) is False

    def test_false_when_a_model_file_is_missing(self, tmp_path) -> None:
        model = ko_model_installer.ko_model_path(tmp_path)
        model.mkdir(parents=True)
        (model / "sj.morph").write_bytes(b"x")
        assert ko_model_installer.is_installed(tmp_path) is False

    def test_true_when_every_model_file_is_present(self, tmp_path) -> None:
        model = ko_model_installer.ko_model_path(tmp_path)
        model.mkdir(parents=True)
        for name in ko_model_installer._MODEL_SENTINELS:
            (model / name).write_bytes(b"x")
        assert ko_model_installer.is_installed(tmp_path) is True

    def test_recovers_dangling_backup(self, tmp_path) -> None:
        backup = tmp_path / "kiwipiepy_model.bak-20260721000000000001"
        backup.mkdir()
        for name in ko_model_installer._MODEL_SENTINELS:
            (backup / name).write_bytes(b"x")

        assert ko_model_installer.is_installed(tmp_path) is True
        assert ko_model_installer.ko_model_path(tmp_path).is_dir()
        assert not backup.exists()


class TestInstall:
    def test_happy_path_stages_then_promotes_the_model(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)

        result = ko_model_installer.install_ko_model(tmp_path)

        assert result == tmp_path
        model = ko_model_installer.ko_model_path(tmp_path)
        assert (model / "sj.morph").read_bytes() == b"morphemes"
        assert (model / "cong.mdl").read_bytes() == b"language-model"
        # sdist metadata outside the package dir is NOT extracted.
        assert not (tmp_path / "PKG-INFO").exists()
        assert not (tmp_path / "kiwipiepy_model-0.23.0").exists()
        assert ko_model_installer.is_installed(tmp_path) is True

    def test_no_part_files_or_staging_left_behind(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)
        ko_model_installer.install_ko_model(tmp_path)
        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob(".staging-*")) == []

    def test_install_sweeps_orphans_from_a_crashed_run(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)
        (tmp_path / "leftover.part").write_bytes(b"x" * 1024)
        orphan = tmp_path / ".staging-ko-model-abcd"
        orphan.mkdir()
        (orphan / "junk").write_bytes(b"junk")

        ko_model_installer.install_ko_model(tmp_path)

        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob(".staging-*")) == []
        assert ko_model_installer.is_installed(tmp_path) is True

    def test_progress_is_forwarded_with_a_label(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)
        calls: list[tuple] = []
        ko_model_installer.install_ko_model(tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))
        assert calls
        assert all(msg == "Korean model: downloading" for _, _, msg in calls)

    def test_reinstall_replaces_the_existing_model(self, tmp_path, monkeypatch) -> None:
        model = ko_model_installer.ko_model_path(tmp_path)
        model.mkdir(parents=True)
        (model / "stale.mdl").write_bytes(b"old")
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)

        ko_model_installer.install_ko_model(tmp_path)

        assert not (model / "stale.mdl").exists()
        assert (model / "sj.morph").exists()


class TestRefusals:
    def test_sha_mismatch_refuses_and_promotes_nothing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(ko_model_installer, "KO_MODEL_SHA256", "0" * 64)
        _patch_download(monkeypatch)

        with pytest.raises(SetupError, match="checksum mismatch"):
            ko_model_installer.install_ko_model(tmp_path)

        assert ko_model_installer.is_installed(tmp_path) is False
        assert list(tmp_path.glob("*.part")) == []

    def test_a_sdist_without_the_package_dir_refuses(self, tmp_path, monkeypatch) -> None:
        payload = _make_sdist({"kiwipiepy_model-0.23.0/PKG-INFO": b"meta"})
        _pin_fixture_sha(monkeypatch, payload)
        _patch_download(monkeypatch, payload)

        with pytest.raises(SetupError, match="no kiwipiepy_model"):
            ko_model_installer.install_ko_model(tmp_path)

        assert ko_model_installer.is_installed(tmp_path) is False

    def test_a_corrupt_archive_refuses(self, tmp_path, monkeypatch) -> None:
        payload = b"not a tarball at all"
        _pin_fixture_sha(monkeypatch, payload)
        _patch_download(monkeypatch, payload)

        with pytest.raises(SetupError, match="not a valid archive"):
            ko_model_installer.install_ko_model(tmp_path)

        assert ko_model_installer.is_installed(tmp_path) is False

    def test_a_traversing_member_refuses(self, tmp_path, monkeypatch) -> None:
        payload = _make_sdist({f"{_PREFIX}../../escaped.mdl": b"pwned"})
        _pin_fixture_sha(monkeypatch, payload)
        _patch_download(monkeypatch, payload)

        with pytest.raises(SetupError, match="unsafe path"):
            ko_model_installer.install_ko_model(tmp_path)

        assert not (tmp_path / "escaped.mdl").exists()
        assert not (tmp_path.parent / "escaped.mdl").exists()

    def test_cancel_before_the_download_promotes_nothing(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        _patch_download(monkeypatch)
        cancel = threading.Event()
        cancel.set()

        with pytest.raises(OperationCancelled):
            ko_model_installer.install_ko_model(tmp_path, cancel_event=cancel)

        assert ko_model_installer.is_installed(tmp_path) is False
        assert list(tmp_path.glob("*.part")) == []

    def test_cancel_after_the_download_cleans_the_part_file(self, tmp_path, monkeypatch) -> None:
        _pin_fixture_sha(monkeypatch)
        cancel = threading.Event()

        def fake_download(url, *, dest_dir, **_kwargs) -> Path:
            dest_dir.mkdir(parents=True, exist_ok=True)
            part = dest_dir / "fake.part"
            part.write_bytes(_MODEL_SDIST)
            cancel.set()  # the user pressed cancel while the bytes were landing
            return part

        monkeypatch.setattr(ko_model_installer, "download_to_temp", fake_download)

        with pytest.raises(OperationCancelled):
            ko_model_installer.install_ko_model(tmp_path, cancel_event=cancel)

        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob(".staging-*")) == []
        assert ko_model_installer.is_installed(tmp_path) is False


class TestPin:
    def test_url_and_sha_are_pinned_together_on_pypi(self) -> None:
        assert ko_model_installer.KO_MODEL_URL.startswith("https://files.pythonhosted.org/packages/")
        assert ko_model_installer.KO_MODEL_URL.endswith(f"kiwipiepy_model-{ko_model_installer.KO_MODEL_VERSION}.tar.gz")
        assert len(ko_model_installer.KO_MODEL_SHA256) == 64

    def test_the_member_prefix_tracks_the_pinned_version(self) -> None:
        version = ko_model_installer.KO_MODEL_VERSION
        assert f"kiwipiepy_model-{version}/kiwipiepy_model/" == ko_model_installer._MEMBER_PREFIX

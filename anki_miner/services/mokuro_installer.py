"""In-app mokuro install: a pinned uv binary, then ``uv venv`` + ``uv pip install``.

mokuro is a Python package (torch + transformers + manga-ocr, 1–4 GB), so the
sha256-pinned-single-binary model of ``alass_installer`` does not fit and the
wheel-pack model of ``language_pack_installer`` (platform wheels pinned by
hand) is unmaintainable for torch. Instead:

1. download uv (~20 MB, sha256-pinned per platform) into ``bin_root`` — the
   only artifact this module vouches for; ``uv.version`` beside it is the
   receipt that lets a later install skip the download;
2. ``uv venv --python 3.12 --clear uv_root/mokuro`` with a uv-managed CPython
   (``UV_PYTHON_PREFERENCE=only-managed``) so the host's Python — absent in
   the frozen app, or too new for torch — is never involved;
3. ``uv pip install --torch-backend=auto mokuro==0.2.5`` — uv's own
   accelerator detection picks the CUDA index on an NVIDIA host and the
   CPU/MPS wheels elsewhere. ``UV_NO_CACHE=1``: no second copy of the wheels.

Everything runs through ``run_supervised`` (cancel, timeout, log tail); the
console script the resolver looks for is ``managed_mokuro_path(uv_root)``.
Stateless, GUI-free; the ``InstallWorker`` task adapts it to Qt.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.mokuro_resolver import (
    managed_mokuro_installed,
    managed_mokuro_path,
    managed_python_path,
    mokuro_env_dir,
    scrubbed_python_env,
)
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised

logger = logging.getLogger(__name__)

UV_VERSION = "0.12.10"
MOKURO_REQUIREMENT = "mokuro==0.2.5"
MOKURO_PYTHON = "3.12"

#: Phase lines handed to ``status``. Exact strings, so the GUI worker can map
#: each to its translation; everything else it receives is raw uv output.
STATUS_DOWNLOADING_UV = "Downloading uv…"
STATUS_PREPARING_PYTHON = f"Preparing Python {MOKURO_PYTHON}…"
STATUS_INSTALLING_MOKURO = f"Installing {MOKURO_REQUIREMENT}…"
STATUS_DOWNLOADING_PACKAGES = "Downloading packages — torch is large, this can take a while…"

_VENV_TIMEOUT_S = 30 * 60  # includes the managed-CPython download
_PIP_TIMEOUT_S = 3 * 60 * 60  # torch + CUDA libs on a slow link
_UV_RELEASES = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"


@dataclass(frozen=True)
class _UvSpec:
    url: str
    sha256: str
    archive_kind: str  # "tar.gz" | "zip"
    member: str  # archive member holding the uv executable


def _tar_spec(target: str, sha256: str) -> _UvSpec:
    return _UvSpec(
        url=f"{_UV_RELEASES}/uv-{target}.tar.gz", sha256=sha256, archive_kind="tar.gz", member=f"uv-{target}/uv"
    )


#: (sys.platform, normalised machine) → pinned asset. Checksums copied from the
#: release's ``*.sha256`` files on 2026-09-07 (linux-x86_64, darwin-arm64 and
#: windows-x86_64 re-hashed locally from the downloaded archives).
_UV_SPECS: dict[tuple[str, str], _UvSpec] = {
    ("linux", "x86_64"): _tar_spec(
        "x86_64-unknown-linux-gnu", "173d95a0c32d18c896c46ba6fafbf3cf9c14ab74b033f81b76c883ef492a976b"
    ),
    ("linux", "aarch64"): _tar_spec(
        "aarch64-unknown-linux-gnu", "9ff6b9d4665edcdd3a88dcc73cd1eb641754deb927f14e8c62ebfde6bf4f5f5e"
    ),
    ("darwin", "aarch64"): _tar_spec(
        "aarch64-apple-darwin", "51c6170e8e3a01cef9f33b94f582b7b81ac65046f55d40afb35f9cff5a68c179"
    ),
    ("darwin", "x86_64"): _tar_spec(
        "x86_64-apple-darwin", "5296d5aa2b9143360405eea866f8ef4d5dc8986b164eb0dc35e8f876a9304d30"
    ),
    ("win32", "x86_64"): _UvSpec(
        url=f"{_UV_RELEASES}/uv-x86_64-pc-windows-msvc.zip",
        sha256="f65744f94072152b1f86ba2aace4d01f1124d9a8ecb235805039e3718c36cac2",
        archive_kind="zip",
        member="uv.exe",
    ),
}


def _machine() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return m


def _current_spec() -> _UvSpec | None:
    return _UV_SPECS.get((sys.platform, _machine()))


def mokuro_install_supported() -> bool:
    """True when a pinned uv asset exists for this platform/arch."""
    return _current_spec() is not None


def uv_target_path(bin_root: Path) -> Path:
    return bin_root / ("uv.exe" if sys.platform == "win32" else "uv")


def _uv_receipt_path(bin_root: Path) -> Path:
    return bin_root / "uv.version"


def is_installed(uv_root: Path) -> bool:
    """True when the managed mokuro console script is present and runnable. Cheap."""
    return managed_mokuro_installed(uv_root)


def _uv_env(uv_root: Path) -> dict[str, str]:
    """Child env for every uv call: managed Python only, no cache, no user config."""
    env = scrubbed_python_env()
    env.update(
        {
            "UV_PYTHON_INSTALL_DIR": str(uv_root / "python"),
            "UV_PYTHON_PREFERENCE": "only-managed",
            "UV_NO_CACHE": "1",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
            "UV_NO_MODIFY_PATH": "1",
            "NO_COLOR": "1",
        }
    )
    return env


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("mokuro installation cancelled")


def _ensure_uv(bin_root: Path, spec: _UvSpec, *, progress: DownloadProgressFn | None, cancel_event) -> Path:
    target = uv_target_path(bin_root)
    receipt = _uv_receipt_path(bin_root)
    if target.is_file() and receipt.is_file() and receipt.read_text(encoding="utf-8").strip() == UV_VERSION:
        return target
    bin_root.mkdir(parents=True, exist_ok=True)
    part = download_to_temp(
        spec.url,
        dest_dir=bin_root,
        progress=progress,
        cancelled_check=cancel_event.is_set if cancel_event is not None else None,
        resume_key=f"uv-{spec.sha256[:16]}",
    )
    try:
        _check_cancel(cancel_event)
        verify_sha256(part, spec.sha256, "uv download")
        placed = _place_uv(part, spec, bin_root)
        receipt.write_text(UV_VERSION, encoding="utf-8")
        log_summary(logger, "uv install done", installed=placed, version=UV_VERSION)
        return placed
    finally:
        cleanup_part(part)


def _place_uv(archive: Path, spec: _UvSpec, bin_root: Path) -> Path:
    """Extract ``spec.member`` from *archive* to ``uv_target_path``, atomically."""
    target = uv_target_path(bin_root)
    staged = target.with_name(target.name + ".staged")
    try:
        if spec.archive_kind == "zip":
            with zipfile.ZipFile(archive) as zf, zf.open(spec.member) as src, staged.open("wb") as dst:
                dst.write(src.read())
        else:
            with tarfile.open(archive, "r:gz") as tar:
                member = tar.extractfile(spec.member)
                if member is None:
                    raise SetupError(f"uv archive is missing {spec.member}")
                with member, staged.open("wb") as dst:
                    dst.write(member.read())
        if sys.platform != "win32":
            os.chmod(staged, 0o755)
        os.replace(staged, target)
    except (KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise SetupError(f"uv archive is not usable: {exc}") from exc
    finally:
        staged.unlink(missing_ok=True)
    return target


def _run_uv(
    cmd: list[str],
    *,
    timeout_s: float,
    env: dict[str, str],
    cwd: Path,
    status: Callable[[str], None] | None,
    cancel_event,
    op: str,
) -> None:
    tail: list[str] = []

    def on_line(line: str) -> None:
        if not line.strip() or line.lstrip().startswith(("+ ", "- ", "~ ")):
            return  # per-package add/remove rows are noise on a status label
        tail.append(line)
        if status is not None:
            status(line.strip())
            if line.startswith("Resolved "):
                status(STATUS_DOWNLOADING_PACKAGES)

    result = run_supervised(
        cmd,
        timeout_s=timeout_s,
        cancel=cancel_event,
        env=env,
        cwd=cwd,
        line_callback=on_line,
        combine_stderr=True,
        retain_output=False,
        op=op,
    )
    if result.state is SupervisedState.CANCELLED:
        raise OperationCancelled("mokuro installation cancelled")
    if result.state is SupervisedState.TIMED_OUT:
        raise SetupError(f"{op} timed out after {int(timeout_s)}s")
    if result.state is not SupervisedState.COMPLETED:
        detail = "\n".join(tail[-15:])
        if isinstance(result.error, FileNotFoundError):
            detail = f"{cmd[0]} could not be started."
        raise SetupError(f"{op} failed (exit code {result.returncode}).\n{detail}".strip())


def install_mokuro(
    bin_root: Path,
    uv_root: Path,
    *,
    status: Callable[[str], None] | None = None,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Install (or reinstall) mokuro under *uv_root*; returns the console script path.

    Raises:
        SetupError: unsupported platform, download/verify failure, or a uv step failing.
        OperationCancelled: cancel requested before or during a step.
    """
    spec = _current_spec()
    if spec is None:
        raise SetupError(
            f"In-app mokuro install is not supported on this platform ({sys.platform}/{platform.machine()})."
        )
    _check_cancel(cancel_event)
    if status is not None:
        status(STATUS_DOWNLOADING_UV)
    uv = _ensure_uv(bin_root, spec, progress=progress, cancel_event=cancel_event)
    _check_cancel(cancel_event)

    uv_root.mkdir(parents=True, exist_ok=True)
    env = _uv_env(uv_root)
    env_dir = mokuro_env_dir(uv_root)
    if status is not None:
        status(STATUS_PREPARING_PYTHON)
    _run_uv(
        [str(uv), "venv", "--python", MOKURO_PYTHON, "--clear", str(env_dir)],
        timeout_s=_VENV_TIMEOUT_S,
        env=env,
        cwd=uv_root,
        status=status,
        cancel_event=cancel_event,
        op="uv venv",
    )
    _check_cancel(cancel_event)
    if status is not None:
        status(STATUS_INSTALLING_MOKURO)
    _run_uv(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            str(managed_python_path(uv_root)),
            "--torch-backend",
            "auto",
            MOKURO_REQUIREMENT,
        ],
        timeout_s=_PIP_TIMEOUT_S,
        env=env,
        cwd=uv_root,
        status=status,
        cancel_event=cancel_event,
        op="uv pip install",
    )
    shim = managed_mokuro_path(uv_root)
    if not is_installed(uv_root):
        raise SetupError(f"uv reported success but {shim} is missing.")
    log_summary(logger, "mokuro install done", shim=shim, requirement=MOKURO_REQUIREMENT)
    return shim

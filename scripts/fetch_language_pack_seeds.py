"""Seed the dependency packs the release bundle smokes need.

Every non-Japanese mining engine ships as an in-app download pack rather than
bundle content (``anki_miner/services/language_pack_installer.py``), so the
release's per-language bundle smokes have to be HANDED one: the frozen app they
exercise carries a tokenizer module and no engine. This fills
``<dest_dir>/<code>/`` with exactly what the app would download, using the app's
own installer against the same pinned manifests -- no URL, checksum or extraction
rule is duplicated in the workflow, which is what the two hand-written
curl-and-tar steps this replaces got wrong once per platform. The ASR engine
pack is seeded the same way under ``<dest_dir>/asr/`` when ``asr`` is among the
codes (``anki_miner/services/asr/asr_pack_installer.py``).

``scripts/bundle_smoke.sh`` copies each seeded directory into its isolated
``ANKI_MINER_HOME`` before running that pack's leg.

Failure policy, and the whole reason this script exists rather than a shell one:

* A download that never completed because the NETWORK was not there -- a 5xx, a
  connection error, a timeout, a mid-stream drop (``DownloadFailed`` whose cause
  is not an HTTP 4xx) -- warns loudly, skips that pack, and leaves the exit code
  0. The bundle is correct; the fetch is not, and a release must not go red over
  someone else's outage. The smoke then reports ``SKIP language-<code>`` (or
  ``SKIP asr``), and ``scripts/release_dryrun.sh`` refuses a release whose asr
  leg skipped.
* A ``DownloadFailed`` caused by an HTTP 4xx is a deterministically WRONG PIN (a
  404 on a moved wheel) and exits 1: falling open there would let every leg skip
  and a release ship with zero proof.
* Anything else -- a checksum mismatch above all, but also a bad archive, a
  missing sentinel, or a platform the pack does not support -- exits 1. Those
  say the bytes or the pins are wrong, and smoking against them proves nothing.

Usage:
    python scripts/fetch_language_pack_seeds.py <dest_dir> <code|asr>...
    python scripts/fetch_language_pack_seeds.py <dest_dir> <code|asr>... --print-manifest
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# scripts/ is on sys.path when this runs as `python scripts/…`, the repo root is
# not; the release job pip-installs the package, but keep a source checkout
# runnable too.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402

from anki_miner.exceptions import DownloadFailed, SetupError  # noqa: E402
from anki_miner.languages.pack_spec import PackComponent  # noqa: E402
from anki_miner.services.asr import asr_pack_installer  # noqa: E402
from anki_miner.services.language_pack_installer import (  # noqa: E402
    install_language_pack,
    load_pack,
    pack_supported,
)
from anki_miner.services.pack_installer import artifact_for  # noqa: E402

# artifact_for is the installer core's own platform/ABI resolution, imported
# rather than reimplemented: --print-manifest exists to answer "what would this
# runner download?", and a second copy of that rule would answer a different
# question the moment either drifts.


def _component_manifest(comp: PackComponent) -> dict[str, Any]:
    """Describe one component and the artifact resolved for THIS platform."""
    spec = artifact_for(comp)
    return {
        "import_name": comp.import_name,
        "required": comp.required,
        "artifact": None if spec is None else {"url": spec.url, "sha256": spec.sha256, "kind": spec.kind},
    }


_ASR = "asr"


def _is_client_error(exc: BaseException) -> bool:
    """True when *exc*'s cause chain holds an HTTP 4xx: the pin is wrong, not the network."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, requests.HTTPError) and cause.response is not None:
            return 400 <= cause.response.status_code < 500
        cause = cause.__cause__ or cause.__context__
    return False


def _noun(code: str) -> str:
    return "ASR engine pack" if code == _ASR else "language pack"


def _pack_manifest(code: str, root: Path) -> dict[str, Any]:
    """Describe one pack, or record that the language has none (ja)."""
    if code == _ASR:
        pack = asr_pack_installer.PACK
        return {
            "code": code,
            "dest": str(root),
            "supported": asr_pack_installer.asr_pack_supported(),
            "approx_download_mb": pack.approx_download_mb,
            "components": [_component_manifest(comp) for comp in pack.components],
        }
    language_pack = load_pack(code)
    if language_pack is None:
        return {"code": code, "dest": str(root), "supported": False, "components": []}
    return {
        "code": code,
        "dest": str(root),
        "supported": pack_supported(code),
        "approx_download_mb": language_pack.approx_download_mb,
        "components": [_component_manifest(comp) for comp in language_pack.components],
    }


def _install(code: str, root: Path) -> None:
    if code == _ASR:
        asr_pack_installer.install_asr_pack(root)
    else:
        install_language_pack(code, root)


def resolved_manifest(codes: Sequence[str], dest: Path) -> dict[str, Any]:
    """Return what this interpreter on this machine would fetch, as plain data."""
    return {
        "platform": {
            "sys_platform": sys.platform,
            "machine": platform.machine(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
        "packs": [_pack_manifest(code, dest / code) for code in codes],
    }


def _describe(root: Path) -> str:
    files = [path for path in root.rglob("*") if path.is_file()]
    megabytes = sum(path.stat().st_size for path in files) / (1024 * 1024)
    return f"{len(files)} files, {megabytes:.1f} MB"


def seed(codes: Sequence[str], dest: Path) -> int:
    """Install every code's pack into ``dest/<code>``; return the exit code."""
    for code in codes:
        root = dest / code
        noun = _noun(code)
        print(f"Seeding the {code} {noun} into {root}", flush=True)
        try:
            _install(code, root)
        except DownloadFailed as exc:
            if _is_client_error(exc):
                print(
                    f"::error::{code} {noun} seed failed on an HTTP client error - the pin is wrong: {exc}", flush=True
                )
                return 1
            print(
                f"::warning::{code} {noun} seed failed ({exc}) - "
                f"the {code} bundle smoke will be skipped. NOT failing the release.",
                flush=True,
            )
            continue
        except SetupError as exc:
            print(f"::error::{code} {noun} seed failed: {exc}", flush=True)
            return 1
        print(f"Seeded the {code} {noun}: {_describe(root)}", flush=True)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the dependency packs for the release bundle smokes.")
    parser.add_argument("dest_dir", help="Directory to fill with one <code>/ subdirectory per pack")
    parser.add_argument(
        "codes", nargs="+", metavar="code", help="Mining language codes, or asr for the ASR engine pack, e.g. zh ko asr"
    )
    parser.add_argument(
        "--print-manifest",
        action="store_true",
        help="Print the artifacts this platform would fetch as JSON and exit, downloading nothing",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    dest = Path(args.dest_dir)
    if args.print_manifest:
        print(json.dumps(resolved_manifest(args.codes, dest), indent=2))
        return 0
    return seed(args.codes, dest)


if __name__ == "__main__":
    sys.exit(main())

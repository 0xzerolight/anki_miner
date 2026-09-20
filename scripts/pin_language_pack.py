"""Generate a ``languages/<code>/pack.py`` manifest from pinned upstream metadata.

A pack manifest is sha256-pinned data that the in-app installer trusts, and a
spaCy runtime pack is ~20 distributions x 5 platforms, so it is never written by
hand. Two modes:

* ``model``   -- one universal wheel from a GitHub release (the spaCy models are
  not on PyPI); the wheel is downloaded and hashed, because the release assets
  carry no digest. ``--with-wheel NAME==VERSION`` adds a pure PyPI wheel the
  model imports at runtime (ru: pymorphy3 and its dictionaries), and
  ``--dist-info-root NAME`` keeps that wheel's ``.dist-info/`` as a pack-root
  member, for a distribution discovered through entry points.
* ``runtime`` -- a requirement set resolved PER PLATFORM by ``uv pip compile``
  under ``--constraint requirements.lock`` (a bundle-resident package the
  resolver would have to move makes the compile fail, never silently omits it),
  minus every lock-pinned distribution the BASE install resolves to (the lock
  also pins the optional extras' closures, which a frozen bundle excludes or
  never imports); sha256 and URL from the PyPI JSON
  index; import names from each wheel's ``RECORD`` (``build/pin-cache`` holds
  the one wheel per distribution this reads).

Both write the manifest and ``tests/fixtures/language_packs/<code>.resolved.json``;
``tests/unit/test_pin_language_pack.py`` re-renders every committed fixture, so a
hand edit to a generated manifest fails the gate. Regeneration is a manual run,
like the yt-dlp pin check. ``UV`` names the uv binary when the one on ``PATH``
is a shim that refuses ``pip compile``.

Usage:
    python scripts/pin_language_pack.py model --code de --package de_core_news_sm --version 3.8.0 --requires _spacy
    python scripts/pin_language_pack.py runtime --code _spacy --requirement "spacy>=3.8,<3.8.15" --abi 3.12
    python scripts/pin_language_pack.py model --code ru --package ru_core_news_sm --version 3.8.0 --requires _spacy \\
        --with-wheel pymorphy3==2.0.6 --with-wheel dawg2-python==0.9.0 \\
        --with-wheel pymorphy3-dicts-ru==2.4.417150.4580142 --dist-info-root pymorphy3-dicts-ru
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The real uv binary; UV overrides it (a shim named uv may refuse pip compile).
UV = os.environ.get("UV") or shutil.which("uv") or "uv"
MODEL_RELEASE = "https://api.github.com/repos/explosion/spacy-models/releases/tags/{package}-{version}"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
PIN_CACHE = REPO_ROOT / "build" / "pin-cache"
#: Named by a requirement in the closure but imported by no pack code at runtime:
#: spaCy's metadata still requires setuptools, whose only mention is a setup.py
#: template string in spacy/cli/package.py.
NOT_IMPORTED_AT_RUNTIME = frozenset({"setuptools"})

#: (sys.platform, platform.machine()) -> uv --python-platform, in manifest order.
PLATFORMS: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "x86_64-manylinux_2_28",
    ("linux", "aarch64"): "aarch64-manylinux_2_28",
    ("win32", "AMD64"): "x86_64-pc-windows-msvc",
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("darwin", "x86_64"): "x86_64-apple-darwin",
}
_TAG_MATCH: dict[tuple[str, str], Callable[[str], bool]] = {
    ("linux", "x86_64"): lambda tag: "manylinux" in tag and "x86_64" in tag,
    ("linux", "aarch64"): lambda tag: "manylinux" in tag and "aarch64" in tag,
    ("win32", "AMD64"): lambda tag: "win_amd64" in tag,
    ("darwin", "arm64"): lambda tag: "macosx" in tag and ("arm64" in tag or "universal2" in tag),
    ("darwin", "x86_64"): lambda tag: "macosx" in tag and ("x86_64" in tag or "universal2" in tag),
}

FetchJson = Callable[[str], Any]
RunCompile = Callable[[list[str], str], str]
ReadTopLevel = Callable[[Mapping[str, Any]], list[str]]
#: ``url -> (sha256 hex digest, size in bytes)`` of the downloaded file.
HashDownload = Callable[[str], tuple[str, int]]


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "anki-miner-pin-language-pack"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https hosts
        return json.load(response)


def run_compile(args: list[str], stdin: str) -> str:
    completed = subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)  # noqa: S603
    if completed.returncode != 0:
        raise SystemExit(f"uv pip compile failed:\n{completed.stderr}")
    return completed.stdout


def _cached_download(url: str, local: Path) -> Path:
    """Fetch *url* to *local* unless it is already there.

    The bytes land in ``<local>.part`` and take the final name only once the
    download finished, so an interrupted fetch is never cached, hashed or read.
    """
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")
        urllib.request.urlretrieve(url, part)  # noqa: S310 - pinned https artifact
        os.replace(part, local)
    return local


def sha256_of_download(url: str, cache: Path = PIN_CACHE) -> tuple[str, int]:
    """Download *url* into the pin cache (once); return its sha256 and its size in bytes."""
    local = _cached_download(url, cache / url.rsplit("/", 1)[-1])
    digest = hashlib.sha256()
    with local.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest(), local.stat().st_size


def _artifact(filename: str, url: str, sha256: str, size: int, member_prefix: str) -> dict[str, Any]:
    return {
        "filename": filename,
        "url": url,
        "sha256": sha256,
        "size": size,
        "kind": "wheel",
        "member_prefix": member_prefix,
        "root_members": [],
        "exclude": [],
    }


def resolve_model(
    code: str,
    package: str,
    version: str,
    requires: Sequence[str],
    fetch_json: FetchJson,
    hash_download: HashDownload = sha256_of_download,
) -> dict[str, Any]:
    """Resolve one spaCy model wheel from its GitHub release.

    The spaCy model release assets carry no ``digest`` (verified 2026-09-13 on
    ``en_core_web_sm-3.8.0``), so the wheel is downloaded and hashed. Its size
    must match the size the release reports, and a digest the API does report is
    a cross-check that must agree.
    """
    release = fetch_json(MODEL_RELEASE.format(package=package, version=version))
    filename = f"{package}-{version}-py3-none-any.whl"
    asset = next((a for a in release.get("assets", []) if a.get("name") == filename), None)
    if asset is None:
        raise SystemExit(f"{filename}: no such release asset")
    sha256, size = hash_download(asset["browser_download_url"])
    if size != int(asset["size"]):
        raise SystemExit(f"{filename}: downloaded {size} bytes, the release asset reports {asset['size']} bytes")
    reported = asset.get("digest")
    if isinstance(reported, str) and reported.startswith("sha256:") and reported[len("sha256:") :] != sha256:
        raise SystemExit(f"{filename}: downloaded sha256 {sha256} disagrees with the release digest {reported}")
    artifact = _artifact(filename, asset["browser_download_url"], sha256, size, f"{package}/")
    return {
        "code": code,
        "approx_download_mb": max(1, math.ceil(size / 1_000_000)),
        "requires": list(requires),
        "components": [
            {
                "import_name": package,
                "required": True,
                "sentinels": ["__init__.py", f"{package}-{version}/config.cfg", f"{package}-{version}/meta.json"],
                "abi": None,
                "universal": artifact,
                "per_platform": None,
            }
        ],
    }


def resolve_companion_wheel(
    requirement: str,
    fetch_json: FetchJson,
    read_top_level: ReadTopLevel,
    *,
    dist_info_root: bool = False,
) -> dict[str, Any]:
    """One pure-Python PyPI wheel a model needs at runtime, as a universal component.

    ``requirement`` is ``NAME==VERSION``. ``dist_info_root`` keeps the wheel's ``*.dist-info/``
    directory as a pack-root member: a distribution found through ``importlib.metadata`` entry points
    (pymorphy3's dictionaries) is invisible from its package directory alone.
    """
    name, sep, version = requirement.partition("==")
    if not (name and sep and version):
        raise SystemExit(f"{requirement}: expected NAME==VERSION")
    entry = _wheel_for(fetch_json(PYPI_JSON.format(name=name, version=version))["urls"], None, "")
    if entry is None:
        raise SystemExit(f"{requirement}: no pure-Python wheel on PyPI")
    packages = read_top_level(entry)
    if len(packages) != 1:
        raise SystemExit(f"{requirement}: expected one top-level package directory, found {packages or 'none'}")
    artifact = _artifact(
        entry["filename"], entry["url"], entry["digests"]["sha256"], int(entry["size"]), f"{packages[0]}/"
    )
    if dist_info_root:
        distribution, wheel_version = str(entry["filename"]).split("-")[:2]
        artifact["root_members"] = [f"{distribution}-{wheel_version}.dist-info/"]
    return {
        "import_name": packages[0],
        "required": True,
        "sentinels": ["__init__.py"],
        "abi": None,
        "universal": artifact,
        "per_platform": None,
    }


def with_companion_wheels(
    resolved: Mapping[str, Any],
    requirements: Sequence[str],
    dist_info_roots: Sequence[str],
    fetch_json: FetchJson,
    read_top_level: ReadTopLevel,
) -> dict[str, Any]:
    """A resolved model pack plus one component per companion wheel, its download size re-rounded."""
    named = {_canonical(requirement.partition("==")[0]) for requirement in requirements}
    roots = {_canonical(name) for name in dist_info_roots}
    if not roots <= named:
        raise SystemExit(f"--dist-info-root names no --with-wheel: {sorted(roots - named)}")
    components = [*resolved["components"]]
    for requirement in requirements:
        keep = _canonical(requirement.partition("==")[0]) in roots
        components.append(resolve_companion_wheel(requirement, fetch_json, read_top_level, dist_info_root=keep))
    total = sum(int(comp["universal"]["size"]) for comp in components)
    return {**resolved, "components": components, "approx_download_mb": max(1, math.ceil(total / 1_000_000))}


def _lock_names(lock_path: Path) -> set[str]:
    names = set()
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            names.add(_canonical(line.split("==", 1)[0]))
    return names


def _base_names(lock_path: Path, python: str, run_compile: RunCompile, pyproject: Path) -> set[str]:
    """Distributions the base install (``[project].dependencies``, no extras) resolves to.

    That closure is what a frozen bundle can carry. The lock pins more: the
    ``[asr]`` extra brings httpx, anyio, typer and shellingham, which the bundle
    excludes (the ASR pack owns them) or never imports. Subtracting the whole
    lock dropped those from the spaCy runtime pack while weasel imports typer and
    httpx at ``import spacy``, so no spaCy language could load in the frozen app.
    """
    with pyproject.open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    args = [
        UV,
        "pip",
        "compile",
        "-",
        "--universal",
        "--python-version",
        python,
        "--constraint",
        str(lock_path),
        "--no-header",
        "--no-annotate",
        "--quiet",
    ]
    return set(_pins(run_compile(args, "\n".join(dependencies))))


def _pins(output: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in output.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[_canonical(name)] = version.split(";", 1)[0].strip()
    return pins


def _wheel_for(
    urls: Sequence[Mapping[str, Any]], key: tuple[str, str] | None, abi_tag: str
) -> Mapping[str, Any] | None:
    """Pick the wheel for *key* (``None`` = the pure-Python ``none-any`` wheel), or None."""
    for entry in urls:
        filename = str(entry.get("filename", ""))
        if entry.get("packagetype") != "bdist_wheel" or "musllinux" in filename:
            continue
        tag = filename[: -len(".whl")].split("-", 2)[-1]
        if key is None:
            if tag.endswith("none-any"):
                return entry
        elif (abi_tag in tag or "abi3" in tag) and _TAG_MATCH[key](tag):
            return entry
    return None


def read_top_level(artifact: Mapping[str, Any], cache: Path = PIN_CACHE) -> list[str]:
    """Top-level import package directories of one wheel, from its RECORD.

    Only a directory whose name is a Python identifier can be imported, so any
    other is not a package: ``*.dist-info``/``*.data``/``*.libs`` and PEP 561
    stub-only directories (wrapt 2.x ships ``wrapt-stubs/`` beside ``wrapt/``).
    """
    local = _cached_download(str(artifact["url"]), cache / str(artifact["filename"]))
    with zipfile.ZipFile(local) as zf:
        record = next(name for name in zf.namelist() if name.endswith(".dist-info/RECORD"))
        paths = [line.split(",", 1)[0] for line in zf.read(record).decode().splitlines() if line]
    return sorted({path.split("/", 1)[0] for path in paths if "/" in path and path.split("/", 1)[0].isidentifier()})


def resolve_runtime(
    code: str,
    requirements: Sequence[str],
    python: str,
    lock_path: Path,
    run_compile: RunCompile,
    fetch_json: FetchJson,
    read_top_level: ReadTopLevel,
    pyproject: Path = REPO_ROOT / "pyproject.toml",
) -> dict[str, Any]:
    """Resolve a runtime closure per platform, minus what the bundle already carries."""
    bundled = _lock_names(lock_path) & _base_names(lock_path, python, run_compile, pyproject)
    abi_tag = "cp" + python.replace(".", "")
    per_key: dict[tuple[str, str], dict[str, str]] = {}
    for key, uv_platform in PLATFORMS.items():
        args = [
            UV,
            "pip",
            "compile",
            "-",
            "--python-version",
            python,
            "--python-platform",
            uv_platform,
            "--constraint",
            str(lock_path),
            "--no-header",
            "--no-annotate",
            "--quiet",
        ]
        pins = _pins(run_compile(args, "\n".join(requirements)))
        per_key[key] = {
            name: version
            for name, version in pins.items()
            if name not in bundled and name not in NOT_IMPORTED_AT_RUNTIME
        }

    components: list[dict[str, Any]] = []
    sizes: dict[tuple[str, str], int] = dict.fromkeys(PLATFORMS, 0)
    for name in sorted({name for pins in per_key.values() for name in pins}):
        versions = {pins[name] for pins in per_key.values() if name in pins}
        if len(versions) != 1:
            raise SystemExit(f"{name}: resolved to different versions per platform: {sorted(versions)}")
        version = versions.pop()
        urls = fetch_json(PYPI_JSON.format(name=name, version=version))["urls"]
        universal = _wheel_for(urls, None, abi_tag)
        wanted = [key for key in PLATFORMS if name in per_key[key]]
        platform_wheels = {} if universal is not None else {key: _wheel_for(urls, key, abi_tag) for key in wanted}
        missing = [f"{key[0]}/{key[1]}" for key, entry in platform_wheels.items() if entry is None]
        if missing:
            raise SystemExit(f"{name}=={version}: no wheel for {', '.join(missing)}")
        sample = universal if universal is not None else platform_wheels[wanted[0]]
        packages = read_top_level(sample)
        if len(packages) != 1:
            raise SystemExit(f"{name}=={version}: expected one top-level package directory, found {packages or 'none'}")
        package = packages[0]

        def as_artifact(entry: Mapping[str, Any], package: str = package) -> dict[str, Any]:
            return _artifact(
                entry["filename"], entry["url"], entry["digests"]["sha256"], int(entry["size"]), f"{package}/"
            )

        for key in wanted:
            sizes[key] += int((universal if universal is not None else platform_wheels[key])["size"])
        components.append(
            {
                "import_name": package,
                "required": True,
                "sentinels": ["__init__.py"],
                "abi": None if universal is not None else [int(part) for part in python.split(".")],
                "universal": as_artifact(universal) if universal is not None else None,
                "per_platform": (
                    None
                    if universal is not None
                    else {f"{key[0]}/{key[1]}": as_artifact(entry) for key, entry in platform_wheels.items()}
                ),
            }
        )
    return {
        "code": code,
        "approx_download_mb": max(1, math.ceil(max(sizes.values()) / 1_000_000)),
        "requires": [],
        "components": components,
    }


def _render_artifact(artifact: Mapping[str, Any], indent: str) -> str:
    lines = [
        f"{indent}ArtifactSpec(",
        f"{indent}    # {artifact['filename']}",
        f"{indent}    url={artifact['url']!r},",
        f"{indent}    sha256={artifact['sha256']!r},",
        f"{indent}    kind={artifact['kind']!r},",
        f"{indent}    member_prefix={artifact['member_prefix']!r},",
    ]
    if artifact["root_members"]:
        lines.append(f"{indent}    root_members={tuple(artifact['root_members'])!r},")
    if artifact["exclude"]:
        lines.append(f"{indent}    exclude={tuple(artifact['exclude'])!r},")
    lines.append(f"{indent})")
    return "\n".join(lines)


def _black_mode() -> Any:
    """The mode ``black --check .`` formats with, read from pyproject ``[tool.black]``."""
    import black  # dev dependency; the generator is a developer tool

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)["tool"]["black"]
    return black.Mode(
        line_length=int(config["line-length"]),
        target_versions={black.TargetVersion[version.upper()] for version in config.get("target-version", [])},
    )


def render_manifest(resolved: Mapping[str, Any]) -> str:
    """The ``pack.py`` text for a resolved pack, already black-formatted.

    ``repr`` emits single-quoted strings that ``black --check`` would rewrite, so
    the text goes through black here; the committed manifest then passes the gate
    and stays byte-equal to this function's output.
    """
    import black

    return black.format_str(_render_unformatted(resolved), mode=_black_mode())


def _render_unformatted(resolved: Mapping[str, Any]) -> str:
    code = resolved["code"]
    out = [
        f'"""Dependency-pack manifest for {code}, GENERATED by scripts/pin_language_pack.py.',
        "",
        f"Do not edit by hand: regenerate it, and commit tests/fixtures/language_packs/{code}.resolved.json with it.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent",
        "",
        "COMPONENTS = (",
    ]
    for comp in resolved["components"]:
        out += [
            "    PackComponent(",
            f"        import_name={comp['import_name']!r},",
            f"        required={comp['required']!r},",
            f"        sentinels={tuple(comp['sentinels'])!r},",
        ]
        if comp["abi"] is not None:
            out.append(f"        abi={tuple(comp['abi'])!r},")
        if comp["universal"] is not None:
            out.append("        universal=" + _render_artifact(comp["universal"], "        ").lstrip() + ",")
        else:
            out.append("        per_platform={")
            # Sorted: the fixture is written with sorted keys, and re-rendering it
            # must give the same text as the run that wrote it.
            for platform_key, artifact in sorted(comp["per_platform"].items()):
                sys_platform, machine = platform_key.split("/")
                rendered = _render_artifact(artifact, "            ").lstrip()
                out.append(f"            ({sys_platform!r}, {machine!r}): {rendered},")
            out.append("        },")
        out.append("    ),")
    out += [
        ")",
        "",
        "PACK = LanguagePack(",
        f"    code={code!r},",
        f"    approx_download_mb={resolved['approx_download_mb']!r},",
        "    components=COMPONENTS,",
        f"    requires={tuple(resolved['requires'])!r},",
        ")",
        "",
        '__all__ = ["PACK"]',
        "",
    ]
    return "\n".join(out)


def _write(resolved: Mapping[str, Any]) -> None:
    code = resolved["code"]
    fixture = REPO_ROOT / "tests" / "fixtures" / "language_packs" / f"{code}.resolved.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = REPO_ROOT / "anki_miner" / "languages" / code / "pack.py"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(render_manifest(resolved), encoding="utf-8")
    print(f"wrote {manifest.relative_to(REPO_ROOT)} and {fixture.relative_to(REPO_ROOT)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a language pack manifest from pinned upstream metadata.")
    sub = parser.add_subparsers(dest="mode", required=True)
    model = sub.add_parser("model", help="one spaCy model wheel from its GitHub release")
    model.add_argument("--code", required=True)
    model.add_argument("--package", required=True)
    model.add_argument("--version", required=True)
    model.add_argument("--requires", action="append", default=[])
    model.add_argument("--with-wheel", action="append", default=[], metavar="NAME==VERSION")
    model.add_argument("--dist-info-root", action="append", default=[], metavar="NAME")
    runtime = sub.add_parser("runtime", help="a requirement set resolved per platform against requirements.lock")
    runtime.add_argument("--code", required=True)
    runtime.add_argument("--requirement", action="append", required=True)
    runtime.add_argument("--abi", default="3.12")
    runtime.add_argument("--lock", type=Path, default=REPO_ROOT / "requirements.lock")
    args = parser.parse_args(argv)
    if args.mode == "model":
        resolved = resolve_model(args.code, args.package, args.version, args.requires, fetch_json)
        if args.with_wheel:
            resolved = with_companion_wheels(resolved, args.with_wheel, args.dist_info_root, fetch_json, read_top_level)
        _write(resolved)
    else:
        _write(
            resolve_runtime(args.code, args.requirement, args.abi, args.lock, run_compile, fetch_json, read_top_level)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

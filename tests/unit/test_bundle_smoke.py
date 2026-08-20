"""Tests for the shared frozen-bundle smoke runner."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_clean_release_preflight_fetches_verified_libmpv_before_pyinstaller(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "anki_miner").mkdir()
    (repo / ".github").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "release_preflight.sh", repo / "scripts" / "release_preflight.sh")
    (repo / "anki_miner" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (repo / ".github" / "ytdlp-pin.json").write_text(
        '{"version":"test","assets":{"linux":{"asset":"yt-dlp_linux","sha256":"feedface","install_as":"yt-dlp"}}}\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").touch()
    (repo / "pyproject.toml").touch()
    (repo / "anki_miner.spec").touch()
    _write_executable(repo / "scripts" / "bundle_smoke.sh", "#!/usr/bin/env bash\nexit 0\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_pip = tmp_path / "fake-pip"
    fake_pyinstaller = tmp_path / "fake-pyinstaller"
    _write_executable(fake_pip, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_pyinstaller,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [ "${1:-}" = "--version" ]; then echo 6.20.0; exit 0; fi\n'
        "test -f vendor/libmpv/libmpv.so.2\n"
        "mkdir -p dist/AnkiMiner\n",
    )
    _write_executable(
        fake_bin / "python3",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then\n'
        '  mkdir -p "$3/bin"\n'
        '  cp "$0" "$3/bin/python"\n'
        '  cp "$FAKE_PIP" "$3/bin/pip"\n'
        '  cp "$FAKE_PYINSTALLER" "$3/bin/pyinstaller"\n'
        "  exit 0\n"
        "fi\n"
        'case "$*" in\n'
        '  *"__version__"*) echo 9.9.9 ;;\n'
        "  *'[\"version\"]'*) echo test ;;\n"
        "  *'[\"asset\"]'*) echo yt-dlp_linux ;;\n"
        "  *'[\"sha256\"]'*) echo feedface ;;\n"
        "  *'[\"install_as\"]'*) echo yt-dlp ;;\n"
        "esac\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-o" ]; then out="$2"; shift 2; else shift; fi\n'
        "done\n"
        'mkdir -p "$(dirname "$out")"\n'
        ': > "$out"\n',
    )
    _write_executable(
        fake_bin / "sha256sum",
        '#!/usr/bin/env bash\nset -euo pipefail\ncat >> "$SHA_RECORD"\n',
    )
    _write_executable(
        fake_bin / "tar",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "archive=''\n"
        "dest=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -C) dest="$2"; shift 2 ;;\n'
        "    -*) shift ;;\n"
        '    *) archive="$1"; shift ;;\n'
        "  esac\n"
        "done\n"
        'case "$archive" in\n'
        '  *ffmpeg*) mkdir -p "$dest/pkg/bin"; touch "$dest/pkg/bin/ffmpeg" "$dest/pkg/bin/ffprobe" ;;\n'
        '  *libmpv*) mkdir -p "$dest"; touch "$dest/libmpv.so.2" "$dest/Copyright" "$dest/SOURCES.txt" ;;\n'
        "esac\n",
    )

    sha_record = tmp_path / "sha-record.txt"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_PIP": str(fake_pip),
            "FAKE_PYINSTALLER": str(fake_pyinstaller),
            "SHA_RECORD": str(sha_record),
        }
    )
    result = subprocess.run(
        ["bash", "scripts/release_preflight.sh", "--clean", "--skip-package"],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "vendor" / "libmpv" / "libmpv.so.2").is_file()
    assert (repo / "licenses" / "libmpv" / "Copyright").is_file()
    assert (repo / "licenses" / "libmpv" / "SOURCES.txt").is_file()
    assert "5d9278463edab8f2a467f45c2c66416070d4e1543024df30fed2f721def663c1" in sha_record.read_text(encoding="utf-8")


def test_release_constraints_cover_direct_dependencies() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    direct = {canonicalize_name(Requirement(raw).name) for raw in project["dependencies"]}
    pins: dict[str, list[str]] = defaultdict(list)
    for line in (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if match:
            pins[canonicalize_name(match.group(1))].append(match.group(2))

    missing = direct - pins.keys()
    duplicates = {name: versions for name, versions in pins.items() if name in direct and len(versions) != 1}
    assert not missing, f"direct dependencies without exact release constraints: {sorted(missing)}"
    assert not duplicates, f"direct dependencies with duplicate release constraints: {duplicates}"


def test_local_audio_notice_is_declared_for_wheel_and_frozen_bundle() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    notice = "licenses/local-audio-yomichan/LICENSE"

    assert notice in pyproject["project"]["license-files"]
    assert '"licenses", "local-audio-yomichan"' in (PROJECT_ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert notice in (PROJECT_ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")


def test_bundle_smoke_pins_c_locale() -> None:
    script = (PROJECT_ROOT / "scripts" / "bundle_smoke.sh").read_text(encoding="utf-8")

    assert "\nexport LC_ALL=C\n" in script
    assert script.index("export LC_ALL=C") < script.index('DIST="${1:')


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_bundle_smoke_uses_one_temporary_anki_miner_home(tmp_path: Path) -> None:
    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    record = tmp_path / "probe-homes.txt"
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    (caller_home / "sentinel").write_text("keep", encoding="utf-8")

    app = dist / "AnkiMiner"
    app.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -d "$ANKI_MINER_HOME"\n'
        'printf \'%s\\n\' "$ANKI_MINER_HOME" >> "$SMOKE_HOME_RECORD"\n'
        'touch "$ANKI_MINER_HOME/probe-touch"\n'
        'if [ "${ANKI_MINER_ASR_VULKAN_PROBE:-}" = 1 ]; then\n'
        "  echo 0\n"
        'elif [ "${ANKI_MINER_MPV_PROBE:-}" = 1 ]; then\n'
        "  echo MPV_PROBE_OK\n"
        "else\n"
        '  case "${ANKI_MINER_SMOKE:-}" in\n'
        "    youtube|asr|whispercpp) echo BUNDLED_SMOKE_PASS ;;\n"
        "    *) exit 3 ;;\n"
        "  esac\n"
        "fi\n",
        encoding="utf-8",
    )
    app.chmod(0o755)

    ffmpeg = dist / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env bash\necho 'libmp3lame libopus libsvtav1 libwebp libwebp_anim'\n",
        encoding="utf-8",
    )
    ffmpeg.chmod(0o755)
    for library in ("libggml-vulkan.so", "libggml-cpu.so", "libmpv.so.2"):
        (dist / library).touch()

    env = os.environ.copy()
    env.update(
        {
            "ANKI_MINER_HOME": str(caller_home),
            "BUNDLE_SMOKE_SKIP_ASR": "0",
            "BUNDLE_SMOKE_SKIP_MPV": "0",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "0",
            "SMOKE_HOME_RECORD": str(record),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "bundle_smoke.sh"), str(dist)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    homes = record.read_text(encoding="utf-8").splitlines()
    assert len(homes) == 5
    assert len(set(homes)) == 1
    assert homes[0] != str(caller_home)
    assert not Path(homes[0]).exists()
    assert not (caller_home / "probe-touch").exists()
    assert (caller_home / "sentinel").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
@pytest.mark.skipif(shutil.which("awk") is None, reason="awk is unavailable")
def test_smoke_graph_matches_build_aselect_graph(tmp_path: Path) -> None:
    """The smoke's awk generator must emit exactly what the app emits.

    bundle_smoke.sh runs against a built bundle with only shell tooling, so it
    rebuilds the aselect graph in awk instead of importing ``build_aselect_graph``.
    That duplication is only safe while the two agree byte-for-byte — this pins it.
    """
    from anki_miner.services.audio_condenser import build_aselect_graph

    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    captured = tmp_path / "captured-graph.txt"

    app = dist / "AnkiMiner"
    app.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    app.chmod(0o755)

    # Fake ffmpeg: echo the encoder list, and copy any -/filter:a payload
    # out so the test can compare the graph the script actually generated.
    ffmpeg = dist / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "-/filter:a" ]; then cp "$arg" "$SMOKE_GRAPH_RECORD"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        "echo 'libmp3lame libopus libsvtav1 libwebp libwebp_anim'\n",
        encoding="utf-8",
    )
    ffmpeg.chmod(0o755)
    for library in ("libggml-vulkan.so", "libggml-cpu.so", "libmpv.so.2"):
        (dist / library).touch()

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.update(
        {
            "BUNDLE_SMOKE_SKIP_ASR": "1",
            "BUNDLE_SMOKE_SKIP_MPV": "1",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "1",
            "BUNDLE_SMOKE_GRAPH_TERMS": "125",
            "LC_NUMERIC": "es_ES.UTF-8",
            "SMOKE_GRAPH_RECORD": str(captured),
            "SMOKE_HOME_RECORD": str(tmp_path / "homes.txt"),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)

    subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "bundle_smoke.sh"), str(dist)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert captured.exists(), "smoke never handed a filter script to ffmpeg"
    expected = build_aselect_graph([(i * 2000, i * 2000 + 1000) for i in range(125)])
    assert captured.read_text(encoding="utf-8") == expected


class TestAvailableImpersonateTargets:
    """The bundled smoke's curl_cffi gate (anki_miner/gui/app.py).

    Captured from a real ``yt-dlp --list-impersonate-targets`` run against a
    pip-installed yt-dlp, which has no curl_cffi — the exact shape a vendored
    zipapp asset would produce, and the shape the gate exists to reject.
    """

    NO_CURL_CFFI = (
        "[info] Available impersonate targets\n"
        "Client    OS   Source\n"
        "--------------------------------------------\n"
        "Tor       -    curl_cffi>=0.11 (unavailable)\n"
        "Edge      -    curl_cffi (unavailable)\n"
        "Firefox   -    curl_cffi>=0.10 (unavailable)\n"
        "Safari    -    curl_cffi (unavailable)\n"
        "Chrome    -    curl_cffi (unavailable)\n"
    )

    WITH_CURL_CFFI = (
        "[info] Available impersonate targets\n"
        "Client    OS   Source\n"
        "--------------------------------------------\n"
        "chrome-110 windows-10 curl_cffi\n"
        "safari-15.5 macos-12 curl_cffi\n"
        "Tor       -    curl_cffi>=0.11 (unavailable)\n"
    )

    def test_no_curl_cffi_yields_nothing(self) -> None:
        """The banner must not survive the filter — "unavailable" is not in "Available"."""
        from anki_miner.gui.app import available_impersonate_targets

        assert available_impersonate_targets(self.NO_CURL_CFFI) == []

    def test_usable_targets_are_returned(self) -> None:
        from anki_miner.gui.app import available_impersonate_targets

        rows = available_impersonate_targets(self.WITH_CURL_CFFI)
        assert [row.split()[0] for row in rows] == ["chrome-110", "safari-15.5"]

    def test_empty_output_yields_nothing(self) -> None:
        from anki_miner.gui.app import available_impersonate_targets

        assert available_impersonate_targets("") == []

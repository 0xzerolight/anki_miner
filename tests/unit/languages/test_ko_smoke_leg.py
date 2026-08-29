"""The ko leg rides Stage 2B's opt-in BUNDLE_SMOKE_LANGS loop; no ko-only leg.

The leg also has to be HANDED a model. The bundle ships the kiwipiepy engine
without its ~88 MB model (that is an in-app download pack), so unlike zh the ko
leg cannot smoke what the bundle carries — CI fetches the pinned model and the
loop seeds it into the isolated home before invoking the app.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.gui import app as app_module
from anki_miner.services import ko_model_installer

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "scripts" / "bundle_smoke.sh"


def test_the_shared_loop_is_opt_in_and_language_generic() -> None:
    text = (ROOT / "scripts" / "bundle_smoke.sh").read_text(encoding="utf-8")
    assert "BUNDLE_SMOKE_LANGS" in text
    assert 'ANKI_MINER_SMOKE="$lang"' in text
    # A per-language leg would re-break the invocation-count test 2B.10 protected.
    assert "ANKI_MINER_SMOKE=ko" not in text


def test_the_release_workflow_requests_the_ko_leg() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested, "release.yml must request the language smoke legs"
    assert any("ko" in group for group in requested)


def test_ko_has_a_bundled_smoke_line() -> None:
    assert "ko" in app_module._LANGUAGE_SMOKE_LINES


def test_the_ko_leg_passes_in_process(capsys) -> None:
    pytest.importorskip("kiwipiepy")
    assert app_module._run_language_bundled_smoke("ko") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_the_seed_writes_where_the_installer_reads() -> None:
    """The shell literal and ``ko_model_installer`` must name the same directory.

    The smoke rebuilds the pack path in shell because it runs against a built
    bundle with no Python of its own; that duplication is only safe while the two
    agree, so this pins it.
    """
    home = Path("/home")
    relative = ko_model_installer.ko_model_path(ko_model_installer.ko_model_root(home)).relative_to(home)

    assert f'"$ANKI_MINER_HOME/{relative.as_posix()}"' in SMOKE.read_text(encoding="utf-8")


def test_the_release_workflow_fetches_the_pinned_model_for_the_leg() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert ko_model_installer.KO_MODEL_URL in workflow
    assert ko_model_installer.KO_MODEL_SHA256 in workflow
    assert "BUNDLE_SMOKE_KO_MODEL: ${{ env.SMOKE_KO_MODEL_PATH }}" in workflow


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is unavailable")
def test_the_smoke_script_still_passes_shellcheck() -> None:
    result = subprocess.run(["shellcheck", str(SMOKE)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def _fake_dist(tmp_path: Path) -> Path:
    """A dist tree whose AnkiMiner records the ko model it was handed."""
    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    app = dist / "AnkiMiner"
    app.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "${ANKI_MINER_SMOKE:-}" in\n'
        "  ko)\n"
        '    test -f "$ANKI_MINER_HOME/ko_model/kiwipiepy_model/sj.morph"\n'
        "    printf '%s\\n' seeded >> \"$KO_SEED_RECORD\"\n"
        "    echo BUNDLED_SMOKE_PASS ;;\n"
        "  youtube|asr|whispercpp) echo BUNDLED_SMOKE_PASS ;;\n"
        "  *)\n"
        '    if [ "${ANKI_MINER_ASR_VULKAN_PROBE:-}" = 1 ]; then echo 0\n'
        '    elif [ "${ANKI_MINER_MPV_PROBE:-}" = 1 ]; then echo MPV_PROBE_OK\n'
        "    else exit 3\n"
        "    fi ;;\n"
        "esac\n",
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
    return dist


def _run_smoke(tmp_path: Path, dist: Path, record: Path, ko_model: Path | None):
    env = os.environ.copy()
    env.update(
        {
            "BUNDLE_SMOKE_SKIP_ASR": "0",
            "BUNDLE_SMOKE_SKIP_MPV": "0",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "0",
            "BUNDLE_SMOKE_LANGS": "ko",
            "KO_SEED_RECORD": str(record),
            "SMOKE_HOME_RECORD": str(tmp_path / "homes.txt"),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)
    if ko_model is None:
        env.pop("BUNDLE_SMOKE_KO_MODEL", None)
    else:
        env["BUNDLE_SMOKE_KO_MODEL"] = str(ko_model)
    return subprocess.run(
        ["bash", str(SMOKE), str(dist)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_a_fetched_model_is_seeded_into_the_isolated_home(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    record = tmp_path / "seed.txt"
    model = tmp_path / "fetched" / "kiwipiepy_model"
    model.mkdir(parents=True)
    for name in ko_model_installer._MODEL_SENTINELS:
        (model / name).write_bytes(b"x")

    result = _run_smoke(tmp_path, dist, record, model)

    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text(encoding="utf-8").split() == ["seeded"]
    assert "PASS language-ko" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_without_a_model_the_leg_skips_loudly_instead_of_failing(tmp_path: Path) -> None:
    # The model is not bundle content, so its absence is a missing fetch step,
    # not a broken bundle: failing here would red every release on an outage.
    dist = _fake_dist(tmp_path)
    record = tmp_path / "seed.txt"

    result = _run_smoke(tmp_path, dist, record, None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not record.exists()
    assert "SKIP language-ko" in result.stdout
    assert "::warning::" in result.stdout

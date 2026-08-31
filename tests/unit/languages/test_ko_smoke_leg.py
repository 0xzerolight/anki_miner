"""The ko leg rides Stage 2B's opt-in BUNDLE_SMOKE_LANGS loop; no ko-only leg.

The leg also has to be HANDED an engine. No non-Japanese engine is bundle
content — each is an in-app language pack — so unlike a bundled engine the leg
cannot smoke what the artifact carries: CI seeds the packs with
``scripts/fetch_language_pack_seeds.py`` and the loop copies each seed into the
isolated home before invoking the app. The seeding is language-generic; ko is
where it is pinned because ko is the heaviest pack.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.config import paths
from anki_miner.gui import app as app_module
from anki_miner.services import language_pack_installer

ROOT = Path(__file__).resolve().parents[3]


def _ko_model_component():
    pack = language_pack_installer.load_pack("ko")
    assert pack is not None
    return next(comp for comp in pack.components if comp.import_name == "kiwipiepy_model")


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
    """The shell literal and the pack root must name the same directory.

    The smoke rebuilds the pack path in shell because it runs against a built
    bundle with no Python of its own; that duplication is only safe while the two
    agree, so this pins it.
    """
    relative = language_pack_installer.language_pack_root("ko").relative_to(paths.ANKI_MINER_HOME).as_posix()
    packs_dir, _, code = relative.rpartition("/")
    assert code == "ko"

    text = SMOKE.read_text(encoding="utf-8")
    assert f'"$ANKI_MINER_HOME/{packs_dir}"' in text
    assert f'"$ANKI_MINER_HOME/{packs_dir}/$lang"' in text


def test_the_release_workflow_seeds_the_packs_instead_of_duplicating_their_pins() -> None:
    """CI drives the app's own installer, so a pin lives in exactly one place."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "scripts/fetch_language_pack_seeds.py" in workflow
    assert "BUNDLE_SMOKE_PACK_SEEDS: ${{ runner.temp }}/lang_pack_seeds" in workflow
    assert "BUNDLE_SMOKE_KO_MODEL" not in workflow

    spec = _ko_model_component().universal
    assert spec is not None
    assert spec.url not in workflow
    assert spec.sha256 not in workflow


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is unavailable")
def test_the_smoke_script_still_passes_shellcheck() -> None:
    result = subprocess.run(["shellcheck", str(SMOKE)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def _fake_dist(tmp_path: Path) -> Path:
    """A dist tree whose AnkiMiner records the language packs it was handed."""
    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    app = dist / "AnkiMiner"
    app.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "${ANKI_MINER_SMOKE:-}" in\n'
        "  ko)\n"
        '    test -f "$ANKI_MINER_HOME/language_packs/ko/kiwipiepy_model/sj.morph"\n'
        "    printf '%s\\n' ko >> \"$SEED_RECORD\"\n"
        "    echo BUNDLED_SMOKE_PASS ;;\n"
        "  zh)\n"
        '    test -f "$ANKI_MINER_HOME/language_packs/zh/jieba/__init__.py"\n'
        "    printf '%s\\n' zh >> \"$SEED_RECORD\"\n"
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


def _seed_ko(seeds: Path) -> None:
    """Write a ko pack seed the fake bundle accepts."""
    model = seeds / "ko" / "kiwipiepy_model"
    model.mkdir(parents=True)
    for name in _ko_model_component().sentinels:
        (model / name).write_bytes(b"x")


def _seed_zh(seeds: Path) -> None:
    jieba = seeds / "zh" / "jieba"
    jieba.mkdir(parents=True)
    (jieba / "__init__.py").write_bytes(b"")


def _run_smoke(tmp_path: Path, dist: Path, record: Path, seeds: Path | None, langs: str = "ko"):
    env = os.environ.copy()
    env.update(
        {
            "BUNDLE_SMOKE_SKIP_ASR": "0",
            "BUNDLE_SMOKE_SKIP_MPV": "0",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "0",
            "BUNDLE_SMOKE_LANGS": langs,
            "SEED_RECORD": str(record),
            "SMOKE_HOME_RECORD": str(tmp_path / "homes.txt"),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)
    if seeds is None:
        env.pop("BUNDLE_SMOKE_PACK_SEEDS", None)
    else:
        env["BUNDLE_SMOKE_PACK_SEEDS"] = str(seeds)
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
def test_a_fetched_pack_is_seeded_into_the_isolated_home(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    record = tmp_path / "seed.txt"
    seeds = tmp_path / "fetched"
    _seed_ko(seeds)

    result = _run_smoke(tmp_path, dist, record, seeds)

    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text(encoding="utf-8").split() == ["ko"]
    assert "PASS language-ko" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_without_a_seed_the_leg_skips_loudly_instead_of_failing(tmp_path: Path) -> None:
    # The engine is not bundle content, so its absence is a missing fetch step,
    # not a broken bundle: failing here would red every release on an outage.
    dist = _fake_dist(tmp_path)
    record = tmp_path / "seed.txt"

    result = _run_smoke(tmp_path, dist, record, None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not record.exists()
    assert "SKIP language-ko" in result.stdout
    assert "::warning::" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_a_missing_seed_skips_only_its_own_language(tmp_path: Path) -> None:
    """One language's outage must not cost the other its leg."""
    dist = _fake_dist(tmp_path)
    record = tmp_path / "seed.txt"
    seeds = tmp_path / "fetched"
    _seed_zh(seeds)

    result = _run_smoke(tmp_path, dist, record, seeds, langs="zh ko")

    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text(encoding="utf-8").split() == ["zh"]
    assert "PASS language-zh" in result.stdout
    assert "SKIP language-ko" in result.stdout

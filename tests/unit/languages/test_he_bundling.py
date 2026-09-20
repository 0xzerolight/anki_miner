"""Hebrew packaging: nothing to download, nothing to exclude, one font (ruling R-PACKLESS-SMOKE).

Hebrew is the second pack-less mining language after Indonesian, so its release smoke runs through
the empty-seed-dir step rather than the seeder. The one binary it adds is the bundled face, and the
one notice it needs (stopwords-iso, MIT) was already shipped by Indonesian.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.gui import app as app_module
from anki_miner.languages.registry import get_profile

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
SEED_ANCHOR = 'fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds"'
FONT = "anki_miner/gui/resources/fonts/NotoSansHebrew-Regular.ttf"
OFL = "anki_miner/gui/resources/fonts/OFL-NotoSansHebrew.txt"


# --------------------------------------------------------------------------
# Nothing downloads, nothing is excluded
# --------------------------------------------------------------------------


def test_hebrew_ships_no_pack():
    from importlib.util import find_spec

    assert find_spec("anki_miner.languages.he.pack") is None
    from anki_miner.services.language_pack_installer import load_pack

    assert load_pack("he") is None


def test_hebrew_adds_no_pip_extra_and_no_mypy_override():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "he" not in project["project"]["optional-dependencies"]
    aggregate = project["project"]["optional-dependencies"]["languages"]
    assert not any(dep.startswith("anki-miner[he]") or dep == "he" for dep in aggregate)
    overrides = project["tool"]["mypy"]["overrides"]
    modules = [m for entry in overrides for m in entry["module"]]
    assert not any(m.startswith("anki_miner.languages.he") or m.startswith("he.") for m in modules)


def test_the_bundle_excludes_no_hebrew_engine():
    """There is none to exclude: the whole tokenizer is a regex in the package."""
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"anki_miner.languages.he"' not in spec
    assert "hebrew" not in spec.lower()


def test_the_only_new_asset_is_the_face_and_its_licence():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    checker = (ROOT / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8")
    assert OFL in project["license-files"]
    assert f'"{FONT}",' in checker and f'"{OFL}",' in checker
    assert (ROOT / FONT).is_file() and (ROOT / OFL).is_file()


def test_the_stopword_notice_is_the_one_indonesian_already_ships():
    """stopwords-iso covers both lists; Hebrew adds no licences/ directory of its own."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "licenses/stopwords-iso/LICENSE" in project["license-files"]
    assert not (ROOT / "licenses" / "hebrew").exists()
    stopwords = (ROOT / "anki_miner" / "languages" / "he" / "stopwords.py").read_text(encoding="utf-8")
    assert "stopwords-iso" in stopwords and "MIT" in stopwords


# --------------------------------------------------------------------------
# The release smoke leg
# --------------------------------------------------------------------------


def test_the_release_smokes_hebrew_without_seeding_a_pack():
    seed_lines = [line for line in WORKFLOW.splitlines() if SEED_ANCHOR in line]
    assert seed_lines and all(
        "he" not in line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split() for line in seed_lines
    )
    requested = [line.split(":", 1)[1].split() for line in WORKFLOW.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested and all("he" in group for group in requested)


def test_the_empty_seed_dir_step_names_hebrew_beside_indonesian():
    """bundle_smoke.sh runs a language leg only when its seed dir exists; an empty one is inert."""
    assert 'mkdir -p "${SEED_ROOT//\\\\//}/id" "${SEED_ROOT//\\\\//}/he"' in WORKFLOW
    assert 'SEED_ROOT="${{ runner.temp }}/lang_pack_seeds"' in WORKFLOW


def test_the_smoke_langs_line_was_appended_and_not_retyped():
    """Every previously-smoked language must still be on the line after the rebase."""
    [group] = [line.split(":", 1)[1].split() for line in WORKFLOW.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert {"zh", "ko", "id", "ar", "th", "fa", "sl", "uk", "vi", "yue", "he"} <= set(group)
    assert group[-1] == "he" and len(group) == len(set(group))


def test_the_he_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("he") == 0
    out = capsys.readouterr().out
    assert "BUNDLED_SMOKE_PASS: language he tokenized" in out


def test_the_smoke_line_is_the_profile_sentence():
    """No entry in the ja/ko/zh override table, so the profile's own line is what runs."""
    assert app_module._LANGUAGE_SMOKE_LINES.get("he") is None
    assert get_profile("he").smoke_sentence

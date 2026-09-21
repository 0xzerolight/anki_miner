"""Every profile's content style is a valid S21/S22 declaration (seam-rtl-fonts contract).

``direction`` is ``ltr`` or ``rtl``; ``writing_system`` names a real
``QFontDatabase.WritingSystem`` member; a declared bundled face ships with every
asset site its licence needs. The matrix is the registry, so a consumer's profile
joins every case with no edit here -- a lead that forgets an asset site fails its
own run, not the release.
"""

from __future__ import annotations

import ast
import hashlib
import os
import tomllib
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFontDatabase  # noqa: E402

from anki_miner.config import AnkiMinerConfig  # noqa: E402
from anki_miner.languages.profile import ContentTextStyle  # noqa: E402
from anki_miner.languages.registry import available_languages, get_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CODES = sorted(available_languages())
FONTS_DIR = "anki_miner/gui/resources/fonts"

#: The languages that shipped before this seam and still declare nothing. Pinned,
#: not derived: they must stay left-to-right with no probe and no face, so every
#: ja/ko/Latin surface and card stays byte-identical. zh left the list when it
#: declared SimplifiedChinese; a later language is not added here.
SHIPPED_BEFORE_THE_SEAM = (
    "ja",
    "ko",
    "en",
    "ca",
    "de",
    "pt",
    "fr",
    "es",
    "it",
    "nl",
    "nb",
    "ro",
    "el",
    "fi",
    "hu",
    "hr",
    "sv",
    "pl",
    "lt",
    "da",
)


def _required_assets(root: Path) -> list[str]:
    tree = ast.parse((root / "scripts" / "check_wheel_assets.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIRED_ASSETS" for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise AssertionError("REQUIRED_ASSETS not found in scripts/check_wheel_assets.py")


def face_asset_problems(root: Path, basename: str) -> list[str]:
    """Every site a bundled face must appear in (contract C6), as a list of misses."""
    stem = basename.split("-")[0]
    face = f"{FONTS_DIR}/{basename}"
    licence = f"{FONTS_DIR}/OFL-{stem}.txt"
    problems = [f"missing file {rel}" for rel in (face, licence) if not (root / rel).is_file()]
    required = _required_assets(root)
    problems += [f"{rel} not in REQUIRED_ASSETS" for rel in (face, licence) if rel not in required]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if licence not in pyproject["project"]["license-files"]:
        problems.append(f"{licence} not in license-files")
    glob = f"*{Path(basename).suffix}"
    if glob not in pyproject["tool"]["setuptools"]["package-data"]["anki_miner.gui.resources.fonts"]:
        problems.append(f"{glob} not in the fonts package-data")
    provenance = (root / FONTS_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
    if f"## {basename}" not in provenance:
        problems.append(f"no '## {basename}' section in PROVENANCE.md")
    if (root / face).is_file() and hashlib.sha256((root / face).read_bytes()).hexdigest() not in provenance:
        problems.append(f"{basename} sha256 not in PROVENANCE.md")
    return problems


def test_the_new_fields_default_to_the_pre_seam_behaviour():
    style = ContentTextStyle(font_role="x", families=("A",), wrap=str)
    assert (style.direction, style.writing_system, style.bundled_fallback) == ("ltr", "", "")
    assert style.card_lang is None


@pytest.mark.parametrize("code", SHIPPED_BEFORE_THE_SEAM)
def test_languages_shipped_before_the_seam_keep_the_defaults(code):
    style = get_profile(code).content_style
    assert (style.direction, style.writing_system, style.bundled_fallback) == ("ltr", "", "")


#: The languages whose cards declare a BCP-47 tag, pinned rather than derived so
#: that a later profile picking one up is a deliberate edit here: every other
#: language's note must stay byte-identical to the pre-seam one.
TAG_THEIR_CARDS = ("yue", "zh")


def test_the_pinned_list_is_the_registry_answer():
    declaring = tuple(code for code in CODES if get_profile(code).content_style.card_lang is not None)
    assert declaring == TAG_THEIR_CARDS


@pytest.mark.parametrize("code", [code for code in CODES if code not in TAG_THEIR_CARDS])
def test_only_the_han_languages_tag_their_cards(code):
    """Han unification is the whole reason the tag exists; everyone else's note is untouched."""
    assert get_profile(code).content_style.card_lang is None


@pytest.mark.parametrize("code", TAG_THEIR_CARDS)
def test_a_han_language_resolves_a_script_subtag(code):
    style = get_profile(code).content_style
    assert style.card_lang is not None
    assert style.card_lang("中文", AnkiMinerConfig(language=code)) in ("zh-Hans", "zh-Hant")


@pytest.mark.parametrize("code", CODES)
def test_direction_is_ltr_or_rtl(code):
    assert get_profile(code).content_style.direction in ("ltr", "rtl")


@pytest.mark.parametrize("code", CODES)
def test_writing_system_is_empty_or_a_qt_member(code):
    name = get_profile(code).content_style.writing_system
    assert name == "" or name in QFontDatabase.WritingSystem.__members__


@pytest.mark.parametrize("code", CODES)
def test_a_bundled_face_ships_with_every_asset_site(code, qapp):
    style = get_profile(code).content_style
    if not style.bundled_fallback:
        return
    assert style.writing_system, "a bundled face needs a writing system to probe, or it never loads"
    assert face_asset_problems(ROOT, style.bundled_fallback) == []
    assert QFontDatabase.addApplicationFont(str(ROOT / FONTS_DIR / style.bundled_fallback)) != -1


def _tree(root: Path, *, assets: list[str], licences: list[str], globs: list[str], provenance: str) -> None:
    (root / "scripts").mkdir()
    (root / "scripts" / "check_wheel_assets.py").write_text(f"REQUIRED_ASSETS = {assets!r}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f"[project]\nlicense-files = {licences!r}\n\n[tool.setuptools.package-data]\n"
        f'"anki_miner.gui.resources.fonts" = {globs!r}\n',
        encoding="utf-8",
    )
    (root / FONTS_DIR).mkdir(parents=True)
    (root / FONTS_DIR / "PROVENANCE.md").write_text(provenance, encoding="utf-8")


def test_the_asset_check_names_every_missing_site(tmp_path):
    _tree(tmp_path, assets=[], licences=[], globs=["*.otf"], provenance="# Bundled font provenance\n")
    assert face_asset_problems(tmp_path, "Stub-Regular.ttf") == [
        f"missing file {FONTS_DIR}/Stub-Regular.ttf",
        f"missing file {FONTS_DIR}/OFL-Stub.txt",
        f"{FONTS_DIR}/Stub-Regular.ttf not in REQUIRED_ASSETS",
        f"{FONTS_DIR}/OFL-Stub.txt not in REQUIRED_ASSETS",
        f"{FONTS_DIR}/OFL-Stub.txt not in license-files",
        "*.ttf not in the fonts package-data",
        "no '## Stub-Regular.ttf' section in PROVENANCE.md",
    ]


def test_the_fonts_package_data_ships_ttf_faces():
    # Added once by the seam (judge r1) so the four face-bearing consumers never
    # collide on this line at rebase.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "*.ttf" in pyproject["tool"]["setuptools"]["package-data"]["anki_miner.gui.resources.fonts"]


def test_the_asset_check_passes_a_complete_tree_and_checks_the_digest(tmp_path):
    face, licence = f"{FONTS_DIR}/Stub-Regular.ttf", f"{FONTS_DIR}/OFL-Stub.txt"
    digest = hashlib.sha256(b"face bytes").hexdigest()
    _tree(
        tmp_path,
        assets=[face, licence],
        licences=[licence],
        globs=["*.otf", "*.ttf"],
        provenance=f"## Stub-Regular.ttf\n\n`{digest}`\n",
    )
    (tmp_path / face).write_bytes(b"face bytes")
    (tmp_path / licence).write_text("Copyright 2026 Stub Authors\n", encoding="utf-8")
    assert face_asset_problems(tmp_path, "Stub-Regular.ttf") == []
    (tmp_path / face).write_bytes(b"other bytes")
    assert face_asset_problems(tmp_path, "Stub-Regular.ttf") == ["Stub-Regular.ttf sha256 not in PROVENANCE.md"]


def test_provenance_intro_covers_per_language_faces():
    text = (ROOT / FONTS_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "One font ships" not in text
    assert "bundled_fallback" in text
    assert "OFL-<Name>.txt" in text

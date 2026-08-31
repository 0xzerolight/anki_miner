"""The zh/ko engines stay OUT of the frozen bundle, their tokenizers stay in.

Every non-Japanese mining engine ships as an in-app language pack
(``services/language_pack_installer.py``), so the spec must exclude the
third-party packages and keep only the first-party tokenizer modules that drive
them. This replaces the hook tests: the four collecting hooks were deleted with
the engines they collected, and what needs pinning now is their absence.

Spec TEXT is parsed rather than executed, the same way ``test_ko_bundling.py``
does it: PyInstaller is a build-time tool and is not installed in this venv.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = PROJECT_ROOT / "PyInstaller-Hooks"
SPEC = PROJECT_ROOT / "anki_miner.spec"

#: The third-party engines the packs deliver; none may reach the frozen graph.
PACKED_ENGINES = ("jieba", "pypinyin", "opencc", "kiwipiepy", "kiwipiepy_model")


def _list_body(name: str) -> str:
    """Return the body of the spec's ``<name>=[ ... ]`` Analysis argument.

    Scoped to the list rather than the whole file: ``"kiwipiepy"`` also appears
    in the licence-datas path (the notice ships even though the engine does
    not), so a file-wide substring check would read that as an import pin.
    """
    text = SPEC.read_text(encoding="utf-8")
    _before, marker, rest = text.partition(f"{name}=[")
    assert marker, f"anki_miner.spec has no {name}=[ list"
    body, closer, _after = rest.partition("\n    ],")
    assert closer, f"anki_miner.spec's {name}=[ list is unterminated"
    return body


def test_the_engines_are_excluded_from_the_graph() -> None:
    excludes = _list_body("excludes")
    for engine in PACKED_ENGINES:
        assert f'"{engine}",' in excludes, f"anki_miner.spec does not exclude {engine}"


def test_no_engine_is_pinned_into_the_import_graph() -> None:
    """A hiddenimport would drag the engine back in past the exclude."""
    hiddenimports = _list_body("hiddenimports")
    for engine in PACKED_ENGINES:
        for pin in (f'"{engine}"', f'"{engine}.'):
            assert pin not in hiddenimports, f"anki_miner.spec still pins {engine} into the graph"


def test_the_first_party_tokenizer_modules_stay_pinned() -> None:
    """importlib resolves these through an f-string bytecode analysis cannot follow."""
    hiddenimports = _list_body("hiddenimports")
    for entry in ('"anki_miner.languages.zh.tokenizer"', '"anki_miner.languages.ko.tokenizer"'):
        assert entry in hiddenimports, f"anki_miner.spec does not pin {entry}"


def test_the_collecting_hooks_are_gone() -> None:
    """A collect_all hook would repopulate what the excludes just removed."""
    for engine in PACKED_ENGINES:
        assert not (HOOKS_DIR / f"hook-{engine}.py").exists(), f"hook-{engine}.py outlived its engine"

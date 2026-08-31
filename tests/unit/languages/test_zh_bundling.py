"""The zh/ko engines stay OUT of the frozen bundle, their tokenizers stay in.

Every non-Japanese mining engine ships as an in-app language pack
(``services/language_pack_installer.py``), so the spec must exclude the
third-party packages and keep only the first-party tokenizer modules that drive
them. This replaces the hook tests: the four collecting hooks were deleted with
the engines they collected, and what needs pinning now is their absence.

Spec TEXT is parsed rather than executed, the same way ``test_ko_bundling.py``
does it: PyInstaller is a build-time tool and is not installed in this venv. The
one exception is the ``language_hiddenimports`` generator, lifted out by AST and
run on its own — a generated pin has no literal to search the text for.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from anki_miner.languages import AVAILABLE_LANGUAGES

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


def generated_language_hiddenimports() -> list[str]:
    """Run the spec's ``language_hiddenimports`` generator, and only that.

    The spec as a whole cannot be executed here (PyInstaller is a build-time
    tool, absent from this venv), so the generator's statements are lifted out
    by AST and run on their own. Deleting or gutting the generator fails this,
    which a text search for the module names could not.
    """
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    block = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "language_hiddenimports" for t in node.targets)
        )
        or (isinstance(node, ast.For) and "language_hiddenimports" in ast.dump(node))
    ]
    assert block, "anki_miner.spec no longer generates language_hiddenimports"
    namespace: dict[str, object] = {"AVAILABLE_LANGUAGES": AVAILABLE_LANGUAGES, "importlib": importlib}
    exec(compile(ast.Module(body=block, type_ignores=[]), str(SPEC), "exec"), namespace)  # noqa: S102
    generated = namespace["language_hiddenimports"]
    assert isinstance(generated, list)
    return generated


def test_the_generated_pins_reach_the_analysis() -> None:
    """A generator nothing splices in pins nothing."""
    assert "*language_hiddenimports," in _list_body("hiddenimports")


def test_the_first_party_tokenizer_and_pack_modules_stay_pinned() -> None:
    """importlib resolves all of these through f-strings bytecode analysis cannot follow.

    ``<code>.pack`` is what ``language_pack_installer.load_pack`` imports: with
    it missing the frozen app took load_pack's pip-absent path, appended no pack
    root to sys.path, and get_tagger died as "No tokenizer registered".
    """
    generated = generated_language_hiddenimports()
    for entry in (
        "anki_miner.languages.pack_spec",
        "anki_miner.languages.zh.tokenizer",
        "anki_miner.languages.ko.tokenizer",
        "anki_miner.languages.zh.pack",
        "anki_miner.languages.ko.pack",
    ):
        assert entry in generated, f"anki_miner.spec does not pin {entry}"


def test_every_language_package_itself_stays_pinned() -> None:
    """registry._discover() reaches every code's package through an f-string
    importlib.import_module, with no other static import left to find it —
    ja in particular has neither a tokenizer nor a pack module to ride in on.
    """
    generated = generated_language_hiddenimports()
    for code in AVAILABLE_LANGUAGES:
        entry = f"anki_miner.languages.{code}"
        assert entry in generated, f"anki_miner.spec does not pin {entry}"


def test_the_generator_pins_only_modules_that_exist() -> None:
    """Japanese has neither module — its engine is bundled, so it has no pack."""
    generated = generated_language_hiddenimports()
    assert not [name for name in generated if ".ja." in name]
    for name in generated:
        assert importlib.util.find_spec(name) is not None, f"{name} is pinned but unimportable"


def test_the_collecting_hooks_are_gone() -> None:
    """A collect_all hook would repopulate what the excludes just removed."""
    for engine in PACKED_ENGINES:
        assert not (HOOKS_DIR / f"hook-{engine}.py").exists(), f"hook-{engine}.py outlived its engine"

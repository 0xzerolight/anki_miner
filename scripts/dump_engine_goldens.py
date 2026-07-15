#!/usr/bin/env python3
"""Emit deterministic Android parity fixtures from a clean desktop engine checkout.

The exporter is intentionally a standalone interface.  Its own revision may move
independently of the engine revision supplied with ``--engine-root``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TOOL_NAME = "anki-miner-engine-golden-dumper"
TOOL_VERSION = "1"

UNIDIC_FEATURE_FIELDS = (
    "pos1",
    "pos2",
    "pos3",
    "pos4",
    "cType",
    "cForm",
    "lForm",
    "lemma",
    "orth",
    "pron",
    "orthBase",
    "pronBase",
    "goshu",
    "iType",
    "iForm",
    "fType",
    "fForm",
    "kana",
    "kanaBase",
    "form",
    "formBase",
    "iConType",
    "fConType",
    "aType",
    "aConType",
    "aModeType",
)

CASE_SECTIONS = (
    "tokenization",
    "morphology",
    "filtering",
    "deinflection",
    "compounds",
    "dictionaries",
    "frequency",
    "pitch",
    "cards",
)

SECTION_STATUS: dict[str, dict[str, str]] = {
    "tokenization": {"state": "implemented"},
    "morphology": {"state": "implemented"},
    "filtering": {"state": "pending", "reason": "Staged exporter: filter decisions and reasons are not emitted yet."},
    "deinflection": {"state": "pending", "reason": "Staged exporter: deinflection traces are not emitted yet."},
    "compounds": {"state": "implemented"},
    "dictionaries": {"state": "pending", "reason": "Staged exporter: ordered dictionary hits are not emitted yet."},
    "frequency": {"state": "pending", "reason": "Staged exporter: frequency tuples are not emitted yet."},
    "pitch": {"state": "pending", "reason": "Staged exporter: pitch records are not emitted yet."},
    "cards": {"state": "pending", "reason": "Staged exporter: rendered card fields are not emitted yet."},
}

RUNTIME_DISTRIBUTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fugashi", ("fugashi",)),
    ("unidic-lite", ("unidic_lite",)),
    ("pysubs2", ("pysubs2",)),
    ("requests", ("requests",)),
    ("Pillow", ("PIL",)),
    ("lxml", ("lxml",)),
    ("charset-normalizer", ("charset_normalizer",)),
    ("certifi", ("certifi",)),
    ("idna", ("idna",)),
    ("urllib3", ("urllib3",)),
)

CORPUS_ROOT_KEYS = frozenset({"schema_version", "cases"})
CORPUS_CASE_KEYS = frozenset({"id", "text", "coverage", "dictionary_terms", "expect"})
EXPECTATION_KEYS = frozenset({"token", "word"})
TOKEN_EXPECTATION_KEYS = frozenset({"surface", "lemma", "orthBase", "is_unknown"})
WORD_EXPECTATION_KEYS = frozenset(
    {"surface", "lemma", "orth_base", "mined_form", "surface_start", "surface_end", "highlight_end"}
)
ASSET_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class GoldenExportError(RuntimeError):
    """The requested fixture set cannot be derived without ambiguity."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise GoldenExportError(f"cannot inspect file {path}: {exc}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise GoldenExportError(f"symlinks are forbidden in golden provenance inputs: {path}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise GoldenExportError(f"golden provenance input is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_tree_files(root: Path) -> Iterable[Path]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise GoldenExportError(f"cannot inspect tree {root}: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise GoldenExportError(f"symlinks are forbidden in golden provenance inputs: {root}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise GoldenExportError(f"tree does not exist: {root}")

    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise GoldenExportError(f"symlinks are forbidden in golden provenance inputs: {path}")
            if not stat.S_ISDIR(entry_stat.st_mode):
                raise GoldenExportError(f"non-directory tree entry cannot be hashed: {path}")
            if name != "__pycache__":
                kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            path = current_path / name
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise GoldenExportError(f"symlinks are forbidden in golden provenance inputs: {path}")
            if not stat.S_ISREG(entry_stat.st_mode):
                raise GoldenExportError(f"non-regular tree entry cannot be hashed: {path}")
            if not name.endswith((".pyc", ".pyo")):
                yield path


def _sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _iter_tree_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _sha256_path(path: Path) -> str:
    if path.is_file():
        return _sha256_file(path)
    return _sha256_tree(path)


def _git(engine_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(engine_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise GoldenExportError(f"git {' '.join(args)} failed: {detail.strip()}") from exc
    return result.stdout.strip()


def _engine_revision(engine_root: Path) -> str:
    top_level = Path(_git(engine_root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != engine_root:
        raise GoldenExportError(f"--engine-root must be the Git top level: expected {top_level}, got {engine_root}")
    revision = _git(engine_root, "rev-parse", "HEAD")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise GoldenExportError(f"engine revision is not a full lowercase SHA: {revision!r}")
    dirty = _git(engine_root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise GoldenExportError(f"engine checkout is not clean:\n{dirty}")
    ignored = _git(engine_root, "ls-files", "--others", "--ignored", "--exclude-standard").splitlines()
    unexpected_ignored = [
        value for value in ignored if "__pycache__" not in Path(value).parts or not value.endswith((".pyc", ".pyo"))
    ]
    if unexpected_ignored:
        rendered = "\n".join(unexpected_ignored)
        raise GoldenExportError(f"engine checkout contains ignored non-bytecode files:\n{rendered}")
    return revision


def _engine_module_names() -> list[str]:
    return sorted(name for name in sys.modules if name == "anki_miner" or name.startswith("anki_miner."))


def _reject_preloaded_engine_modules() -> None:
    loaded = _engine_module_names()
    if loaded:
        raise GoldenExportError(f"engine modules were imported before isolation: {', '.join(loaded)}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_engine_module_origins(engine_package: Path) -> None:
    loaded = _engine_module_names()
    if not loaded:
        raise GoldenExportError("engine derivation loaded no anki_miner modules")
    for name in loaded:
        module = sys.modules[name]
        raw_file = getattr(module, "__file__", None)
        if raw_file is not None:
            origin = Path(raw_file).resolve()
            if not _is_relative_to(origin, engine_package):
                raise GoldenExportError(f"engine module {name} loaded outside --engine-root")
            continue
        raw_paths = getattr(module, "__path__", None)
        if raw_paths is None:
            raise GoldenExportError(f"engine module {name} has no verifiable import origin")
        origins = [Path(value).resolve() for value in raw_paths]
        if not origins or any(not _is_relative_to(origin, engine_package) for origin in origins):
            raise GoldenExportError(f"engine module {name} loaded outside --engine-root")


def _remove_engine_modules() -> None:
    for name in reversed(_engine_module_names()):
        sys.modules.pop(name, None)


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenExportError(f"invalid corpus {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GoldenExportError("corpus root must be an object")
    _reject_unknown_keys(payload, CORPUS_ROOT_KEYS, "corpus root")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise GoldenExportError(f"corpus schema_version must be {SCHEMA_VERSION}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise GoldenExportError("corpus cases must be a non-empty array")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise GoldenExportError("every corpus case must be an object")
        _reject_unknown_keys(case, CORPUS_CASE_KEYS, "corpus case")
        if not isinstance(case.get("id"), str) or not case["id"] or not isinstance(case.get("text"), str):
            raise GoldenExportError("every corpus case requires string id and text fields")
        case_id = case["id"]
        if case_id in seen:
            raise GoldenExportError(f"duplicate corpus case id: {case_id}")
        seen.add(case_id)
        _validate_string_array(case.get("coverage"), f"{case_id}.coverage", required=True)
        if "dictionary_terms" in case:
            _validate_string_array(case["dictionary_terms"], f"{case_id}.dictionary_terms", required=True)
        _validate_expectation(case_id, case.get("expect"))
    return cases


def _reject_unknown_keys(value: Mapping[str, Any], allowed: frozenset[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GoldenExportError(f"{location} has unknown keys: {', '.join(unknown)}")


def _validate_string_array(value: Any, location: str, *, required: bool) -> None:
    if not isinstance(value, list) or (required and not value):
        raise GoldenExportError(f"{location} must be a non-empty string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise GoldenExportError(f"{location} must contain only non-empty strings")
    if len(set(value)) != len(value):
        raise GoldenExportError(f"{location} must not contain duplicates")


def _validate_nullable_string(value: Any, location: str) -> None:
    if value is not None and not isinstance(value, str):
        raise GoldenExportError(f"{location} must be a string or null")


def _validate_expectation(case_id: str, value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise GoldenExportError(f"{case_id}.expect must be a non-empty object")
    _reject_unknown_keys(value, EXPECTATION_KEYS, f"{case_id}.expect")

    token = value.get("token")
    if token is not None:
        if not isinstance(token, dict):
            raise GoldenExportError(f"{case_id}.expect.token must be an object")
        _reject_unknown_keys(token, TOKEN_EXPECTATION_KEYS, f"{case_id}.expect.token")
        if not isinstance(token.get("surface"), str) or not token["surface"]:
            raise GoldenExportError(f"{case_id}.expect.token.surface must be a non-empty string")
        for key in ("lemma", "orthBase"):
            if key in token:
                _validate_nullable_string(token[key], f"{case_id}.expect.token.{key}")
        if "is_unknown" in token and not isinstance(token["is_unknown"], bool):
            raise GoldenExportError(f"{case_id}.expect.token.is_unknown must be boolean")

    word = value.get("word")
    if word is not None:
        if not isinstance(word, dict):
            raise GoldenExportError(f"{case_id}.expect.word must be an object")
        _reject_unknown_keys(word, WORD_EXPECTATION_KEYS, f"{case_id}.expect.word")
        for key in ("surface", "lemma", "orth_base", "mined_form"):
            if key in word and (not isinstance(word[key], str) or not word[key]):
                raise GoldenExportError(f"{case_id}.expect.word.{key} must be a non-empty string")
        if not isinstance(word.get("surface"), str) or not word["surface"]:
            raise GoldenExportError(f"{case_id}.expect.word.surface must be a non-empty string")
        for key in ("surface_start", "surface_end", "highlight_end"):
            if key in word and (not isinstance(word[key], int) or isinstance(word[key], bool) or word[key] < 0):
                raise GoldenExportError(f"{case_id}.expect.word.{key} must be a non-negative integer")

    if token is None and word is None:
        raise GoldenExportError(f"{case_id}.expect must define token and/or word")


def _utf16_offset(text: str, codepoint_offset: int) -> int:
    return len(text[:codepoint_offset].encode("utf-16-le")) // 2


def _normalise_feature(value: Any) -> str | None:
    if value is None or value == "*":
        return None
    return str(value)


def _locate_tokens(text: str, tokens: Sequence[Any]) -> list[tuple[Any, int, int]]:
    cursor = 0
    located: list[tuple[Any, int, int]] = []
    for token in tokens:
        surface = str(token.surface)
        if not surface:
            raise GoldenExportError("token surfaces must not be empty")
        start = text.find(surface, cursor)
        if start < 0:
            raise GoldenExportError(f"token surface {surface!r} is not locatable after offset {cursor} in {text!r}")
        gap = text[cursor:start]
        if gap and not gap.isspace():
            raise GoldenExportError(f"token stream omitted non-whitespace text {gap!r} at offset {cursor} in {text!r}")
        end = start + len(surface)
        located.append((token, start, end))
        cursor = end
    trailing = text[cursor:]
    if trailing and not trailing.isspace():
        raise GoldenExportError(
            f"token stream omitted trailing non-whitespace text {trailing!r} at offset {cursor} in {text!r}"
        )
    return located


def _token_record(text: str, token: Any, start: int, end: int) -> dict[str, Any]:
    feature = token.feature
    features = {name: _normalise_feature(getattr(feature, name, None)) for name in UNIDIC_FEATURE_FIELDS}
    return {
        "surface": str(token.surface),
        "is_unknown": bool(getattr(token, "is_unk", False)),
        "offsets": {
            "codepoint_start": start,
            "codepoint_end": end,
            "utf16_start": _utf16_offset(text, start),
            "utf16_end": _utf16_offset(text, end),
        },
        "features": features,
    }


def _make_tagger(dicdir: Path) -> Any:
    import fugashi

    resolved = dicdir.resolve()
    if any(char.isspace() for char in str(resolved)):
        raise GoldenExportError("--dicdir paths containing whitespace are unsupported by MeCab's option parser")
    mecabrc = resolved / "mecabrc"
    system_dictionary = resolved / "sys.dic"
    if not mecabrc.is_file():
        raise GoldenExportError(f"UniDic directory has no mecabrc: {resolved}")
    if not system_dictionary.is_file():
        raise GoldenExportError(f"UniDic directory has no sys.dic: {resolved}")
    tagger = fugashi.Tagger(f'-r "{mecabrc}" -d "{resolved}"')
    dictionary_info = tagger.dictionary_info
    if not dictionary_info:
        raise GoldenExportError("fugashi reported no loaded dictionaries")
    loaded_paths: list[Path] = []
    for info in dictionary_info:
        filename = info.get("filename")
        if not isinstance(filename, str) or not filename:
            raise GoldenExportError("fugashi returned dictionary metadata without a filename")
        loaded = Path(filename).resolve()
        if not _is_relative_to(loaded, resolved):
            raise GoldenExportError("fugashi loaded a dictionary outside --dicdir")
        loaded_paths.append(loaded)
    if system_dictionary.resolve() not in loaded_paths:
        raise GoldenExportError("fugashi did not load --dicdir/sys.dic")
    return tagger


def _tokenization_cases(corpus: Sequence[Mapping[str, Any]], tagger: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for case in corpus:
        text = str(case["text"])
        tokens = list(tagger(text))
        token_records = [_token_record(text, token, start, end) for token, start, end in _locate_tokens(text, tokens)]
        _assert_token_expectation(case, token_records)
        output.append({"id": case["id"], "text": text, "tokens": token_records})
    return output


def _assert_token_expectation(case: Mapping[str, Any], tokens: Sequence[Mapping[str, Any]]) -> None:
    expectation = case["expect"]
    expected = expectation.get("token")
    if not isinstance(expected, Mapping):
        return
    surface = expected["surface"]
    matching = [token for token in tokens if token["surface"] == surface]
    if len(matching) != 1:
        raise GoldenExportError(f"{case['id']}: expected exactly one token with surface {surface!r}")
    token = matching[0]
    features = token["features"]
    comparisons: dict[str, Any] = {
        "lemma": features["lemma"],
        "orthBase": features["orthBase"],
        "is_unknown": token["is_unknown"],
    }
    for key, actual in comparisons.items():
        if key in expected and expected[key] != actual:
            raise GoldenExportError(f"{case['id']}: token {key} expected {expected[key]!r}, derived {actual!r}")


def _word_record(word: Any) -> dict[str, Any]:
    return {
        "surface": word.surface,
        "lemma": word.lemma,
        "orth_base": word.orth_base,
        "mined_form": word.mined_form,
        "reading": word.reading,
        "pos": word.pos,
        "surface_start": word.surface_start,
        "surface_end": word.surface_end,
        "highlight_end": word.highlight_end,
        "sentence": word.sentence,
        "expression_furigana": word.expression_furigana,
        "expression_reading": word.expression_reading,
        "sentence_furigana": word.sentence_furigana,
        "sentence_reading": word.sentence_reading,
    }


def _morphology_and_compound_cases(
    corpus: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.models.reading import ReadingUnit
    from anki_miner.services.subtitle_parser import SubtitleParserService

    morphology: list[dict[str, Any]] = []
    compounds: list[dict[str, Any]] = []
    for case in corpus:
        dictionary_terms = frozenset(str(value) for value in case.get("dictionary_terms", []))

        def term_lookup(candidates: list[str], *, terms: frozenset[str] = dictionary_terms) -> set[str]:
            return set(candidates) & terms

        parser = SubtitleParserService(
            AnkiMinerConfig(),
            term_lookup=term_lookup if dictionary_terms else None,
        )
        words, _line_index, _counts = parser.parse_text_units(
            [ReadingUnit(text=str(case["text"]), index=0, location_label=str(case["id"]))],
            want_line_index=False,
        )
        word_records = [_word_record(word) for word in words]
        record = {
            "id": case["id"],
            "input": {"text": case["text"]},
            "output": {"words": word_records},
        }
        morphology.append(record)
        if dictionary_terms:
            compounds.append(
                {
                    "id": case["id"],
                    "input": {"text": case["text"], "dictionary_terms": sorted(dictionary_terms)},
                    "output": {"words": word_records},
                }
            )
        _assert_word_expectation(case, words)
    return morphology, compounds


def _install_shared_tagger(tagger: Any) -> None:
    """Make parser-derived sections use the exact same externally pointed dictionary."""
    from anki_miner.services import tagger as tagger_module

    tagger_module._tagger = tagger
    tagger_module._locked_tagger = tagger_module.LockedTagger(tagger)


def _assert_word_expectation(case: Mapping[str, Any], words: Sequence[Any]) -> None:
    expectation = case["expect"]
    expected = expectation.get("word")
    if not isinstance(expected, Mapping):
        return
    surface = expected["surface"]
    matching = [word for word in words if word.surface == surface]
    if len(matching) != 1:
        raise GoldenExportError(f"{case['id']}: expected exactly one mined word with surface {surface!r}")
    word = matching[0]
    comparisons: dict[str, Any] = {
        "lemma": word.lemma,
        "orth_base": word.orth_base,
        "mined_form": word.mined_form,
        "surface_start": word.surface_start,
        "surface_end": word.surface_end,
        "highlight_end": word.highlight_end,
    }
    for key, actual in comparisons.items():
        if key in expected and expected[key] != actual:
            raise GoldenExportError(f"{case['id']}: {key} expected {expected[key]!r}, derived {actual!r}")


def _distribution_file_set(distribution: importlib.metadata.Distribution) -> set[Path]:
    files = distribution.files
    if files is None:
        raise GoldenExportError(f"runtime distribution has no file manifest: {distribution.metadata['Name']}")
    return {Path(str(distribution.locate_file(entry))).resolve() for entry in files}


def _module_content_hash(import_name: str, distribution_files: set[Path], engine_root: Path) -> str:
    module = importlib.import_module(import_name)
    raw_file = getattr(module, "__file__", None)
    if raw_file is None:
        raise GoldenExportError(f"runtime import has no verifiable file origin: {import_name}")
    module_file = Path(raw_file).resolve()
    if _is_relative_to(module_file, engine_root):
        raise GoldenExportError(f"runtime import was shadowed by --engine-root: {import_name}")
    if module_file not in distribution_files:
        raise GoldenExportError(f"runtime import is not owned by its declared distribution: {import_name}")

    raw_paths = getattr(module, "__path__", None)
    if raw_paths is None:
        return _sha256_file(module_file)
    package_roots = sorted({Path(value).resolve() for value in raw_paths})
    if not package_roots:
        raise GoldenExportError(f"runtime package has no content roots: {import_name}")
    content = {str(index): _sha256_tree(root) for index, root in enumerate(package_roots)}
    return _sha256_bytes(_canonical_json_bytes(content))


def _runtime_provenance(engine_root: Path) -> dict[str, Any]:
    dependencies: dict[str, dict[str, str]] = {}
    for distribution_name, import_names in RUNTIME_DISTRIBUTIONS:
        try:
            distribution = importlib.metadata.distribution(distribution_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise GoldenExportError(f"required golden runtime distribution is missing: {distribution_name}") from exc
        distribution_files = _distribution_file_set(distribution)
        import_hashes = {
            import_name: _module_content_hash(import_name, distribution_files, engine_root)
            for import_name in import_names
        }
        dependencies[distribution_name] = {
            "version": distribution.version,
            "content_sha256": _sha256_bytes(_canonical_json_bytes(import_hashes)),
        }
    runtime: dict[str, Any] = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine().lower()}",
        "dependencies": dependencies,
    }
    runtime["sha256"] = _sha256_bytes(_canonical_json_bytes(runtime))
    return runtime


def _parse_assets(values: Sequence[str]) -> dict[str, Path]:
    assets: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise GoldenExportError("--asset values must have the form NAME=PATH")
        if ASSET_NAME_PATTERN.fullmatch(name) is None:
            raise GoldenExportError(f"asset name must be a stable lowercase identifier: {name!r}")
        if name in assets:
            raise GoldenExportError(f"duplicate asset name: {name}")
        unresolved = Path(raw_path).expanduser().absolute()
        if unresolved.is_symlink():
            raise GoldenExportError(f"asset roots must not be symlinks: {unresolved}")
        path = unresolved.resolve()
        if not path.exists():
            raise GoldenExportError(f"asset does not exist: {path}")
        assets[name] = path
    return assets


def _normalise_input_path(path: Path, *, label: str) -> Path:
    unresolved = path.expanduser().absolute()
    if unresolved.is_symlink():
        raise GoldenExportError(f"{label} must not be a symlink: {unresolved}")
    try:
        return unresolved.resolve(strict=True)
    except OSError as exc:
        raise GoldenExportError(f"{label} does not exist: {unresolved}") from exc


def _validated_section_status(cases: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, dict[str, str]]:
    if tuple(cases) != CASE_SECTIONS or tuple(SECTION_STATUS) != CASE_SECTIONS:
        raise GoldenExportError("golden section order/status does not match the frozen schema")
    manifest: dict[str, dict[str, str]] = {}
    for section in CASE_SECTIONS:
        status = SECTION_STATUS[section]
        state = status.get("state")
        records = cases[section]
        if state == "implemented":
            if not records:
                raise GoldenExportError(f"implemented golden section is empty: {section}")
            if set(status) != {"state"}:
                raise GoldenExportError(f"implemented section status is malformed: {section}")
        elif state == "pending":
            reason = status.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise GoldenExportError(f"pending golden section has no reason: {section}")
            if records:
                raise GoldenExportError(f"pending golden section unexpectedly contains records: {section}")
            if set(status) != {"state", "reason"}:
                raise GoldenExportError(f"pending section status is malformed: {section}")
        else:
            raise GoldenExportError(f"golden section has unknown state: {section}")
        manifest[section] = dict(status)
    return manifest


def build_goldens(
    *,
    engine_root: Path,
    corpus_path: Path,
    dicdir: Path | None,
    assets: Mapping[str, Path],
) -> dict[str, Any]:
    _reject_preloaded_engine_modules()
    engine_root = _normalise_input_path(engine_root, label="--engine-root")
    corpus_path = _normalise_input_path(corpus_path, label="--corpus")
    engine_package = engine_root / "anki_miner"
    if not (engine_package / "__init__.py").is_file():
        raise GoldenExportError(f"--engine-root does not contain anki_miner: {engine_root}")
    revision = _engine_revision(engine_root)
    engine_tree_sha256 = _sha256_tree(engine_package)
    corpus = _load_corpus(corpus_path)
    if dicdir is None:
        raise GoldenExportError("--dicdir is required so UniDic provenance is never ambient")
    resolved_dicdir = _normalise_input_path(dicdir, label="--dicdir")
    effective_assets: dict[str, Path] = {}
    for name, path in assets.items():
        if ASSET_NAME_PATTERN.fullmatch(name) is None:
            raise GoldenExportError(f"asset name must be a stable lowercase identifier: {name!r}")
        effective_assets[name] = _normalise_input_path(path, label=f"asset {name!r}")
    if "unidic_dicdir" in effective_assets:
        raise GoldenExportError("asset name 'unidic_dicdir' is reserved for --dicdir provenance")
    effective_assets["unidic_dicdir"] = resolved_dicdir
    asset_hashes = {name: _sha256_path(path) for name, path in sorted(effective_assets.items())}
    data: dict[str, Any] = {
        "corpus_sha256": _sha256_file(corpus_path),
        "assets_sha256": asset_hashes,
    }
    data["sha256"] = _sha256_bytes(_canonical_json_bytes(data))

    # config.paths freezes ANKI_MINER_HOME at import time.  The environment and
    # import root therefore must be established before importing any engine code.
    previous_home = os.environ.get("ANKI_MINER_HOME")
    previous_sys_path = list(sys.path)
    with tempfile.TemporaryDirectory(prefix="anki-miner-goldens-") as isolated_home:
        os.environ["ANKI_MINER_HOME"] = isolated_home
        try:
            # Validate and preload third-party modules before exposing engine_root,
            # so a root-level shadow module can never execute as runtime code.
            runtime = _runtime_provenance(engine_root)
            sys.path.insert(0, str(engine_root))
            tagger = _make_tagger(resolved_dicdir)
            _install_shared_tagger(tagger)
            tokenization = _tokenization_cases(corpus, tagger)
            morphology, compounds = _morphology_and_compound_cases(corpus)
            _assert_engine_module_origins(engine_package)
        finally:
            _remove_engine_modules()
            sys.path[:] = previous_sys_path
            if previous_home is None:
                os.environ.pop("ANKI_MINER_HOME", None)
            else:
                os.environ["ANKI_MINER_HOME"] = previous_home

    if _engine_revision(engine_root) != revision or _sha256_tree(engine_package) != engine_tree_sha256:
        raise GoldenExportError("engine checkout changed while goldens were being derived")
    tool_path = Path(__file__).resolve()
    cases: dict[str, list[dict[str, Any]]] = {section: [] for section in CASE_SECTIONS}
    cases["tokenization"] = tokenization
    cases["morphology"] = morphology
    cases["compounds"] = compounds
    section_status = _validated_section_status(cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "engine": {"revision": revision, "tree_sha256": engine_tree_sha256},
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION, "sha256": _sha256_file(tool_path)},
            "runtime": runtime,
            "data": data,
        },
        "unidic_feature_fields": list(UNIDIC_FEATURE_FIELDS),
        "section_status": section_status,
        "cases": cases,
    }


def _parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True, help="clean desktop checkout to load as the engine")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=repository_root / "tests" / "fixtures" / "goldens" / "tokenizer-v1.json",
    )
    parser.add_argument(
        "--dicdir",
        type=Path,
        required=True,
        help="external UniDic directory recorded in fixture provenance",
    )
    parser.add_argument("--asset", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--output", type=Path, help="destination JSON; omit for stdout")
    parser.add_argument("--compact", action="store_true", help="emit canonical compact JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_goldens(
            engine_root=args.engine_root,
            corpus_path=args.corpus,
            dicdir=args.dicdir,
            assets=_parse_assets(args.asset),
        )
    except GoldenExportError as exc:
        print(f"golden export failed: {exc}", file=sys.stderr)
        return 2
    if args.compact:
        rendered = _canonical_json_bytes(result).decode("utf-8") + "\n"
    else:
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

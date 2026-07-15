#!/usr/bin/env python3
"""Emit deterministic Android parity fixtures from a clean desktop engine checkout.

The exporter is intentionally a standalone interface.  Its own revision may move
independently of the engine revision supplied with ``--engine-root``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
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

RUNTIME_DISTRIBUTIONS = (
    "fugashi",
    "unidic-lite",
    "pysubs2",
    "requests",
    "Pillow",
    "lxml",
    "charset-normalizer",
)


class GoldenExportError(RuntimeError):
    """The requested fixture set cannot be derived without ambiguity."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_tree_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith((".pyc", ".pyo")):
            yield path


def _sha256_tree(root: Path) -> str:
    if not root.is_dir():
        raise GoldenExportError(f"tree does not exist: {root}")
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
    revision = _git(engine_root, "rev-parse", "HEAD")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise GoldenExportError(f"engine revision is not a full lowercase SHA: {revision!r}")
    dirty = _git(engine_root, "status", "--porcelain", "--", "anki_miner")
    if dirty:
        raise GoldenExportError("engine checkout has uncommitted changes under anki_miner")
    return revision


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenExportError(f"invalid corpus {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise GoldenExportError(f"corpus schema_version must be {SCHEMA_VERSION}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise GoldenExportError("corpus cases must be a non-empty array")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not isinstance(case.get("text"), str):
            raise GoldenExportError("every corpus case requires string id and text fields")
        case_id = case["id"]
        if case_id in seen:
            raise GoldenExportError(f"duplicate corpus case id: {case_id}")
        seen.add(case_id)
    return cases


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
        start = text.find(surface, cursor)
        if start < 0:
            raise GoldenExportError(f"token surface {surface!r} is not locatable after offset {cursor} in {text!r}")
        end = start + len(surface)
        located.append((token, start, end))
        cursor = end
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


def _make_tagger(dicdir: Path | None) -> Any:
    import fugashi

    if dicdir is None:
        return fugashi.Tagger()
    resolved = dicdir.resolve()
    if not (resolved / "sys.dic").is_file():
        raise GoldenExportError(f"UniDic directory has no sys.dic: {resolved}")
    if any(char.isspace() for char in str(resolved)):
        raise GoldenExportError("--dicdir paths containing whitespace are unsupported by MeCab's option parser")
    return fugashi.Tagger(f"-d {resolved}")


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
    expected = case.get("expect")
    if not isinstance(expected, Mapping) or not isinstance(expected.get("surface"), str):
        return
    surface = expected["surface"]
    matching = [token for token in tokens if token["surface"] == surface]
    if len(matching) != 1:
        raise GoldenExportError(f"{case['id']}: expected exactly one token with surface {surface!r}")
    token = matching[0]
    features = token["features"]
    comparisons = {
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
        _assert_seed_expectation(case, words)
    return morphology, compounds


def _install_shared_tagger(tagger: Any) -> None:
    """Make parser-derived sections use the exact same externally pointed dictionary."""
    from anki_miner.services import tagger as tagger_module

    tagger_module._tagger = tagger
    tagger_module._locked_tagger = tagger_module.LockedTagger(tagger)


def _assert_seed_expectation(case: Mapping[str, Any], words: Sequence[Any]) -> None:
    expected = case.get("expect")
    if not isinstance(expected, Mapping):
        return
    surface = expected.get("surface")
    if not isinstance(surface, str):
        return
    matching = [word for word in words if word.surface == surface]
    # OOV-only tokenizer cases are allowed to be rejected by the mining POS gate.
    if expected.get("is_unknown") is True and not matching:
        return
    if len(matching) != 1:
        raise GoldenExportError(f"{case['id']}: expected exactly one mined word with surface {surface!r}")
    word = matching[0]
    comparisons = {
        "lemma": word.lemma,
        "mining_base": word.mined_form,
    }
    for key, actual in comparisons.items():
        if key in expected and expected[key] != actual:
            raise GoldenExportError(f"{case['id']}: {key} expected {expected[key]!r}, derived {actual!r}")


def _runtime_provenance() -> dict[str, Any]:
    dependencies: dict[str, str] = {}
    for distribution in RUNTIME_DISTRIBUTIONS:
        try:
            dependencies[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise GoldenExportError(f"required golden runtime distribution is missing: {distribution}") from exc
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
        if name in assets:
            raise GoldenExportError(f"duplicate asset name: {name}")
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            raise GoldenExportError(f"asset does not exist: {path}")
        assets[name] = path
    return assets


def build_goldens(
    *,
    engine_root: Path,
    corpus_path: Path,
    dicdir: Path | None,
    assets: Mapping[str, Path],
) -> dict[str, Any]:
    engine_root = engine_root.expanduser().resolve()
    corpus_path = corpus_path.expanduser().resolve()
    engine_package = engine_root / "anki_miner"
    if not (engine_package / "__init__.py").is_file():
        raise GoldenExportError(f"--engine-root does not contain anki_miner: {engine_root}")
    revision = _engine_revision(engine_root)
    corpus = _load_corpus(corpus_path)
    resolved_dicdir = dicdir.expanduser().resolve() if dicdir is not None else None
    effective_assets = dict(assets)
    if resolved_dicdir is not None:
        if "unidic_dicdir" in effective_assets:
            raise GoldenExportError("asset name 'unidic_dicdir' is reserved for --dicdir provenance")
        effective_assets["unidic_dicdir"] = resolved_dicdir

    # config.paths freezes ANKI_MINER_HOME at import time.  The environment and
    # import root therefore must be established before importing any engine code.
    previous_home = os.environ.get("ANKI_MINER_HOME")
    with tempfile.TemporaryDirectory(prefix="anki-miner-goldens-") as isolated_home:
        os.environ["ANKI_MINER_HOME"] = isolated_home
        sys.path.insert(0, str(engine_root))
        try:
            tagger = _make_tagger(resolved_dicdir)
            _install_shared_tagger(tagger)
            tokenization = _tokenization_cases(corpus, tagger)
            morphology, compounds = _morphology_and_compound_cases(corpus)
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(engine_root))
            if previous_home is None:
                os.environ.pop("ANKI_MINER_HOME", None)
            else:
                os.environ["ANKI_MINER_HOME"] = previous_home

    asset_hashes = {name: _sha256_path(path) for name, path in sorted(effective_assets.items())}
    data: dict[str, Any] = {
        "corpus_sha256": _sha256_file(corpus_path),
        "assets_sha256": asset_hashes,
    }
    data["sha256"] = _sha256_bytes(_canonical_json_bytes(data))
    tool_path = Path(__file__).resolve()
    cases: dict[str, list[dict[str, Any]]] = {section: [] for section in CASE_SECTIONS}
    cases["tokenization"] = tokenization
    cases["morphology"] = morphology
    cases["compounds"] = compounds
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "engine": {"revision": revision, "tree_sha256": _sha256_tree(engine_package)},
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION, "sha256": _sha256_file(tool_path)},
            "runtime": _runtime_provenance(),
            "data": data,
        },
        "unidic_feature_fields": list(UNIDIC_FEATURE_FIELDS),
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
    parser.add_argument("--dicdir", type=Path, help="external UniDic directory; defaults to fugashi discovery")
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

"""Complete v2 Android parity contract derived from the desktop engine.

This module is loaded by :mod:`dump_engine_goldens`.  It keeps the historical
v1 derivation readable while making every M1 parity section executable.  All
resource data is synthetic and tiny except the externally supplied, pinned
UniDic tree.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import dump_engine_goldens as v1
from prepare_golden_unidic import resource_record as unidic_resource_record
from prepare_golden_unidic import verify_tree as verify_unidic_tree

SCHEMA_VERSION = 2
TOOL_NAME = "anki-miner-engine-golden-dumper"
TOOL_VERSION = "2"
PINNED_ENGINE_REVISION = "ba3b3cfbcc53e57a440c8b9f157209851408c62a"

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

RUNTIME_DISTRIBUTIONS = tuple(item for item in v1.RUNTIME_DISTRIBUTIONS if item[0] != "unidic-lite")

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "identity",
        "filtering",
        "deinflection",
        "dictionaries",
        "frequency",
        "pitch",
        "card",
    }
)
_IDENTITY_KEYS = frozenset(
    {
        "run_id",
        "request_id",
        "client_note_id",
        "note_id",
        "card_audio_asset_id",
        "card_image_asset_id",
        "dictionary_image_asset_id",
    }
)
_OPAQUE_ID_RE = re.compile(r"^(?:run|anki|note|asset)_[0-9a-f]{32}$")
_MARKED_DICTIONARY_IMG_RE = re.compile(
    r'<img\b[^>]*class="[^"]*\banki\-miner\-dict\-media\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000159c8e1740000000049454e44ae426082"
)


def _mapping(value: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise v1.GoldenExportError(f"{location} must be an object")
    return value


def _list(value: Any, *, location: str, nonempty: bool = True) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise v1.GoldenExportError(f"{location} must be a {qualifier}array")
    return value


def _string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise v1.GoldenExportError(f"{location} must be a non-empty string")
    return value


def _exact_keys(value: Mapping[str, Any], keys: frozenset[str], *, location: str) -> None:
    missing = sorted(keys - set(value))
    unknown = sorted(set(value) - keys)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise v1.GoldenExportError(f"{location} has {'; '.join(details)}")


def _load_contract_input(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise v1.GoldenExportError(f"invalid v2 contract input {path}: {exc}") from exc
    root = _mapping(payload, location="v2 input root")
    _exact_keys(root, _ROOT_KEYS, location="v2 input root")
    if root["schema_version"] != SCHEMA_VERSION:
        raise v1.GoldenExportError(f"v2 input schema_version must be {SCHEMA_VERSION}")

    identity = _mapping(root["identity"], location="v2 identity")
    _exact_keys(identity, _IDENTITY_KEYS, location="v2 identity")
    for key in _IDENTITY_KEYS - {"note_id"}:
        value = _string(identity[key], location=f"v2 identity.{key}")
        if _OPAQUE_ID_RE.fullmatch(value) is None:
            raise v1.GoldenExportError(f"v2 identity.{key} is not a fixed opaque ID")
    note_id = identity["note_id"]
    if not isinstance(note_id, int) or isinstance(note_id, bool) or note_id <= 0:
        raise v1.GoldenExportError("v2 identity.note_id must be a positive integer")
    for key in _ROOT_KEYS - {"schema_version", "identity"}:
        expected = list if key == "deinflection" else dict
        if not isinstance(root[key], expected):
            kind = "array" if expected is list else "object"
            raise v1.GoldenExportError(f"v2 input {key} must be an {kind}")
    return root


def _word_from_record(record: Mapping[str, Any]) -> Any:
    from anki_miner.models import TokenizedWord

    surface = _string(record.get("surface"), location="filtering word.surface")
    sentence = _string(record.get("sentence"), location="filtering word.sentence")
    try:
        surface_start = sentence.index(surface)
    except ValueError as exc:
        raise v1.GoldenExportError(f"word surface {surface!r} is absent from sentence {sentence!r}") from exc
    start_time = record.get("start_time")
    if not isinstance(start_time, (int, float)) or isinstance(start_time, bool):
        raise v1.GoldenExportError("filtering word.start_time must be numeric")
    return TokenizedWord(
        surface=surface,
        lemma=_string(record.get("lemma"), location="filtering word.lemma"),
        reading=_string(record.get("reading"), location="filtering word.reading"),
        sentence=sentence,
        start_time=float(start_time),
        end_time=float(start_time) + 1.0,
        duration=1.0,
        orth_base=_string(record.get("orth_base"), location="filtering word.orth_base"),
        expression_furigana=surface,
        expression_reading=str(record.get("expression_reading", "")),
        lemma_reading=str(record.get("lemma_reading", "")),
        sentence_furigana=sentence,
        sentence_reading=sentence,
        pos=str(record["pos"]) if record.get("pos") is not None else None,
        surface_start=surface_start,
        surface_end=surface_start + len(surface),
        highlight_end=surface_start + len(surface),
    )


class _GoldenPresenter:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    def show_info(self, message: str) -> None:
        self.events.append({"level": "info", "message": str(message)})

    def show_success(self, message: str) -> None:
        self.events.append({"level": "success", "message": str(message)})

    def show_warning(self, message: str) -> None:
        self.events.append({"level": "warning", "message": str(message)})

    def show_error(self, message: str) -> None:
        self.events.append({"level": "error", "message": str(message)})


class _GoldenAnkiRead:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing

    def get_existing_vocabulary(self) -> set[str]:
        return set(self._existing)


def _phase2_filter_run(
    filtering_input: Mapping[str, Any],
    *,
    frequency_service: Any,
    definition_service: Any,
    allow_duplicates: bool,
) -> dict[str, Any]:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.orchestration.episode_processor import EpisodeProcessor, _EpisodeContext
    from anki_miner.services.anki_note_builder import _strip_for_dedup
    from anki_miner.services.word_filter import WordFilterService

    raw_fields = _list(filtering_input.get("existing_first_fields"), location="filtering.existing_first_fields")
    normalized = [_strip_for_dedup(_string(value, location="filtering existing field")) for value in raw_fields]
    records = _list(filtering_input.get("words"), location="filtering.words")
    words: list[Any] = []
    ids_by_object: dict[int, str] = {}
    for raw_record in records:
        record = _mapping(raw_record, location="filtering word")
        word = _word_from_record(record)
        word_id = _string(record.get("id"), location="filtering word.id")
        words.append(word)
        ids_by_object[id(word)] = word_id

    config = AnkiMinerConfig(
        bypass_optional_filters=True,
        allow_duplicate_cards=allow_duplicates,
    )
    presenter = _GoldenPresenter()
    processor = EpisodeProcessor(
        config=config,
        subtitle_parser=None,
        word_filter=WordFilterService(config),
        media_extractor=None,
        definition_service=definition_service,
        anki_service=_GoldenAnkiRead(set(normalized)),
        presenter=presenter,
        frequency_service=frequency_service,
    )
    context = _EpisodeContext(
        start_time=0.0,
        video_file_str="golden-video.mkv",
        subtitle_file_str="golden-subtitles.srt",
        episode_name="Golden Episode",
        series_name="Golden Series",
        source_label="Golden Episode",
    )
    survivors = processor._phase2_filter(context, words, None, None)
    return {
        "allow_duplicate_cards": allow_duplicates,
        "normalized_existing_first_fields": [
            {"raw": raw, "normalized": normalized_value}
            for raw, normalized_value in zip(raw_fields, normalized, strict=True)
        ],
        "survivor_ids": [ids_by_object[id(word)] for word in survivors],
        "survivors": [
            {
                "id": ids_by_object[id(word)],
                "mined_form": word.mined_form,
                "frequency_sources": [list(source) for source in word.frequency_sources],
                "frequency_rank": word.frequency_rank,
                "frequency_harmonic_rank": word.frequency_harmonic_rank,
            }
            for word in survivors
        ],
        "candidate_words_found": context.candidate_words_found,
        "new_words_found": context.new_words_found,
        "comprehension_percentage": context.comprehension_percentage,
        "events": presenter.events,
    }


def _derive_deinflection(cases: Sequence[Any]) -> list[dict[str, Any]]:
    from anki_miner.services.deinflection import get_japanese_deinflector

    deinflector = get_japanese_deinflector()
    output: list[dict[str, Any]] = []
    for raw_case in cases:
        case = _mapping(raw_case, location="deinflection case")
        source = _string(case.get("source"), location="deinflection source")
        target = _string(case.get("target"), location="deinflection target")
        matches = [result for result in deinflector.transform(source) if result.text == target]
        matches.sort(key=lambda result: (len(result.trace), result.trace, result.conditions))
        if not matches:
            raise v1.GoldenExportError(f"deinflection case {case.get('id')!r} did not reach {target!r}")
        output.append(
            {
                "id": _string(case.get("id"), location="deinflection id"),
                "input": {"source": source, "target": target},
                "output": [
                    {
                        "text": result.text,
                        "conditions": result.conditions,
                        "trace_surface_first": [
                            {
                                "transform_id": frame[0],
                                "rule_index": frame[1],
                                "source_text": frame[2],
                            }
                            for frame in result.trace
                        ],
                        "inflection_rules_attachment_order": [frame[0] for frame in reversed(result.trace)],
                    }
                    for result in matches
                ],
            }
        )
    return output


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _build_dictionary_zip(path: Path, provider: Mapping[str, Any]) -> None:
    entries = _list(provider.get("entries"), location="dictionary provider.entries")
    term_bank: list[list[Any]] = []
    has_media = False
    for raw_entry in entries:
        entry = _mapping(raw_entry, location="dictionary entry")
        glossary = _list(entry.get("glossary"), location="dictionary entry.glossary")
        has_media = has_media or "images/wager.png" in json.dumps(glossary, ensure_ascii=False)
        term_bank.append(
            [
                _string(entry.get("term"), location="dictionary entry.term"),
                _string(entry.get("reading"), location="dictionary entry.reading"),
                str(entry.get("definition_tags", "")),
                str(entry.get("rules", "")),
                int(entry.get("score", 0)),
                glossary,
                int(entry["sequence"]) if entry.get("sequence") is not None else None,
                str(entry.get("term_tags", "")),
            ]
        )
    index = {
        "title": _string(provider.get("display_name"), location="dictionary provider.display_name"),
        "revision": "golden-v2",
        "format": 3,
        "sequenced": True,
    }
    tag_bank = [
        ["v1", "partOfSpeech", -3, "Ichidan verb", 1],
        ["n", "partOfSpeech", -3, "noun", 1],
        ["common", "frequency", 0, "common term", 1],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        _zip_write(zf, "index.json", json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        _zip_write(
            zf,
            "term_bank_1.json",
            json.dumps(term_bank, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        _zip_write(
            zf,
            "tag_bank_1.json",
            json.dumps(tag_bank, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        if has_media:
            _zip_write(zf, "images/wager.png", _PNG_1X1)


def _dictionary_resolution(
    service: Any, providers: Sequence[Any], query: Mapping[str, Any], output: str | None
) -> dict[str, Any] | None:
    if output is None:
        return None
    term = str(query["term"])
    reading = str(query["reading"])
    for provider in providers:
        hit = provider.lookup_many([(term, reading)]).get(term)
        if hit == output:
            return {"provider": provider.name, "candidate": term, "mode": "exact"}
    orth_base = str(query.get("fallback_orth_base", ""))
    ctype = str(query["fallback_ctype"]) if query.get("fallback_ctype") is not None else None
    for candidate, conditions in service._fallback_candidates(term, orth_base, ctype):
        for provider in providers:
            hit = provider.lookup_fallback(candidate, conditions)
            if hit == output:
                return {
                    "provider": provider.name,
                    "candidate": candidate,
                    "conditions": conditions,
                    "mode": "fallback",
                }
    raise v1.GoldenExportError(f"could not attribute dictionary result for {term!r}")


def _derive_dictionaries(
    root: Path, value: Mapping[str, Any], config: Any
) -> tuple[list[dict[str, Any]], Any, list[Any]]:
    from anki_miner.services.definition_service import DefinitionService
    from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
    from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

    provider_specs = _list(value.get("providers"), location="dictionaries.providers")
    providers: list[Any] = []
    entry_identity: list[dict[str, Any]] = []
    for raw_provider in provider_specs:
        provider_spec = _mapping(raw_provider, location="dictionary provider")
        dict_id = _string(provider_spec.get("dict_id"), location="dictionary provider.dict_id")
        display_name = _string(provider_spec.get("display_name"), location="dictionary provider.display_name")
        source_zip = root / f"{dict_id}.zip"
        _build_dictionary_zip(source_zip, provider_spec)
        imported = import_yomitan_zip(source_zip, config.dicts_root, dict_id=dict_id)
        db_path = config.dicts_root / imported.dict_id / "index.sqlite"
        provider = IndexedDictProvider(imported.dict_id, db_path, display_name=display_name)
        if not provider.load():
            raise v1.GoldenExportError(f"could not load synthetic dictionary {dict_id}")
        providers.append(provider)
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                "SELECT id, term, reading, sequence, score, rules FROM entries ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        entry_identity.extend(
            {
                "provider": display_name,
                "row_id": row[0],
                "term": row[1],
                "reading": row[2],
                "sequence": row[3],
                "score": row[4],
                "rules": row[5],
            }
            for row in rows
        )

    service = DefinitionService(config, providers)
    raw_queries = _list(value.get("queries"), location="dictionaries.queries")
    queries = [_mapping(item, location="dictionary query") for item in raw_queries]
    pairs = [(str(query["term"]), str(query["reading"])) for query in queries]
    fallback_context = {
        str(query["term"]): (
            str(query.get("fallback_orth_base", "")),
            str(query["fallback_ctype"]) if query.get("fallback_ctype") is not None else None,
        )
        for query in queries
        if "fallback_orth_base" in query
    }
    first_hits = service.get_definitions_batch(pairs, fallback_context=fallback_context)
    glossaries = service.get_glossaries_batch(pairs)
    output: list[dict[str, Any]] = []
    for query, first_hit, glossary in zip(queries, first_hits, glossaries, strict=True):
        term = str(query["term"])
        offline_hits = service.lookup_all_offline(term)
        output.append(
            {
                "id": _string(query.get("id"), location="dictionary query.id"),
                "input": {
                    "term": term,
                    "reading": str(query["reading"]),
                    "fallback_orth_base": query.get("fallback_orth_base"),
                    "fallback_ctype": query.get("fallback_ctype"),
                },
                "output": {
                    "first_hit": first_hit,
                    "first_hit_resolution": _dictionary_resolution(service, providers, query, first_hit),
                    "glossary": glossary,
                    "offline_hits": [
                        {"provider": provider_name, "html": rendered} for provider_name, rendered in offline_hits
                    ],
                },
            }
        )
    return [{"entry_identity": entry_identity, "queries": output}], service, providers


def _derive_frequency(root: Path, value: Mapping[str, Any]) -> tuple[list[dict[str, Any]], Any, list[Any]]:
    from anki_miner.services.frequency import storage
    from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService, harmonic_rank, min_rank
    from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider

    providers: list[Any] = []
    for raw_provider in _list(value.get("providers"), location="frequency.providers"):
        spec = _mapping(raw_provider, location="frequency provider")
        source_id = _string(spec.get("source_id"), location="frequency source_id")
        display_name = _string(spec.get("display_name"), location="frequency display_name")
        rows: list[tuple[str, str | None, int, str | None]] = []
        for raw_row in _list(spec.get("rows"), location="frequency rows"):
            row = _list(raw_row, location="frequency row")
            if len(row) != 4:
                raise v1.GoldenExportError("frequency rows must have four columns")
            rows.append((str(row[0]), str(row[1]) if row[1] is not None else None, int(row[2]), row[3]))
        db_path = root / source_id / "index.sqlite"
        storage.build_index(
            db_path,
            rows,
            {
                "schema_version": str(storage.SCHEMA_VERSION),
                "source_name": display_name,
                "source_revision": "golden-v2",
                "format": "golden",
                "entry_count": str(len(rows)),
                "is_categorical": "0",
            },
        )
        provider = IndexedFreqProvider(source_id, db_path, display_name)
        if not provider.load():
            raise v1.GoldenExportError(f"could not load synthetic frequency source {source_id}")
        providers.append(provider)
    service = MultiFrequencyService(providers)
    output: list[dict[str, Any]] = []
    for raw_query in _list(value.get("queries"), location="frequency.queries"):
        query = _mapping(raw_query, location="frequency query")
        term = _string(query.get("term"), location="frequency term")
        reading = _string(query.get("reading"), location="frequency reading")
        sources = service.lookup_all(term, reading)
        output.append(
            {
                "id": _string(query.get("id"), location="frequency id"),
                "input": {"term": term, "reading": reading},
                "output": {
                    "sources": [list(source) for source in sources],
                    "minimum_rank": min_rank(sources),
                    "harmonic_rank": harmonic_rank(sources),
                },
            }
        )
    return output, service, providers


def _derive_pitch(root: Path, value: Mapping[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    from anki_miner.services.pitch_accent.render import render_pitch_graph_field, render_pitch_text_field
    from anki_miner.services.pitch_accent_service import PitchAccentService

    root.mkdir(parents=True, exist_ok=True)
    csv_path = root / "pitch.csv"
    rows = _list(value.get("rows"), location="pitch.rows")
    lines: list[str] = []
    for raw_row in rows:
        row = _list(raw_row, location="pitch row")
        if len(row) != 5 or any(not isinstance(item, str) for item in row):
            raise v1.GoldenExportError("pitch rows must contain five strings")
        lines.append(",".join(row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    service = PitchAccentService(csv_path)
    if not service.load():
        raise v1.GoldenExportError("could not load synthetic pitch fixture")
    output: list[dict[str, Any]] = []
    for raw_query in _list(value.get("queries"), location="pitch.queries"):
        query = _mapping(raw_query, location="pitch query")
        term = _string(query.get("term"), location="pitch term")
        reading = _string(query.get("reading"), location="pitch reading")
        pos = _string(query.get("pos"), location="pitch pos")
        pattern, category = service.lookup_detailed(term, reading, pos)
        entry = service.lookup_entry(term, reading)
        output.append(
            {
                "id": _string(query.get("id"), location="pitch id"),
                "input": {"term": term, "reading": reading, "pos": pos},
                "output": {
                    "pattern": pattern,
                    "category": category,
                    "nasal_morae": list(entry.nasal) if entry else [],
                    "devoiced_morae": list(entry.devoice) if entry else [],
                    "graph_html": render_pitch_graph_field(pattern, reading) if pattern else "",
                    "text_html": (
                        render_pitch_text_field(
                            pattern,
                            reading,
                            entry.nasal if entry else (),
                            entry.devoice if entry else (),
                        )
                        if pattern
                        else ""
                    ),
                },
            }
        )
    return output, service


def _content_addressed_name(filename: str, raw: bytes) -> str:
    path = Path(filename)
    return f"{path.stem}_{hashlib.sha1(raw).hexdigest()[:12]}{path.suffix}"


def _provider_actual_name(requested: str) -> str:
    path = Path(requested)
    preferred = (path.stem if path.suffix else requested).replace(" ", "_")
    if len(preferred) < 2:
        preferred = f"{preferred}_"
    return f"{preferred}_provider{path.suffix}"


def _dictionary_actual_name(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    suffix = Path(source).suffix or ".bin"
    return f"anki_miner_dict_{digest}_provider{suffix}"


def _rewrite_dictionary_html(value: str, actual_names: Mapping[str, str]) -> str:
    from anki_miner.services.anki_media_store import _DICT_MEDIA_IMG_RE, _IMG_SRC_RE

    def rewrite_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _IMG_SRC_RE.search(tag)
        if src_match is None:
            return tag
        original = html.unescape(src_match.group(1))
        actual = actual_names.get(original)
        if actual is None or actual == original:
            return tag
        escaped = html.escape(actual, quote=True)
        return tag[: src_match.start(1)] + escaped + tag[src_match.end(1) :]

    rewritten = _DICT_MEDIA_IMG_RE.sub(rewrite_tag, value)
    # Yomitan's image envelope repeats the same source in the sibling mask
    # layer used for monochrome artwork.  Provider-returned names must update
    # that CSS URL as well as the marked img src or the background layer would
    # still request a file which Android never stored.
    for original, actual in actual_names.items():
        original_url = f"url(&quot;{html.escape(original, quote=True)}&quot;)"
        actual_url = f"url(&quot;{html.escape(actual, quote=True)}&quot;)"
        rewritten = rewritten.replace(original_url, actual_url)
    return rewritten


def _marked_dictionary_sources(value: str) -> list[str]:
    sources: list[str] = []
    for tag in _MARKED_DICTIONARY_IMG_RE.findall(value):
        match = _IMG_SRC_RE.search(tag)
        if match is not None:
            sources.append(html.unescape(match.group(1)))
    return sources


def _derive_card(
    root: Path,
    value: Mapping[str, Any],
    identity: Mapping[str, Any],
    filtering_input: Mapping[str, Any],
    dictionary_section: Sequence[dict[str, Any]],
    frequency_service: Any,
    pitch_service: Any,
) -> list[dict[str, Any]]:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.models import CardPayload, MediaData
    from anki_miner.services.anki_media_store import _extract_dict_media_srcs
    from anki_miner.services.anki_note_builder import _strip_for_dedup, build_note
    from anki_miner.services.frequency.multi_frequency_service import harmonic_rank
    from anki_miner.services.frequency.render import render_frequency_html
    from anki_miner.services.pitch_accent.render import render_pitch_graph_field, render_pitch_text_field

    root.mkdir(parents=True, exist_ok=True)
    word_id = _string(value.get("word_id"), location="card.word_id")
    word_record = next(
        (
            _mapping(item, location="filtering word")
            for item in _list(filtering_input.get("words"), location="filtering.words")
            if isinstance(item, dict) and item.get("id") == word_id
        ),
        None,
    )
    if word_record is None:
        raise v1.GoldenExportError(f"card.word_id does not name a filtering word: {word_id}")
    word = _word_from_record(word_record)
    word.sentence_bolded = _string(value.get("sentence_bolded"), location="card.sentence_bolded")
    word.sentence_furigana = _string(value.get("sentence_furigana"), location="card.sentence_furigana")
    word.sentence_furigana_bolded = _string(
        value.get("sentence_furigana_bolded"), location="card.sentence_furigana_bolded"
    )
    word.sentence_reading = _string(value.get("sentence_reading"), location="card.sentence_reading")
    word.expression_furigana = "賭[か]ける"

    frequency_sources = frequency_service.lookup_all(word.mined_form, word.expression_reading)
    word.frequency_sources = frequency_sources
    word.frequency_rank = min((row[1] for row in frequency_sources), default=None)
    word.frequency_harmonic_rank = harmonic_rank(frequency_sources)

    pattern, category = pitch_service.lookup_detailed(word.lemma, word.lemma_reading, word.pos)
    pitch_entry = pitch_service.lookup_entry(word.lemma, word.lemma_reading)
    if pattern is None:
        raise v1.GoldenExportError("card pitch input did not resolve")

    query_records = dictionary_section[0]["queries"]
    dictionary_record = next(record for record in query_records if record["id"] == "first-hit-and-glossary-order")
    definition = dictionary_record["output"]["first_hit"]
    glossary = dictionary_record["output"]["glossary"]
    if not isinstance(definition, str) or not isinstance(glossary, str):
        raise v1.GoldenExportError("card dictionary input did not resolve")
    dictionary_sources = sorted(set(_extract_dict_media_srcs(definition)) | set(_extract_dict_media_srcs(glossary)))
    if len(dictionary_sources) != 1:
        raise v1.GoldenExportError("v2 card requires exactly one marked dictionary image")
    dictionary_source = dictionary_sources[0]
    dictionary_actual = _dictionary_actual_name(dictionary_source)
    dictionary_rewrites = {dictionary_source: dictionary_actual}
    rewritten_definition = _rewrite_dictionary_html(definition, dictionary_rewrites)
    rewritten_glossary = _rewrite_dictionary_html(glossary, dictionary_rewrites)

    def direct_asset(config_key: str, identity_key: str, media_kind: str) -> dict[str, Any]:
        spec = _mapping(value.get(config_key), location=f"card.{config_key}")
        original = _string(spec.get("original_filename"), location=f"card.{config_key}.original_filename")
        raw = _string(spec.get("content_utf8"), location=f"card.{config_key}.content_utf8").encode("utf-8")
        path = root / original
        path.write_bytes(raw)
        requested = _content_addressed_name(original, raw)
        return {
            "asset_id": identity[identity_key],
            "source_fixture": config_key,
            "purpose": "card",
            "media_kind": media_kind,
            "source_path": path,
            "original_filename": original,
            "requested_filename": requested,
            "actual_filename": _provider_actual_name(requested),
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    audio = direct_asset("card_audio", "card_audio_asset_id", "audio")
    image = direct_asset("card_image", "card_image_asset_id", "image")
    media = MediaData(
        screenshot_path=image["source_path"],
        audio_path=audio["source_path"],
        screenshot_filename=image["actual_filename"],
        audio_filename=audio["actual_filename"],
    )

    field_mapping = _mapping(value.get("field_mapping"), location="card.field_mapping")
    config_args = {
        "anki_deck_name": _string(value.get("deck_name"), location="card.deck_name"),
        "anki_note_type": _string(value.get("model_name"), location="card.model_name"),
        "anki_fields": dict(field_mapping),
        "anki_tags": _string(value.get("tags"), location="card.tags"),
        "card_type": _string(value.get("card_type"), location="card.card_type"),
        "bold_target_in_sentence": True,
    }
    pitch_graph = render_pitch_graph_field(pattern, word.lemma_reading)
    pitch_text = render_pitch_text_field(
        pattern,
        word.lemma_reading,
        pitch_entry.nasal if pitch_entry else (),
        pitch_entry.devoice if pitch_entry else (),
    )
    extra_fields = {
        "glossary": rewritten_glossary,
        "pitch_position": pattern,
        "pitch_category": category or "",
        "pitch_graph": pitch_graph,
        "pitch_text": pitch_text,
        "frequency": render_frequency_html(frequency_sources),
        "frequency_sort": str(word.frequency_harmonic_rank),
        "source": _string(value.get("source"), location="card.source"),
    }
    payload = CardPayload(word=word, media=media, definition=rewritten_definition, extra_fields=extra_fields)
    stored_files = {str(audio["actual_filename"]), str(image["actual_filename"])}

    normal_config = AnkiMinerConfig(**config_args, allow_duplicate_cards=False)
    duplicate_config = AnkiMinerConfig(**config_args, allow_duplicate_cards=True)
    normal_note = build_note(payload, normal_config, stored_files).note
    duplicate_note = build_note(payload, duplicate_config, stored_files).note
    field_order = list(normal_note["fields"])
    fields = [{"name": name, "value": normal_note["fields"][name]} for name in field_order]
    first_field_name = str(field_mapping["word"])
    first_field = str(normal_note["fields"][first_field_name])
    duplicate_key = _strip_for_dedup(first_field)
    media_assets = [
        {
            key: asset[key]
            for key in (
                "asset_id",
                "source_fixture",
                "purpose",
                "media_kind",
                "original_filename",
                "requested_filename",
                "actual_filename",
                "size_bytes",
                "sha256",
            )
        }
        for asset in (audio, image)
    ]
    media_assets.append(
        {
            "asset_id": identity["dictionary_image_asset_id"],
            "source_fixture": "dictionary_image",
            "purpose": "dictionary",
            "media_kind": "image",
            "original_filename": dictionary_source,
            "requested_filename": dictionary_source,
            "actual_filename": dictionary_actual,
            "size_bytes": len(_PNG_1X1),
            "sha256": hashlib.sha256(_PNG_1X1).hexdigest(),
        }
    )
    bindings = [{"assetId": asset["asset_id"], "actualFilename": asset["actual_filename"]} for asset in media_assets]
    wire_note = {
        "clientNoteId": identity["client_note_id"],
        "fieldOrder": field_order,
        "fields": fields,
        "tags": normal_note["tags"],
        "mediaBindings": bindings,
        "duplicateCandidate": {
            "key": duplicate_key,
            "firstField": first_field,
            "occurrence": 0,
        },
    }
    request = {
        "runId": identity["run_id"],
        "requestId": identity["request_id"],
        "deckName": normal_note["deckName"],
        "modelName": normal_note["modelName"],
        "firstFieldName": first_field_name,
        "notes": [wire_note],
    }
    result = {
        "runId": identity["run_id"],
        "requestId": identity["request_id"],
        "results": [
            {
                "clientNoteId": identity["client_note_id"],
                "status": "created",
                "noteId": identity["note_id"],
            }
        ],
    }
    return [
        {
            "id": "desktop-note-and-android-transport",
            "input": {
                "word_id": word_id,
                "unrewritten_definition": definition,
                "unrewritten_glossary": glossary,
                "media_store_request": {
                    "runId": identity["run_id"],
                    "requestId": identity["request_id"],
                    "assets": media_assets,
                },
                "media_store_result": {
                    "runId": identity["run_id"],
                    "requestId": identity["request_id"],
                    "results": [
                        {
                            "assetId": asset["asset_id"],
                            "status": "stored",
                            "actualFilename": asset["actual_filename"],
                        }
                        for asset in media_assets
                    ],
                },
            },
            "output": {
                "rewritten_definition": rewritten_definition,
                "rewritten_glossary": rewritten_glossary,
                "route": {
                    "deck_name": normal_note["deckName"],
                    "model_name": normal_note["modelName"],
                    "tags": normal_note["tags"],
                    "card_type": value["card_type"],
                    "card_type_field": normal_config.card_type_marker_fields[normal_config.card_type],
                },
                "normal_duplicate_options": normal_note.get("options"),
                "allow_duplicate_options": duplicate_note.get("options"),
                "create_notes_request": request,
                "create_notes_result": result,
            },
        }
    ]


def _validate_output(result: Mapping[str, Any]) -> None:
    if result.get("schema_version") != SCHEMA_VERSION:
        raise v1.GoldenExportError("v2 output schema version drifted")
    sections = _mapping(result.get("cases"), location="v2 output cases")
    if tuple(sections) != CASE_SECTIONS:
        raise v1.GoldenExportError("v2 output section order drifted")
    for name in CASE_SECTIONS:
        if not isinstance(sections[name], list) or not sections[name]:
            raise v1.GoldenExportError(f"v2 section is empty: {name}")
    card = sections["cards"][0]
    card_input = card["input"]
    card_output = card["output"]
    request = card_output["create_notes_request"]
    response = card_output["create_notes_result"]
    for key in ("runId", "requestId"):
        if request[key] != response[key] or request[key] != card_input["media_store_request"][key]:
            raise v1.GoldenExportError(f"v2 transport did not preserve {key}")
    client_id = request["notes"][0]["clientNoteId"]
    if response["results"][0]["clientNoteId"] != client_id:
        raise v1.GoldenExportError("v2 transport did not preserve clientNoteId")
    field_names = request["notes"][0]["fieldOrder"]
    listed_names = [field["name"] for field in request["notes"][0]["fields"]]
    if field_names != listed_names or len(set(field_names)) != len(field_names):
        raise v1.GoldenExportError("v2 card field order is not explicit and unique")
    rendered_fields = "\n".join(field["value"] for field in request["notes"][0]["fields"])
    for asset in card_input["media_store_request"]["assets"]:
        if asset["actual_filename"] not in rendered_fields and asset["purpose"] == "card":
            raise v1.GoldenExportError("v2 card does not reference a returned provider filename")
        if asset["purpose"] == "card" and asset["requested_filename"] in rendered_fields:
            raise v1.GoldenExportError("v2 card retained a pre-provider card-media filename")
    dictionary_assets = [
        asset for asset in card_input["media_store_request"]["assets"] if asset["purpose"] == "dictionary"
    ]
    rewritten_sources = set(_marked_dictionary_sources(card_output["rewritten_definition"]))
    rewritten_dictionary_html = card_output["rewritten_definition"] + card_output["rewritten_glossary"]
    for asset in dictionary_assets:
        if asset["actual_filename"] not in rewritten_sources:
            raise v1.GoldenExportError("v2 dictionary HTML did not use the returned provider filename")
        if asset["requested_filename"] in rewritten_sources:
            raise v1.GoldenExportError("v2 dictionary HTML retained its logical media filename")
        if asset["actual_filename"] not in rewritten_dictionary_html:
            raise v1.GoldenExportError("v2 dictionary HTML lost its provider media binding")
        if asset["requested_filename"] in rewritten_dictionary_html:
            raise v1.GoldenExportError("v2 dictionary HTML retained a pre-provider media reference")


def build_goldens_v2(
    *,
    engine_root: Path,
    tokenizer_corpus_path: Path,
    contract_input_path: Path,
    dicdir: Path | None,
    assets: Mapping[str, Path],
) -> dict[str, Any]:
    v1._reject_preloaded_engine_modules()
    engine_root = v1._normalise_input_path(engine_root, label="--engine-root")
    tokenizer_corpus_path = v1._normalise_input_path(tokenizer_corpus_path, label="--corpus")
    contract_input_path = v1._normalise_input_path(contract_input_path, label="--v2-input")
    schema_path = v1._normalise_input_path(
        Path(__file__).resolve().parents[1] / "tests/fixtures/goldens/engine-v2.schema.json",
        label="v2 schema",
    )
    if dicdir is None:
        raise v1.GoldenExportError("--dicdir is required for v2")
    resolved_dicdir = v1._normalise_input_path(dicdir, label="--dicdir")
    unidic_tree = verify_unidic_tree(resolved_dicdir)
    unidic_record = unidic_resource_record(unidic_tree)

    engine_package = engine_root / "anki_miner"
    if not (engine_package / "__init__.py").is_file():
        raise v1.GoldenExportError(f"--engine-root does not contain anki_miner: {engine_root}")
    revision = v1._engine_revision(engine_root)
    if revision != PINNED_ENGINE_REVISION:
        raise v1.GoldenExportError(
            f"v2 must be derived from Android engine.lock {PINNED_ENGINE_REVISION}, got {revision}"
        )
    engine_tree_sha256 = v1._sha256_tree(engine_package)
    tokenizer_corpus = v1._load_corpus(tokenizer_corpus_path)
    contract_input = _load_contract_input(contract_input_path)

    effective_assets: dict[str, Path] = {}
    for name, path in assets.items():
        if v1.ASSET_NAME_PATTERN.fullmatch(name) is None:
            raise v1.GoldenExportError(f"asset name must be a stable lowercase identifier: {name!r}")
        effective_assets[name] = v1._normalise_input_path(path, label=f"asset {name!r}")
    asset_hashes = {name: v1._sha256_path(path) for name, path in sorted(effective_assets.items())}
    data: dict[str, Any] = {
        "tokenizer_corpus_sha256": v1._sha256_file(tokenizer_corpus_path),
        "contract_input_sha256": v1._sha256_file(contract_input_path),
        "schema_sha256": v1._sha256_file(schema_path),
        "assets_sha256": asset_hashes,
        "unidic": unidic_record,
    }
    data["sha256"] = v1._sha256_bytes(v1._canonical_json_bytes(data))

    previous_home = os.environ.get("ANKI_MINER_HOME")
    previous_sys_path = list(sys.path)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    providers_to_close: list[Any] = []
    frequency_providers_to_close: list[Any] = []
    with tempfile.TemporaryDirectory(prefix="anki-miner-goldens-v2-") as isolated_home:
        os.environ["ANKI_MINER_HOME"] = isolated_home
        sys.dont_write_bytecode = True
        try:
            runtime = v1._runtime_provenance(engine_root, RUNTIME_DISTRIBUTIONS)
            sys.path.insert(0, str(engine_root))
            tagger = v1._make_tagger(resolved_dicdir)
            v1._install_shared_tagger(tagger)
            tokenization = v1._tokenization_cases(tokenizer_corpus, tagger)
            morphology, compounds = v1._morphology_and_compound_cases(tokenizer_corpus)

            from anki_miner.config import AnkiMinerConfig

            home = Path(isolated_home)
            config = AnkiMinerConfig(
                dicts_root=home / "dicts",
                freqs_root=home / "freqs",
                pitch_accent_path=home / "pitch.csv",
            )
            dictionary_cases, definition_service, providers_to_close = _derive_dictionaries(
                home / "dictionary-build",
                _mapping(contract_input["dictionaries"], location="dictionaries"),
                config,
            )
            frequency_cases, frequency_service, frequency_providers_to_close = _derive_frequency(
                home / "frequency-build",
                _mapping(contract_input["frequency"], location="frequency"),
            )
            pitch_cases, pitch_service = _derive_pitch(
                home / "pitch-build",
                _mapping(contract_input["pitch"], location="pitch"),
            )
            filtering_input = _mapping(contract_input["filtering"], location="filtering")
            filtering_cases = [
                {
                    "id": "phase2-known-and-within-run-duplicates",
                    "input": filtering_input,
                    "output": {
                        "normal": _phase2_filter_run(
                            filtering_input,
                            frequency_service=frequency_service,
                            definition_service=definition_service,
                            allow_duplicates=False,
                        ),
                        "allow_duplicates": _phase2_filter_run(
                            filtering_input,
                            frequency_service=frequency_service,
                            definition_service=definition_service,
                            allow_duplicates=True,
                        ),
                    },
                }
            ]
            deinflection_cases = _derive_deinflection(_list(contract_input["deinflection"], location="deinflection"))
            card_cases = _derive_card(
                home / "card-build",
                _mapping(contract_input["card"], location="card"),
                _mapping(contract_input["identity"], location="identity"),
                filtering_input,
                dictionary_cases,
                frequency_service,
                pitch_service,
            )
            v1._assert_engine_module_origins(engine_package)
        finally:
            for provider in reversed(frequency_providers_to_close):
                provider.close()
            for provider in reversed(providers_to_close):
                provider.close()
            v1._remove_engine_modules()
            sys.path[:] = previous_sys_path
            sys.dont_write_bytecode = previous_dont_write_bytecode
            if previous_home is None:
                os.environ.pop("ANKI_MINER_HOME", None)
            else:
                os.environ["ANKI_MINER_HOME"] = previous_home

    if v1._engine_revision(engine_root) != revision or v1._sha256_tree(engine_package) != engine_tree_sha256:
        raise v1.GoldenExportError("engine checkout changed while v2 goldens were being derived")

    scripts_root = Path(__file__).resolve().parent
    tool_files = {
        "dump_engine_goldens.py": scripts_root / "dump_engine_goldens.py",
        "engine_golden_contract_v2.py": scripts_root / "engine_golden_contract_v2.py",
        "prepare_golden_unidic.py": scripts_root / "prepare_golden_unidic.py",
    }
    cases = {
        "tokenization": tokenization,
        "morphology": morphology,
        "filtering": filtering_cases,
        "deinflection": deinflection_cases,
        "compounds": compounds,
        "dictionaries": dictionary_cases,
        "frequency": frequency_cases,
        "pitch": pitch_cases,
        "cards": card_cases,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "engine": {"revision": revision, "tree_sha256": engine_tree_sha256},
            "tool": {
                "name": TOOL_NAME,
                "version": TOOL_VERSION,
                "files_sha256": {name: v1._sha256_file(path) for name, path in sorted(tool_files.items())},
                "sha256": v1._sha256_named_files(tool_files),
            },
            "runtime": runtime,
            "data": data,
        },
        "unidic_feature_fields": list(v1.UNIDIC_FEATURE_FIELDS),
        "section_status": {name: {"state": "implemented"} for name in CASE_SECTIONS},
        "cases": cases,
    }
    _validate_output(result)
    return result

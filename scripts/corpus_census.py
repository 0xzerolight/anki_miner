#!/usr/bin/env python
"""Corpus census: replay the real parser over a subtitle library and rank what looks wrong.

Dev tool. NEVER imported by the app (it lives in ``scripts/``, outside the
``anki_miner`` package). Sibling of ``scripts/junk_replay.py`` and
``scripts/parse_benchmark.py``; this one is for a library too large to read,
where there is no gold labelling and the only way in is to count.

Subcommands
-----------
    build CORPUS --out DIR   replay every .srt/.ass/.ssa into DIR/census.sqlite
    suspects DIR             write the ranked suspect lists to DIR/suspects/
    count DIR WHERE          occurrences and shows a SQL condition selects, per split
    diff DIR_A DIR_B         what changed between two builds (two commits, one corpus)
    probe TEXT...            tokens, mined words and rejects for single lines

What ``build`` replays
----------------------
Per file, ``parse_raw_entries`` (the video path's own lines: songs and signs
skipped, the script gate on, the subtitle cleanup applied) and then
``parse_text_units(..., every_occurrence=True)`` over those lines. That route
gives the fronts ``count_fronts`` counts for the file, with every occurrence's
span, and never fills the per-file token cache ``parse_subtitle_file`` keeps.
The parser is built by ``gui/utils/service_factory._create_subtitle_parser``
with the lookups ``create_services`` passes, over the dictionaries in ``--dicts``.

``anki_miner`` is whatever the interpreter imports, so a build can score another
checkout: run this file with ``PYTHONPATH`` pointing at that worktree. The
checkout's path and commit are stored in the ``meta`` table.

Tables (``occ`` and ``rejected`` are the views to query; both add show, path, split)
---------------------------------------------------------------------------------
    files     one row per subtitle file: show (top folder), split, cue and occurrence totals, error
    agg/occ   mined occurrences per file, grouped by (front, lemma, pos, surface, reading):
              n, n_inrun (occurrences that sit inside a longer kanji or katakana run),
              first_ms / inrun_ms (start time of an example cue)
    rej/rejected  tokens the acceptance check turned away, grouped by (surface, lemma, pos1, pos2, reason)
    residue   characters left in card sentences that are not plain Japanese text, per file
    cue_sample  a stable 1-in-N sample of distinct dev sentences, for a reference tokenizer pass

Every show is ``dev`` or ``heldout`` by a hash of its folder name. Tune on dev;
read heldout only as counts.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import multiprocessing
import random
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import anki_miner
from anki_miner.config.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils.service_factory import _create_subtitle_parser
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.wordset_service import WordsetService
from anki_miner.utils.text_utils import katakana_to_hiragana

SUBTITLE_SUFFIXES = frozenset({".srt", ".ass", ".ssa"})
LENS_TOP = 250
EXAMPLES_PER_SUSPECT = 5

# Tokens whose rejection says nothing about vocabulary: punctuation and spacing.
_UNLISTED_REJECT_POS1 = frozenset({"補助記号", "記号", "空白"})
# Function words: rejected by design, and they would fill the lost-words lens.
_GRAMMAR_POS1 = frozenset({"助詞", "助動詞"})

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files(id INTEGER PRIMARY KEY, show TEXT, path TEXT, split TEXT,
                   cues INTEGER, occurrences INTEGER, error TEXT);
CREATE TABLE agg(file_id INTEGER, front TEXT, lemma TEXT, pos TEXT, surface TEXT, reading TEXT,
                 n INTEGER, n_inrun INTEGER, first_ms INTEGER, inrun_ms INTEGER);
CREATE TABLE rej(file_id INTEGER, surface TEXT, lemma TEXT, pos1 TEXT, pos2 TEXT, reason TEXT,
                 n INTEGER, first_ms INTEGER);
CREATE TABLE residue(file_id INTEGER, key TEXT, n INTEGER, first_ms INTEGER);
CREATE TABLE cue_sample(text TEXT PRIMARY KEY, file_id INTEGER, ms INTEGER);
CREATE VIEW occ AS SELECT a.*, f.show, f.path, f.split FROM agg a JOIN files f ON f.id = a.file_id;
CREATE VIEW rejected AS SELECT r.*, f.show, f.path, f.split FROM rej r JOIN files f ON f.id = r.file_id;
"""
_INDEXES = """
CREATE INDEX agg_front ON agg(front);
CREATE INDEX agg_lemma ON agg(lemma);
CREATE INDEX agg_surface ON agg(surface);
CREATE INDEX rej_lemma ON rej(lemma);
CREATE INDEX residue_key ON residue(key);
"""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser(dicts_root: Path) -> tuple[SubtitleParserService, DefinitionService]:
    """The production-wired Japanese parser over every dictionary in ``dicts_root``.

    Mirrors the fresh-parser branch of ``create_services``: the seven lookups are
    passed when an indexed dictionary is enabled and left out when none is, and
    the name wordsets supply ``name_lookup``. No whitelist, so no ``force_include``.
    """
    base = AnkiMinerConfig()
    registry = DictionaryRegistry(dicts_root)
    registry.load()
    installed = registry.unlisted(replace(base, dictionary_chain=()))
    chain = tuple(ChainEntry(kind="indexed", dict_id=meta.dict_id, enabled=True) for meta in installed)
    config = replace(base, dicts_root=dicts_root, dictionary_chain=chain)
    definitions = DefinitionService(config, providers=registry.build_provider_chain(config), registry=registry)
    lookups: dict[str, Any] = {}
    if chain and definitions.ensure_loaded():
        lookups = {
            "term_lookup": definitions.offline_terms_exist,
            "reading_lookup": definitions.offline_term_readings,
            "kana_attest_lookup": definitions.has_offline_definitions,
            "term_common_lookup": definitions.offline_term_commonness,
            "term_rules_lookup": definitions.offline_deinflection_terms_exist,
            "form_lookup": definitions.offline_term_rows,
        }
    wordsets = WordsetService(enabled_ids=config.excluded_wordsets)
    wordsets.load()
    name_lookup = wordsets.excluded_terms if wordsets.is_available() else None
    return _create_subtitle_parser(config, name_lookup=name_lookup, **lookups), definitions


def hook_rejects(parser: SubtitleParserService, sink: list[tuple[Any, str, str]]) -> None:
    """Record every token the mining pass turns away, with the reason and its line, into ``sink``.

    ``_mine_token`` is the parser's single acceptance check. Only the mining pass
    hands it a ``rejects`` record, so the count loop's identical calls are not
    logged twice. The verdict is passed through untouched.
    """
    inner = parser._mine_token
    rule = parser._inclusion_rule

    def mine_token(word_token, text, tok_start, tok_end, tokens, raw_tokens, *, rejects=None):
        accepted = inner(word_token, text, tok_start, tok_end, tokens, raw_tokens, rejects=rejects)
        if not accepted and rejects is not None:
            reason = rule.rejection(word_token)
            if reason is None:
                reason = "fragment"  # included by the rule, then cut as a run or ellipsis fragment
            elif (how := parser._kana_recovery(word_token)) is not None:
                # Recovered as a kana word, then lost to a longer attested expression, or cut as a
                # fragment (a bare いえ, a っ stem nothing continues, an ellipsis stutter).
                windowed = how != "listed" and parser._rejected_by_lexicalized_window(word_token, tokens)
                reason = "window" if windowed else "fragment"
            sink.append((word_token, reason, text))
        return accepted

    parser._mine_token = mine_token  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Per-file census
# ---------------------------------------------------------------------------


def split_of(show: str) -> str:
    """``heldout`` for one show in five, by a hash of its folder name; else ``dev``."""
    return "heldout" if hashlib.sha1(show.encode("utf-8")).digest()[0] % 5 == 0 else "dev"


def _script(ch: str) -> str:
    if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or ch in "々〆":
        return "kanji"
    if "ァ" <= ch <= "ヺ" or ch == "ー":
        return "katakana"
    return ""


def in_longer_run(sentence: str, start: int, end: int) -> bool:
    """Whether the character just outside ``[start, end)`` continues its kanji or katakana run."""
    if not 0 <= start < end <= len(sentence):
        return False
    first, last = _script(sentence[start]), _script(sentence[end - 1])
    left = _script(sentence[start - 1]) if start > 0 else ""
    right = _script(sentence[end]) if end < len(sentence) else ""
    return bool((first and left == first) or (last and right == last))


_PLAIN_PUNCTUATION = frozenset("、。！？…‥・「」『』ー〜～!?,. 　")


def residue_keys(sentence: str) -> collections.Counter[str]:
    """Characters of a card sentence that are not plain Japanese text, plus two caption shapes."""
    keys: collections.Counter[str] = collections.Counter()
    for ch in sentence:
        if ch in _PLAIN_PUNCTUATION or "ぁ" <= ch <= "ゟ" or _script(ch) or ch.isdigit():
            continue
        if "ｦ" <= ch <= "ﾟ":
            keys["half-width kana"] += 1
        elif (ch.isascii() and ch.isalpha()) or "Ａ" <= ch <= "ｚ":
            keys["latin letter"] += 1
        else:
            keys[ch] += 1
    head = sentence[:14]
    if head[:1] in "(（" and any(c in head[1:] for c in ")）"):
        keys["speaker label"] += 1
    return keys


_worker_parser: SubtitleParserService | None = None
_worker_sink: list[tuple[Any, str, str]] = []
_worker_cue_sample = 0


def _init_worker(dicts_root: str, cue_sample: int) -> None:
    global _worker_parser, _worker_cue_sample
    _worker_parser, _definitions = build_parser(Path(dicts_root))
    hook_rejects(_worker_parser, _worker_sink)
    _worker_cue_sample = cue_sample


def census_file(job: tuple[int, str, bool]) -> dict[str, Any]:
    """Replay one subtitle file; returns its grouped rows (or ``error``)."""
    file_id, path, sample_cues = job
    parser = _worker_parser
    assert parser is not None
    try:
        entries = parser.parse_raw_entries(Path(path), 0.0)
        units = [ReadingUnit(text=text, index=i, location_label="") for i, (_s, _e, text) in enumerate(entries)]
        _worker_sink.clear()
        words, _index, _counts = parser.parse_text_units(units, False, every_occurrence=True)
    except Exception as exc:  # one unreadable file must not take the run down
        return {"id": file_id, "error": f"{type(exc).__name__}: {exc}"[:300]}

    def cue_ms(index: int) -> int:
        return round(entries[index][0] * 1000)

    agg: dict[tuple[str, str, str, str, str], list[int]] = {}
    for word in words:
        ms = cue_ms(int(word.start_time))
        start, end = word.surface_start, word.bold_end
        key = (
            word.mined_form,
            word.lemma,
            word.pos or "",
            word.sentence[start:end],
            katakana_to_hiragana(word.expression_reading),
        )
        row = agg.setdefault(key, [0, 0, ms, -1])
        row[0] += 1
        if in_longer_run(word.sentence, start, end):
            row[1] += 1
            if row[3] < 0:
                row[3] = ms

    # A token carries no cue, so a reject is placed by the line it was read from.
    sentences = [parser.reading_sentence(text) for _s, _e, text in entries]
    cue_of_line: dict[str, int] = {}
    for index, sentence in enumerate(sentences):
        cue_of_line.setdefault(sentence, index)
    rej: dict[tuple[str, str, str, str, str], list[int]] = {}
    for token, reason, line in _worker_sink:
        feature = token.feature
        pos1 = getattr(feature, "pos1", "") or ""
        if pos1 in _UNLISTED_REJECT_POS1:
            continue
        rej_key = (token.surface, parser._extract_lemma(token), pos1, getattr(feature, "pos2", "") or "", reason)
        rej.setdefault(rej_key, [0, cue_ms(cue_of_line[line]) if line in cue_of_line else -1])[0] += 1
    _worker_sink.clear()

    residue: dict[str, list[int]] = {}
    sample: list[tuple[str, int]] = []
    for index, sentence in enumerate(sentences):
        for key_name, n in residue_keys(sentence).items():
            row = residue.setdefault(key_name, [0, cue_ms(index)])
            row[0] += n
        if sample_cues and _worker_cue_sample and sentence:
            digest = hashlib.blake2b(sentence.encode("utf-8"), digest_size=4).digest()
            if int.from_bytes(digest, "big") % _worker_cue_sample == 0:
                sample.append((sentence, cue_ms(index)))

    return {
        "id": file_id,
        "cues": len(entries),
        "occurrences": len(words),
        "agg": [(*key, *row) for key, row in agg.items()],
        "rej": [(*key, *row) for key, row in rej.items()],
        "residue": [(key, *row) for key, row in residue.items()],
        "sample": sample,
    }


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def list_files(corpus: Path, split: str, max_shows: int | None) -> list[tuple[str, str]]:
    """``(show, relative path)`` for every subtitle file, sorted; a show is the top folder."""
    rows: list[tuple[str, str]] = []
    for path in sorted(corpus.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUBTITLE_SUFFIXES:
            rel = path.relative_to(corpus)
            show = rel.parts[0] if len(rel.parts) > 1 else rel.stem
            if split in ("all", split_of(show)):
                rows.append((show, rel.as_posix()))
    if max_shows is not None:
        # The shows with the smallest hashes: the same slice on every run.
        shows = sorted({show for show, _ in rows}, key=lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest())
        keep = set(shows[:max_shows])
        rows = [row for row in rows if row[0] in keep]
    return rows


def _checkout_commit() -> str:
    checkout = Path(anki_miner.__file__).resolve().parents[1]
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True)
    return head.stdout.strip()


def build(
    corpus: Path,
    out: Path,
    dicts_root: Path,
    *,
    jobs: int = 1,
    split: str = "all",
    max_shows: int | None = None,
    cue_sample: int = 0,
) -> dict[str, int]:
    """Replay ``corpus`` into ``out/census.sqlite``; returns the run totals."""
    started = time.monotonic()
    files = list_files(corpus, split, max_shows)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "census.sqlite"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + _SCHEMA)
    con.executemany(
        "INSERT INTO files(id, show, path, split) VALUES (?, ?, ?, ?)",
        [(i, show, rel, split_of(show)) for i, (show, rel) in enumerate(files)],
    )
    jobs_list = [(i, str(corpus / rel), split_of(show) == "dev") for i, (show, rel) in enumerate(files)]

    totals = {"files": len(files), "failed": 0, "empty": 0, "cues": 0, "occurrences": 0}

    def store(result: dict[str, Any]) -> None:
        file_id = result["id"]
        if "error" in result:
            totals["failed"] += 1
            con.execute("UPDATE files SET error = ? WHERE id = ?", (result["error"], file_id))
            return
        totals["cues"] += result["cues"]
        totals["occurrences"] += result["occurrences"]
        totals["empty"] += result["occurrences"] == 0
        con.execute(
            "UPDATE files SET cues = ?, occurrences = ? WHERE id = ?",
            (result["cues"], result["occurrences"], file_id),
        )
        con.executemany("INSERT INTO agg VALUES (?,?,?,?,?,?,?,?,?,?)", [(file_id, *row) for row in result["agg"]])
        con.executemany("INSERT INTO rej VALUES (?,?,?,?,?,?,?,?)", [(file_id, *row) for row in result["rej"]])
        con.executemany("INSERT INTO residue VALUES (?,?,?,?)", [(file_id, *row) for row in result["residue"]])
        con.executemany(
            "INSERT OR IGNORE INTO cue_sample VALUES (?,?,?)", [(text, file_id, ms) for text, ms in result["sample"]]
        )

    def report(done: int) -> None:
        if done % 500 == 0 or done == len(files):
            print(f"  {done}/{len(files)} files, {time.monotonic() - started:.0f}s", file=sys.stderr)

    if jobs <= 1:
        _init_worker(str(dicts_root), cue_sample)
        for done, job in enumerate(jobs_list, start=1):
            store(census_file(job))
            report(done)
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(jobs, initializer=_init_worker, initargs=(str(dicts_root), cue_sample)) as pool:
            for done, result in enumerate(pool.imap_unordered(census_file, jobs_list, chunksize=4), start=1):
                store(result)
                report(done)

    meta = {
        "corpus": str(corpus.resolve()),
        "dicts": str(dicts_root.resolve()),
        "anki_miner": str(Path(anki_miner.__file__).resolve().parent),
        "commit": _checkout_commit(),
        "split": split,
        "max_shows": "" if max_shows is None else str(max_shows),
        "cue_sample": str(cue_sample),
        **{key: str(value) for key, value in totals.items()},
    }
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    con.executescript(_INDEXES)
    con.commit()
    con.close()
    totals["seconds"] = round(time.monotonic() - started)
    return totals


# ---------------------------------------------------------------------------
# Reading a census
# ---------------------------------------------------------------------------


def open_census(directory: Path) -> sqlite3.Connection:
    """The census database of ``directory``, read-only."""
    db_path = directory / "census.sqlite"
    if not db_path.is_file():
        raise SystemExit(f"No census in {directory}; run `build` first.")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _meta(con: sqlite3.Connection) -> dict[str, str]:
    return dict(con.execute("SELECT key, value FROM meta"))


class CueReader:
    """Card sentences by (file, start time), read back from the corpus on demand."""

    def __init__(self, corpus: Path, parser: SubtitleParserService) -> None:
        self._corpus = corpus
        self._parser = parser
        self._file = lru_cache(maxsize=128)(self._load)

    def _load(self, rel: str) -> dict[int, list[str]]:
        cues: dict[int, list[str]] = {}
        try:
            entries = self._parser.parse_raw_entries(self._corpus / rel, 0.0)
        except Exception:
            return cues
        for start, _end, text in entries:
            cues.setdefault(round(start * 1000), []).append(self._parser.reading_sentence(text))
        return cues

    def sentence(self, rel: str, ms: int, containing: str = "") -> str:
        """The sentence of the cue starting at ``ms``; of several, the one that has ``containing``."""
        candidates = self._file(rel).get(ms, [])
        for text in candidates:
            if containing in text:
                return text
        return candidates[0] if candidates else ""


def _examples(
    con: sqlite3.Connection,
    cues: CueReader,
    table: str,
    where: str,
    params: tuple[Any, ...],
    seed: str,
    *,
    ms_column: str = "first_ms",
) -> list[dict[str, Any]]:
    """Up to five example cues for a suspect, each from a different dev show, chosen by ``seed``."""
    rows = con.execute(
        f"SELECT show, path, MIN({ms_column}), surface FROM {table} "
        f"WHERE split = 'dev' AND {ms_column} >= 0 AND {where} GROUP BY show LIMIT 60",
        params,
    ).fetchall()
    chosen = random.Random(seed).sample(rows, min(EXAMPLES_PER_SUSPECT, len(rows)))
    return [
        {"path": path, "ms": ms, "surface": surface, "sentence": cues.sentence(path, ms, surface)}
        for _show, path, ms, surface in sorted(chosen)
    ]


def _chunks(items: list[str], size: int = 2000) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _load_suppressed(path: Path | None) -> set[tuple[str, str]]:
    """``(lens, key)`` pairs from a rulings TSV (columns lens, key, ...); lens ``*`` hides a key everywhere."""
    if path is None:
        return set()
    with path.open(encoding="utf-8", newline="") as fh:
        return {(row[0], row[1]) for row in csv.reader(fh, delimiter="\t") if len(row) >= 2 and row[0] != "lens"}


def _load_frequency(path: Path | None) -> dict[str, int]:
    """A ``term,rank`` reference frequency list; the best rank of a repeated term wins."""
    ranks: dict[str, int] = {}
    if path is None:
        return ranks
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[1].isdigit():
                ranks.setdefault(row[0], int(row[1]))
    return ranks


# ---------------------------------------------------------------------------
# suspects
# ---------------------------------------------------------------------------


def lemma_split_suspects(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """F1: a front mined under several lemmas, or a surface mined as several fronts.

    Ranked by how many shows have the SECOND most common reading of the key:
    one stray tagging is noise, a second lemma across fifty shows is a class.
    """
    suspects: list[dict[str, Any]] = []
    for kind, key_column, variant_column in (
        ("front-lemmas", "front", "lemma"),
        ("surface-fronts", "surface", "front"),
    ):
        grouped: dict[str, list[tuple[str, int, int]]] = collections.defaultdict(list)
        query = (
            f"SELECT {key_column}, {variant_column}, SUM(n), COUNT(DISTINCT show) FROM occ "
            f"WHERE split = 'dev' GROUP BY {key_column}, {variant_column}"
        )
        for key, variant, n, shows in con.execute(query):
            grouped[key].append((variant, n, shows))
        for key, variants in grouped.items():
            if len(variants) < 2:
                continue
            variants.sort(key=lambda v: (-v[2], -v[1], v[0]))
            suspects.append(
                {
                    "lens": "F1",
                    "kind": kind,
                    "key": key,
                    "shows": variants[1][2],
                    "n": variants[1][1],
                    "variants": [{variant_column: v, "n": n, "shows": shows} for v, n, shows in variants[:6]],
                    "_where": (key_column, variant_column),
                }
            )
    return suspects


def fragment_suspects(con: sqlite3.Connection, frequency: dict[str, int]) -> list[dict[str, Any]]:
    """F2: fronts that mostly occur inside a longer kanji or katakana run."""
    query = (
        "SELECT front, SUM(n), SUM(n_inrun), COUNT(DISTINCT show) FROM occ WHERE split = 'dev' "
        "GROUP BY front HAVING SUM(n) >= 5 AND SUM(n_inrun) * 5 >= SUM(n) * 3"
    )
    return [
        {
            "lens": "F2",
            "kind": "fragment",
            "key": front,
            "shows": shows,
            "n": n,
            "in_run_share": round(inrun / n, 3),
            "reference_rank": frequency.get(front),
        }
        for front, n, inrun, shows in con.execute(query)
    ]


def lost_word_suspects(
    con: sqlite3.Connection, definitions: DefinitionService, frequency: dict[str, int]
) -> list[dict[str, Any]]:
    """F3: dictionary words the parser turns away, and mined fronts no dictionary has."""
    suspects: list[dict[str, Any]] = []
    grammar = ",".join("?" * len(_GRAMMAR_POS1))
    rejected = con.execute(
        "SELECT lemma, pos1, pos2, reason, SUM(n), COUNT(DISTINCT show) FROM rejected "
        f"WHERE split = 'dev' AND pos1 NOT IN ({grammar}) GROUP BY lemma, pos1, pos2, reason "
        "HAVING COUNT(DISTINCT show) >= 3",
        tuple(sorted(_GRAMMAR_POS1)),
    ).fetchall()
    attested: dict[str, bool] = {}
    for chunk in _chunks(sorted({row[0] for row in rejected})):
        attested.update(definitions.has_offline_definitions(chunk))
    for lemma, pos1, pos2, reason, n, shows in rejected:
        if attested.get(lemma):
            suspects.append(
                {
                    "lens": "F3",
                    "kind": "rejected",
                    "key": lemma,
                    "shows": shows,
                    "n": n,
                    "pos1": pos1,
                    "pos2": pos2,
                    "reason": reason,
                    "reference_rank": frequency.get(lemma),
                }
            )

    fronts = con.execute(
        "SELECT front, SUM(n), COUNT(DISTINCT show) FROM occ WHERE split = 'dev' "
        "GROUP BY front HAVING COUNT(DISTINCT show) >= 2"
    ).fetchall()
    attested = {}
    for chunk in _chunks(sorted({row[0] for row in fronts})):
        attested.update(definitions.has_offline_definitions(chunk))
    suspects.extend(
        {"lens": "F3", "kind": "unattested-front", "key": front, "shows": shows, "n": n}
        for front, n, shows in fronts
        if not attested.get(front)
    )
    return suspects


def reading_suspects(con: sqlite3.Connection, definitions: DefinitionService) -> list[dict[str, Any]]:
    """T1: a card reading the dictionary does not list for the front."""
    rows = con.execute(
        "SELECT front, reading, SUM(n), COUNT(DISTINCT show) FROM occ "
        "WHERE split = 'dev' AND reading != '' GROUP BY front, reading"
    ).fetchall()
    listed: dict[str, list[str]] = {}
    for chunk in _chunks(sorted({row[0] for row in rows})):
        listed.update(definitions.offline_term_readings(chunk))
    suspects = []
    for front, reading, n, shows in rows:
        known = {katakana_to_hiragana(r) for r in listed.get(front, []) if r}
        if known and reading not in known:
            suspects.append(
                {
                    "lens": "T1",
                    "kind": "reading",
                    "key": front,
                    "shows": shows,
                    "n": n,
                    "reading": reading,
                    "dictionary_readings": sorted(known),
                }
            )
    return suspects


def residue_suspects(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """T2: what is left in card sentences besides Japanese text, and the files that gave nothing."""
    suspects: list[dict[str, Any]] = [
        {"lens": "T2", "kind": "residue", "key": key, "shows": shows, "n": n, "files": files}
        for key, n, files, shows in con.execute(
            "SELECT r.key, SUM(r.n), COUNT(DISTINCT r.file_id), COUNT(DISTINCT f.show) "
            "FROM residue r JOIN files f ON f.id = r.file_id WHERE f.split = 'dev' GROUP BY r.key"
        )
    ]
    for path, error, occurrences in con.execute(
        "SELECT path, error, occurrences FROM files WHERE error IS NOT NULL OR occurrences = 0 ORDER BY path"
    ):
        kind = "file-error" if error else "file-empty"
        suspects.append(
            {"lens": "T2", "kind": kind, "key": path, "shows": 1, "n": occurrences or 0, "error": error or ""}
        )
    return suspects


def _attach_examples(con: sqlite3.Connection, cues: CueReader, suspect: dict[str, Any]) -> None:
    lens, key = suspect["lens"], suspect["key"]
    where = suspect.pop("_where", None)
    if where is not None:
        key_column, variant_column = where
        for variant in suspect["variants"]:
            variant["examples"] = _examples(
                con,
                cues,
                "occ",
                f"{key_column} = ? AND {variant_column} = ?",
                (key, variant[variant_column]),
                f"{lens}:{key}:{variant[variant_column]}",
            )[:3]
    elif suspect["kind"] == "fragment":
        suspect["examples"] = _examples(con, cues, "occ", "front = ?", (key,), f"{lens}:{key}", ms_column="inrun_ms")
    elif suspect["kind"] == "rejected":
        suspect["examples"] = _examples(
            con,
            cues,
            "rejected",
            "lemma = ? AND pos1 = ? AND pos2 = ? AND reason = ?",
            (key, suspect["pos1"], suspect["pos2"], suspect["reason"]),
            f"{lens}:{key}:{suspect['reason']}",
        )
    elif suspect["kind"] in ("unattested-front", "reading"):
        extra = " AND reading = ?" if suspect["kind"] == "reading" else ""
        params = (key, suspect["reading"]) if extra else (key,)
        suspect["examples"] = _examples(con, cues, "occ", f"front = ?{extra}", params, f"{lens}:{key}")
    elif suspect["kind"] == "residue":
        rows = con.execute(
            "SELECT f.show, f.path, MIN(r.first_ms) FROM residue r JOIN files f ON f.id = r.file_id "
            "WHERE f.split = 'dev' AND r.key = ? GROUP BY f.show LIMIT 60",
            (key,),
        ).fetchall()
        chosen = sorted(random.Random(f"{lens}:{key}").sample(rows, min(EXAMPLES_PER_SUSPECT, len(rows))))
        suspect["examples"] = [{"path": path, "ms": ms, "sentence": cues.sentence(path, ms)} for _s, path, ms in chosen]


_LENS_FILES = {
    "F1": ("F1-lemma-split", ("front-lemmas", "surface-fronts")),
    "F2": ("F2-fragments", ("fragment",)),
    "F3": ("F3-lost-words", ("rejected", "unattested-front")),
    "T1": ("T1-readings", ("reading",)),
    "T2": ("T2-residue", ("residue", "file-error", "file-empty")),
}


def _clip(sentence: str, surface: str = "", width: int = 46) -> str:
    """``sentence`` cut to about ``width`` characters around ``surface``, which is bracketed."""
    at = sentence.find(surface) if surface else -1
    if at < 0:
        return sentence[:width]
    start = max(0, min(at - (width - len(surface)) // 2, len(sentence) - width))
    marked = f"{sentence[start:at]}【{surface}】{sentence[at + len(surface) : start + width]}"
    return ("…" if start else "") + marked


def brief(suspect: dict[str, Any]) -> str:
    """One line per suspect for a reader: rank, key, size, what is odd, short example sentences."""
    head = f"{suspect['rank']:>3} {suspect['key']}  shows={suspect['shows']} n={suspect['n']}"
    if "variants" in suspect:
        label = "lemma" if suspect["kind"] == "front-lemmas" else "front"
        parts = []
        for variant in suspect["variants"]:
            examples = variant.get("examples") or []
            shown = f" 〔{_clip(examples[0]['sentence'], examples[0]['surface'])}〕" if examples else ""
            parts.append(f"{variant[label]} {variant['n']}/{variant['shows']}{shown}")
        return f"{head}  " + " | ".join(parts)
    kind = suspect["kind"]
    detail = ""
    if kind == "fragment":
        detail = f" in_run={suspect['in_run_share']} ref={suspect['reference_rank']}"
    elif kind == "rejected":
        detail = f" {suspect['pos1']}/{suspect['pos2']} {suspect['reason']} ref={suspect['reference_rank']}"
    elif kind == "reading":
        detail = f" reads={suspect['reading']} dictionary={'/'.join(suspect['dictionary_readings'])}"
    elif kind == "residue":
        detail = f" files={suspect['files']}"
    elif kind == "file-error":
        detail = f" {suspect['error']}"
    examples = " ‖ ".join(_clip(e["sentence"], e.get("surface", "")) for e in suspect.get("examples", [])[:3])
    return f"{head}{detail}  {examples}".rstrip()


def _ruled(suspect: dict[str, Any], suppressed: set[tuple[str, str]]) -> bool:
    """A rulings row hides a suspect by its key, or a rejected token by ``pos1:X`` / ``pos2:X``."""
    keys = [suspect["key"], *(f"{name}:{suspect[name]}" for name in ("pos1", "pos2") if name in suspect)]
    return any((lens, key) in suppressed for lens in (suspect["lens"], "*") for key in keys)


def write_suspects(
    directory: Path,
    dicts_root: Path,
    *,
    top: int = LENS_TOP,
    suppress: Path | None = None,
    frequency: Path | None = None,
) -> dict[str, int]:
    """Rank every lens over the dev shows into ``directory/suspects``.

    Each lens gets ``<name>.jsonl`` (everything, with example cues) and
    ``<name>.txt`` (one line per suspect, for reading). Ranks restart per kind,
    at most ``top`` of each, by distinct shows and then occurrences.
    """
    con = open_census(directory)
    parser, definitions = build_parser(dicts_root)
    cues = CueReader(Path(_meta(con)["corpus"]), parser)
    suppressed = _load_suppressed(suppress)
    ranks = _load_frequency(frequency)
    by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for suspect in (
        *lemma_split_suspects(con),
        *fragment_suspects(con, ranks),
        *lost_word_suspects(con, definitions, ranks),
        *reading_suspects(con, definitions),
        *residue_suspects(con),
    ):
        if not _ruled(suspect, suppressed):
            by_kind[suspect["kind"]].append(suspect)

    out = directory / "suspects"
    out.mkdir(exist_ok=True)
    written: dict[str, int] = {}
    for lens, (name, kinds) in _LENS_FILES.items():
        written[lens] = 0
        with (
            (out / f"{name}.jsonl").open("w", encoding="utf-8") as full,
            (out / f"{name}.txt").open("w", encoding="utf-8") as short,
        ):
            for kind in kinds:
                ranked = sorted(by_kind[kind], key=lambda s: (-s["shows"], -s["n"], s["key"]))[:top]
                short.write(f"## {kind} ({len(ranked)} of {len(by_kind[kind])})\n")
                for rank, suspect in enumerate(ranked, start=1):
                    _attach_examples(con, cues, suspect)
                    row = {"rank": rank, **suspect}
                    full.write(json.dumps(row, ensure_ascii=False) + "\n")
                    short.write(brief(row) + "\n")
                written[lens] += len(ranked)
    con.close()
    return written


# ---------------------------------------------------------------------------
# count / diff / probe
# ---------------------------------------------------------------------------


def count(directory: Path, where: str, table: str = "occ") -> dict[str, tuple[int, int, int]]:
    """``{split: (occurrences, shows, files)}`` for the rows of ``table`` that ``where`` selects."""
    con = open_census(directory)
    rows = con.execute(
        f"SELECT split, SUM(n), COUNT(DISTINCT show), COUNT(DISTINCT file_id) FROM {table} WHERE {where} GROUP BY split"
    ).fetchall()
    con.close()
    return {split: (n, shows, files) for split, n, shows, files in rows}


def selected_examples(directory: Path, where: str, table: str, limit: int, seed: int) -> list[dict[str, Any]]:
    """``limit`` dev rows that ``where`` selects, drawn with ``seed``, each with its card sentence.

    A random draw, so a class is judged on what it usually looks like and not
    on its most frequent member.
    """
    con = open_census(directory)
    rows = con.execute(
        f"SELECT path, first_ms, surface FROM {table} WHERE split = 'dev' AND first_ms >= 0 AND ({where}) "
        "ORDER BY path, first_ms, surface"
    ).fetchall()
    meta = _meta(con)
    con.close()
    parser, _definitions = build_parser(Path(meta["dicts"]))
    cues = CueReader(Path(meta["corpus"]), parser)
    chosen = sorted(random.Random(seed).sample(rows, min(limit, len(rows))))
    return [
        {"path": path, "ms": ms, "surface": surface, "sentence": cues.sentence(path, ms, surface)}
        for path, ms, surface in chosen
    ]


def _pairs(con: sqlite3.Connection, split: str) -> dict[tuple[str, str], tuple[int, int, str]]:
    where = "" if split == "all" else "WHERE split = ?"
    params = () if split == "all" else (split,)
    return {
        (front, lemma): (n, shows, readings)
        for front, lemma, n, shows, readings in con.execute(
            "SELECT front, lemma, SUM(n), COUNT(DISTINCT show), GROUP_CONCAT(DISTINCT reading) "
            f"FROM occ {where} GROUP BY front, lemma",
            params,
        )
    }


def diff(directory_a: Path, directory_b: Path, *, split: str = "dev", sample: int = 0, seed: int = 0) -> dict[str, Any]:
    """What a parser change did: (front, lemma) pairs added, lost and recounted between two builds.

    ``sample`` adds that many changed (file, front, lemma) rows, drawn with
    ``seed``, each with a sentence to label: the junk share of a change is read
    off a random sample, never off the top of the list.
    """
    con_a, con_b = open_census(directory_a), open_census(directory_b)
    a, b = _pairs(con_a, split), _pairs(con_b, split)
    added = sorted(((b[k][1], b[k][0], *k) for k in b.keys() - a.keys()), reverse=True)
    lost = sorted(((a[k][1], a[k][0], *k) for k in a.keys() - b.keys()), reverse=True)
    kept = a.keys() & b.keys()
    recounted = sorted(
        ((abs(b[k][0] - a[k][0]), a[k][0], b[k][0], *k) for k in kept if a[k][0] != b[k][0]), reverse=True
    )
    reread = sorted((k[0], k[1], a[k][2], b[k][2]) for k in kept if set(a[k][2].split(",")) != set(b[k][2].split(",")))
    result: dict[str, Any] = {"added": added, "lost": lost, "recounted": recounted, "reread": reread, "sample": []}

    if sample:

        def per_file(con: sqlite3.Connection) -> dict[tuple[str, str, str], tuple[int, int, str]]:
            where = "" if split == "all" else "WHERE split = ?"
            params = () if split == "all" else (split,)
            return {
                (path, front, lemma): (n, ms, surface)
                for path, front, lemma, n, ms, surface in con.execute(
                    f"SELECT path, front, lemma, SUM(n), MIN(first_ms), surface FROM occ {where} "
                    "GROUP BY path, front, lemma",
                    params,
                )
            }

        rows_a, rows_b = per_file(con_a), per_file(con_b)
        changed = sorted(k for k in rows_a.keys() | rows_b.keys() if rows_a.get(k, (0,))[0] != rows_b.get(k, (0,))[0])
        parser, _definitions = build_parser(Path(_meta(con_b)["dicts"]))
        cues = CueReader(Path(_meta(con_b)["corpus"]), parser)
        for key in random.Random(seed).sample(changed, min(sample, len(changed))):
            path, front, lemma = key
            before, after = rows_a.get(key, (0, -1, "")), rows_b.get(key, (0, -1, ""))
            n, ms, surface = after if after[0] else before
            result["sample"].append(
                {
                    "path": path,
                    "front": front,
                    "lemma": lemma,
                    "before": before[0],
                    "after": after[0],
                    "surface": surface,
                    "sentence": cues.sentence(path, ms, surface),
                }
            )
    con_a.close()
    con_b.close()
    return result


def print_diff(result: dict[str, Any], limit: int) -> None:
    for title, rows in (("ADDED", result["added"]), ("LOST", result["lost"])):
        print(f"{title} {len(rows)} pairs ({sum(row[1] for row in rows)} occurrences)")
        for shows, n, front, lemma in rows[:limit]:
            print(f"  {shows:5} shows {n:7}  {front}\t{lemma}")
    print(f"RECOUNTED {len(result['recounted'])} pairs")
    for _delta, before, after, front, lemma in result["recounted"][:limit]:
        print(f"  {before:7} -> {after:7}  {front}\t{lemma}")
    print(f"REREAD {len(result['reread'])} pairs")
    for front, lemma, before, after in result["reread"][:limit]:
        print(f"  {front}\t{lemma}\t{before} -> {after}")
    for row in result["sample"]:
        print(json.dumps(row, ensure_ascii=False))


def probe(parser: SubtitleParserService, sink: list[tuple[Any, str, str]], text: str) -> dict[str, Any]:
    """One line as mining sees it: the cleaned sentence, its tokens, the mined words and the rejects.

    ``sink`` is the list ``hook_rejects`` was given for this parser.
    """
    sink.clear()
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="")], False, subtitle_cleanup=True, every_occurrence=True
    )
    sentence = parser.reading_sentence(text, subtitle_cleanup=True)
    merged = parser._build_line_state(sentence, 0.0, 0.0)[2] if sentence else []
    return {
        "sentence": sentence,
        "tokens": [
            "/".join(
                str(part)
                for part in (
                    token.surface,
                    parser._extract_lemma(token),
                    getattr(token.feature, "orthBase", ""),
                    getattr(token.feature, "pos1", ""),
                    getattr(token.feature, "pos2", ""),
                )
            )
            for token in merged
        ],
        "mined": [
            f"{w.sentence[w.surface_start : w.bold_end]}→{w.mined_form} (lemma {w.lemma}, {w.expression_reading})"
            for w in words
        ],
        "rejected": [f"{token.surface} ({getattr(token.feature, 'pos1', '')}): {reason}" for token, reason, _ in sink],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--dicts",
        type=Path,
        default=AnkiMinerConfig().dicts_root,
        help="Dictionary folder the parser's lookups read (default: the configured one).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    p_build = commands.add_parser("build", help="Replay a subtitle library into DIR/census.sqlite.")
    p_build.add_argument("corpus", type=Path)
    p_build.add_argument("--out", type=Path, required=True)
    p_build.add_argument("--jobs", type=int, default=1)
    p_build.add_argument("--split", choices=("all", "dev", "heldout"), default="all")
    p_build.add_argument("--max-shows", type=int, default=None, help="Only the N shows with the smallest name hash.")
    p_build.add_argument("--cue-sample", type=int, default=0, help="Keep 1 in N distinct dev sentences (0 = none).")

    p_suspects = commands.add_parser("suspects", help="Write the ranked suspect lists.")
    p_suspects.add_argument("directory", type=Path)
    p_suspects.add_argument("--top", type=int, default=LENS_TOP)
    p_suspects.add_argument("--suppress", type=Path, default=None, help="TSV of (lens, key) already ruled on.")
    p_suspects.add_argument("--frequency", type=Path, default=None, help="Reference list, CSV of term,rank.")

    p_count = commands.add_parser("count", help="Occurrences and shows a SQL condition selects.")
    p_count.add_argument("directory", type=Path)
    p_count.add_argument("where", help="SQL condition over the table's columns, e.g. \"front = 'はやる'\".")
    p_count.add_argument("--table", choices=("occ", "rejected"), default="occ")
    p_count.add_argument("--examples", type=int, default=0, help="Also print N randomly drawn dev sentences.")
    p_count.add_argument("--seed", type=int, default=0)

    p_diff = commands.add_parser("diff", help="What changed between two builds.")
    p_diff.add_argument("directory_a", type=Path)
    p_diff.add_argument("directory_b", type=Path)
    p_diff.add_argument("--split", choices=("all", "dev", "heldout"), default="dev")
    p_diff.add_argument("--sample", type=int, default=0)
    p_diff.add_argument("--seed", type=int, default=0)
    p_diff.add_argument("--limit", type=int, default=200, help="Rows printed per section.")
    p_diff.add_argument("--exit-code", action="store_true", help="Exit 1 when the two builds differ.")

    p_probe = commands.add_parser("probe", help="Tokens, mined words and rejects for single lines.")
    p_probe.add_argument("text", nargs="+")

    args = parser.parse_args(argv)
    if args.command == "build":
        if not args.corpus.is_dir():
            print(f"Not a directory: {args.corpus}", file=sys.stderr)
            return 1
        totals = build(
            args.corpus,
            args.out,
            args.dicts,
            jobs=args.jobs,
            split=args.split,
            max_shows=args.max_shows,
            cue_sample=args.cue_sample,
        )
        print(json.dumps(totals))
    elif args.command == "suspects":
        print(
            json.dumps(
                write_suspects(
                    args.directory, args.dicts, top=args.top, suppress=args.suppress, frequency=args.frequency
                )
            )
        )
    elif args.command == "count":
        for split, (n, shows, files) in sorted(count(args.directory, args.where, args.table).items()):
            print(f"{split}\t{n} occurrences\t{shows} shows\t{files} files")
        if args.examples:
            for row in selected_examples(args.directory, args.where, args.table, args.examples, args.seed):
                print(json.dumps(row, ensure_ascii=False))
    elif args.command == "diff":
        result = diff(args.directory_a, args.directory_b, split=args.split, sample=args.sample, seed=args.seed)
        print_diff(result, args.limit)
        if args.exit_code and any(result[key] for key in ("added", "lost", "recounted", "reread")):
            return 1
    else:
        service, _definitions = build_parser(args.dicts)
        sink: list[tuple[Any, str, str]] = []
        hook_rejects(service, sink)
        for text in args.text:
            print(json.dumps(probe(service, sink, text), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

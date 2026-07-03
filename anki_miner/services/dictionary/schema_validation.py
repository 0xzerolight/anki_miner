"""Hand-rolled structural validation for Yomitan bank files.

Yomitan validates every ``*_bank_*.json`` entry against a bundled ajv JSON
schema before writing it to its database (``ext/js/dictionary/dictionary-importer.js``
``_getDataBankSchemas`` / ``_validateSchema``, upstream e2ed450). We do not vendor
those schemas or take on an ``fastjsonschema``/``ajv`` dependency (Appendix B);
instead this module encodes the *structural* invariants the importers already
implicitly assume — each bank file is a JSON array, and each usable entry is a
list of a minimum arity whose leading term is present and non-blank.

Entries failing the structural check are *counted and skipped* by the caller so
the count can be surfaced to the user ("N entries skipped (malformed)"), rather
than silently dropped — a malformed zip otherwise imports with drastically
reduced coverage and the user never learns. A bank file whose top-level JSON is
not an array is *wholly unreadable* (no entries can be extracted) and raises.
"""

from __future__ import annotations

from urllib.parse import urlparse

from anki_miner.exceptions import SetupError

# Positional arity the importers assume. Term banks index up to position 7
# (termTags) and require through position 5 (glossary); meta banks are
# ``[term, mode, data]`` triples.
TERM_BANK_MIN_ARITY = 6
META_BANK_MIN_ARITY = 3


def ensure_bank_array(bank: object, filename: str) -> list:
    """Return ``bank`` as a list, or raise if it is not a JSON array.

    A bank file whose top-level JSON is an object, string, or number is wholly
    unreadable — the importer cannot iterate entries out of it — so this is a
    hard error naming the file rather than a per-entry skip.
    """
    if not isinstance(bank, list):
        raise SetupError(
            f"{filename} is not a valid Yomitan bank file "
            f"(expected a JSON array of entries, got {type(bank).__name__})"
        )
    return bank


def _has_valid_term(entry: list) -> bool:
    term = entry[0]
    return term is not None and bool(str(term).strip())


def is_valid_term_bank_entry(entry: object) -> bool:
    """Structural shape ``import_yomitan_zip`` implicitly assumes: a list of at
    least :data:`TERM_BANK_MIN_ARITY` positions whose term (position 0) is
    present and non-blank."""
    return isinstance(entry, list) and len(entry) >= TERM_BANK_MIN_ARITY and _has_valid_term(entry)


def is_valid_meta_bank_entry(entry: object) -> bool:
    """Structural shape both meta-bank importers assume: a list of at least
    :data:`META_BANK_MIN_ARITY` positions whose term (position 0) is present and
    non-blank. Mode/data validity is the importer's concern, not this check's."""
    return isinstance(entry, list) and len(entry) >= META_BANK_MIN_ARITY and _has_valid_term(entry)


def validate_http_url(value: object) -> bool:
    """Return True only for an ``http:``/``https:`` URL string.

    Ported from Yomitan ``DictionaryImporter._validateUrl``
    (ext/js/dictionary/dictionary-importer.js, upstream e2ed450): the update
    ``indexUrl``/``downloadUrl`` fields are dictionary-supplied and are only
    trusted when they parse to an ``http``/``https`` URL — a ``file:``,
    ``ftp:``, or ``javascript:`` scheme is rejected so neither the import
    metadata nor a remote-index override can point the app at a non-web
    resource. Used by the importer (recording update meta) and the updater
    (accepting a remote-declared download URL).
    """
    if not isinstance(value, str):
        return False
    try:
        scheme = urlparse(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


def is_valid_dictionary_index(index: object) -> bool:
    """Structural check for a Yomitan ``index.json`` object.

    Structural subset of Yomitan's ajv ``dictionaryIndex`` schema
    (``ext/js/dictionary/dictionary-importer.js`` ``_readAndValidateIndex`` /
    ``_getSchemas``, upstream e2ed450): a *remote* index fetched during an
    update check is distrusted and re-validated before its ``revision`` is
    compared. We do not vendor the ajv schema (Appendix B); the invariants the
    update comparison actually depends on are that the payload is a JSON object
    carrying a non-blank string ``title`` and a non-blank string ``revision``.
    """
    if not isinstance(index, dict):
        return False
    title = index.get("title")
    revision = index.get("revision")
    if not isinstance(title, str) or not title.strip():
        return False
    return isinstance(revision, str) and bool(revision.strip())

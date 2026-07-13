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

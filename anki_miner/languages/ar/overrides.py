"""Where calima-msa-r13 misfiles a common word: the lexeme repair ``summarise`` applies.

``AR_LEX_REPAIRS`` renames, per ``(lex, pos)``, a lexeme the database files under another word; the
token's lemma and reading are derived from the repaired lexeme. The analyzer port stays as upstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: calima files the verb stems of ``\u0631\u064e\u0623\u064e\u0649`` "see; think; believe" (root ``\u0631.#.#``) under the lexeme
#: ``\u0631\u0627\u0648\u064e\u0646\u0652\u062f`` "rhubarb": every "I saw / you see" card fronted rhubarb. The noun rhubarb keeps its lexeme.
AR_LEX_REPAIRS: Mapping[tuple[str, str], str] = MappingProxyType(
    {("\u0631\u0627\u0648\u064e\u0646\u0652\u062f", "verb"): "\u0631\u064e\u0623\u064e\u0649"}
)

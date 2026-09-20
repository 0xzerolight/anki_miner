"""Feature merging for prefix + stem + suffix analyses (ported, see ``__init__``).

A faithful transcription of upstream ``camel_tools.morphology.utils`` minus the
generator-only helpers. The rewrite rules turn the database's segmented ``diac``
and tokenisation strings (``#`` article markers, ``+`` morpheme joins) into the
surface spellings the analyzer reports.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # database.py imports strip_lex from here
    from .database import MorphologyDB

__all__ = ["merge_features", "strip_lex"]

#: Features concatenated across prefix, stem and suffix.
_JOIN_FEATS = frozenset(["gloss", "bw"])
_CONCAT_FEATS = frozenset(["diac", "pattern", "caphi", "catib6", "ud"])
_CONCAT_FEATS_NONE = frozenset(["d3tok", "d3seg", "atbseg", "d2seg", "d1seg", "d1tok", "d2tok", "atbtok", "bwtok"])
_LOGPROB_FEATS = frozenset(["pos_logprob", "lex_logprob", "pos_lex_logprob"])

#: Tokenisation schemes the sun-letter and fatha-after-alif rules apply to.
_TOK_SCHEMES_1 = frozenset(["d1tok", "d2tok", "atbtok", "d1seg", "d2seg", "d3seg", "atbseg"])
#: Tokenisation schemes only the fatha-after-alif rule applies to.
_TOK_SCHEMES_2 = frozenset(["d3tok", "d3seg"])

_STRIP_LEX_RE = re.compile("_|-")

# Sun letters after the definite article.
_REWRITE_DIAC_RE_1 = re.compile(
    "#\\+*([\u062a\u062b\u062f\u0630\u0631\u0632\u0633\u0634\u0635\u0636\u0637\u0638\u0644\u0646])"
)
# Moon letters after the definite article.
_REWRITE_DIAC_RE_2 = re.compile("#\\+*")
# Fatha after alif.
_REWRITE_DIAC_RE_3 = re.compile("\u0627\\+?\u064e([\u0629\u062a])")
# Hamzat wasl.
_REWRITE_DIAC_RE_4 = re.compile("\u0671")
# Morpheme joins.
_REWRITE_DIAC_RE_5 = re.compile("\\+")
# Repeated shadda.
_REWRITE_DIAC_RE_6 = re.compile("\u0651+")

_REWRITE_CAPHI_RE_1 = re.compile(
    "(l-)\\+(t\\_|th\\_|d\\_|th\\.\\_|r\\_|z\\_|s\\_|sh\\_|s\\.\\_|d\\.\\_|t\\.\\_|dh\\.\\_|l\\_|n\\_|dh\\_)"
)
_REWRITE_CAPHI_RE_2 = re.compile("(\\S)[-]*\\+~")
_REWRITE_CAPHI_RE_3 = re.compile("i\\_y-\\+([^iau]+|$)")
_REWRITE_CAPHI_RE_4 = re.compile("u\\_w-\\+([^iau]+|$)")
_REWRITE_CAPHI_RE_5 = re.compile("([iua])\\+-2_[iua]")
_REWRITE_CAPHI_RE_6 = re.compile("(.+)\\+-2_([iua])")
_REWRITE_CAPHI_RE_7 = re.compile("u\\+w(_+[^ioua])")
_REWRITE_CAPHI_RE_8 = re.compile("p-\\+([iua])")
_REWRITE_CAPHI_RE_9 = re.compile("aa\\+a[_]*")
_REWRITE_CAPHI_RE_10 = re.compile("[\\+-]")
_REWRITE_CAPHI_RE_11 = re.compile("_+")
_REWRITE_CAPHI_RE_12 = re.compile("((^\\_+)|(\\_p?\\_*$))")

# Tanween written before the alif/alif maqsura seat moves after it ("AF" mode).
_NORMALIZE_TANWYN_AF_RE = re.compile("\u0627\u064b")
_NORMALIZE_TANWYN_YF_RE = re.compile("\u0649\u064b")


def strip_lex(lex: str) -> str:
    """Drop the database's ``_N`` / ``-x`` lemma disambiguation suffix."""
    return _STRIP_LEX_RE.split(lex)[0]


def _normalize_tanwyn(word: str) -> str:
    word = _NORMALIZE_TANWYN_AF_RE.sub("\u0627\u064b", word)
    return _NORMALIZE_TANWYN_YF_RE.sub("\u0649\u064b", word)


def _rewrite_diac(word: str) -> str:
    word = _REWRITE_DIAC_RE_1.sub("\\1\u0651", word)
    word = _REWRITE_DIAC_RE_2.sub("", word)
    word = _REWRITE_DIAC_RE_3.sub("\u0627\\1", word)
    word = _REWRITE_DIAC_RE_4.sub("\u0627", word)
    word = _REWRITE_DIAC_RE_5.sub("", word)
    return _REWRITE_DIAC_RE_6.sub("\u0651", word)


def _rewrite_caphi(word: str) -> str:
    word = _REWRITE_CAPHI_RE_1.sub("\\2\\2", word)
    word = _REWRITE_CAPHI_RE_2.sub("\\1_\\1", word)
    word = _REWRITE_CAPHI_RE_3.sub("ii_\\1", word)
    word = _REWRITE_CAPHI_RE_4.sub("uu_\\1", word)
    word = _REWRITE_CAPHI_RE_5.sub("\\1", word)
    word = _REWRITE_CAPHI_RE_6.sub("\\1_\\2", word)
    word = _REWRITE_CAPHI_RE_7.sub("uu\\1", word)
    word = _REWRITE_CAPHI_RE_8.sub("t_\\1", word)
    word = _REWRITE_CAPHI_RE_9.sub("aa_", word)
    word = _REWRITE_CAPHI_RE_10.sub("_", word)
    word = _REWRITE_CAPHI_RE_11.sub("_", word)
    return _REWRITE_CAPHI_RE_12.sub("", word)


def _rewrite_tok_1(word: str) -> str:
    word = _REWRITE_DIAC_RE_1.sub("\\1\u0651", word)
    word = _REWRITE_DIAC_RE_2.sub("", word)
    return _REWRITE_DIAC_RE_3.sub("\u0627\\1", word)


def _rewrite_tok_2(word: str) -> str:
    return _REWRITE_DIAC_RE_3.sub("\u0627\\1", word)


def _rewrite_pattern(word: str) -> str:
    return _REWRITE_DIAC_RE_2.sub("", word)


def merge_features(
    db: MorphologyDB, prefix_feats: dict[str, Any], stem_feats: dict[str, Any], suffix_feats: dict[str, Any]
) -> dict[str, Any]:
    """Combine one prefix, stem and suffix analysis into a word analysis."""
    result = copy.copy(stem_feats)

    for stem_feat in stem_feats:
        suffix_feat_val = suffix_feats.get(stem_feat, "")
        if suffix_feat_val not in ("-", ""):
            result[stem_feat] = suffix_feat_val
        prefix_feat_val = prefix_feats.get(stem_feat, "")
        if prefix_feat_val not in ("-", ""):
            result[stem_feat] = prefix_feat_val

    for join_feat in _JOIN_FEATS:
        if join_feat in db.defines:
            feat_vals = [prefix_feats.get(join_feat), stem_feats.get(join_feat), suffix_feats.get(join_feat)]
            result[join_feat] = "+".join(fv for fv in feat_vals if fv is not None and fv != "")

    for concat_feat in _CONCAT_FEATS:
        if concat_feat in db.defines:
            parts = [
                prefix_feats.get(concat_feat, ""),
                stem_feats.get(concat_feat, ""),
                suffix_feats.get(concat_feat, ""),
            ]
            result[concat_feat] = "+".join(x for x in parts if len(x) > 0)

    for concat_feat in _CONCAT_FEATS_NONE:
        if concat_feat in db.defines:
            result[concat_feat] = "{}{}{}".format(
                prefix_feats.get(concat_feat, ""),
                stem_feats.get(concat_feat, stem_feats.get("diac", "")),
                suffix_feats.get(concat_feat, ""),
            )

    result["stem"] = stem_feats["diac"]
    result["stemgloss"] = stem_feats.get("gloss", "")
    result["diac"] = _normalize_tanwyn(_rewrite_diac(result["diac"]))

    for feat in _TOK_SCHEMES_1:
        if feat in db.defines:
            result[feat] = _rewrite_tok_1(result.get(feat, ""))
    for feat in _TOK_SCHEMES_2:
        if feat in db.defines:
            result[feat] = _rewrite_tok_2(result.get(feat, ""))

    if "caphi" in db.defines:
        result["caphi"] = _rewrite_caphi(result.get("caphi", ""))
    if "form_gen" in db.defines and result["gen"] == "-":
        result["gen"] = result["form_gen"]
    if "form_num" in db.defines and result["num"] == "-":
        result["num"] = result["form_num"]

    if "pattern" in db.compute_feats:
        pattern = "{}{}{}".format(
            prefix_feats.get("diac", ""),
            stem_feats.get("pattern", stem_feats.get("diac", "")),
            suffix_feats.get("diac", ""),
        )
        result["pattern"] = _rewrite_pattern(pattern)

    for logprob_feat in _LOGPROB_FEATS:
        if logprob_feat in db.defines:
            result[logprob_feat] = float(result.get(logprob_feat, -99.0))

    return result

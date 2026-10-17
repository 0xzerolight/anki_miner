# Note-type CSS fixtures

A verbatim copy of a community Anki note type's shipped stylesheet, kept so
`tests/unit/test_glossary_css.py` can check that the gloss list still escapes its list
rules. It is an input to tests, not an assertion about the note type itself. Nothing
here is loaded at runtime.

Fetched 2026-09-09.

| File | Source | Commit | Licence |
|---|---|---|---|
| `lapis.css` | https://github.com/donkuri/lapis `src/styling.css` | `f4eb29bd4129ae1dde068c64cf6557b7603462dc` | GPL-3.0 |

Anki Miner is GPL-3.0-or-later; the GPL-3.0 source is redistributable inside it with the
attribution above, and the upstream repository carries its own LICENSE file. The GPL-3.0
text that covers `lapis.css` is the repository's own `LICENSE`. This fixture is a test
input and is not packaged into the wheel, so it carries no `license-files` entry in
`pyproject.toml`.

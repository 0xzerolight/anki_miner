# Note-type CSS fixtures

Verbatim copies of three community Anki note types' shipped stylesheets, kept so the
glossary-CSS cohabitation audit (`docs/superpowers/plans/2026-09-09-note-type-css-audit.md`)
is reproducible. They are inputs to a rendering audit, not assertions about the note types
themselves. Nothing here is loaded at runtime.

Fetched 2026-09-09.

| File | Source | Commit | Licence |
|---|---|---|---|
| `lapis.css` | https://github.com/donkuri/lapis `src/styling.css` | `f4eb29bd4129ae1dde068c64cf6557b7603462dc` | GPL-3.0 |
| `senren.css` | https://github.com/BrenoAqua/Senren `Template/styling.css` | `21ede8fbd1276e5c890ef9a799bf065d522717c2` | GPL-3.0 |
| `senren_defaults.css` | https://github.com/BrenoAqua/Senren `Template/_senren_defaults_v5.1.0.css` | `21ede8fbd1276e5c890ef9a799bf065d522717c2` | GPL-3.0 |
| `senren_settings.css` | https://github.com/BrenoAqua/Senren `Template/_senren_settings_v5.1.0.css` | `21ede8fbd1276e5c890ef9a799bf065d522717c2` | GPL-3.0 |
| `kiku.css` | https://github.com/youyoumu/kiku `packages/note/template/style.css` | `2a7b295006f5b86dd34cc0c119601820178bef56` | MIT |
| `kiku_plugin.css` | https://github.com/youyoumu/kiku `packages/note/template/_kiku_plugin.css` | `2a7b295006f5b86dd34cc0c119601820178bef56` | MIT |

Anki Miner is GPL-3.0-or-later; GPL-3.0 and MIT sources are both redistributable inside it
with the attribution above. Each upstream repository carries its own LICENSE file. These
fixtures are test inputs and are not packaged into the wheel, so they carry no
`license-files` entry in `pyproject.toml`.

The GPL-3.0 text that covers `lapis.css` and the three `senren*.css` files is the repository's
own `LICENSE`. The MIT notice that must travel with `kiku.css` and `kiku_plugin.css`:

```
MIT License

Copyright (c) 2025 youyoumu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Senren loads `senren_defaults.css` and `senren_settings.css` as Anki media, injected by
`_senren_settings_v5.1.0.js`; `senren.css` alone is the note type's Styling box.

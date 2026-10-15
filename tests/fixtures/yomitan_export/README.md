# Yomitan export parity fixtures

- `jicchuuhakku.yomitan.html`: a genuine Yomitan Anki export of 十中八九 (MainDefinition column of
  Lapis `build/example_card.csv`, https://github.com/donkuri/lapis commit f4eb29bd, GPL-3.0).
- `jicchuuhakku.internal.html`: Anki Miner's internal `_render` markup for the same word from
  JMdict 2026-06-28.

Both carry JMdict data, © Electronic Dictionary Research and Development Group, CC BY-SA 4.0.
The two JMdict releases differ: the 2026 forms table has no `<tbody>` and no `lang` on its header cells
(checked against the 2026 source zip), which the parity test normalizes away.

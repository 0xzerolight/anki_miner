# Yomitan-derived Japanese code — license and provenance

`anki_miner/services/deinflection.py` (engine),
`anki_miner/services/japanese_transforms.py` (rule table),
`anki_miner/utils/furigana_distribute.py` (furigana distribution),
`anki_miner/services/ja_normalize.py` (pre-tokenization normalization), and their
test corpus are Python ports of code from
[Yomitan](https://github.com/yomidevs/yomitan), Copyright (C)
Yomitan Authors, licensed under the **GNU General Public License, version 3
or (at your option) any later version** — the full text is in
[`COPYING.GPLv3`](COPYING.GPLv3). Anki Miner is itself GPL-3.0-or-later, so
the combined work carries the same license.

## Upstream provenance

| Ported file | Upstream source |
|-------------|-----------------|
| `anki_miner/services/deinflection.py` | `ext/js/language/language-transformer.js`, `ext/js/language/language-transforms.js` |
| `anki_miner/services/japanese_transforms.py` | `ext/js/language/ja/japanese-transforms.js` |
| `anki_miner/utils/furigana_distribute.py` | `ext/js/language/ja/japanese.js` (`distributeFurigana` family) |
| `anki_miner/services/ja_normalize.py` | `ext/js/language/ja/japanese.js` (`convertHalfWidthKanaToFullWidth`, `HALFWIDTH_KATAKANA_MAPPING`, `normalizeCJKCompatibilityCharacters`), `ext/js/language/CJK-util.js` (`normalizeRadicals`, `CJK_IDEOGRAPH_RANGES`, `isCodePointInRanges`) |
| `tests/unit/test_ja_normalize.py` (fixture slices) | `test/japanese-util.test.js` |
| `anki_miner/services/pitch_accent_service.py` (`classify_pitch`, `downstep_positions`, `format_categories` H/L handling) | `ext/js/language/ja/japanese.js` (`getPitchCategory`, `getDownstepPositions`) |
| `anki_miner/services/pitch_accent/yomitan_pitch_importer.py` (`_to_number_array`) | `ext/js/language/translator.js` (`Translator._toNumberArray`) |
| `anki_miner/services/_ankiconnect.py` (`_expect_list`) | `ext/js/comm/anki-connect.js` (`_normalizeArray`) |
| `anki_miner/services/anki_service.py` (`_probe_duplicates`, `_probe_duplicates_fallback`, `_strip_note_to_first_field`) | `ext/js/background/backend.js` (`partitionAddibleNotes`, `_findDuplicates`, `_findDuplicatesFallback`, `_stripNotesArray`), `ext/js/comm/anki-connect.js` (`canAddNotesWithErrorDetail`) |
| `anki_miner/services/anki_service.py` (`update_notes_coalesce`, `_coalesce_field`, `_find_notes_query_for`, `_escape_dup_query`) | `ext/js/display/display-anki.js` (`_getOverwrittenNote`, `_getOverwrittenField` coalesce case), `ext/js/comm/anki-connect.js` (`findNoteIds`, `_getNoteQuery`, `_fieldsToQuery`, `_escapeQuery`) |
| `anki_miner/services/expression_audio_fetcher.py` (`_classify_request_exception` + failure-cause tally) | `ext/js/background/backend.js` (`Backend._getAudioDownloadError`) |
| `anki_miner/services/expression_audio_fetcher.py` (`AUDIO_MEDIA_TYPE_EXTENSIONS`, `audio_extension_for_media_type`) | `ext/js/media/media-util.js` (`getFileExtensionFromAudioMediaType`) |
| `anki_miner/services/custom_audio_fetcher.py` (`_substitute_custom_url`, `_resolve_json_sources` shape check) | `ext/js/media/audio-downloader.js` (`AudioDownloader._getCustomUrl`, `_getInfoCustom`, `_getInfoCustomJson`) |
| `anki_miner/services/scrape_audio_fetchers.py` (`_extract_jpod101_audio_urls`, `_validate_jpod101_row`, `_extract_jisho_audio_urls`) | `ext/js/media/audio-downloader.js` (`AudioDownloader._getInfoLanguagePod101`, `_validateLanguagePod101Row`, `_getInfoJisho`) |
| `tests/unit/data/japanese_transforms_cases.py` (case table) | `test/language/japanese-transforms.test.js` |
| `tests/unit/test_japanese_transforms_cases.py::has_term_reasons` | `test/fixtures/language-transformer-test.js` (`hasTermReasons`) |
| `tests/unit/test_deinflection_cycles.py` | `test/language-transformer-cycles.test.js` |
| `anki_miner/services/frequency/multi_frequency_service.py` (`lookup_harmonic`) | `ext/js/data/anki-note-data-creator.js` (`getFrequencyHarmonic`) |
| `anki_miner/services/anki_note_builder.py` (`build_cloze_fields`) | `ext/js/data/anki-note-data-creator.js` (`getCloze`) |
| `anki_miner/services/anki_note_builder.py` (`_get_root_deck_name`, `build_duplicate_scope_options`) | `ext/js/data/anki-util.js` (`getRootDeckName`), `ext/js/data/anki-note-builder.js` (`createNote` duplicateScope/duplicateScopeOptions block) |
| `anki_miner/services/anki_media_store.py` (`_content_addressed_name`), `anki_miner/utils/file_utils.py` (`safe_filename` `[`/`]` strip) | `ext/js/data/anki-util.js` (`mediaFileNameHashOrTimestamp`, `generateAnkiNoteMediaFileName`), `ext/js/background/backend.js` (`]` strip on audio filenames) |
| `anki_miner/services/deinflection.py` (`find_highlight_end_with_trace` attachment-order chain) | `ext/js/language/language-transformer.js` (`_extendTrace`), `ext/js/language/translator.js` (`inflectionRules` mapping) |
| `anki_miner/utils/sentence_extract.py` (`extract_sentence`, terminator/quote maps) | `ext/js/dom/text-source-generator.js` (`extractSentence`), `ext/data/schemas/options-schema.json` (default `terminationCharacters`) |
| `anki_miner/services/frequency/providers/indexed_freq_provider.py` (`_resolve_scoped_rank` reading-scoping) | `ext/js/language/translator.js` (term-meta `freq` case: reading-tagged rows filter by reading, bare rows apply to all readings) |
| `anki_miner/services/frequency/csv_parse.py` (`_NUMBER_RE`, `_string_to_rank`, `normalize_freq_rank` displayValue triple) | `ext/js/language/translator.js` (`_numberRegex`, `_convertStringToNumber`, `_getFrequencyInfo`) |
| `anki_miner/services/frequency/render.py` (`render_frequency_html` displayValue-or-rank) | `ext/js/data/anki-note-data-creator.js` (`getTermFrequencies`: `displayValue !== null ? displayValue : frequency`) |
| `anki_miner/services/frequency/mode_probe.py` (`MORE_COMMON_TERMS`/`LESS_COMMON_TERMS` lists, `probe_direction` paired-sign heuristic) | `ext/js/pages/settings/sort-frequency-dictionary-controller.js` (`SortFrequencyDictionaryController._getFrequencyOrder`) |
| `anki_miner/services/dictionary/yomitan_renderer.py` (`structured_content_to_html` typed-glossary dispatch, `_text_to_html`, `_render_attrs` internal-link neutering, `_coerce_style_value` style-value semantics) | `ext/js/dictionary/dictionary-importer.js` (`_formatDictionaryTermGlossaryObject`), `ext/js/templates/anki-template-renderer.js` (`_formatGlossary`, `_replaceNewlines`), `ext/js/templates/anki-template-renderer-content-manager.js` (`prepareLink`), `ext/js/display/structured-content-generator.js` (`_setStructuredContentElementStyle`, `_createLinkElement`) |
| `anki_miner/services/dictionary/zip_safety.py` (`find_redundant_index_dir`, `raise_if_index_nested`) | `ext/js/dictionary/dictionary-importer.js` (`_findRedundantDirectories`, redundant-directory branch of `_readAndValidateIndex`) |
| `anki_miner/services/dictionary/importers/yomitan_importer.py` (`_MEDIA_EXTENSION_WHITELIST`) | `ext/js/media/media-util.js` (`getImageMediaTypeFromFileName`) |
| `anki_miner/services/dictionary/importers/yomitan_importer.py` (`_convert_tag_bank_entry`, `_convert_old_index_tag_meta`), `anki_miner/services/dictionary/storage.py` (`TagMeta`) | `ext/js/dictionary/dictionary-importer.js` (`_convertTagBankEntry`, `_addOldIndexTags`) |
| `anki_miner/services/dictionary/providers/indexed_provider.py` (`_render` tag-chip union+sort by `(ord, -score, name)`, lazy `_tag_meta` cache) | `ext/js/language/translator.js` (`_getTermTagsSort` / `_mergeSimilarTags` order, per-dictionary `_tagCache`) |
| `anki_miner/services/dictionary/schema_validation.py` (structural bank checks) | `ext/js/dictionary/dictionary-importer.js` (`_getDataBankSchemas` / ajv bank validation — structural subset, no vendored schemas) |
| `anki_miner/services/dictionary/yomitan_renderer.py` (`structured_content_to_html` typed-glossary dispatch, `_text_to_html`, `_render_attrs` internal-link neutering, `_coerce_style_value` style-value semantics, `_img_presentation_attrs` image data-* stamping) | `ext/js/dictionary/dictionary-importer.js` (`_formatDictionaryTermGlossaryObject`), `ext/js/templates/anki-template-renderer.js` (`_formatGlossary`, `_replaceNewlines`), `ext/js/templates/anki-template-renderer-content-manager.js` (`prepareLink`), `ext/js/display/structured-content-generator.js` (`_setStructuredContentElementStyle`, `_createLinkElement`, `createDefinitionImage`) |
| `anki_miner/services/dictionary/resources/glossary.css` (monochrome recolor, pixelated image-rendering) | `ext/data/structured-content-style.json` (`.gloss-image-background`, `[data-appearance=monochrome]`, `[data-image-rendering=pixelated]` rules) |
| `anki_miner/services/dictionary/storage.py` (`_LOOKUP_SQL` reading-boost key + `_reading_priority`, mirrored in `lookup`/`lookup_many`) | `ext/js/language/translator.js` (`Translator._sortTermDictionaryEntries` — `matchPrimaryReading` leading sort key, a boost not a filter) |
| `anki_miner/services/dictionary/providers/indexed_provider.py` (`_render` sequence grouping + per-group tag lines) | `ext/js/language/translator.js` (`Translator._getRelatedDictionaryEntries`, `_createGroupedDictionaryEntry` — group definitions by `sequence`) |
| `anki_miner/services/pitch_accent/render.py` (`is_mora_pitch_high`, `get_kana_morae`, `get_kana_diacritic_info`, `render_pitch_graph_svg`, `render_pitch_text`) | `ext/js/language/ja/japanese.js` (`isMoraPitchHigh`, `getKanaMorae`, `getKanaDiacriticInfo` + `DIACRITIC_MAPPING`), `ext/js/display/pronunciation-generator.js` (`createPronunciationGraph`, `createPronunciationText` + graph-dot/triangle helpers), with the inlined element styles hand-resolved from `ext/data/pronunciation-style.json` |
| `anki_miner/services/dictionary/updater.py` (`compare_revisions`), `check_for_update` control flow | `ext/js/dictionary/dictionary-data-util.js` (`compareRevisions`), `ext/js/pages/settings/dictionary-controller.js` (`DictionaryEntry.checkForUpdate`) |
| `anki_miner/services/dictionary/schema_validation.py` (`validate_http_url`, `is_valid_dictionary_index`), `anki_miner/services/dictionary/importers/yomitan_importer.py` (`_read_update_meta`) | `ext/js/dictionary/dictionary-importer.js` (`_validateUrl`, `_readAndValidateIndex` / ajv `dictionaryIndex` schema — structural subset, `_createSummary` update/attribution fields) |

Pinned upstream commit: `e2ed450c2f11a591922822e77f008e70a87daf0c`.

## Regenerating the rule table against a newer upstream

`japanese_transforms.py` is generated by materializing the upstream ES module
(generator helpers pre-expanded), not hand-transcribed. After cloning yomitan
at the desired commit, regeneration is one command:

```bash
node scripts/regen_japanese_transforms.mjs /path/to/yomitan
```

It imports `{japaneseTransforms}` from
`ext/js/language/ja/japanese-transforms.js`, recovers each rule's raw
inflected/deinflected strings (suffix rules: regex source minus the `$` anchor
+ `deinflected`; whole-word rules: regex source minus `^`/`$` + `deinflect('')`)
plus the `conditions` tree, and rewrites the Python module — one
`suffix_inflection`/`whole_word_inflection` call per rule, upstream argument
order, with the pinned commit hash taken from the checkout's `git rev-parse HEAD`.
Add `--check` to diff against the committed file without writing (exits nonzero
on drift). After regenerating:

1. Update the materialized rule counts asserted in `tests/unit/test_deinflection.py`
   and the pinned commit above if they changed.
2. Re-transcribe the case table with the same tooling and refresh
   `tests/unit/data/japanese_transforms_cases.py`, then run
   `tests/unit/test_japanese_transforms_cases.py` (the differential spec: the
   Python `Deinflector.transform` result set must satisfy every upstream case)
   and `tests/unit/test_deinflection_cycles.py` (static cycle-freedom proof).

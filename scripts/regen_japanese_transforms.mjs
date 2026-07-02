#!/usr/bin/env node
/*
 * Regenerate anki_miner/services/japanese_transforms.py from a Yomitan checkout.
 *
 * Materializes the upstream ES module `ext/js/language/ja/japanese-transforms.js`
 * (generator helpers pre-expanded) into the Python rule table consumed by
 * anki_miner/services/deinflection.py. Reads the raw inflected/deinflected
 * strings back out of each rule the same way upstream's cycle test does — the
 * `isInflected` RegExp source minus its `^`/`$` anchors — so no upstream API
 * beyond the exported `japaneseTransforms` descriptor is required.
 *
 * Usage:
 *   node scripts/regen_japanese_transforms.mjs <yomitan-repo> [--check]
 *     <yomitan-repo>  path to a Yomitan clone at the desired commit
 *     --check         print a unified-diff-ish report and exit nonzero if the
 *                     generated output differs from the committed file (CI/verify)
 *
 * Plain node + ESM only; no package install.
 *
 * Copyright (C) 2026  anki_miner contributors
 * Licensed under the GNU General Public License v3.0 or later (same as the
 * Yomitan sources it materializes); see licenses/yomitan/.
 */
import {execSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const outputPath = resolve(scriptDir, '..', 'anki_miner', 'services', 'japanese_transforms.py');

function fail(message) {
    process.stderr.write(`ERROR: ${message}\n`);
    process.exit(2);
}

const args = process.argv.slice(2);
const check = args.includes('--check');
const repoArg = args.find((a) => !a.startsWith('--'));
if (typeof repoArg === 'undefined') {
    fail('missing <yomitan-repo> path\nUsage: node scripts/regen_japanese_transforms.mjs <yomitan-repo> [--check]');
}
const repo = resolve(repoArg);

const modulePath = join(repo, 'ext', 'js', 'language', 'ja', 'japanese-transforms.js');
let japaneseTransforms;
try {
    ({japaneseTransforms} = await import(pathToFileURL(modulePath).href));
} catch (e) {
    fail(`could not import ${modulePath}\n${e && e.message ? e.message : e}`);
}
if (!japaneseTransforms) { fail(`${modulePath} did not export japaneseTransforms`); }

let commit;
try {
    commit = execSync('git rev-parse HEAD', {cwd: repo}).toString().trim();
} catch {
    fail(`could not read the git commit of ${repo}`);
}

/** Python string literal for a JS string; JSON escaping matches Python for this data (kana/kanji/ASCII). */
function pyStr(value) {
    return JSON.stringify(value);
}

/** Python list-of-strings literal, e.g. ["a", "b"]. */
function pyList(items) {
    return `[${items.map(pyStr).join(', ')}]`;
}

/** Recover the raw inflected string from a materialized rule's isInflected RegExp. */
function inflectedOf(rule) {
    const src = rule.isInflected.source;
    if (rule.type === 'suffix') {
        return src.slice(0, -1); // drop trailing "$"
    }
    if (rule.type === 'wholeWord') {
        return src.slice(1, -1); // drop leading "^" and trailing "$"
    }
    return fail(`unsupported rule type: ${rule.type}`);
}

/** Recover the raw deinflected string. */
function deinflectedOf(rule) {
    if (rule.type === 'suffix') {
        return rule.deinflected;
    }
    // wholeWord rules expose only deinflect(); it ignores its argument.
    return rule.deinflect('');
}

function ruleCall(rule) {
    const fn = rule.type === 'suffix' ? 'suffix_inflection' : 'whole_word_inflection';
    const inflected = inflectedOf(rule);
    const deinflected = deinflectedOf(rule);
    return `${fn}(${pyStr(inflected)}, ${pyStr(deinflected)}, ${pyList(rule.conditionsIn)}, ${pyList(rule.conditionsOut)})`;
}

const conditionEntries = Object.entries(japaneseTransforms.conditions);
const transformEntries = Object.entries(japaneseTransforms.transforms);
const ruleCount = transformEntries.reduce((n, [, t]) => n + t.rules.length, 0);

const out = [];
const push = (line) => out.push(line);

// --- header ---
push('# Derived from Yomitan (https://github.com/yomidevs/yomitan),');
push(`# ext/js/language/ja/japanese-transforms.js, commit ${commit},`);
push('# mechanically materialized (generator helpers pre-expanded, transform');
push('# names and rule argument order preserved verbatim; description strings');
push('# omitted — they are upstream UI text). Regenerate against a newer');
push('# upstream commit by re-running the dump documented in licenses/yomitan/.');
push('#');
push('# Copyright (C) 2024-2026  Yomitan Authors');
push('# Copyright (C) 2026  anki_miner contributors (Python port)');
push('#');
push('# This program is free software: you can redistribute it and/or modify');
push('# it under the terms of the GNU General Public License as published by');
push('# the Free Software Foundation, either version 3 of the License, or');
push('# (at your option) any later version.');
push('#');
push('# This program is distributed in the hope that it will be useful,');
push('# but WITHOUT ANY WARRANTY; without even the implied warranty of');
push('# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the');
push('# GNU General Public License for more details.');
push('#');
push('# You should have received a copy of the GNU General Public License');
push('# along with this program.  If not, see <https://www.gnu.org/licenses/>.');
push('');
push('"""Yomitan Japanese deinflection rule table (data only, no engine import).');
push('');
push(`${transformEntries.length} transforms / ${ruleCount} materialized rules /`);
push(`${conditionEntries.length} condition types, consumed by`);
push('``deinflection.Deinflector``. GENERATED FILE — edit upstream and');
push('regenerate rather than hand-editing rules.');
push('"""');
push('');
push('from __future__ import annotations');
push('');
push('from typing import Any');
push('');
push('');
push('def suffix_inflection(');
push('    inflected: str,');
push('    deinflected: str,');
push('    conditions_in: list[str],');
push('    conditions_out: list[str],');
push(') -> dict[str, Any]:');
push('    """Rule matching an inflected suffix (upstream ``suffixInflection``)."""');
push('    return {');
push('        "type": "suffix",');
push('        "inflected": inflected,');
push('        "deinflected": deinflected,');
push('        "conditionsIn": conditions_in,');
push('        "conditionsOut": conditions_out,');
push('    }');
push('');
push('');
push('def whole_word_inflection(');
push('    inflected: str,');
push('    deinflected: str,');
push('    conditions_in: list[str],');
push('    conditions_out: list[str],');
push(') -> dict[str, Any]:');
push('    """Rule matching an entire word (upstream ``wholeWordInflection``)."""');
push('    return {');
push('        "type": "wholeWord",');
push('        "inflected": inflected,');
push('        "deinflected": deinflected,');
push('        "conditionsIn": conditions_in,');
push('        "conditionsOut": conditions_out,');
push('    }');
push('');
push('');

// --- CONDITIONS ---
push('CONDITIONS: dict[str, dict[str, Any]] = {');
for (const [type, cond] of conditionEntries) {
    const fields = [`"isDictionaryForm": ${cond.isDictionaryForm ? 'True' : 'False'}`];
    if (Array.isArray(cond.subConditions)) {
        fields.push(`"subConditions": ${pyList(cond.subConditions)}`);
    }
    push(`    ${pyStr(type)}: {${fields.join(', ')}},`);
}
push('}');
push('');
push('');

// --- TRANSFORMS ---
push('TRANSFORMS: list[dict[str, Any]] = [');
for (const [id, transform] of transformEntries) {
    push('    {');
    push(`        "id": ${pyStr(id)},`);
    push(`        "name": ${pyStr(transform.name)},`);
    push('        "rules": [');
    for (const rule of transform.rules) {
        push(`            ${ruleCall(rule)},`);
    }
    push('        ],');
    push('    },');
}
push(']');
push('');

const generated = out.join('\n');

if (check) {
    const current = readFileSync(outputPath, 'utf8');
    if (current === generated) {
        process.stderr.write(`OK: ${outputPath} matches regeneration at commit ${commit}\n`);
        process.exit(0);
    }
    // Minimal line-level diff report.
    const a = current.split('\n');
    const b = generated.split('\n');
    const max = Math.max(a.length, b.length);
    let shown = 0;
    for (let i = 0; i < max && shown < 40; ++i) {
        if (a[i] !== b[i]) {
            process.stderr.write(`L${i + 1}\n  committed: ${JSON.stringify(a[i])}\n  generated: ${JSON.stringify(b[i])}\n`);
            ++shown;
        }
    }
    process.stderr.write(`DIFF: generated output differs from ${outputPath}\n`);
    process.exit(1);
}

writeFileSync(outputPath, generated);
process.stderr.write(`wrote ${outputPath} (${transformEntries.length} transforms, ${ruleCount} rules, commit ${commit})\n`);

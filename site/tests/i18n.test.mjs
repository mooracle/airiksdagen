// The UI string table, checked for the two failures it can have silently.
//
// `ui.ts` carried TWO `en:` blocks for some time. In an object literal the second
// wins, so the first — 343 lines of it, 66 keys still holding Swedish text from
// when it was copied off `sv` as a starting point — was dead code. Nothing broke
// while every key happened to exist in both, and then seven new keys were added
// to the dead one and the English site quietly rendered Swedish for them.
//
// Neither failure raises: a duplicate key is legal JavaScript, and a missing key
// falls back to Swedish by design (which is right for a half-finished
// translation, and is exactly what hides this).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.join(import.meta.dirname, '..', 'src', 'i18n', 'ui.ts');
const src = fs.readFileSync(SRC, 'utf-8');
const lines = src.split('\n');

/** Key names between two line indices, ignoring comments and nesting. */
function keysBetween(from, to) {
  const out = new Set();
  for (const line of lines.slice(from, to)) {
    const m = line.match(/^\s*'([^']+)':/);
    if (m) out.add(m[1]);
  }
  return out;
}

test('each language appears exactly once', () => {
  // The whole bug in one assertion. A second `en: {` silently discards the first.
  for (const lang of ['sv', 'en']) {
    const n = lines.filter((l) => l.trim() === `${lang}: {`).length;
    assert.equal(n, 1, `${lang}: ${n} blocks — a duplicate silently wins over the earlier one`);
  }
});

test('every Swedish string has an English one', () => {
  const sv = lines.findIndex((l) => l.trim() === 'sv: {');
  const en = lines.findIndex((l) => l.trim() === 'en: {');
  assert.ok(sv >= 0 && en > sv, 'both blocks found, sv first');
  const svKeys = keysBetween(sv, en);
  const enKeys = keysBetween(en, lines.length);
  assert.ok(svKeys.size > 250, `only ${svKeys.size} Swedish keys parsed`);
  // A missing key is not an error at runtime — it falls back to Swedish — so it
  // shows up as Swedish text on the English site rather than as a failure.
  assert.deepEqual([...svKeys].filter((k) => !enKeys.has(k)), [], 'keys with no English');
  assert.deepEqual([...enKeys].filter((k) => !svKeys.has(k)), [], 'English keys with no Swedish');
});

test('no English value is left holding Swedish text', () => {
  // The dead block was a copy of `sv`, so 66 of its "English" values were still
  // Swedish. Not every such string is detectable, but the Swedish-only letters
  // are: any of åäö in an English value is either untranslated or a proper noun.
  const en = lines.findIndex((l) => l.trim() === 'en: {');
  const KNOWN = new Set([
    // Swedish proper nouns and terms the English copy keeps on purpose.
    'vote.Avstår', 'stance.impliesVote', 'party.absence',
  ]);
  const suspect = [];
  for (const line of lines.slice(en)) {
    const m = line.match(/^\s*'([^']+)':\s*'(.*)'/);
    if (!m || KNOWN.has(m[1])) continue;
    // Swedish function words are the giveaway; a proper noun like "Miljöpartiet"
    // or "Avstår" legitimately carries the letters on its own.
    if (/\b(och|för|som|inte|att|är|med|den|det|av|på)\b/.test(m[2])) suspect.push(m[1]);
  }
  assert.deepEqual(suspect, [], 'English values that read as Swedish');
});

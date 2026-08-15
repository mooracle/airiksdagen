// lib/data.ts — the pure helpers. The loaders that read src/data/ are covered in
// corpus.test.mjs, against the committed export.
import test from 'node:test';
import assert from 'node:assert/strict';

const { quoteFragment } = await import('../src/lib/data.ts');

test('a short quote becomes one text-fragment term', () => {
  assert.equal(decodeURIComponent(quoteFragment('Stram migrationspolitik kravs')), 'Stram migrationspolitik kravs');
});

test('a long quote becomes a start,end range', () => {
  const q = 'ett tva tre fyra fem sex sju atta nio tio';
  const [start, end] = quoteFragment(q).split(',').map(decodeURIComponent);
  assert.equal(start, 'ett tva tre fyra fem');
  assert.equal(end, 'sju atta nio tio');
});

test('an invisible character is dropped, because the page does not draw it', () => {
  // 51 committed quotes carry the BEL that sits after valmanifest-2022-s's bullet
  // glyph. Encoded into the fragment it matches nothing, on any rendering.
  assert.equal(decodeURIComponent(quoteFragment('• Ingen ung ska bli kriminell.')), '\u2022 Ingen ung ska bli kriminell.');
});

test('a hyphen is escaped, because the parser reads it as a prefix delimiter', () => {
  // `text=` is `[prefix-,]textStart[,textEnd][,-suffix]` and the browser applies
  // the `-` test to the encoded token, before percent-decoding. Left literal,
  // "mötes-" ends the first term and the whole quote is read as a prefix with no
  // textStart of its own — 119 of full-v4's links, all silently unhighlighted.
  const q = 'Ett starkt skydd for yttrande- och motesfriheten ar av stor vikt';
  const enc = quoteFragment(q);
  const toks = enc.split(',');
  assert.ok(!toks[0].endsWith('-'), enc);
  assert.ok(!toks[toks.length - 1].startsWith('-'), enc);
  // and it still searches for the same text
  assert.equal(decodeURIComponent(toks[0]), 'Ett starkt skydd for yttrande-');
});

test('whitespace around and inside the quote is normalised', () => {
  assert.equal(decodeURIComponent(quoteFragment('  Vi vill\n  se ett tryggt land  ')), 'Vi vill se ett tryggt land');
});

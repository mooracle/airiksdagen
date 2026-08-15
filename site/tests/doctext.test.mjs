// Unit tests for the document-page render model. `npm test` (node --test) runs
// them; they import the .ts modules directly, so they need a Node that strips
// types without a flag (>= 22.18).
//
// The Python suite covers everything that writes the corpus. What is only
// testable here is the last hop: blocks -> what the reader sees, and whether a
// citation's #:~:text= fragment can still find its quote in it.
import test from 'node:test';
import assert from 'node:assert/strict';

const { groupBlocks, renderDoc, stripInvisible, formatCorpusDoc } = await import(
  '../src/lib/doctext.ts'
);

const block = (id, role, text, page = 0) => ({ id, role, page, size: 11, bold: false, text });
const file = (blocks) => ({ slug: 't', source: 'pdf', pages: 1, dropped: [], blocks });
const texts = (groups) => groups.map((g) => g.parts.map((p) => p.text).join(' '));

test('a block becomes one paragraph, keeping its id and role', () => {
  const groups = groupBlocks([block('b0000', 'h1', 'KAPITEL 1'), block('b0001', 'para', 'Vi vill.')]);
  assert.deepEqual(
    groups.map((g) => [g.role, g.parts[0].id, g.parts[0].text]),
    [
      ['h1', 'b0000', 'KAPITEL 1'],
      ['para', 'b0001', 'Vi vill.'],
    ],
  );
});

test('a page number is dropped, because the served text does not carry it', () => {
  // corpus.normalize() removes digit-only lines and rules; the block file keeps
  // them, so the page has to apply the same drop set or it shows text no agent
  // was ever served.
  const groups = groupBlocks([
    block('b0000', 'caption', '42'),
    block('b0001', 'caption', '— · —'),
    block('b0002', 'para', 'Riktig text.'),
  ]);
  assert.deepEqual(texts(groups), ['Riktig text.']);
});

test('an unreadable heading survives as a placeholder rather than vanishing', () => {
  // mp-2013's 50 display-font headings decode to glyph soup. normalize() drops
  // them from the served text; dropping them here too would tell the reader the
  // document has no heading there.
  const groups = groupBlocks([block('b0000', 'unreadable', '')]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].role, 'unreadable');
  assert.equal(groups[0].parts[0].id, 'b0000');
});

test('invisible characters never reach the page', () => {
  // valmanifest-2022-s carries a BEL after 40 of its bullet glyphs.
  const groups = groupBlocks([
    block('b0000', 'bullet', '\u2022 \u0007Ingen ung ska bli\u00ADkriminell.'),
  ]);
  assert.equal(groups[0].parts[0].text, '\u2022 Ingen ung ska blikriminell.');
});

test('a paragraph the extraction cut mid-sentence is rejoined, both ids kept', () => {
  const groups = groupBlocks([
    block('b0000', 'para', 'Moderaterna vill reformera bistandet och rikta det mot'),
    block('b0001', 'para', 'oreformerade organ som brister i transparens.', 1),
  ]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].parts.map((p) => p.id), ['b0000', 'b0001']);
  assert.equal(
    texts(groups)[0],
    'Moderaterna vill reformera bistandet och rikta det mot oreformerade organ som brister i transparens.',
  );
});

test('a bold lead-in and its continuation are one paragraph', () => {
  // valmanifest-2022-s: the first clause is set bold and clusters as `label`.
  const groups = groupBlocks([
    block('b0019', 'label', 'I Sverige staller vi upp for varandra. Vi lamnar nyckeln'),
    block('b0020', 'para', 'till grannen for att fa blommorna vattnade.'),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].role, 'label');
  assert.deepEqual(groups[0].parts.map((p) => p.role), ['label', 'para']);
});

test('a finished sentence is not joined to what follows', () => {
  const groups = groupBlocks([
    block('b0000', 'para', 'Sverige ar ett fantastiskt land.'),
    block('b0001', 'para', 'har finns alla forutsattningar.'),
  ]);
  assert.equal(groups.length, 2);
});

test('a capitalised opener is not a continuation', () => {
  const groups = groupBlocks([
    block('b0000', 'para', 'Vi vill se fler insatser och'),
    block('b0001', 'para', 'Foraldrar ska medverka.'),
  ]);
  assert.equal(groups.length, 2);
});

test('nothing joins across a heading', () => {
  const groups = groupBlocks([
    block('b0000', 'para', 'ett stycke som slutar utan punkt'),
    block('b0001', 'h2', 'KAPITEL 2'),
    block('b0002', 'para', 'fortsattningen.'),
  ]);
  assert.equal(groups.length, 3);
});

test('a glossary is not chained into one paragraph', () => {
  // KD 2015's appendix alternates term (`label`) and definition (`para`), none of
  // them ending in a full stop. Joining on prose alone fused 39 entries into one
  // block of text; a label that begins mid-page begins a new entry.
  const glossary = [
    block('b0000', 'label', 'segregation'),
    block('b0001', 'para', 'atskillnad mellan befolkningsgrupper'),
    block('b0002', 'label', 'sekretess'),
    block('b0003', 'para', 'att halla nagot hemligt'),
  ];
  const groups = groupBlocks(glossary);
  assert.deepEqual(texts(groups), [
    'segregation atskillnad mellan befolkningsgrupper',
    'sekretess att halla nagot hemligt',
  ]);
});

test('a label opening a new page IS a continuation', () => {
  // The same shape as the glossary, but the page turned — which is where the
  // extractor cut the paragraph in the first place.
  const groups = groupBlocks([
    block('b0150', 'para', 'Ryssland inledde ett anfall mot var', 13),
    block('b0151', 'label', 'fred och frihet. Darfor vill vi att:', 14),
  ]);
  assert.equal(groups.length, 1);
});

test('a heading set over two lines is one heading', () => {
  // valmanifest-m sets 14pt titles over two blocks, and a citation quotes across
  // the break. Two <h3>s would break both the outline and that link.
  const groups = groupBlocks([
    block('b0194', 'h2', 'Statens utgifter ska minska'),
    block('b0195', 'h2', '– vi ska stoppa sloseriet med skattepengar'),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].role, 'h2');
  assert.equal(texts(groups)[0], 'Statens utgifter ska minska – vi ska stoppa sloseriet med skattepengar');
});

test('two headings at different sizes stay two headings', () => {
  // A chapter title followed by its first subheading, not a wrapped line.
  const groups = groupBlocks([
    { ...block('b0000', 'h1', 'KAPITEL 5'), size: 29 },
    { ...block('b0001', 'h1', 'inledning'), size: 15 },
  ]);
  assert.equal(groups.length, 2);
});

test('a dash opener joins a heading but never prose', () => {
  // Half this corpus writes its list items with "– ", so a dash continuation is
  // only safe where a bullet cannot be: inside a heading of the same size.
  const groups = groupBlocks([
    block('b0000', 'para', 'Vi vill se en politik som'),
    block('b0001', 'para', '– sanker skatten pa arbete'),
  ]);
  assert.equal(groups.length, 2);
});

test('renderDoc uses the blocks when there are blocks', () => {
  const raw = '<!-- Principprogram | antaget 2015-10-01 -->\nKAPITEL 1\nVi vill.\n';
  const doc = renderDoc(raw, file([block('b0000', 'h1', 'KAPITEL 1'), block('b0001', 'para', 'Vi vill.')]));
  assert.equal(doc.stats.source, 'blocks');
  assert.equal(doc.provenance?.title, 'Principprogram');
  assert.deepEqual(doc.groups.map((g) => g.role), ['h1', 'para']);
});

test('renderDoc falls back to the flat text when there are none', () => {
  // The 16 budgetmotioner and Tidoavtalet have no block file, and must not
  // regress to an unstructured wall.
  const raw = '<!-- Budgetmotion 2024/25 (V) | dok_id HC021924 -->\nEn rubrik\n\nEtt stycke som ar langt nog att lasa.\n';
  const doc = renderDoc(raw, null);
  assert.equal(doc.stats.source, 'text');
  assert.equal(doc.provenance?.title, 'Budgetmotion 2024/25 (V)');
  assert.ok(doc.groups.length >= 2);
  assert.equal(doc.groups[0].parts[0].id, null);
});

test('the fallback path renders exactly what formatCorpusDoc produced', () => {
  // Parity is the whole promise for those 17 documents: same blocks, same order,
  // same text, only the vocabulary renamed.
  const raw = '<!-- x -->\nEN RUBRIK\n\n• ett\n• tva\n\nEtt vanligt stycke som fortsatter har.\n';
  const flat = formatCorpusDoc(raw);
  const doc = renderDoc(raw, null);
  assert.deepEqual(doc.groups.map((g) => g.parts[0].text), flat.blocks.map((b) => b.text));
  const asRole = { heading: 'h1', subheading: 'h2', para: 'para', bullet: 'bullet', toc: 'toc', unreadable: 'unreadable' };
  assert.deepEqual(doc.groups.map((g) => g.role), flat.blocks.map((b) => asRole[b.kind]));
});

test('stripInvisible drops what the page will not draw and leaves prose alone', () => {
  assert.equal(stripInvisible('Vi vill se ett tryggt Sverige.'), 'Vi vill se ett tryggt Sverige.');
  assert.equal(stripInvisible('sam\u00ADhalle\u00A0och'), 'samhalle och');
});

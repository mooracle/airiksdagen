// lib/anchors.ts — the citation index as the document page reads it.
//
// Two halves: the pure shaping (what a paragraph carries, what the rail lists),
// on hand-built groups; and the guarantee that only the committed export can
// answer — that the number on a panel's summary is the number of rows the panel
// fetches when it is opened.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.join(import.meta.dirname, '..');
process.chdir(ROOT); // lib/data.ts resolves src/data/ from cwd, as astro does

const { citedLines, chapters, refRows } = await import('../src/lib/anchors.ts');
const { getCorpusAnchors, getCorpusBlocks, getCorpusDoc } = await import('../src/lib/data.ts');
const { renderDoc, groupBlocks } = await import('../src/lib/doctext.ts');

const CORPUS = path.join(ROOT, 'src', 'data', 'corpus');
const exported = fs.existsSync(path.join(CORPUS, 'anchors'));
const skip = exported ? false : 'src/data/corpus/anchors not exported (run: uv run aidag build-anchors)';
const slugs = exported
  ? fs.readdirSync(path.join(CORPUS, 'anchors')).map((f) => f.replace(/\.json$/, ''))
  : [];

/** One rendered paragraph, the shape doctext.groupBlocks() emits. */
const group = (role, page, ...parts) => ({
  role,
  page,
  parts: parts.map(([id, text, partRole]) => ({ id, role: partRole ?? role, page, text })),
});

/** A block's tallies, with the fields the page reads. */
const cites = (n, kept, diverged, extra = {}) => ({
  refs: n,
  kept,
  diverged,
  avstar: n - kept - diverged,
  franvarande: 0,
  svag: 0,
  decisions: { n, kept, diverged, avstar: n - kept - diverged, franvarande: 0 },
  tiers: { explicit: n },
  parties: { KD: n },
  anchors: [],
  ...extra,
});

const summaryOf = (blocks) => ({
  slug: 'test',
  run_id: 'test-run',
  totals: { anchors: 0, refs: 0, kept: 0, diverged: 0, avstar: 0, franvarande: 0,
            decisions: { n: 0, kept: 0, diverged: 0, avstar: 0, franvarande: 0 } },
  blocks,
});

// --- what a paragraph carries -------------------------------------------------

test('a paragraph with no citations carries no panel', () => {
  const g = group('para', 3, ['b0007', 'Vi vill se ett tryggt land.']);
  assert.deepEqual(citedLines(g, summaryOf({ b0009: cites(2, 1, 1) })), []);
});

test('a document with no anchors at all yields nothing rather than throwing', () => {
  // The 17 documents without blocks, and any re-extracted one no decision cited.
  const g = group('para', 0, ['b0001', 'Text.']);
  assert.deepEqual(citedLines(g, null), []);
});

test('a rejoined paragraph reports each cited block separately', () => {
  // 69 of the 8,168 rendered paragraphs hold more than one cited block, and 57
  // of those share a vote between the two — summing them would count it twice,
  // so each block keeps its own panel and its own count.
  const g = group('para', 4, ['b0010', 'En mening som fortsätter'], ['b0011', 'på nästa sida.']);
  const lines = citedLines(g, summaryOf({ b0010: cites(3, 1, 2), b0011: cites(5, 5, 0) }));
  assert.deepEqual(lines.map((l) => l.id), ['b0010', 'b0011']);
  assert.deepEqual(lines.map((l) => l.cites.decisions.n), [3, 5]);
});

const flagged = (role, text) => {
  const g = group(role, 1, ['b0002', text]);
  return citedLines(g, summaryOf({ b0002: cites(4, 1, 3) }))[0].navigational;
};

test('a citation landing on a topic label is flagged as navigational', () => {
  // "EN STRAM MIGRATION" as a back-cover label promises nothing, and the panel
  // has to say so rather than let it read as evidence of a commitment.
  // valmanifest-2022-kd's five are roled h3, so role vocabulary alone is not it.
  assert.equal(flagged('label', 'EN STRAM MIGRATION'), true);
  assert.equal(flagged('h3', 'FLER JOBB FLER FÖRETAG'), true);
  assert.equal(flagged('h1', 'KAPITEL 1. Kristdemokratins värdegrund'), true);
});

test('a contents entry is navigation whatever it says', () => {
  // The one role where the text cannot argue otherwise — that is what a contents
  // list is.
  assert.equal(flagged('toc', 'Kampen mot kriminaliteten och våldet ska vinnas i varje del av landet.'), true);
});

test('a pledge set as a bold label is NOT flagged', () => {
  // The failure this rule exists to avoid: `label` is "a minor bold cluster at
  // or below body size", which in valmanifest-2022-s is the bullet list of the
  // party's actual promises and in -l its 60 numbered ones. Marking those
  // "promises nothing" would be a false claim on 1,163 of the 1,165 decisions
  // the flag reaches.
  assert.equal(flagged('label', '• Kraftigt öka antalet poliser på våra gator och torg.'), false);
  assert.equal(
    flagged('label', '13. En högre utbildning med högre ambitioner. Vi vill öka den lärarledda undervisningen.'),
    false,
  );
});

test('body text is never flagged, whatever it looks like', () => {
  for (const role of ['para', 'bullet', 'caption', 'unreadable']) {
    assert.equal(flagged(role, 'EN STRAM MIGRATION'), false, role);
  }
});

test('a heading that states something is not reduced to a label', () => {
  assert.equal(flagged('h2', 'Vi ska avskaffa fastighetsskatten.'), false);
  assert.equal(
    flagged('h2', 'Ett samhälle där varje människa räknas och där ingen lämnas efter av staten'),
    false,
  );
});

test('a pledge heading is not reduced to a label by dropping its full stop', () => {
  // The heading-side half of the same false claim. A heading omits the terminal
  // period by typographic convention, so testing punctuation alone marked 10 of
  // valmanifest-2022-m's and -s's headline pledges "promises nothing" — 96 of
  // the 180 (vote, line) pairs the flag then reached. These are the real lines,
  // verbatim and unpunctuated as the documents set them.
  for (const t of [
    'Statens utgifter ska minska',
    'Vi ska stötta, inte styra, jord- och skogsbruket',
    'Vi ska stoppa mäns våld mot kvinnor',
    'Du ska ha råd med elräkningen',
    'Vi ska stå upp för hbtq-personers rättigheter',
    'Bryt segregationen för att hålla ihop Sverige', // imperative, no finite verb
  ]) {
    assert.equal(flagged('h2', t), false, t);
  }
});

test('the flag follows the block, not the paragraph it was joined into', () => {
  // A label opening a page is rejoined onto the paragraph before it, and the two
  // are then one <p> — but the citation still landed on one block or the other.
  const g = group('para', 2, ['b0020', 'Brottsligheten ska bekämpas.'], ['b0021', 'EN TRYGG VARDAG', 'label']);
  const lines = citedLines(g, summaryOf({ b0020: cites(1, 1, 0), b0021: cites(1, 0, 1) }));
  assert.deepEqual(lines.map((l) => l.navigational), [false, true]);
});

// --- the chapter rail ---------------------------------------------------------

const doc = (...groups) => groups;

test('the rail lists h1 and h2 blocks, with their depth', () => {
  const chs = chapters(
    doc(
      group('h1', 5, ['b0100', 'KAPITEL 1. Värdegrunden']),
      group('para', 5, ['b0101', 'Brödtext.']),
      group('h2', 6, ['b0102', '1.1 Människovärdet']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => [c.level, c.text, c.id]), [
    [1, 'KAPITEL 1. Värdegrunden', 'b0100'],
    [2, '1.1 Människovärdet', 'b0102'],
  ]);
});

test('a chapter counts the cited lines under it, its own heading included', () => {
  const chs = chapters(
    doc(
      group('h1', 5, ['b0100', 'KAPITEL 1']),
      group('para', 5, ['b0101', 'Citerad rad.']),
      group('para', 5, ['b0102', 'Ociterad rad.']),
      group('h1', 7, ['b0103', 'KAPITEL 2']),
      group('para', 7, ['b0104', 'Citerad rad.']),
    ),
    summaryOf({ b0100: cites(1, 1, 0), b0101: cites(2, 0, 2), b0104: cites(3, 1, 2) }),
  );
  assert.deepEqual(chs.map((c) => c.lines), [2, 1]);
});

test('a rejoined heading is one entry, and links to its first block', () => {
  const chs = chapters(
    doc(group('h1', 8, ['b0200', 'KAPITEL 5. Ett medmänskligt samhälle'],
                       ['b0201', '– går solidaritet och effektivitet att förena?'])),
    null,
  );
  assert.equal(chs.length, 1);
  assert.equal(chs[0].id, 'b0200');
  assert.match(chs[0].text, /^KAPITEL 5\. .* förena\?$/);
});

test('cover-page titles are demoted, not listed as the first three chapters', () => {
  // partiprogram-kd-2015 opens "PRINCIPPROGRAM" / "ANTAGET VID
  // KRISTDEMOKRATERNAS" / "RIKSTING 2015" — all h1, none a chapter.
  const chs = chapters(
    doc(
      group('h1', 0, ['b0000', 'PRINCIPPROGRAM']),
      group('h1', 0, ['b0001', 'ANTAGET VID KRISTDEMOKRATERNAS']),
      group('h1', 2, ['b0002', 'KAPITEL 1. Värdegrunden']),
      group('para', 9, ['b0003', 'Sista sidan.']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => c.text), ['KAPITEL 1. Värdegrunden']);
});

test('a four-page leaflet keeps its first page — there the cover IS the content', () => {
  const chs = chapters(
    doc(
      group('h1', 0, ['b0000', 'Vårt Sverige kan bättre']),
      group('para', 3, ['b0001', 'Sista sidan.']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => c.text), ['Vårt Sverige kan bättre']);
});

test("the document's own contents list is not a chapter — the rail is that list", () => {
  const chs = chapters(
    doc(
      group('h1', 1, ['b0010', 'Innehållsförteckning']),
      group('h1', 1, ['b0011', 'INNEHÅLL']),
      group('h1', 2, ['b0012', 'KAPITEL 1']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => c.text), ['KAPITEL 1']);
});

test('a drop cap left behind by the column cut is not a chapter', () => {
  // partiprogram-m-2021 sets "M" and "P" as their own h1 blocks.
  const chs = chapters(
    doc(
      group('h1', 3, ['b0030', 'M']),
      group('h1', 3, ['b0031', 'Liberalkonservatismen']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => c.text), ['Liberalkonservatismen']);
});

test('a demoted heading gives its cited lines to the chapter it sits in', () => {
  const chs = chapters(
    doc(
      group('h1', 2, ['b0040', 'KAPITEL 1']),
      group('h1', 2, ['b0041', 'Ö']),
      group('para', 2, ['b0042', 'Brödtext.']),
    ),
    summaryOf({ b0041: cites(1, 1, 0), b0042: cites(1, 1, 0) }),
  );
  assert.deepEqual(chs.map((c) => [c.text, c.lines]), [['KAPITEL 1', 2]]);
});

test('a title set twice is one entry', () => {
  const chs = chapters(
    doc(
      group('h1', 1, ['b0050', 'PRINCIPPROGRAM 2015']),
      group('h1', 2, ['b0051', 'principprogram 2015']),
      group('h1', 3, ['b0052', 'KAPITEL 1']),
    ),
    null,
  );
  assert.deepEqual(chs.map((c) => c.text), ['PRINCIPPROGRAM 2015', 'KAPITEL 1']);
});

test('past the cap the rail keeps the h1 outline instead of scrolling', () => {
  // valmanifest-2022-m sets 116 headings and partiprogram-kd-2015 85.
  const groups = [];
  for (let i = 0; i < 12; i += 1) {
    groups.push(group('h1', i + 1, [`b1${i}`, `Kapitel ${i}`]));
    for (let j = 0; j < 5; j += 1) groups.push(group('h2', i + 1, [`b2${i}${j}`, `Avsnitt ${i}.${j}`]));
  }
  const capped = chapters(groups, null, { max: 20 });
  assert.equal(capped.length, 12);
  assert.ok(capped.every((c) => c.level === 1));
});

test('a collapsed h2 hands its cited lines up to its chapter, not into the bin', () => {
  // Filtering the h2s out dropped their counts with them, and the rail then
  // reported 28 cited lines for valmanifest-2022-m where its chapters hold 388.
  const groups = [];
  const tally = {};
  for (let i = 0; i < 12; i += 1) {
    groups.push(group('h1', i + 1, [`b1${i}`, `Kapitel ${i}`]));
    for (let j = 0; j < 5; j += 1) {
      const id = `b2${i}${j}`;
      groups.push(group('h2', i + 1, [id, `Avsnitt ${i}.${j}`]));
      tally[id] = cites(1, 1, 0);
    }
  }
  const capped = chapters(groups, summaryOf(tally), { max: 20 });
  // every chapter's five subsections are cited once each, and nothing is lost
  assert.deepEqual(capped.map((c) => c.lines), Array(12).fill(5));
  assert.equal(capped.reduce((n, c) => n + c.lines, 0), 60);
});

test('collapsing does not mutate the long rail it may have to fall back to', () => {
  // The h1s here are too few to outline the document, so `chapters` returns the
  // full list — which must not have been accumulated into on the way past.
  const groups = [];
  const tally = {};
  for (let i = 0; i < 3; i += 1) {
    groups.push(group('h1', i + 1, [`b1${i}`, `Kapitel ${i}`]));
    for (let j = 0; j < 9; j += 1) {
      const id = `b2${i}${j}`;
      groups.push(group('h2', i + 1, [id, `Avsnitt ${i}.${j}`]));
      tally[id] = cites(1, 1, 0);
    }
  }
  const chs = chapters(groups, summaryOf(tally), { max: 20 });
  assert.equal(chs.length, 30);
  assert.deepEqual(chs.filter((c) => c.level === 1).map((c) => c.lines), [0, 0, 0]);
});

test('...but not when the h1s alone would hide the structure', () => {
  // partiprogram-l-2023 has 6 h1 against 51 h2. Six entries is not an outline of
  // that document, it is a quarter of one, so the long rail is the lesser wrong.
  const groups = [];
  for (let i = 0; i < 3; i += 1) {
    groups.push(group('h1', i + 1, [`b1${i}`, `Kapitel ${i}`]));
    for (let j = 0; j < 9; j += 1) groups.push(group('h2', i + 1, [`b2${i}${j}`, `Avsnitt ${i}.${j}`]));
  }
  const chs = chapters(groups, null, { max: 20 });
  assert.equal(chs.length, 30);
});

// --- the rows a panel fetches -------------------------------------------------

const FIELDS = ['votering_id', 'parti', 'verdict', 'tier', 'utskott', 'datum', 'svag'];
const compact = (fields, anchors) => ({ slug: 'test', run_id: 'test-run', fields, anchors });
const ref = (vid, verdict, datum, extra = {}) => {
  const row = { votering_id: vid, parti: 'KD', verdict, tier: 'explicit',
                utskott: 'SfU', datum, svag: false, ...extra };
  return (fields) => fields.map((f) => row[f]);
};

test('a panel lists the decisions of its own block, newest first', () => {
  const rows = refRows(
    compact(FIELDS, [
      { block_id: 'b0001', offset: 0, length: 10, refs: [ref('V1', 'kept', '2023-04-12')(FIELDS)] },
      { block_id: 'b0002', offset: 0, length: 10, refs: [ref('V2', 'diverged', '2024-01-09')(FIELDS)] },
      { block_id: 'b0001', offset: 40, length: 10, refs: [ref('V3', 'diverged', '2025-02-03')(FIELDS)] },
    ]),
    'b0001',
  );
  assert.deepEqual(rows.map((r) => r.votering_id), ['V3', 'V1']);
  assert.deepEqual(rows.map((r) => r.verdict), ['diverged', 'kept']);
});

test('a vote quoting the same line twice is listed once', () => {
  // The number on the summary counts it once, and a panel that promises 1 and
  // lists 2 is the contradiction the reader would see.
  const rows = refRows(
    compact(FIELDS, [
      { block_id: 'b0001', offset: 0, length: 10, refs: [ref('V1', 'kept', '2023-04-12')(FIELDS)] },
      { block_id: 'b0001', offset: 60, length: 10, refs: [ref('V1', 'kept', '2023-04-12')(FIELDS)] },
    ]),
    'b0001',
  );
  assert.equal(rows.length, 1);
});

test('the field order is read from the payload, not assumed', () => {
  // anchors.REF_FIELDS is the payload's own header; hard-coding indices here
  // would put the date in the party column the day it changes.
  const shuffled = ['datum', 'svag', 'parti', 'utskott', 'verdict', 'tier', 'votering_id'];
  const [row] = refRows(
    compact(shuffled, [
      { block_id: 'b0001', offset: 0, length: 10, refs: [ref('V9', 'avstar', '2026-03-01')(shuffled)] },
    ]),
    'b0001',
  );
  assert.deepEqual(row, {
    votering_id: 'V9', parti: 'KD', verdict: 'avstar', tier: 'explicit',
    utskott: 'SfU', datum: '2026-03-01', svag: false,
  });
});

// --- against the committed export ---------------------------------------------

test('every rail entry links to an id the page actually renders', { skip }, () => {
  let checked = 0;
  for (const slug of slugs) {
    const rendered = renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug));
    const ids = new Set(rendered.groups.flatMap((g) => g.parts.map((p) => p.id)));
    for (const c of chapters(rendered.groups, getCorpusAnchors(slug))) {
      assert.ok(ids.has(c.id), `${slug}: rail links to #${c.id}, which is not rendered`);
      checked += 1;
    }
  }
  assert.ok(checked > 100, `only ${checked} rail entries checked`);
});

test('every cited block is a block the page renders', { skip }, () => {
  // An anchor on a block that never reaches the page would be a citation the
  // document silently loses — the same failure the build gate catches from the
  // other side.
  for (const slug of slugs) {
    const ids = new Set(groupBlocks(getCorpusBlocks(slug).blocks).flatMap((g) => g.parts.map((p) => p.id)));
    for (const id of Object.keys(getCorpusAnchors(slug).blocks)) {
      assert.ok(ids.has(id), `${slug}: ${id} is cited but not rendered`);
    }
  }
});

test('a panel promises exactly as many decisions as it then lists', { skip }, () => {
  // The summary count is built in Python (anchors.summarize), the list is built
  // in the browser by refRows() from public/data/anchors/. Two code paths over
  // two files, and a reader sees both — "12 decisions" opening onto 9 rows is
  // the failure this rules out.
  let blocks = 0;
  for (const slug of slugs) {
    const summary = getCorpusAnchors(slug);
    const fetched = JSON.parse(
      fs.readFileSync(path.join(ROOT, 'public', 'data', 'anchors', `${slug}.json`), 'utf-8'),
    );
    for (const [id, b] of Object.entries(summary.blocks)) {
      const rows = refRows(fetched, id);
      assert.equal(b.decisions.n, rows.length, `${slug}/${id}`);
      const tally = { kept: 0, diverged: 0, avstar: 0, franvarande: 0 };
      for (const r of rows) tally[r.verdict] += 1;
      assert.deepEqual(
        tally,
        { kept: b.decisions.kept, diverged: b.decisions.diverged,
          avstar: b.decisions.avstar, franvarande: b.decisions.franvarande },
        `${slug}/${id}: the bar and the list disagree`,
      );
      assert.ok(b.decisions.n <= b.refs, `${slug}/${id}: more decisions than citations`);
      blocks += 1;
    }
  }
  assert.ok(blocks > 4000, `only ${blocks} cited blocks checked`);
});

test('the document total counts a vote once however many lines it cited', { skip }, () => {
  for (const slug of slugs) {
    const summary = getCorpusAnchors(slug);
    const perBlock = Object.values(summary.blocks).reduce((n, b) => n + b.decisions.n, 0);
    assert.ok(
      summary.totals.decisions.n <= perBlock,
      `${slug}: document total exceeds the sum of its blocks`,
    );
    assert.ok(summary.totals.decisions.n <= summary.totals.refs);
  }
});

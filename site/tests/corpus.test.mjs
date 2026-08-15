// The document pages against the committed data in src/data/ — the loader, and
// the one guarantee that cannot be checked on fixtures: that every citation's
// #:~:text= deep link still finds its quote in the rendered page.
//
// These read `src/data/corpus/`, which `aidag export-site` writes. They skip
// rather than fail when it is absent, the way tests/test_corpus.py does.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.join(import.meta.dirname, '..');
process.chdir(ROOT); // lib/data.ts resolves src/data/ from cwd, as astro does

const { getCorpusBlocks, getCorpusDoc, quoteFragment } = await import('../src/lib/data.ts');
const { renderDoc, groupBlocks, stripInvisible } = await import('../src/lib/doctext.ts');

const CORPUS = path.join(ROOT, 'src', 'data', 'corpus');
const exported = fs.existsSync(CORPUS);
const slugs = exported
  ? fs.readdirSync(CORPUS).filter((f) => f.endsWith('.txt')).map((f) => f.replace(/\.txt$/, ''))
  : [];
const withBlocks = slugs.filter((s) => fs.existsSync(path.join(CORPUS, 'blocks', `${s}.json`)));
const skip = exported ? false : 'src/data/corpus not exported (run: uv run aidag export-site)';

test('the loader returns the blocks for a re-extracted document', { skip }, () => {
  const file = getCorpusBlocks('partiprogram-kd-2015');
  assert.ok(file, 'partiprogram-kd-2015 has a block file');
  assert.equal(file.slug, 'partiprogram-kd-2015');
  assert.ok(file.blocks.length > 100);
  const roles = new Set(['h1', 'h2', 'h3', 'para', 'bullet', 'label', 'toc', 'caption', 'unreadable']);
  for (const b of file.blocks) {
    assert.ok(roles.has(b.role), `${b.id}: unknown role ${b.role}`);
    assert.match(b.id, /^b\d+$/);
  }
});

test('the loader returns null for a document that has none', { skip }, () => {
  // The 16 budgetmotioner and Tidoavtalet are not re-extracted. Absence is the
  // normal case for them, and null is how the page knows to fall back.
  assert.equal(getCorpusBlocks('budgetmotion-v-202425'), null);
  assert.equal(getCorpusBlocks('tidoavtalet-2022'), null);
  assert.equal(getCorpusBlocks('no-such-document'), null);
});

test('every published document renders, from blocks or from text', { skip }, () => {
  let fromBlocks = 0;
  let fromText = 0;
  for (const slug of slugs) {
    const doc = renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug));
    assert.ok(doc.groups.length > 0, `${slug} rendered nothing`);
    if (doc.stats.source === 'blocks') fromBlocks += 1;
    else fromText += 1;
  }
  assert.equal(fromBlocks, withBlocks.length);
  assert.equal(fromBlocks + fromText, slugs.length);
});

test('mp-2013 still says where its undecodable headings were', { skip }, () => {
  const doc = renderDoc(getCorpusDoc('partiprogram-mp-2013'), getCorpusBlocks('partiprogram-mp-2013'));
  assert.equal(doc.stats.unreadable, 50);
});

test('valmanifest-2022-v is no longer one wall of text', { skip }, () => {
  // The document that had zero headings across 51k characters.
  const doc = renderDoc(getCorpusDoc('valmanifest-2022-v'), getCorpusBlocks('valmanifest-2022-v'));
  const headings = doc.groups.filter((g) => g.role === 'h1' || g.role === 'h2' || g.role === 'h3');
  assert.ok(headings.length >= 5, `only ${headings.length} headings`);
});

// --- the deep-link regression check ------------------------------------------
//
// A citation links to /dokument/<slug>/#:~:text=<fragment>, and the browser
// matches that fragment against the rendered text. Its matcher does not cross a
// block-level boundary, so every term of the fragment has to sit inside ONE
// rendered paragraph. `src/data/corpus/anchors/` gives the block and the span of
// every located citation, which is enough to rebuild each quote and check it.

/** One rendered document: its paragraphs, and the served string they came from.
 *
 *  `anchors.py` measures an offset inside a block, over blocks joined by single
 *  spaces in reading order — `extract_corpus.text_from_blocks` is what makes the
 *  served lines and the blocks the same thing. Rebuilding that string here is
 *  what lets an anchor be turned back into its quote. Placeholders are left out
 *  of it: an unreadable heading is not in the served text either. */
function rendered(slug) {
  const groups = groupBlocks(getCorpusBlocks(slug).blocks);
  const paragraph = new Map(); // block id -> index of the paragraph it renders in
  const start = new Map(); // block id -> its offset in the served string
  const pieces = [];
  let pos = 0;
  groups.forEach((g, i) =>
    g.parts.forEach((p) => {
      paragraph.set(p.id, i);
      if (!p.text) return;
      if (pieces.length) pos += 1; // the space that joins two served lines
      start.set(p.id, pos);
      pieces.push(p.text);
      pos += p.text.length;
    }),
  );
  return {
    served: pieces.join(' '),
    texts: groups.map((g) => g.parts.map((p) => p.text).join(' ')),
    paragraph,
    start,
  };
}

test('the rendered text is the text the agents were served', { skip }, () => {
  // The page tells the reader "this is what the AI agents read". It is only true
  // if the blocks it draws reassemble the .txt that `verify simulate` checks
  // every quote against — modulo whitespace, and modulo the invisible characters
  // no rendering can show.
  for (const slug of withBlocks) {
    const doc = rendered(slug);
    const txt = stripInvisible(getCorpusDoc(slug).replace(/^<!--[\s\S]*?-->\n/, ''))
      .replace(/\s+/g, ' ')
      .trim();
    assert.equal(doc.served, txt, `${slug} does not render its served text`);
  }
});

test('every citation deep link resolves inside one rendered paragraph', { skip }, () => {
  // A #:~:text= fragment is matched against the page, and the browser's matcher
  // will not cross a block-level boundary: each term has to sit inside one <p>.
  // 56 anchors quote across a paragraph the extraction cut in two, which is what
  // doctext.groupBlocks() rejoins — this is the check that says so.
  const dir = path.join(CORPUS, 'anchors');
  if (!fs.existsSync(dir)) return; // no run indexed yet — build-anchors has not run
  let checked = 0;
  const broken = [];
  // One anchor is not recoverable here and does not resolve on the current site
  // either: valmanifest-m's `b0198` runs into a block roled `bullet` that opens
  // with an en dash ("– samordningsnummer."). Admitting a dash-led continuation
  // would fuse every genuine "– " list in the corpus into its preceding
  // paragraph, which is a far larger wrong than one lost highlight. Listed, so a
  // second one cannot arrive unnoticed.
  const KNOWN = new Set(['valmanifest-2022-m/b0198: tillfälliga personnummer – samordningsnummer.']);
  for (const f of fs.readdirSync(dir)) {
    const slug = f.replace(/\.json$/, '');
    const summary = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
    const doc = rendered(slug);
    for (const [blockId, b] of Object.entries(summary.blocks)) {
      const from = doc.start.get(blockId);
      assert.notEqual(from, undefined, `${slug}/${blockId}: cited block is not rendered`);
      for (const a of b.anchors) {
        const quote = doc.served.slice(from + a.offset, from + a.offset + a.length);
        checked += 1;
        // Exactly what CasePage.astro puts in the URL, decoded back to terms.
        const terms = quoteFragment(quote).split(',').map(decodeURIComponent);
        const at = doc.paragraph.get(blockId);
        // The quote starts in that paragraph; a long one runs into the next few.
        const window = doc.texts.slice(at, at + 4);
        for (const term of terms) {
          const miss = `${slug}/${blockId}: ${term}`;
          if (!window.some((t) => t.includes(term)) && !KNOWN.has(miss)) broken.push(miss);
        }
      }
    }
  }
  assert.ok(checked > 1000, `only ${checked} anchors checked`);
  assert.deepEqual(broken, [], `${broken.length} of ${checked} anchors lost their deep link`);
});

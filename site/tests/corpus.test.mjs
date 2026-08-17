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

const { getCorpusBlocks, getCorpusDoc, getCorpusLexicon, quoteFragment } = await import(
  '../src/lib/data.ts'
);
const { renderDoc, groupBlocks, stripInvisible } = await import('../src/lib/doctext.ts');
const { citedLines } = await import('../src/lib/anchors.ts');

const CORPUS = path.join(ROOT, 'src', 'data', 'corpus');
const exported = fs.existsSync(CORPUS);
const slugs = exported
  ? fs.readdirSync(CORPUS).filter((f) => f.endsWith('.txt')).map((f) => f.replace(/\.txt$/, ''))
  : [];
const withBlocks = slugs.filter((s) => fs.existsSync(path.join(CORPUS, 'blocks', `${s}.json`)));
const skip = exported ? false : 'src/data/corpus not exported (run: uv run aidag export-site)';

// The citation index is a separate artifact with a separate pass behind it, so
// it needs its own skip: returning early from inside a test body instead reports
// a green sweep that checked nothing.
const ANCHORS = path.join(CORPUS, 'anchors');
const anchorSkip =
  skip || (fs.existsSync(ANCHORS) ? false : 'anchors not indexed (run: uv run aidag build-anchors)');

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
  // The plan's acceptance figures, pinned rather than derived: 23 re-extracted
  // documents and the 17 keeping formatCorpusDoc(). Deriving both from the
  // directory listing makes the counts agree with themselves and with nothing
  // else, which is exactly what a corpus half-exported would also do.
  assert.equal(slugs.length, 40);
  assert.equal(fromBlocks, 23);
  assert.equal(fromText, 17);
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

/** The same document as the PAGE draws it: contents blocks dropped, all-capital
 *  titles in sentence case.
 *
 *  `rendered()` above deliberately does not go through renderDoc — it is the
 *  round-trip back to the served .txt, and has to see the blocks untouched. But
 *  a deep link is matched against what the reader's browser holds, so the term
 *  sweep asks this one instead. Splitting them is the whole point: it is what
 *  makes "the agents' text" and "the reader's text" two checkable claims rather
 *  than one that quietly covers whichever the rendering happens to produce. */
function drawn(slug) {
  const groups = renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug), {
    lexicon: getCorpusLexicon(),
  }).groups;
  const paragraph = new Map();
  groups.forEach((g, i) => g.parts.forEach((p) => paragraph.set(p.id, i)));
  return { texts: groups.map((g) => g.parts.map((p) => p.text).join(' ')), paragraph };
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

// The quotes behind the exported spans. `summarize()` leaves them out of
// src/data/ on purpose — the page has the block text and the span recovers the
// quote from it — but that contract is only checkable with the quote in hand, so
// the sweep below reads the index the export was made from. Repo data, not site
// data, hence its own existence check.
const RESULTS = path.join(ROOT, '..', 'data', 'results', 'anchors');

/** The located quotes of one document, keyed by block, offset AND length.
 *
 *  All three: a decision quoting one sentence and another quoting that sentence
 *  plus the next are two anchors sharing a block and an offset, and 1,679 of the
 *  corpus's cited blocks carry such a pair. */
function quotesOf(slug, runId) {
  const p = path.join(RESULTS, runId, `${slug}.json`);
  if (!fs.existsSync(p)) return null;
  const out = new Map();
  for (const a of JSON.parse(fs.readFileSync(p, 'utf-8')).anchors) {
    const drawn = stripInvisible(a.quote).replace(/\s+/g, ' ').trim();
    out.set(`${a.block_id}:${a.offset}:${a.length}`, drawn);
  }
  return out;
}

test('every citation deep link resolves inside one rendered paragraph', { skip: anchorSkip }, () => {
  // A #:~:text= fragment is matched against the page, and the browser's matcher
  // will not cross a block-level boundary: each term has to sit inside one <p>.
  // 56 anchors quote across a paragraph the extraction cut in two, which is what
  // doctext.groupBlocks() rejoins — this is the check that says so.
  const dir = ANCHORS;
  let checked = 0;
  const broken = [];
  // One anchor is not recoverable here and does not resolve on the current site
  // either: valmanifest-m's `b0198` runs into a block roled `bullet` that opens
  // with an en dash ("– samordningsnummer."). Admitting a dash-led continuation
  // would fuse every genuine "– " list in the corpus into its preceding
  // paragraph, which is a far larger wrong than one lost highlight. Listed, so a
  // second one cannot arrive unnoticed.
  // The id moved b0198 -> b0191 when the rotated 'Valmanifest 2022' margin stamp
  // stopped being extracted: block ids are positional, and seven of the 39 stamps
  // sit above this line. The limitation itself is unchanged.
  const KNOWN = new Set(['valmanifest-2022-m/b0191: tillfälliga personnummer – samordningsnummer.']);
  const used = new Set();
  const wrong = [];
  const misparsed = [];
  // Blocks whose deep link matches only once case is folded — see the comment at
  // the term check. Pinned, not tolerated in bulk: the list growing means the
  // rendering has started changing text somewhere new.
  const needsFolding = new Set();
  let verified = 0;
  for (const f of fs.readdirSync(dir)) {
    const slug = f.replace(/\.json$/, '');
    const summary = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
    const doc = rendered(slug);
    const page = drawn(slug);
    const located = quotesOf(slug, summary.run_id);
    for (const [blockId, b] of Object.entries(summary.blocks)) {
      const from = doc.start.get(blockId);
      assert.notEqual(from, undefined, `${slug}/${blockId}: cited block is not rendered`);
      // …and still on the page after renderDoc has had its say. A cited block
      // dropped by the contents-list filter would otherwise vanish silently:
      // its panel, its votes and its deep link all go with it, and every
      // assertion below still passes because they read `doc`, not the page.
      assert.notEqual(
        page.paragraph.get(blockId),
        undefined,
        `${slug}/${blockId}: cited block is not drawn (dropped by renderDoc)`,
      );
      for (const a of b.anchors) {
        const at0 = from + a.offset;
        const quote = doc.served.slice(at0, at0 + a.length);
        checked += 1;
        // Is the span the RIGHT span? Slicing the quote out of the text and then
        // asking whether it occurs there is true by construction, so this is the
        // only part of the sweep that can see a wrong offset — and offsets do go
        // wrong: `valmanifest-2022-s` carries a literal BEL after 40 of its
        // bullet glyphs, kept in the served text and deleted by the page, and
        // measuring on the served text put 126 of its anchors one character late
        // ("raftigt öka antalet poliser") with everything below green.
        //
        // A span the index does not have is a failure, not a skip: the summary
        // IS `summarize()` of that file, so the two disagreeing about a span
        // means one of them is stale — which is the same wrong offset arriving
        // by another route, and the route a lookup-and-skip would hide.
        if (located) {
          const key = `${blockId}:${a.offset}:${a.length}`;
          const want = located.get(key);
          verified += 1;
          if (want === undefined) wrong.push(`${slug}/${key}: not in the index it was summarised from`);
          else if (quote !== want) wrong.push(`${slug}/${key}: ${JSON.stringify(quote.slice(0, 48))}`);
        }
        // Exactly what CasePage.astro puts in the URL, decoded back to terms.
        const encoded = quoteFragment(quote).split(',');
        const terms = encoded.map(decodeURIComponent);
        // Decoding first is what the term check below needs and is exactly what
        // hides a directive that never parses as terms at all: `text=` reads
        // `[prefix-,]textStart[,textEnd][,-suffix]`, and the browser applies that
        // `-` test to the ENCODED token. A term ending in a literal hyphen —
        // "mötes-", "grundskole-", which Swedish produces constantly — is taken
        // as a prefix, the rest as textStart, and the link matches nothing while
        // both halves still decode to text that is genuinely on the page.
        if (encoded[0].endsWith('-') || encoded[encoded.length - 1].startsWith('-')) {
          misparsed.push(`${slug}/${blockId}: ${encoded.join(',')}`);
        }
        const at = page.paragraph.get(blockId);
        // The quote starts in that paragraph; a long one runs into the next few.
        const window = page.texts.slice(at, at + 4);
        for (const term of terms) {
          const miss = `${slug}/${blockId}: ${term}`;
          if (window.some((t) => t.includes(term))) continue;
          // Not found as written. The one legitimate reason is the title casing:
          // a quote taken verbatim from a heading the source set in capitals no
          // longer matches the page byte-for-byte once the page draws it in
          // sentence case. `#:~:text=` is specified to match case-insensitively
          // (the spec's "find a string in range" folds case), so those links
          // still resolve — but the reliance is real, so it is counted and
          // pinned below rather than folded into the sweep. Anything that is
          // still missing after folding is broken, and anything found only after
          // folding must be a title this rendering recased and nothing else.
          if (window.some((t) => t.toLowerCase().includes(term.toLowerCase()))) {
            needsFolding.add(`${slug}/${blockId}`);
            continue;
          }
          if (KNOWN.has(miss)) used.add(miss);
          else broken.push(miss);
        }
      }
    }
  }
  assert.ok(checked > 1000, `only ${checked} anchors checked`);
  // ratchet, the way data/corpus/known-unrecovered.json is: an allowlist entry
  // that has stopped being needed masks the next regression at that block
  assert.deepEqual([...KNOWN].filter((k) => !used.has(k)), [], 'stale KNOWN entry');
  assert.deepEqual(broken, [], `${broken.length} of ${checked} anchors lost their deep link`);
  assert.deepEqual(misparsed, [], `${misparsed.length} of ${checked} fragments parse as prefix-/-suffix`);
  assert.deepEqual(wrong, [], `${wrong.length} of ${verified} spans recover the wrong text`);
  // Exactly the five back-cover topic labels of valmanifest-2022-kd, which are
  // the only cited blocks the source sets in full capitals. Every other one of
  // the 77,599 citations still matches the page byte-for-byte.
  assert.deepEqual(
    [...needsFolding].sort(),
    [
      'valmanifest-2022-kd/b0095',
      'valmanifest-2022-kd/b0096',
      'valmanifest-2022-kd/b0097',
      'valmanifest-2022-kd/b0098',
      'valmanifest-2022-kd/b0099',
    ],
    'the set of deep links relying on case-insensitive matching has changed',
  );
  // the span check is the load-bearing half; a missing results/ directory would
  // otherwise turn it off and leave a green sweep that checked only grouping
  assert.ok(verified > 1000, `only ${verified} spans checked against their quote`);
});

test('the printed contents list is dropped, and nothing cited goes with it', { skip: anchorSkip }, () => {
  // renderDoc() drops `toc` blocks on the block path because the chapter rail is
  // the same navigation, built from the same headings. That is only free while
  // no citation lands on one — so this measures the premise rather than trusting
  // it. Both numbers are zero today across 281 toc blocks in 10 documents; if a
  // re-extraction ever roles a cited line `toc`, this fails here instead of the
  // line disappearing off the page with its votes.
  let dropped = 0;
  let cited = 0;
  for (const slug of withBlocks) {
    const blocks = getCorpusBlocks(slug).blocks;
    const toc = new Set(blocks.filter((b) => b.role === 'toc').map((b) => b.id));
    dropped += toc.size;
    const p = path.join(ANCHORS, `${slug}.json`);
    if (!fs.existsSync(p)) continue;
    const summary = JSON.parse(fs.readFileSync(p, 'utf-8'));
    for (const id of Object.keys(summary.blocks)) if (toc.has(id)) cited += 1;
    // Nothing is drawn for them either — the assertion the page depends on.
    const drawnIds = drawn(slug).paragraph;
    for (const id of toc) assert.ok(!drawnIds.has(id), `${slug}/${id}: toc block still drawn`);
  }
  assert.equal(dropped, 281);
  assert.equal(cited, 0, `${cited} citations land on a contents-list block`);

  // The heading over the list goes with it, or it is left announcing contents
  // that are not there. 8 of the 10 documents print one; the other two set their
  // contents under no heading at all.
  //
  // A ratchet rather than a zero, because the zero is not the truth: these nine
  // set their contents list as ordinary headings and paragraphs, so the list
  // itself is still drawn and its heading belongs over it. See
  // doctext.dropContents() for why they are left alone rather than guessed at.
  // Listed so that a document LEAVING the set — its heading newly stranded over
  // no contents, or its real "Innehåll" chapter newly deleted — has to be looked
  // at rather than absorbed into a count.
  const KEEPS_A_PRINTED_LIST = [
    'partiprogram-c-2013',
    'partiprogram-kd-2025',
    'partiprogram-m-2021',
    'partiprogram-mp-2013',
    'partiprogram-mp-2025',
    'partiprogram-sd-2019',
    'partiprogram-v-2016',
    'valmanifest-2022-m',
  ];
  const stillDrawn = withBlocks.filter((slug) =>
    drawn(slug).texts.some((t) => /^inneh[åa]ll/i.test(t.trim())),
  );
  assert.deepEqual(stillDrawn, KEEPS_A_PRINTED_LIST);
});

test('all-capital titles are drawn in sentence case, acronyms intact', { skip }, () => {
  // The rule is corpus-attested, not guessed (doctext.ts), and these are the
  // cases that decide whether it is: a proper noun the corpus capitalises, an
  // ordinary word the citing document itself never uses in prose, a glossary
  // acronym, and a title the extraction split across two blocks.
  const lexicon = getCorpusLexicon();
  const titleOf = (slug, id) => {
    const doc = renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug), { lexicon });
    for (const g of doc.groups) {
      if (g.parts.some((p) => p.id === id)) return g.parts.map((p) => p.text).join(' ');
    }
    return null;
  };
  const has = (slug, text) =>
    renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug), { lexicon }).groups.some(
      (g) => g.parts.map((p) => p.text).join(' ') === text,
    );

  // "VÅRT SVERIGE KAN BÄTTRE" — Sverige is capitalised 858 times in the corpus's
  // own prose and never lowercased, so it keeps its capital while the rest drops.
  assert.ok(has('valmanifest-2022-s', 'Vårt Sverige kan bättre'));
  // "SÅ FÅR VI BUKT MED SVERIGES" — `bukt` appears nowhere in valmanifest-2022-kd
  // outside its capitals, which is why the lexicon is corpus-wide and not
  // per-document: on this document's own evidence it stayed "BUKT".
  assert.ok(has('valmanifest-2022-kd', 'Så får vi bukt med Sveriges'));
  // A glossary term the corpus cannot attest is left alone rather than guessed.
  assert.ok(has('partiprogram-kd-2015', 'OSSE'));
  assert.ok(has('valmanifest-2022-sd', 'HBT+'));
  // Numbered chapter titles keep their number.
  assert.ok(has('partiprogram-l-2021', '4.4 Jordbruk, skogsbruk och fiske'));
  // A title set over two blocks is cased as one, so the second half is not
  // re-capitalised: "TVÅ PERSPEKTIV" + "– EN VERKLIGHET".
  assert.equal(titleOf('partiprogram-v-2016', 'b0034'), 'Två perspektiv – en verklighet');
  // Swedish letters are letters. `\w` is ASCII even under the `u` flag, and the
  // first cut of this used `[^\W\d_]`: "VÅRT SVERIGE" tokenised as V/RT/SVERIGE
  // and came out "VÅrt Sverige" on eight of the nine documents.
  assert.ok(!has('valmanifest-2022-s', 'VÅrt Sverige'));

  // And nothing that was already mixed case is touched, anywhere in the corpus.
  for (const slug of withBlocks) {
    const before = new Map();
    for (const g of groupBlocks(getCorpusBlocks(slug).blocks))
      for (const p of g.parts) before.set(p.id, p.text);
    for (const g of renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug), { lexicon }).groups) {
      for (const p of g.parts) {
        const was = before.get(p.id);
        if (p.text === was) continue;
        assert.equal(p.text.toLowerCase(), was.toLowerCase(), `${slug}/${p.id}: wording changed`);
        assert.equal(was, was.toUpperCase(), `${slug}/${p.id}: recased a mixed-case block`);
      }
    }
  }
});

test('the navigational flag reads the same corpus the same way Python does', { skip: anchorSkip }, () => {
  // `is_navigational` exists twice — anchors.py for the report, anchors.ts for
  // the page — and the duplication is only safe if both read the committed
  // corpus to the same number. tests/test_anchors.py::TestNavigationParity says
  // this is "the site measured independently in TypeScript"; without this test
  // that sentence was true of a comment, not of anything that runs, and a
  // TS-only edit to NAV_MAX_WORDS or the label regexes would put "promises
  // nothing" on real pledges with nothing failing.
  //
  // 6 blocks / 84 (vote, line) pairs — docs/topic-label-citations.md.
  //
  // Rendered the way the PAGE renders — lexicon and all — even though Python
  // reads the raw block text. The two agreeing is the extra thing this says: the
  // title casing must not move the flag, and it cannot, because every test in
  // `isNavigational` folds case or counts words. Rendering it raw here would
  // make that a claim about code nothing runs.
  let blocks = 0;
  let blockDecisions = 0;
  for (const f of fs.readdirSync(ANCHORS)) {
    const slug = f.replace(/\.json$/, '');
    const summary = JSON.parse(fs.readFileSync(path.join(ANCHORS, f), 'utf-8'));
    const doc = renderDoc(getCorpusDoc(slug), getCorpusBlocks(slug), {
      lexicon: getCorpusLexicon(),
    });
    for (const g of doc.groups) {
      for (const line of citedLines(g, summary)) {
        if (!line.navigational) continue;
        blocks += 1;
        blockDecisions += line.cites.decisions.n;
      }
    }
  }
  assert.equal(blocks, 6);
  // 83, was 84: the p6 sign-inversion repair re-ran 56 decisions and deleted 24,
  // so the vote tally moved with the run. `blocks` is unchanged at 6 — the rule
  // still reaches the same six lines, which is what the parity actually guards.
  // Both sides are edited together on purpose; if only one moves, that is drift.
  assert.equal(blockDecisions, 83);
});

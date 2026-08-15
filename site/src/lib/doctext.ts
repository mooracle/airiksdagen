// Render-time formatting for the party corpus documents shown at /dokument/<slug>/.
//
// WHERE THE TEXT COMES FROM, AND WHAT IS LEFT TO DO HERE
// ------------------------------------------------------
// `site/src/data/corpus/*.txt` is written by export_site.export_corpus() and holds
// `corpus.normalize()` output — the same text `documents_for()` feeds the agents,
// and the text `repair-citations` / `verify simulate` check quotes against. So the
// character-level damage is already gone before this module sees a document:
// f-ligatures, soft hyphens, BOMs, NBSPs, dot leaders, page furniture, and the
// undecodable display-font headings in mp-2013. The repairs below are kept as a
// cheap safety net (a stale export, or a doc added outside that path) — on current
// data they are no-ops.
//
// WHAT THIS MODULE IS ACTUALLY FOR
// -------------------------------
// Structure. `normalize()` is line-oriented and leaves the extraction's line
// breaks in place; the page used to render them under `white-space: pre-wrap`, so
// every document appeared as a ragged ~90-column block that ignored the reader's
// viewport, with headings, lists and tables of contents indistinguishable from
// body text. This module recovers paragraphs, headings and lists from those lines.
//
// Rejoining wrapped lines is lossless: the break IS the wrap, so a single space is
// exactly what belonged there. Explicitly hyphenated breaks ("demokrati-" /
// "sering") are closed up, which is equally unambiguous.
//
// WHAT IS DELIBERATELY NOT REPAIRED
// --------------------------------
// Words split with the hyphen already dropped — "Infla" / "tionen", "skatte" /
// "höjningar". 4,565 of these sit in the budgetmotion files, whose extractor turns
// every HTML tag into a newline (fetch_corpus.budget_narrative), so an inline
// <span> inside a word splits it and the de-hyphenation regex has no hyphen left
// to match. Only ~650 can be resolved with confidence; Swedish compounding makes
// the rest ambiguous (`allt`/`mer` is both "alltmer" and "allt mer", `vapen`/`samt`
// must stay two words). Guessing would change the text, so they are joined with a
// plain space like any other break and left visible. The fix belongs in the
// extractor, which still has the markup that says where the hyphen was.
// Note p6 (full-v4) does not show budgetmotion to agents at all — only valmanifest
// and partiprogram, which have 2 and 254 such breaks respectively.

/** Human title for a corpus slug. Shared by /om/#dokument and the document page,
 *  which used to carry two copies — the page's handled only tidoavtalet and
 *  valmanifest, so every partiprogram and budgetmotion page was titled with its
 *  raw slug ("partiprogram-sd-2019"). */
export function docTitle(
  slug: string,
  opts: { sv: boolean; partyName: (code: string) => string },
): string {
  const { sv, partyName } = opts;
  if (slug === 'tidoavtalet-2022') return 'Tidöavtalet (2022)';
  const rmYears = (y: string) => (y.length === 6 ? `${y.slice(0, 4)}/${y.slice(4)}` : y);
  let m: RegExpMatchArray | null;
  if ((m = slug.match(/^valmanifest-2022-(\w+)$/)))
    return sv ? `Valmanifest 2022 — ${partyName(m[1])}` : `2022 election manifesto — ${partyName(m[1])}`;
  if ((m = slug.match(/^partiprogram-(\w+)-(\d+)$/)))
    return sv ? `Partiprogram ${m[2]} — ${partyName(m[1])}` : `Party programme ${m[2]} — ${partyName(m[1])}`;
  if ((m = slug.match(/^budgetmotion-(\w+)-(\d+)$/)))
    return sv
      ? `Budgetmotion ${rmYears(m[2])} — ${partyName(m[1])}`
      : `Shadow budget ${rmYears(m[2])} — ${partyName(m[1])}`;
  return slug;
}

export interface DocProvenance {
  title: string;
  note: string | null;
  href: string | null;
  hrefLabel: string | null;
}

// --- the structured corpus ---------------------------------------------------
//
// 23 of the 40 documents are re-extracted by `aidag extract-corpus` and ship as
// `src/data/corpus/blocks/<slug>.json`: one record per paragraph, with the role
// recovered from the PDF's own font metrics and column geometry rather than
// guessed back from line lengths. Those render from their roles; the remaining
// 17 (16 budgetmotioner + Tidöavtalet) have no block file and keep the
// `formatCorpusDoc()` path below until the budgetmotion parser lands.

export type DocRole =
  | 'h1'
  | 'h2'
  | 'h3'
  | 'para'
  | 'bullet'
  | 'label'
  | 'toc'
  | 'caption'
  | 'unreadable';

export interface CorpusBlock {
  id: string;
  role: DocRole;
  page: number;
  size: number;
  bold: boolean;
  text: string;
}

export interface CorpusBlockFile {
  slug: string;
  /** 'pdf', or 'text' for valmanifest-2022-c, whose PDF has no text layer. */
  source: string;
  pages: number;
  /** Running headers/footers stripped at extraction — kept for auditing. */
  dropped: string[];
  blocks: CorpusBlock[];
}

/** One block as rendered: its own id, so a citation anchor can address it. */
export interface RenderPart {
  /** Block id, or null on the `formatCorpusDoc()` fallback path. */
  id: string | null;
  role: DocRole;
  /** 0-based source page, null on the fallback path. */
  page: number | null;
  text: string;
}

/** One paragraph on the page. Usually one block; more when the extraction split
 *  a paragraph at a column or page break (see `joinsOn`). */
export interface RenderGroup {
  role: DocRole;
  /** 0-based source page, null on the fallback path. */
  page: number | null;
  parts: RenderPart[];
}

export interface RenderedDoc {
  provenance: DocProvenance | null;
  groups: RenderGroup[];
  stats: {
    /** 'blocks' when the structured extraction was used, 'text' for fallback. */
    source: 'blocks' | 'text';
    unreadable: number;
  };
}

export type BlockKind = 'heading' | 'subheading' | 'para' | 'bullet' | 'toc' | 'unreadable';

export interface DocBlock {
  kind: BlockKind;
  text: string;
}

export interface FormattedDoc {
  provenance: DocProvenance | null;
  blocks: DocBlock[];
  /** Counts for the build log / debugging — not rendered. */
  stats: { hardWrapped: boolean; wrapWidth: number; unreadable: number; dehyphenated: number };
}

// --- character repair -------------------------------------------------------

const LIGATURES: [RegExp, string][] = [
  [/ﬀ/g, 'ff'],
  [/ﬁ/g, 'fi'],
  [/ﬂ/g, 'fl'],
  [/ﬃ/g, 'ffi'],
  [/ﬄ/g, 'ffl'],
  [/ﬅ/g, 'st'], // long-s + t
  [/ﬆ/g, 'st'],
];

// C0/C1 controls (keeping \n and \t), plus invisible formatting characters that
// PDF extraction leaves behind. All are artifacts; none carry text.
const INVISIBLE =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u00AD\u200B-\u200D\u2060\uFEFF]/g;
const HARD_SPACES = /[\u00A0\u2007\u202F]/g;

function repairChars(raw: string): string {
  let t = raw.replace(/\r\n?/g, '\n');
  for (const [re, sub] of LIGATURES) t = t.replace(re, sub);
  return t.replace(INVISIBLE, '').replace(HARD_SPACES, ' ');
}

/** Drop the characters that never render, so a caller matching against the page
 *  matches what the page actually shows. `valmanifest-2022-s` carries a literal
 *  BEL (U+0007) after 40 of its bullet glyphs — it is in the source PDF, so it
 *  is in the corpus text and in 51 committed citation quotes, but it is stripped
 *  before anything is drawn. A `#:~:text=` fragment built from the raw quote
 *  encodes it as %07 and can never match. */
export function stripInvisible(s: string): string {
  return s.replace(INVISIBLE, '').replace(HARD_SPACES, ' ');
}

// Letters that legitimately occur in this corpus (Swedish plus the accents that
// show up in loanwords and foreign names). Anything else alphabetic is a glyph
// that failed to map back to Unicode during PDF extraction.
const SANE_LETTERS =
  /[A-Za-zÅÄÖåäöÉéÈèÊêËëÜüÁáÀàÂâÍíÎîÓóÔôÒòØøÆæÑñÇçÝýÞþŠšŽžŁłĆćČčŚśŃńĐđ]/;
const ANY_LETTER = /\p{L}/u;

/** True for a line that is mostly undecodable glyph soup (the MP 2013 programme's
 *  display-font section headings, 234 characters across ~40 lines). Such a line is
 *  surfaced as a placeholder rather than deleted — the text is unreadable, but
 *  silently dropping it would misrepresent the document. */
function isUnreadable(line: string): boolean {
  const letters = [...line].filter((c) => ANY_LETTER.test(c));
  if (letters.length < 3) return false;
  const bad = letters.filter((c) => !SANE_LETTERS.test(c)).length;
  return bad / letters.length > 0.3;
}

// --- provenance -------------------------------------------------------------

/** The corpus files carry their source as an HTML comment on line 1, e.g.
 *  `<!-- Budgetmotion 2024/25:1924 (V) | inlämnad 2024-10-03 | dok_id HC021924 -->`
 *  Under `pre-wrap` this rendered on the page as literal `<!-- ... -->` text.
 *  31 of the 40 files have one. */
function parseProvenance(firstLine: string): DocProvenance | null {
  const m = firstLine.match(/^<!--\s*(.*?)\s*-->$/);
  if (!m) return null;
  const parts = m[1].split('|').map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return null;

  let href: string | null = null;
  let hrefLabel: string | null = null;
  const notes: string[] = [];
  for (const part of parts.slice(1)) {
    const dok = part.match(/^dok_id\s+(\S+)$/);
    if (/^https?:\/\//.test(part)) {
      href = part;
      hrefLabel = 'källa';
    } else if (dok) {
      href = `https://data.riksdagen.se/dokument/${dok[1]}.html`;
      hrefLabel = dok[1];
    } else {
      notes.push(part);
    }
  }
  return { title: parts[0], note: notes.join(' · ') || null, href, hrefLabel };
}

// --- block assembly ---------------------------------------------------------

const BULLET = /^\s*(?:[•▪◦·]|[-–—*])\s+\S/;
// A section number, not just any leading digits: at most two digits per component
// and a capitalised word after it. Matching bare `\d+` here broke paragraphs mid
// number — "…ökat med cirka 57" / "000 personer" was read as heading "000".
const NUMBERED = /^\s*(\d{1,2}(?:\.\d{1,2})*)[.)]?\s+\p{Lu}/u;
const SENTENCE_END = /[.!?:;»”"']$/;

function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
}

/** Merge the physical lines of one blank-line-delimited group into logical blocks.
 *
 *  Hard-wrapped files: a line that filled the column continued onto the next, so
 *  join it. A short line ended its paragraph — and short standalone lines are how
 *  headings, list items and tables of contents appear.
 *
 *  Paragraph-per-line files (budgetmotion, valmanifest): the newline itself is the
 *  paragraph break, so only join when the line stopped mid-sentence, which marks
 *  the extractor's spurious breaks around dropped hyphens and inline emphasis. */
function mergeLines(
  lines: string[],
  hardWrapped: boolean,
  wrapWidth: number,
  counters: { dehyphenated: number },
): string[] {
  const out: string[] = [];
  let buf = '';
  let lastPhysical = '';

  for (const line of lines) {
    if (!buf) {
      buf = line.trim();
      lastPhysical = line;
      continue;
    }
    const next = line.trim();
    // Unambiguous: a trailing hyphen with a lowercase continuation is a
    // hyphenation point, so drop the hyphen and close the word up.
    if (/[a-zåäöéü]-$/.test(buf) && /^[a-zåäöéü]/.test(next)) {
      buf = buf.slice(0, -1) + next;
      counters.dehyphenated += 1;
      lastPhysical = line;
      continue;
    }
    const cont = hardWrapped
      ? lastPhysical.trim().length >= 0.72 * wrapWidth
      : !SENTENCE_END.test(buf) && !BULLET.test(next) && !NUMBERED.test(next);
    if (cont) {
      buf += ' ' + next;
    } else {
      out.push(buf);
      buf = next;
    }
    lastPhysical = line;
  }
  if (buf) out.push(buf);
  return out;
}

function classify(text: string): BlockKind {
  if (isUnreadable(text)) return 'unreadable';
  if (BULLET.test(text)) return 'bullet';
  const words = text.split(/\s+/).length;
  const short = text.length <= 90 && words <= 14;
  if (!short || SENTENCE_END.test(text.replace(/[:;]$/, ''))) return 'para';
  const num = text.match(NUMBERED);
  if (num) return num[1].includes('.') ? 'subheading' : 'heading';
  const letters = text.replace(/[^\p{L}]/gu, '');
  if (letters.length >= 3 && letters === letters.toUpperCase()) return 'heading';
  // Short, unpunctuated, starts with a capital — a section title.
  if (/^[A-ZÅÄÖ]/.test(text)) return 'subheading';
  return 'para';
}

/** A run of 4+ consecutive heading-like blocks is a table of contents, not 4+
 *  real headings. Rendering it as a list keeps the page's heading outline honest. */
function collapseTocRuns(blocks: DocBlock[]): DocBlock[] {
  const isHeadingish = (b: DocBlock) => b.kind === 'heading' || b.kind === 'subheading';
  const out = [...blocks];
  let i = 0;
  while (i < out.length) {
    if (!isHeadingish(out[i])) {
      i += 1;
      continue;
    }
    let j = i;
    while (j < out.length && isHeadingish(out[j])) j += 1;
    if (j - i >= 4) for (let k = i; k < j; k += 1) out[k] = { ...out[k], kind: 'toc' };
    i = j;
  }
  return out;
}

export function formatCorpusDoc(raw: string): FormattedDoc {
  const text = repairChars(raw);
  const allLines = text.split('\n');

  const provenance = parseProvenance(allLines[0].trim());
  const body = provenance ? allLines.slice(1) : allLines;

  const lens = body.filter((l) => l.trim()).map((l) => l.trim().length).sort((a, b) => a - b);
  const wrapWidth = percentile(lens, 0.9);
  // The two families are cleanly separated by this: the wrapped files sit at
  // p90 73-134, the paragraph-per-line files at 223-1107.
  const hardWrapped = wrapWidth > 0 && wrapWidth < 150;

  const counters = { dehyphenated: 0 };
  const blocks: DocBlock[] = [];
  let group: string[] = [];
  const flush = () => {
    if (!group.length) return;
    for (const merged of mergeLines(group, hardWrapped, wrapWidth, counters)) {
      if (merged) blocks.push({ kind: classify(merged), text: merged });
    }
    group = [];
  };
  for (const line of body) {
    if (line.trim()) group.push(line);
    else flush();
  }
  flush();

  const final = collapseTocRuns(blocks);
  return {
    provenance,
    blocks: final,
    stats: {
      hardWrapped,
      wrapWidth,
      unreadable: final.filter((b) => b.kind === 'unreadable').length,
      dehyphenated: counters.dehyphenated,
    },
  };
}

// --- rendering from blocks --------------------------------------------------

/** Blocks `corpus.normalize()` removes on its way to the served .txt: bare page
 *  numbers and rules. Mirrors `extract_corpus.normalize_drops()`, which is the
 *  documented drop set the block -> .txt round-trip is asserted modulo. The other
 *  half of that set is the undecodable display-font lines, which arrive here as
 *  role `unreadable` and are surfaced as placeholders instead of dropped. */
const SERVED_DROP = /^[\p{Nd}\s.,%‑‒–—•·|-]+$/u;

/** Roles that end a paragraph by definition, so nothing joins across them. */
const STRUCTURAL = new Set<DocRole>(['h1', 'h2', 'h3', 'toc', 'caption', 'unreadable']);
const HEADINGS = new Set<DocRole>(['h1', 'h2', 'h3']);
const TERMINAL = /[.!?:;»”"')\]]$/;
const CONTINUATION = /^[a-zåäöéüàèïóáíøæ]/;
/** A heading may also continue into a dash — "KAPITEL 5. Ett medmänskligt
 *  samhälle" / "– går solidaritet och effektivitet att förena?". A dash opener is
 *  not allowed for prose, where it is how half this corpus writes a list item. */
const HEAD_CONTINUATION = /^(?:[a-zåäöéüàèïóáíøæ]|[–—-]\s*[a-zåäöéü])/;

/** True when the block carrying `text` finishes the paragraph `prev` started.
 *
 *  The extraction splits a block at every column and page break, and
 *  `docx._runs_on()` rejoins only the ones it can prove (same face, adjacent
 *  pages). What it leaves behind is paragraphs cut in two mid-sentence — and a
 *  citation quoting across that cut would have its `#:~:text=` fragment straddle
 *  two <p> elements, which the browser's matcher will not cross. 56 of full-v4's
 *  anchors do exactly that. Rejoining here is presentation-only: the served .txt,
 *  the quotes and the block ids are untouched, and the reader gets the paragraph
 *  the party actually wrote.
 *
 *  A continuation is either plain prose or the first thing on a new page. A
 *  `label` or `bullet` beginning mid-page begins something new — KD 2015's
 *  marginal glossary alternates term (`label`) and definition (`para`), neither
 *  ending in a full stop, and would otherwise chain 39 dictionary entries into
 *  one paragraph. One that opens a page is the tail the extractor cut off.
 *
 *  Headings are their own case: a title set over two lines arrives as two blocks
 *  at the same size on the same page (101 of them), and rendering it as two
 *  headings breaks both the outline and any citation quoting across the line. */
function joinsOn(prev: CorpusBlock, prevText: string, block: CorpusBlock, text: string): boolean {
  if (HEADINGS.has(prev.role) && block.role === prev.role) {
    if (block.page !== prev.page || block.size !== prev.size) return false;
    return !TERMINAL.test(prevText) && HEAD_CONTINUATION.test(text);
  }
  if (STRUCTURAL.has(prev.role) || STRUCTURAL.has(block.role)) return false;
  if (block.role !== 'para' && block.page === prev.page) return false;
  return !TERMINAL.test(prevText) && CONTINUATION.test(text);
}

/** Blocks in reading order -> the paragraphs the page draws. */
export function groupBlocks(blocks: CorpusBlock[]): RenderGroup[] {
  const groups: RenderGroup[] = [];
  // The block behind the last rendered part — dropped page numbers never become
  // one, so they cannot break a paragraph in two.
  let prevBlock: CorpusBlock | null = null;
  for (const b of blocks) {
    // A heading whose glyphs never mapped back to Unicode (mp-2013's 50 display
    // headings). It is not in the served text — the reader is told a heading is
    // there rather than shown 40 characters of soup, and rather than nothing.
    if (b.role === 'unreadable') {
      groups.push({
        role: 'unreadable',
        page: b.page,
        parts: [{ id: b.id, role: 'unreadable', page: b.page, text: '' }],
      });
      prevBlock = b;
      continue;
    }
    const text = repairChars(b.text).replace(/\s+/g, ' ').trim();
    if (!text || SERVED_DROP.test(text)) continue;
    const part = { id: b.id, role: b.role, page: b.page, text };
    const last = groups[groups.length - 1];
    const prev = last?.parts[last.parts.length - 1];
    if (last && prev && prevBlock && joinsOn(prevBlock, prev.text, b, text)) {
      last.parts.push(part);
    } else {
      groups.push({ role: b.role, page: b.page, parts: [part] });
    }
    prevBlock = b;
  }
  return groups;
}

// --- title casing ------------------------------------------------------------
//
// A party document sets its chapter titles in the face its designer chose, and
// nine of the 23 chose full capitals: "REDO FÖR EN NY REGERING", "1.1
// LIBERALISMEN", 35 of them in `partiprogram-s-2025` alone. That is typography,
// not wording — the same title is set in caps on the cover and in mixed case in
// the contents list — so rendering it verbatim shouts at the reader in a page
// that is otherwise prose.
//
// Undoing it is not `toLowerCase()`. Sentence case needs to know which words are
// proper nouns ("VÅRT SVERIGE KAN BÄTTRE" is "Vårt Sverige kan bättre", not
// "Vårt sverige…") and which are acronyms ("EU", "OSSE", "HBT+"), and guessing
// either would put words in a party's mouth that it did not write — the failure
// this project exists to avoid, arriving through the presentation layer.
//
// So nothing is guessed: the casing comes from the corpus's own prose. Every
// word the 23 documents set in mixed case is counted, and a capitalised title
// word is restored to whichever form those documents attest. `Sverige` is
// capitalised 858 times mid-sentence and never lowercased; `vid` is lowercased
// 286 times; `EU` appears 574 times in capitals. Words the corpus never sets in
// mixed case at all fall to lowercase, which is why the rule is applied to
// TITLES only — a heading is the one place where an unattested word is far more
// likely to be an ordinary word the corpus happens not to repeat than a
// hidden acronym.

/** Word-shaped token: letters only, so "1.1" and "(S)" are punctuation around
 *  one, not part of it. `\p{L}` rather than `[^\W\d_]`, because JavaScript's `\w`
 *  stays ASCII even under the `u` flag: the latter cuts "VÅRT" into "V" and "RT"
 *  and the casing below reassembles it as "VÅrt". */
const LEX_WORD = /\p{L}+/gu;
const SENTENCE_BOUNDARY = /[.!?:]$/;

/** How the corpus's own prose sets each word: capitalised mid-sentence (a proper
 *  noun), lowercased (an ordinary word), or in full capitals (an acronym). */
export interface CasingLexicon {
  cap: Map<string, number>;
  low: Map<string, number>;
  up: Map<string, number>;
}

/** True for text the source set in full capitals. Three letters is the floor:
 *  below it "I" and "(S)" are not evidence of anything. */
function isAllCaps(text: string): boolean {
  const letters = [...text].filter((c) => /\p{L}/u.test(c));
  if (letters.length < 3) return false;
  const s = letters.join('');
  return s === s.toUpperCase() && s !== s.toLowerCase();
}

const bump = (m: Map<string, number>, k: string) => m.set(k, (m.get(k) ?? 0) + 1);

/** Count how the given documents set every word they use.
 *
 *  All-caps blocks are excluded from the count — they are the input to the
 *  rule, and letting them vote would make a title that shouts its own evidence
 *  for shouting. A word in first position is not counted as capitalised either:
 *  every sentence starts with one, and taking that as proof of a proper noun
 *  capitalises half the language. */
export function casingLexicon(files: Iterable<CorpusBlockFile>): CasingLexicon {
  const lex: CasingLexicon = { cap: new Map(), low: new Map(), up: new Map() };
  for (const file of files) {
    for (const b of file.blocks) {
      if (isAllCaps(b.text)) continue;
      for (const m of b.text.matchAll(LEX_WORD)) {
        const w = m[0];
        const k = w.toLowerCase();
        if (w.length > 1 && w === w.toUpperCase()) {
          bump(lex.up, k);
          continue;
        }
        if (w[0] !== w[0].toLowerCase()) {
          const before = b.text.slice(0, m.index).trimEnd();
          if (before && !SENTENCE_BOUNDARY.test(before)) bump(lex.cap, k);
        } else {
          bump(lex.low, k);
        }
      }
    }
  }
  return lex;
}

/** The form the corpus attests for one word of a title.
 *
 *  Both thresholds are two occurrences, not one: a single stray capital is how
 *  "SÅ FÅR VI BUKT MED SVERIGES" became "…vi Bukt med…" on the way here, off one
 *  mid-sentence "Bukt" in 9,409 blocks. */
function attested(word: string, lex: CasingLexicon): string {
  const k = word.toLowerCase();
  const cap = lex.cap.get(k) ?? 0;
  const low = lex.low.get(k) ?? 0;
  const up = lex.up.get(k) ?? 0;
  if (up >= 2 && up >= cap + low) return word; // acronym — leave the capitals
  if (cap >= 2 && cap > low) return word[0] + word.slice(1).toLowerCase();
  return word.toLowerCase();
}

/** A block that is one short word is a term, not a title: `partiprogram-kd-2015`
 *  ends in a glossary whose entries are single `label` blocks reading "CDI",
 *  "EPP", "IMF", "OSSE", "WTO", and `valmanifest-2022-sd` heads a section "HBT+".
 *  Sentence-casing those produces "Cdi" and "Hbt+". The corpus cannot attest them
 *  — a glossary defines a word precisely because the prose does not use it — so
 *  the rule declines to touch them rather than guess, at the cost of leaving
 *  KD's four-letter cover word "REDO" in capitals. */
const TERM_MAX_LETTERS = 4;

/** One title, in sentence case — or unchanged when it is not in full capitals.
 *
 *  Length is preserved exactly (case mapping is 1:1 for every character in this
 *  corpus), which is what lets a title split across two blocks be cased as one
 *  string and sliced back apart. The caller checks it. */
export function sentenceCase(text: string, lex: CasingLexicon): string {
  if (!isAllCaps(text)) return text;
  const words = [...text.matchAll(LEX_WORD)];
  if (words.length === 1 && words[0][0].length <= TERM_MAX_LETTERS) return text;
  let out = '';
  let at = 0;
  let first = true;
  for (const m of words) {
    out += text.slice(at, m.index);
    let w = attested(m[0], lex);
    if (first) {
      w = w[0].toUpperCase() + w.slice(1);
      first = false;
    }
    out += w;
    at = m.index + m[0].length;
  }
  return out + text.slice(at);
}

/** Roles whose text is a title rather than something the document argues. */
const TITLE_ROLES = new Set<DocRole>(['h1', 'h2', 'h3', 'label']);

/** "Innehåll", "Innehållsförteckning", "INNEHÅLL:" — the heading over a printed
 *  contents list. Same test as `anchors.CONTENTS`, which keeps the entry out of
 *  the rail for the same reason. */
const CONTENTS_HEADING = /^inneh[åa]ll/i;

/** Roles the word can arrive in. `caption` is in here because it is what
 *  `partiprogram-v-2024` roles both of its contents headings — a heading is only
 *  an `h1` if the geometry says so, and a small one over a list does not. */
const CONTENTS_ROLES = new Set<DocRole>(['h1', 'h2', 'h3', 'label', 'caption']);

/** Drop the printed contents list, and the heading that introduces it.
 *
 *  The heading has to go with the list or it is left standing over the next
 *  chapter, announcing contents that are not there — `valmanifest-2022-s` reads
 *  "Innehåll:" and then straight into "Vårt Sverige kan bättre". Only when the
 *  very next thing IS the list, though: half the programmes in this corpus set
 *  their contents as ordinary headings rather than `toc` blocks, and there the
 *  same word is a real heading over real text.
 *
 *  Those nine keep their printed contents list, and that is the deliberate half
 *  of this. Nothing in the roles separates their entries from prose, so the only
 *  way to reach them is to guess — and the rule that suggests itself, "an entry
 *  repeats a heading found later", was measured against the corpus and is not
 *  safe: it stops three entries into `partiprogram-c-2013`'s list, leaving half
 *  of it drawn, and in `partiprogram-sd-2019` it runs on past the list and takes
 *  the document's real first chapter heading with it. Deleting a party's own
 *  words to tidy a page is the wrong side to err on, so the untidy list stays. */
function dropContents(groups: RenderGroup[]): RenderGroup[] {
  const text = (g: RenderGroup) => g.parts.map((p) => p.text).join(' ').trim();
  const says = (g: RenderGroup) => CONTENTS_ROLES.has(g.role) && CONTENTS_HEADING.test(text(g));
  /** Is this heading the one over the printed list?
   *
   *  "The next group is a `toc`" is not quite the test: `partiprogram-v-2024`
   *  sets the word twice over each of its two lists — once as a `caption` and
   *  once as an `h1` — so the first of the pair has a heading between it and the
   *  entries. Repeats of the same word are skipped; anything else answers no. */
  const introduces = (i: number): boolean => {
    for (let j = i + 1; j < groups.length; j += 1) {
      if (groups[j].role === 'toc') return true;
      if (!says(groups[j])) return false;
    }
    return false;
  };
  const out: RenderGroup[] = [];
  for (let i = 0; i < groups.length; i += 1) {
    if (groups[i].role === 'toc') continue;
    if (says(groups[i]) && introduces(i)) continue;
    out.push(groups[i]);
  }
  return out;
}

/** Sentence-case one paragraph's title, across all the blocks it was split into.
 *
 *  Per group, not per block: 101 titles in this corpus are set over two lines and
 *  arrive as two blocks, and casing them separately would capitalise the second
 *  half's first word — "REDO FÖR" / "EN NY REGERING" as "Redo för" / "En ny
 *  regering". The group is cased as the one string it renders as, then sliced
 *  back on the parts' own lengths. */
function caseGroup(group: RenderGroup, lex: CasingLexicon): RenderGroup {
  if (!TITLE_ROLES.has(group.role)) return group;
  if (!group.parts.every((p) => TITLE_ROLES.has(p.role))) return group;
  const joined = group.parts.map((p) => p.text).join(' ');
  const cased = sentenceCase(joined, lex);
  if (cased === joined) return group;
  // Case mapping is 1:1 across this corpus, and the slicing below depends on it.
  // A future character where it is not (ß -> SS) leaves the title in capitals
  // rather than cutting the parts at the wrong offsets.
  if (cased.length !== joined.length) return group;
  let at = 0;
  const parts = group.parts.map((p) => {
    const text = cased.slice(at, at + p.text.length);
    at += p.text.length + 1; // the joining space
    return { ...p, text };
  });
  return { ...group, parts };
}

/** The fallback path's kinds, in the block vocabulary. `heading`/`subheading`
 *  keep their current depth — the page draws h1 as <h2> and h2 as <h3>, so the
 *  17 documents without blocks render byte-for-byte as they do today. */
const KIND_ROLE: Record<BlockKind, DocRole> = {
  heading: 'h1',
  subheading: 'h2',
  para: 'para',
  bullet: 'bullet',
  toc: 'toc',
  unreadable: 'unreadable',
};

/** One document, ready to draw: from its blocks when it has them, from the flat
 *  text when it does not. `raw` is the served .txt either way — the block file
 *  carries no provenance header, and that header is the page's source credit.
 *
 *  `lexicon` turns on the title casing above. It is optional because it is
 *  corpus-wide — the page passes `getCorpusLexicon()`, and a caller holding one
 *  document renders it as the extraction left it.
 *
 *  WHAT THE BLOCK PATH DROPS
 *  -------------------------
 *  Its `toc` blocks: the contents list the document printed for a reader holding
 *  the PDF. The page builds its own from the same headings (the sticky rail, see
 *  `anchors.chapters`), so the printed one is the same navigation a second time,
 *  minus the page numbers that made it work — and set as prose it reads as 51
 *  headings the document does not have. 281 blocks across 10 documents.
 *
 *  Nothing is lost with them: no citation in full-v4 lands on a `toc` block, and
 *  no located quote's span so much as overlaps one, so the annotation layer and
 *  every `#:~:text=` deep link are untouched. `site/tests/corpus.test.mjs` holds
 *  both to zero, because the day a citation does land on one this has to become
 *  a visible failure rather than a line that quietly stops rendering.
 *
 *  The 17 documents without blocks keep theirs. Their contents lists are guessed
 *  back from line lengths by `collapseTocRuns()` rather than read off the PDF's
 *  own geometry, and they have no rail to replace them with — `chapters()` needs
 *  block ids, which the fallback path has none of. */
export function renderDoc(
  raw: string,
  file: CorpusBlockFile | null,
  opts: { lexicon?: CasingLexicon } = {},
): RenderedDoc {
  const flat = file ? null : formatCorpusDoc(raw);
  let groups: RenderGroup[] = file
    ? dropContents(groupBlocks(file.blocks))
    : flat!.blocks.map((b) => ({
        role: KIND_ROLE[b.kind],
        page: null,
        parts: [{ id: null, role: KIND_ROLE[b.kind], page: null, text: b.text }],
      }));
  if (opts.lexicon) groups = groups.map((g) => caseGroup(g, opts.lexicon!));
  const provenance = flat
    ? flat.provenance
    : parseProvenance(repairChars(raw).split('\n', 1)[0].trim());
  return {
    provenance,
    groups,
    stats: {
      source: file ? 'blocks' : 'text',
      unreadable: groups.filter((g) => g.role === 'unreadable').length,
    },
  };
}

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

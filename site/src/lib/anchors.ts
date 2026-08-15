// The citation index, read from the page's point of view.
//
// `aidag build-anchors` walks every citation in a run, locates its quote in the
// re-extracted corpus and records a ref against the block it landed in. Two
// halves reach the site (the split is `anchors.py`'s page-weight decision):
//
//   src/data/corpus/anchors/<slug>.json   per-block aggregates — read at build
//                                         time, rendered into the page
//   public/data/anchors/<slug>.json       the ref rows behind them — fetched by
//                                         the browser when a panel is opened
//
// This module is the build-time half: the types, and the two questions the
// document page asks of them — "what does this paragraph carry" and "what is in
// this chapter".
//
// COUNTS: DECISIONS, NOT CITATIONS
// --------------------------------
// A block's headline number is `decisions.n`, not `refs`. One vote may quote two
// spans of the same line — 1,679 of full-v4's 4,715 cited blocks carry such a
// pair — and the page's sentence is "N votes leaned on this line", which is a
// count of votes. `refs` stays available for the citation total, and the ref list
// the browser fetches is deduplicated the same way, so the panel can never
// contradict the number on its own summary.
import type { DocRole, RenderGroup } from './doctext';

/** Distinct deciding votes, split by what they then did on the floor. */
export interface DecisionTally {
  n: number;
  kept: number;
  diverged: number;
  avstar: number;
  franvarande: number;
}

/** One located quote's span inside its block, with its citation tallies. */
export interface AnchorSpan {
  offset: number;
  length: number;
  refs: number;
  kept: number;
  diverged: number;
  avstar: number;
  franvarande: number;
}

export interface BlockCites {
  /** citations (a vote quoting the line twice counts twice) */
  refs: number;
  kept: number;
  diverged: number;
  avstar: number;
  franvarande: number;
  /** citations flagged `svag` — supporting but generic (blocklist.WEAK_LIST) */
  svag: number;
  /** distinct votes; the number the page shows */
  decisions: DecisionTally;
  /** evidence tier per distinct vote */
  tiers: Record<string, number>;
  /** citing party per distinct vote — one party per document, in practice */
  parties: Record<string, number>;
  anchors: AnchorSpan[];
}

export interface AnchorSummary {
  slug: string;
  run_id: string;
  totals: {
    anchors: number;
    refs: number;
    kept: number;
    diverged: number;
    avstar: number;
    franvarande: number;
    decisions: DecisionTally;
  };
  blocks: Record<string, BlockCites>;
}

/** One cited line, ready to annotate: the block, its role, and its tallies.
 *
 *  Per block rather than per rendered paragraph. `groupBlocks()` rejoins the
 *  paragraphs the extraction cut at a column or page break, and 69 of the 8,168
 *  rendered paragraphs end up holding more than one cited block — 57 of those
 *  share a vote between the two, which summing would count twice. Each block
 *  keeps its own panel, whose number then matches its own ref list exactly. */
export interface CitedLine {
  id: string;
  role: DocRole;
  cites: BlockCites;
  /** the citation landed on a navigation label, which promises nothing */
  navigational: boolean;
}

const BULLET_GLYPH = /^\s*[•▪◦·]/;
const STATES_SOMETHING = /[.!?]["»”']?$/;

const NAV_ROLES = new Set<DocRole>(['h1', 'h2', 'h3', 'label', 'toc']);

/** True for a citation that landed on something naming a topic rather than
 *  stating anything — a contents entry, a chapter title, a bare label like "EN
 *  STRAM MIGRATION". Such a quote is verbatim from the document and still cannot
 *  show that a commitment was broken, which is what the panel says.
 *
 *  Role alone is not the test, and assuming it was got this wrong first time
 *  round. `label` is "a minor bold cluster at or below body size", which in
 *  `partiprogram-kd-2015` is a back-cover topic list but in `valmanifest-2022-s`
 *  is the bold bullet list of the party's actual pledges ("• Kraftigt öka
 *  antalet poliser…") and in `valmanifest-2022-l` its 60 numbered ones. Flagging
 *  those would have put "promises nothing" against 1,163 of the 1,165 decisions
 *  the flag reaches — a false claim, and the kind this project exists to avoid.
 *
 *  So the text has to read as a label too: no bullet glyph, no sentence-ending
 *  punctuation, and short. `toc` is exempt from all three — a contents entry is
 *  navigation whatever it says, that being what a contents list is. */
function isNavigational(role: DocRole, text: string): boolean {
  if (role === 'toc') return true;
  if (!NAV_ROLES.has(role)) return false;
  const t = text.trim();
  if (BULLET_GLYPH.test(t) || STATES_SOMETHING.test(t)) return false;
  return t.split(/\s+/).length <= 8;
}

/** The cited blocks of one rendered paragraph, in reading order. */
export function citedLines(group: RenderGroup, summary: AnchorSummary | null): CitedLine[] {
  if (!summary) return [];
  const out: CitedLine[] = [];
  for (const part of group.parts) {
    const cites = part.id ? summary.blocks[part.id] : undefined;
    if (part.id && cites) {
      out.push({
        id: part.id,
        role: part.role,
        cites,
        navigational: isNavigational(part.role, part.text),
      });
    }
  }
  return out;
}

// --- the fetched half ---------------------------------------------------------
//
// public/data/anchors/<slug>.json: the same refs as row arrays keyed by a
// declared `fields` header, because the seven field names would otherwise repeat
// 77,599 times and be the larger half of the payload. Read in the browser, when
// a reader opens a panel.

export interface CompactRefs {
  slug: string;
  run_id: string;
  fields: string[];
  anchors: { block_id: string; offset: number; length: number; refs: unknown[][] }[];
}

export interface RefRow {
  votering_id: string;
  parti: string;
  verdict: string;
  tier: string | null;
  utskott: string | null;
  datum: string | null;
  svag: boolean;
}

/** The decisions behind one block's ratio bar: newest first, one row per vote.
 *
 *  Deduplicated per (vote, block) for the same reason `summarize()` counts
 *  decisions rather than citations — a vote quoting two spans of the line is one
 *  vote, and the panel's own summary says so. The two have to agree: a panel
 *  promising 12 decisions and then listing 9 is the failure this rules out. */
export function refRows(payload: CompactRefs, blockId: string): RefRow[] {
  const at: Record<string, number> = {};
  payload.fields.forEach((f, i) => { at[f] = i; });
  const seen = new Map<string, RefRow>();
  for (const a of payload.anchors) {
    if (a.block_id !== blockId) continue;
    for (const r of a.refs) {
      const row = {
        votering_id: String(r[at.votering_id]),
        parti: String(r[at.parti]),
        verdict: String(r[at.verdict]),
        tier: (r[at.tier] ?? null) as string | null,
        utskott: (r[at.utskott] ?? null) as string | null,
        datum: (r[at.datum] ?? null) as string | null,
        svag: Boolean(r[at.svag]),
      };
      const key = `${row.votering_id}|${row.parti}`;
      if (!seen.has(key)) seen.set(key, row);
    }
  }
  return [...seen.values()].sort((a, b) => String(b.datum).localeCompare(String(a.datum)));
}

export interface Chapter {
  /** the id of the block the heading starts in — the rail's link target */
  id: string;
  text: string;
  /** 1 for an h1, 2 for an h2 */
  level: number;
  /** cited lines in the chapter, its own heading included */
  lines: number;
}

const HEADINGS: Record<string, number> = { h1: 1, h2: 2 };

/** The document's own contents list. The rail IS that list, and every programme
 *  in this corpus sets one — rendered as headings rather than `toc` blocks in
 *  half of them, so it arrives here as a chapter called "Innehållsförteckning"
 *  followed by every chapter title a second time. */
const CONTENTS = /^inneh[åa]ll/i;

/** True for a heading that names the document rather than a chapter of it.
 *
 *  Cover pages are typographically identical to chapter openings — a large face,
 *  a short line — so the extraction roles them `h1` and they arrive at the top of
 *  the rail as "PRINCIPPROGRAM", "ANTAGET VID KRISTDEMOKRATERNAS", "RIKSTING
 *  2015". The page already shows the document's title above the text; repeating
 *  it three times as the first three chapters is worse than not having them. A
 *  document of four pages or fewer is a leaflet whose first page IS content, so
 *  the rule does not apply there.
 *
 *  The length floor catches what the column geometry leaves behind: `m-2021`'s
 *  drop caps ("M", "P") and the fragment of a cover title split across two
 *  blocks ("ING"). */
function isFurniture(text: string, page: number | null, pages: number): boolean {
  if (text.length < 4) return true;
  if (CONTENTS.test(text)) return true;
  return pages > 4 && page === 0;
}

/** The sticky rail's entries: the document's chapters, and how much of each one
 *  the votes actually leaned on.
 *
 *  The count is CITED LINES, not votes. Votes cannot be summed across blocks
 *  without counting a vote that cited three lines of one chapter three times,
 *  and the per-chapter number is a measure of how much of the chapter is under
 *  citation anyway — the exact vote total for the whole document sits at the top
 *  of the rail, where `totals.decisions` makes it exact.
 *
 *  `max` caps the rail: `valmanifest-2022-m` sets 116 headings and
 *  `partiprogram-kd-2015` 85, which is a scrollbar, not a table of contents. Past
 *  the cap the h2s are dropped — but only when the h1s alone still make an
 *  outline (`partiprogram-l-2023` has 6 h1 against 51 h2, and reducing it to six
 *  entries would hide the document's structure rather than summarise it). */
export function chapters(
  groups: RenderGroup[],
  summary: AnchorSummary | null,
  opts: { max?: number; minTopLevel?: number } = {},
): Chapter[] {
  const { max = 60, minTopLevel = 8 } = opts;
  const pages = groups.reduce((n, g) => Math.max(n, (g.page ?? 0) + 1), 0);
  const out: Chapter[] = [];
  for (const g of groups) {
    const level = HEADINGS[g.role];
    const cited = citedLines(g, summary).length;
    const text = level ? g.parts.map((p) => p.text).join(' ').trim() : '';
    const last = out[out.length - 1];
    // A rejected heading is not dropped, it is demoted: its cited lines belong
    // to the chapter it sits in, exactly as a paragraph's would. Same for a
    // repeat of the heading above it — cover titles are routinely set twice.
    const opens =
      level &&
      g.parts[0]?.id &&
      text &&
      !isFurniture(text, g.page, pages) &&
      text.toLowerCase() !== last?.text.toLowerCase();
    if (opens) {
      out.push({ id: g.parts[0].id as string, text, level, lines: cited });
      continue;
    }
    if (last) last.lines += cited;
  }
  if (out.length <= max) return out;
  const top = out.filter((c) => c.level === 1);
  return top.length >= minTopLevel ? top : out;
}

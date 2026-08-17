# Citations that land on a topic label

Measured 2026-08-17 on `full-v4` (p6, 20,288 decisions, 77,642 citations), from the
committed anchor index. Reproduce with:

```sh
uv run aidag navigation-report --run-id full-v4 [--out report.json]
```

The question: a citation quotes a party document verbatim, and the site draws a
kept-vs-diverged bar beside it. How often is the quoted line not a commitment at all,
but a heading or a topic label — something that names a subject and promises nothing?

**Answer: 6 blocks, 83 citations, 82 distinct votes — 0.12% of the 69,360
(vote, line) pairs in the corpus.** The number that matters more is the one it is not:
on the role vocabulary alone it reads **1,161**, and 1,159 of those are genuine pledges.

---

## 1. What the votes actually cite

Every cited block in the run, by the role the extraction assigned it:

| role | cited blocks | citations | (vote, line) pairs |
|---|---:|---:|---:|
| `para` | 3,856 | 58,704 | 51,187 |
| `bullet` | 775 | 17,330 | 16,742 |
| `label` | 56 | 1,218 | 1,161 |
| `h2` | 20 | 145 | 145 |
| `h3` | 6 | 122 | 122 |
| `caption` | 2 | 3 | 3 |
| **total** | **4,715** | **77,522** | **69,360** |

`toc` is absent, and that is a result rather than a gap: the corpus holds **281 `toc`
blocks and not one of them is cited**. The contents lists are never quoted, so the role
this finding was expected to be about contributes nothing to it.

(120 further citations are blank — `repair-citations` withdrew them — which is why the
citation column sums to 77,522 rather than 77,642.)

## 2. Why `label` is the wrong question

The plan asked for "citations landing on `label`/`toc` blocks". Taken literally that is
**56 blocks / 1,161 (vote, line) pairs**, and it would be a false claim on almost all of
them:

| document | blocks | votes | what the `label` blocks are |
|---|---:|---:|---|
| `valmanifest-2022-s` | 38 | 917 | the party's own pledge bullets — `• Krafttag för att stoppa hedersrelaterat våld…` |
| `valmanifest-2022-l` | 17 | 128 | its 60 numbered pledges |
| `partiprogram-v-2016` | 1 | 2 | a section label |

`label` is a **typographic** class — "a minor bold cluster at or below body size"
(`docx.style_clusters`). Two parties set their manifesto promises in exactly that face.
Marking those "promises nothing" would assert the opposite of what the document says,
which is the failure mode this project exists to avoid.

Nor does widening to every navigational role fix it: on `h1`/`h2`/`h3`/`label`/`toc` the
count is 82 blocks / 1,428 pairs, and the pledge lists are still in it.

## 3. The rule, and the 6 lines it finds

A cited line is navigation when its **role and its text** both say so: a navigational
role, no bullet glyph, ≤ 8 words, and not stating anything. `toc` is exempt from the text
test — a contents entry is navigation whatever it says. The rule is
`anchors.is_navigational()`, mirrored in `site/src/lib/anchors.ts:isNavigational` for
the per-block flag the document pages draw.

**The ≤ 8 is measured, not a round number.** At 9 the rule reaches three more blocks,
and two of them are `valmanifest-2022-l` pledges — blocks that begin
"21. Bekämpa hedersbrott och hedersförtryck. Parallella samhällen med odemokratiska…",
where the extraction has fused a numbered commitment with the start of its body text.
Marking those is the same false claim as the `label` role test, at smaller scale. At no
cap at all it takes eight of them.

The price is one known miss in the safe direction. `valmanifest-2022-kd`'s `b0098` —
"BRA SKOLA EN STRAM MIGRATION MINSKADE KLIMATUTSLÄPP INTERNATIONELLT ANSVARSTAGANDE",
41 votes — is the sixth entry of the same back-cover topic list as the five the rule does
find, set in the same 10.9pt bold `h3`, and goes unflagged because the extraction fused
several labels into one 9-word block. So one spread shows the flag on five lines and not
on the sixth. That is a missed annotation, which is the error this rule is built to
prefer: every threshold here is set so that when it is wrong, it says nothing rather than
says something false about a party's pledge.

**"States anything" is a clause test, not a punctuation test**, and getting that wrong is
the same false claim entering by the other door. A topic label is a noun phrase; a pledge
is a clause, and a clause needs a verb. Headings drop the terminal period by typographic
convention, so testing punctuation alone flagged ten of `valmanifest-2022-m`'s and `-s`'s
headline pledges — "Vi ska stoppa mäns våld mot kvinnor", "Statens utgifter ska minska",
"Bryt segregationen för att hålla ihop Sverige" — as lines that promise nothing. Those
are commitments set as headings, and they were **96 of the 180 (vote, line) pairs** the
flag then reached, more than half of the finding. So the text test looks for the finite
verb that fills a Swedish declarative (`ska`, `vill`, `kommer`, `är`, …) and for the bare
imperative stem at the head of the line, and a line carrying either states something.

| document | block | role | votes | text |
|---|---|---|---:|---|
| `valmanifest-2022-kd` | b0097 | h3 | 33 | STÄRKT CIVILSAMHÄLLE FÖRBÄTTRAD INTEGRATION FLER BOSTÄDER |
| `valmanifest-2022-kd` | b0096 | h3 | 29 | BÄTTRE OMSORG FÖR UTSATTA SJÄLVBESTÄMMANDE VID FUNKTIONSNEDSÄTTNING |
| `valmanifest-2022-kd` | b0094 | h3 | 9 | FLER JOBB FLER FöRETAG |
| `valmanifest-2022-kd` | b0099 | h3 | 8 | JÄMSTÄLLDHET PÅ RIKTIGT |
| `valmanifest-2022-kd` | b0095 | h3 | 3 | STARKARE FAMILJER |
| `partiprogram-v-2016` | b0104 | label | 2 | Den politiska demokratin – ofullgången men ovärderlig |

By party: **KD 81 · V 2**. By document: `valmanifest-2022-kd` 5 blocks,
`partiprogram-v-2016` 1. (The lower-case ö in `FöRETAG` is verbatim — KD's display font
maps that glyph so, and the committed text carries it. Pre-existing character damage, out
of this plan's scope.)

What survives is a narrow, specific phenomenon rather than a general one: KD's five are
the back-cover topic list this plan's Overview opens with, one spread of a single
manifesto — six entries on the page, of which the rule flags five (see the word-cap note
in §3 for the sixth). In the final extraction they carry the role `h3`, not `label`, which is the
direct reason the flag could not be a role test. The error the rule now avoids in both
directions — 1,163 pledges on the `label` side, 96 vote-lines on the heading side — is
an order of magnitude larger than the finding itself.

## 4. What the flag is worth

The 84 citations, by the evidence tier the decision claimed and the gap verdict it
produced:

| | |
|---|---|
| tier | `off_axis` 44 · `extrapolated` 30 · **`explicit` 10** |
| verdict | `diverged` 79 · `kept` 5 |
| already `svag` (`blocklist.WEAK_LIST`) | **0** |

The 10 are the sharp end: an `explicit` tier asserts *the party wrote this down as a
commitment*, and for these 10 votes the line it points at is "STARKARE FAMILJER" or
"JÄMSTÄLLDHET PÅ RIKTIGT" — a subject heading. That is 11.9% of the flagged citations
against 19.9% `explicit` corpus-wide, so the model does lean on headings less confidently
than on prose, but not so much less that the claim can be left unmarked. 79 of the 84 sit
under a `diverged` verdict, where the "broken promise" reading is strongest and a heading
can least support it — a concentration sharper than the whole-corpus split, and the
reason the remaining set is still worth marking.

The final row is the reason this is a distinct phenomenon: **none of these citations is
already caught by `WEAK_LIST`.** Generic boilerplate and topic labels are different
failures — a boilerplate quote is broadly applicable, a topic label is narrow and
specific and simply makes no promise.

## 5. `WEAK_LIST`: assessed, and not proposed

The plan's fallback was to register the offending phrases in `blocklist.WEAK_LIST`, which
would mark any citation of them `svag`. Every one of the 6 phrases was scored against
the bar `blocklist.py:21-26` states — "long and distinctive enough that either-direction
containment cannot catch a genuine policy quote" — on two tests:

- **against the committed citations**: which of the 16,723 distinct quotes the entry
  would match, in either containment direction, across *every* party's document of the
  same class (an entry names a document class, not a slug). **0 false positives.**
- **against the documents**: whether the phrase occurs verbatim anywhere in another
  party's document of that class, which needs no committed quote to exist to become one.
  **0 occurrences.**

So all 6 pass, and none is proposed anyway. Three reasons, in order of weight:

1. **`svag` would make a claim that is not true of them.** It renders as "supporting but
   generic". These quotes are the opposite of generic — "BÄTTRE OMSORG FÖR UTSATTA
   SJÄLVBESTÄMMANDE VID FUNKTIONSNEDSÄTTNING" is highly specific. What they cannot carry
   is a *commitment*, and that is what the per-block flag says instead ("citat ur en
   rubrik" / "quoted from a heading").
2. **The exact mechanism already exists and is exact.** Task 8b marks these per block,
   per document, from the role and the text of the line the citation actually landed on.
   A containment rule over a document class is a blunter instrument for a job that is
   already done precisely.
3. **Clean today is not clean tomorrow.** Both tests are measurements over 23 documents
   and one run. `config.py`'s own TRAP note records that every party has already replaced
   its pinned programme edition once; a `WEAK_LIST` entry is not re-validated when a new
   edition lands, whereas the per-block flag is re-derived from the corpus on every build.
   "STARKARE FAMILJER" is two words, and a two-word entry surviving a corpus change is
   luck, not design.

The assessment is not a one-off: `weak_list_candidates()` runs it on every invocation of
the report, so if a future run's flagged set does contain a phrase that meets the bar,
the report says so with the false positives named.

## 6. How this is held

- `aidag navigation-report --run-id <run>` recomputes everything above, and `--out`
  writes it as JSON.
- `tests/test_anchors.py::TestNavigationParity` pins **6 blocks / 83 citations / 82
  votes** and the 1,161-vs-83 gap as a ratchet against the committed run, and asserts
  that no `Vi ska …` heading and no block of `valmanifest-2022-m` is among them.
- `tests/test_anchors.py::TestNavigationRule` and `site/tests/anchors.test.mjs` assert the
  same rule case for case in both languages, because it is written twice — once for the
  report, once for the page. Both carry the ten real pledge headings *unpunctuated*, which
  is the fixture the punctuation-only version was missing.
- `site/tests/corpus.test.mjs` re-measures the 6 / 84 in TypeScript against the committed
  export, so the duplicated rule cannot drift in one language only.

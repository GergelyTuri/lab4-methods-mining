# Full-name (non-initials) CRediT statement audit (read-only)

Scope: for every (paper, target author) pair currently sitting at Tier 3
(`position_heuristic`) or Tier 2 (`methods_text_inference`) in
`manifest.db`/`*_attribution.json`, checked whether that paper's
`_main.txt` contains an Author Contributions / CRediT section that
names the target author by **full name** (first + last, not initials)
— the QQIBRBDR pattern (`"Zhenrui Liao, Software, Investigation..."`).
**No files were modified** — this is a report only.

## Headline result

**Only 1 of the 14 candidate papers — QQIBRBDR — shows the genuine
full-name pattern**, and it affects **3 of its 3** Tier-3 target-author
pairs (Zhenrui Liao, Wenke Li, James B Priestley), not just the one
(Liao) already known. QQIBRBDR is also the corpus's **only** eLife
paper (1 of 1 in the full 84-row manifest, not just among Tier 2/3
candidates), so "correlates with eLife" is true but resting on a single
data point — not something a broader claim about eLife papers in
general can be built on from this corpus alone, even though it matches
eLife's documented editorial policy of following CRediT's own
recommended full-name format.

The other 13 candidate papers turned out to show **different, unrelated
defects** that happen to produce the same downstream symptom (a real,
findable statement that still lands at Tier 2/3) — worth documenting
since they surfaced during the same read-through, but they are not the
pattern this audit was scoped to find, and are flagged separately below
rather than folded into the headline count.

## Method

For each candidate (paper, author) pair:

1. Located any Author Contributions / CRediT section in `_main.txt`
   (searching case-insensitively for `author contributions`,
   `authorcontributions`, and `CRediT`-adjacent phrasing, since several
   papers in this corpus glue words together — see
   `outputs/word_gluing_audit.md`).
2. Read the surrounding text directly (not via a scripted matcher) to
   determine: is the target author named there at all, and if so, in
   what form (full name vs. initials vs. not present)?

This was done by direct reading for all 14 papers rather than building
a second automated scanner — with only 14 candidates, manual
verification was more reliable than trying to generalize a regex, and
the word-gluing audit already showed how easily an automated text
heuristic can misfire on this corpus.

## Results by paper

| Paper | Journal | Target author(s) at Tier 2/3 | What's actually in `_main.txt` | Classification |
|---|---|---|---|---|
| QQIBRBDR | eLife | Zhenrui Liao, Wenke Li, James B Priestley | `"Wenke Li, Conceptualization, Software, Investigation, Methodology; Bovey Rao, ...; Zhenrui Liao, Software, Investigation, Methodology, ...; James B Priestley, Data curation, Software, Investigation, Methodology, ..."` — full names, real CRediT roles | **Full-name miss (this audit's target pattern)** |
| 5ZPAWQC8 | bioRxiv | Stephanie A Herrlinger | `"S.H. and B.R. collected and analyzed experimental data."` — initials present, but **missing her middle initial** (expected `S.A.H.`, text has `S.H.`) | Different defect: incomplete initials in source |
| PNCRGARS | bioRxiv | Stephanie A Herrlinger | `"S.H. and B.R. performed experiments and analyzed the data..."` — same `S.H.` vs. expected `S.A.H.` gap | Different defect: incomplete initials in source |
| ETEC7ELI | Neuron | Fraser T Sparks | `"M.L.-B., F.S., and Z.L. collected data."` — `F.S.` vs. expected `F.T.S.` | Different defect: incomplete initials in source |
| SECXDAKS | Nature Communications | Kevin C Gonzalez, Tommy L Jr Lewis | `"Investigation: ...K.G.,E.B.,F.P.,T.L."` — `K.G.` vs. expected `K.C.G.`; `T.L.` for Lewis is his own genuinely ambiguous self-abbreviation (middle initial collides with surname initial) | Different defect: incomplete/ambiguous initials in source |
| BIWVCPEH | Nature Neuroscience | Andres D Grosmark, Fraser T Sparks | Real statement exists (`"A.D.G., F.T.S. and M.J.D. performed the experiments"`, complete correct initials) but stage 4's actual match came from a Nature Reporting Summary boilerplate mention of "Andres Grosmark" instead — already flagged in CLAUDE.md as a contamination-handling edge case; Sparks got no match at all despite `F.T.S.` being present in the text | Different, already-partially-documented defect (search/tier logic, not naming format) |
| SWL5RJLJ | Neuron | John C Bowler, Satoshi Terada, Fraser T Sparks | `"F.T.S., J.C.B., and S.T. designed, performed, and analyzed axonal calcium imaging experiments."` — complete, correct initials, found by stage 4 (confirmed via evidence_quote) but classified as Tier 2 instead of Tier 1 because the text is interleaved with STAR-Methods TOC-bullet fragments in a `possible_trailing_contamination`-flagged region | Different, already-partially-documented defect (contamination interleaving demotes a real Tier 1 to Tier 2) |
| UMWW7XYA | Nature Communications | Kevin C Gonzalez | `"K.C.G.performed surgeries,performedexperimentsandpreprocesseddata"` — complete, correct initials, same contamination-interleaving demotion as SWL5RJLJ | Different, already-partially-documented defect |
| NYAYJKM8 | Current Biology | Gergely F Turi, Tommy L Jr Lewis | (Previously found this session) `"T.L. performed cranial window surgeries...G.T. performed cranial window surgeries..."` — initials, not full names; missed because the real Author Contributions section sits in a region stage 4 apparently doesn't reach, not because of naming format | Related but distinct defect (per the task's own "(partially)" framing) |
| 6T8UW6LJ | Science | Gergely F Turi, Jeffrey D Zaremba, Nathan B Danielson, Patrick Kaifosh, Matthew Lovett-Barron | No Author Contributions section found in `_main.txt` at all (only an "these authors contributed equally" byline footnote) | No CRediT-style statement present in cached text |
| V55JCIHQ | Science | Jeffrey D Zaremba | Same as above — no Author Contributions section in `_main.txt` | No CRediT-style statement present in cached text |
| RJ39DEXI | Neuropharmacology (Elsevier) | Gergely F Turi | No Author Contributions section found (2015 paper, predates CRediT taxonomy's ~2018-2020 standardization) | No CRediT-style statement present in cached text |
| Y9DG73R4 | IEEE ICASSP (conference) | Stephanie A Herrlinger | No Author Contributions section found (conference proceedings don't carry this convention) | No CRediT-style statement present in cached text |
| YZESCRFK | Springer (conference) | Stephanie A Herrlinger | No Author Contributions section found (same reason as above) | No CRediT-style statement present in cached text |

## Interpretation

Of the 25 (paper, author) pairs currently at Tier 2/3:

- **3 pairs** (all in QQIBRBDR: Liao, Li, Priestley) are misclassified
  specifically because of the full-name-vs-initials naming convention
  this audit targeted.
- **5 pairs** (5ZPAWQC8's Herrlinger, PNCRGARS's Herrlinger, ETEC7ELI's
  Sparks, SECXDAKS's Gonzalez and Lewis) reflect incomplete or
  ambiguous initials *in the source text itself* — the paper's own
  CRediT statement drops a middle initial the target-author record
  expects (e.g. `S.H.` where `S.A.H.` is expected), so the
  complete-string-match requirement (see CLAUDE.md's "Attribution
  initials matching requires the complete form") correctly fails to
  match, but arguably *shouldn't* need to fail here.
- **6 pairs** (SWL5RJLJ's Bowler/Terada/Sparks, UMWW7XYA's Gonzalez,
  BIWVCPEH's Grosmark and Sparks) have complete, correct initials
  present in the real Author Contributions text — confirmed present in
  stage 4's own evidence_quote for 4 of these 6 — but tier
  classification or matching gets confused by contamination-interleaved
  context, a variant of the already-documented
  `possible_trailing_contamination` interaction (BIWVCPEH's Sparks pair
  gets no match at all despite `F.T.S.` being present in the text,
  which looks like the same family of issue but with a different
  concrete failure mode worth a closer look if this bucket is
  investigated).
- **2 pairs** (NYAYJKM8's Turi and Lewis) are the previously-found
  column-merge-hidden-initials case, related but distinct from full
  names.
- **9 pairs** (6T8UW6LJ's 5 authors, V55JCIHQ's Zaremba, RJ39DEXI's
  Turi, Y9DG73R4's Herrlinger, YZESCRFK's Herrlinger) have no
  CRediT-style statement in the cached text at all, for reasons
  unrelated to any matching defect (pre-CRediT-era papers, conference
  proceedings, or Science's historically looser convention).

**On the specific question asked** — does the full-name pattern
warrant a general stage-4 fix vs. a targeted re-run like the O'Hare
fix — the evidence here points toward **targeted**, not general: it's
currently confined to a single paper (QQIBRBDR) that also happens to be
the corpus's only eLife entry. A generic "also try matching full names"
enhancement to stage 4 would, on the current 84-paper corpus, only ever
change QQIBRBDR's outcome — though it's a reasonable one to keep in
mind if more eLife (or other full-name-convention) papers get added to
the corpus later, since it's a one-line pattern to check that reflects
a real, standardized editorial policy rather than a one-off quirk.

The other patterns surfaced above (incomplete initials, contamination-
interleaving tier downgrades) are outside this audit's scope but
directly observed while checking these 14 papers — noted here so they
aren't lost, not as a recommendation to act on them as part of this
task.

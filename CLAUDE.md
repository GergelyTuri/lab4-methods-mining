# lab4-methods-mining

## Purpose
Extract structured, step-by-step data-analysis pipelines from the Methods
(and Methods-relevant Supplementary) sections of 82 papers from the
Losonczy Lab (2010–present), scoped to specific target authors. Output
feeds a downstream project that matches these pipelines against actual
code in `lab3`.

## Critical ground rules
- **Never fabricate.** Every extracted pipeline step must be traceable to
  actual text in the paper or supplement. If something is ambiguous or
  absent, mark it explicitly as missing/uncertain rather than inferring
  silently.
- **Attribution matters.** A paper's Methods section describes what the
  *group* did. Only attribute a step to a target author if there is
  supporting evidence per the tiered attribution scheme in
  `prompts/author_attribution.md`. Always record `attribution_source`
  and `attribution_confidence`.
- **No raw text leaves the data root.** PDFs, docx, and full extracted
  fulltext live only in `/code/lab4-methods-mining-data`, which is
  gitignored. Only structured JSON summaries (schema-conformant,
  short quotes ≤15 words if any) are committed to this repo.
- **Data root path is configurable.** Always read `data_root` from
  `config/project_config.yaml`. Never hardcode
  `/code/lab4-methods-mining-data` in scripts.

## Pipeline stages (run in order)
1. `scripts/01_fetch_corpus.py` — pull the Zotero collection via MCP,
   populate data_root, build manifest.db
2. `scripts/02_check_coverage.py` — gate: report missing PDFs/supplements
   before proceeding
3. `scripts/03_extract_methods.py` — isolate Methods text (main + supplement)
4. `scripts/04_extract_attribution.py` — determine which target author(s)
   performed which steps
5. `scripts/05_extract_pipeline.py` — LLM extraction into
   `schema/pipeline_step.schema.json` format
6. `scripts/06_aggregate.py` — per-author timeline/rollup into
   `outputs/author_pipelines/`

## Stage 5 is interactive, not scripted
`05_extract_pipeline` is not a standalone script. Extraction happens as an
interactive Claude Code session: load the isolated methods text for a paper
(or small batch) from `fulltext_cache/`, extract pipeline steps per
`schema/pipeline_step.schema.json` and the attribution rules in
`prompts/author_attribution.md`, write output to
`outputs/author_pipelines/{zotero_key}.json`, and validate against the
schema before moving to the next paper. Work in small batches (5-10 papers)
and spot-check quality before scaling up.

## Conventions
- Papers are identified by `zotero_key` throughout, not filename or DOI,
  to keep everything traceable back to the Zotero record.
- All scripts should be runnable independently and idempotent — re-running
  stage N should not re-do work stage N already completed successfully
  (check manifest.db / output existence first).
- Prefer SQLite (`manifest.db`, `pipelines.db`) over scattered flat files
  for anything queryable.

## Known issues and conventions

- **Supplement filename conventions.** Three publisher-specific naming
  conventions are recognized when identifying supplement attachments:
  Cell Press "mmc" (e.g. `mmc1.pdf`), Springer/Nature "ESM" (digit-adjacent,
  e.g. `esm1.pdf`, or the `_ESM` suffix form), and Science "_sm" (e.g.
  `science.abh4272_sm.pdf` — anchored immediately before the file
  extension to avoid false positives). This matching logic is
  duplicated independently in `scripts/02_check_coverage.py` and
  `scripts/03_extract_methods.py` — if a fourth convention is ever
  found, both copies need updating.
- **Manual methods-boundary overrides.** `config/manual_methods_overrides.json`
  holds human-verified start/end markers for papers where the header
  heuristic can't isolate Methods at all (currently: SECXDAKS, UMWW7XYA,
  9U8LL9DE) or found the wrong content entirely (N9FA3VEL — a bioRxiv
  preprint whose genuine Methods content sits in a "Supplemental
  Information" appendix *after* the References section, which stage
  3's heuristic never searches past). `scripts/03_extract_methods.py`
  checks this file before falling back to the heuristic — check it
  before assuming a paper's `extraction_failed` status is final.
  **Adding an override for a paper that already has
  `methods_extracted_main = 1`** (i.e. it "succeeded" with the *wrong*
  content, as N9FA3VEL did, rather than failing outright) doesn't take
  effect on its own: `is_methods_processed()` skips any paper with that
  flag already set, so a plain re-run silently protects the old, wrong
  extraction instead of applying the new override. Manually reset that
  one paper's `methods_extracted_main` to 0 in manifest.db before
  re-running stage 3 — this is a targeted, reversible data reset (not a
  script-logic change); every other field gets correctly rewritten by
  the normal `update_manifest_methods()` call once the row is
  reprocessed.
- **Word-gluing is a common, corpus-wide extraction defect, not a
  per-paper curiosity.** Some papers' cached text (`_main.txt` and/or
  `_methods_main.txt`) has no spaces between words across long
  stretches — `"Allexperimentswereconductedinaccordance..."` — a
  PDF-extraction column-merge artifact. First noticed on EV4PID4B and
  D5I4EKZM, a corpus-wide audit (`outputs/word_gluing_audit.md`) then
  confirmed it affects 35 of the 65 recovered papers (53%), strongly
  correlated with Cell Press/STAR★Methods format (86% of Cell Press
  papers affected) but also present in Nature Communications, Science,
  and bioRxiv papers — and confirmed independent of the
  `possible_trailing_contamination` flag (no meaningful correlation)
  and only partially overlapping with the STAR★Methods TOC-stub
  isolation failure documented in `prompts/pipeline_extraction.md`'s
  sanity-check subsection (3 of 4 known TOC-stub papers are also glued;
  one, LKVXCUIR, is not). **Deliberately not fixed at the code
  level**: an automated de-gluing heuristic risks corrupting technical
  terms, gene names, and
  units (e.g. splitting "CamKII" or failing to preserve "in vivo" vs.
  "invivo") more than it helps. Per-paper careful reading during stage
  5 has proven sufficient so far — see the "Word-gluing is common"
  note in `prompts/pipeline_extraction.md`. Revisit only if a future
  paper's severity genuinely blocks accurate extraction, not just
  slows it.
- **Exclusions.** `exclusion_reason` in manifest.db marks papers
  intentionally out of scope for stage 4/5 — review/commentary,
  software-description, retracted, manually judged not_relevant, or
  `superseded_duplicate`: one processed record for a study (e.g. a
  bioRxiv preprint) that's since been superseded by another, canonical
  record for the same study (e.g. its published version) — both stay in
  manifest.db, only the superseded one is marked out of scope, to avoid
  duplicate stage 5 extractions of the same underlying paper. A related
  but distinct `defunct_zotero_key` reason marks rows whose zotero_key
  no longer resolves in the live Zotero library at all (see the
  zotero_key-instability note below) — these aren't "out of scope," the
  row is just retained as a historical record. Currently 18 papers
  excluded (16 substantive + 5TKU4RYR as superseded_duplicate +
  IBUNAE3Y as defunct_zotero_key); the substantive-exclusion list is
  stable but may grow as more papers are reviewed.
- **`zotero_key`s are not guaranteed stable.** Manual Zotero library
  maintenance (trash/restore, duplicate-merge) can retire a key
  entirely rather than just relabeling it. Observed concretely with
  IBUNAE3Y: it started as a correct-metadata-wrong-PDF duplicate of
  5TKU4RYR (the bioRxiv preprint of the same study), got trashed during
  manual cleanup of that problem, and was then *permanently* removed
  from the library — a direct key lookup now 404s — while a brand-new
  item, LKVXCUIR, appeared via a fresh ScienceDirect fetch with the
  correct PDF and a `dc:replaces` relation pointing at IBUNAE3Y.
  LKVXCUIR was onboarded as the canonical record (re-run through
  stages 1/3/4; same three target authors matched at the same
  tier/confidence as the preprint) and 5TKU4RYR was marked
  `superseded_duplicate`; IBUNAE3Y's row was kept but marked
  `defunct_zotero_key` rather than deleted, so the history isn't lost.
  **If a paper's expected zotero_key 404s, search by title/author for
  its replacement rather than assuming it's been removed from the
  collection.** A second row, 2FGQLRRH, was also found to no longer be
  present in the live `pubmed-LosonczyA-set` collection during this
  same audit — this one is not a new mystery, though: it's the same
  stale-row artifact already flagged by stage 2's original coverage
  audit (see `outputs/coverage_report.md`, "stale manifest row — item
  removed from collection"), predating the IBUNAE3Y episode. Noted here
  only so it isn't mistaken for a plain extraction failure, not as an
  open investigation.
  Separately, stage 1's `find_pdf_attachment()` tie-break (prefer the
  attachment with the longer `md5` string) is a no-op when *neither*
  candidate PDF has an md5 populated — this bit LKVXCUIR's own fetch,
  which initially saved the supplement as `main_pdf_filename` instead
  of the actual article; caught by inspecting the fetched PDF's size/
  content rather than trusting the manifest row, and corrected as a
  manual data fix (correct PDF copied in from Zotero's local storage,
  fulltext re-extracted, manifest row corrected) rather than a change
  to the tie-break logic itself. Worth a sanity-check (does
  `main_pdf_filename` actually look like the paper, not a supplement?)
  on any future paper with multiple PDF attachments and no md5 on
  either.
- **Attribution initials matching.** `scripts/04_extract_attribution.py`
  treats dotted initials (e.g. "P.K.") as self-delimiting substrings
  rather than requiring word boundaries, to handle column-merge
  artifacts from PDF extraction that glue initials directly onto
  adjacent prose with no separating space. This carries an
  acknowledged, not-yet-observed risk of false-matching inside
  numbered-reference-list citation styles (e.g. "45. P.K. Someone et
  al."). Flag this for extra scrutiny during any manual spot-check of
  attribution output.
- **Attribution initials matching requires the complete form.** Initials
  matching requires an exact match of an author's complete initials
  string, not a prefix/substring match — an earlier version let a
  shorter variant (e.g. "J.O.") match as a prefix of a different,
  longer real initials string ("J.O.H."), and separately let one target
  author's short initials coincidentally match text naming an unrelated
  person. This was deliberately not engineered into a more general
  collision-avoidance system, since no current target author's initials
  are a prefix of another's — if that changes (a new target author is
  added whose initials collide with or prefix-match an existing one),
  this matching logic will need revisiting at that time.
- **Attribution notes flag contamination bleed-through.** Because stage
  3's Methods isolation can bleed a trailing fragment of the real
  Author Contributions section (or a headerless Acknowledgments/funding
  paragraph) into the extracted Methods text, `04_extract_attribution.py`
  now cross-references stage 3's `possible_trailing_contamination` flag:
  any evidence found in a flagged paper's methods-extracted text gets an
  explicit note calling that out, and a Tier 2 (methods_text_inference)
  match is demoted to "low" confidence when its surrounding text reads
  as Acknowledgments/funding language (e.g. "supported by", "grant",
  "fellowship") with no task-attribution verbs ("performed", "designed",
  "analyzed", etc.) — that only confirms authorship, not technique
  involvement. **Update**: the SWL5RJLJ shape below is now handled
  automatically (see the "Tier 1c" bullet just below) — this note is
  kept for the still-open BIWVCPEH case and for context on why Tier 1c
  exists. BIWVCPEH has contamination-flagged evidence that doesn't
  cleanly fit either the Ack-vs-task-verb heuristic or the
  Author-Contributions header detection: its only match for Andres D
  Grosmark comes from Nature Reporting Summary boilerplate ("Corresponding
  author(s): Andres Grosmark"), not the real Author Contributions
  statement, and Fraser T Sparks gets no match at all despite both
  authors' correct initials being clearly present in the paper's real
  statement ("A.D.G., F.T.S. and M.J.D. performed the experiments").
  Root cause (diagnosed, not fixed): `find_contributions_window()`
  anchors on the *first* case-insensitive "author contributions" match
  in the whole document, and BIWVCPEH has an earlier false positive — a
  Discussion-section sentence mentioning "...author contributions and
  competing interests..." as a generic phrase, not a real section
  heading — which then gets its search window truncated to 143
  characters by an end-marker hit in the very same sentence, so the
  real section (~1,200 lines later) is never reached. This is a
  different bug from Tier 1c below (header-anchor selection, not
  interleaved-content classification) and remains flagged for manual
  review / targeted reprocessing rather than fixed generically.
- **Tier 1c: promoting contamination-displaced Author Contributions
  text found in the methods cache.** SWL5RJLJ and UMWW7XYA share a
  distinct shape from the note above: a two-column page break splits
  the real Author Contributions statement mid-sentence, leaving the
  header and first half in `_main.txt` while a *headerless* second-half
  fragment — containing the target author's initials, glued to
  unrelated STAR-Methods-table or reference-list text — bleeds into the
  methods-text cache on its own. With no header there for the existing
  Tier 1b bleed-through check to anchor on, this fragment used to only
  ever surface as weaker Tier 2 (`methods_text_inference`) evidence.
  `04_extract_attribution.py` now promotes it to Tier 1
  (`author_contributions_statement`) when both prerequisites hold: a
  real Contributions section was independently confirmed elsewhere in
  the same paper's main text (Tier 1a succeeded), and the paper is
  flagged `possible_trailing_contamination` — and only when the match
  itself is a dotted-initials form (e.g. "J.C.B."), not a bare surname,
  since a bare-surname match is exactly BIWVCPEH's shape above and
  isn't reliable enough evidence on its own. Verified corpus-wide via
  `--reprocess-authors`: promotes exactly the 4 known affected pairs
  (John C Bowler, Satoshi Terada, Fraser T Sparks on SWL5RJLJ; Kevin C
  Gonzalez on UMWW7XYA) and changes nothing else in the corpus.
- **Contamination isn't only a tail phenomenon.** `possible_trailing_contamination`
  was named, and stage 3's detection logic was built, around the
  originally observed tail-only bibliography bleed-through shape. Real
  stage 5 extraction work has since surfaced a second shape: recurring
  mid-document copyright-footer/journal-boilerplate artifacts appearing
  at page breaks throughout the document, not just at the tail (observed
  in 4UQQ3CM5 — a Nature Neuroscience paper whose page-footer copyright
  block was extracted character-reversed and interleaved mid-paragraph
  at multiple page breaks, not only at the end). The flag name and
  stage 3's detection logic remain tail-focused and have not been
  generalized to catch this shape. Stage 5 extractors should be aware
  contamination artifacts can appear anywhere in a flagged paper's
  document, not only at the end — don't assume clean text just because
  you're not near the tail of the file.
- **`materialsandmethods` header match can false-positive on an inline
  cross-reference split across a line break.** ZIRAQSPC (a Science
  paper) had `methods_extracted_main = 1` but the isolated
  `_methods_main.txt` was not real Methods content — it was a Results
  paragraph. `find_section_start()`'s `START_PREFIX_PHRASES` include
  `"materialsandmethods"`, matched via `collapsed.startswith(...)`
  against the collapsed line text. Results-section prose in this paper
  parenthetically references the Methods section — `"(Fig. 1G and
  materials and methods)."` — and stage 1's column-merged PDF
  extraction happened to break the line right after "and", leaving
  `"materials and methods)."` as the start of the next physical line
  with its opening `"("` stranded on the previous line.
  `is_candidate_header_line()`'s guard against inline cross-references
  only rejects a line that itself starts with `"("`/`"["`, so it can't
  catch a cross-reference whose opening paren fell on the prior line
  during column-merge extraction. Diagnosed, not fixed (same posture as
  BIWVCPEH above) — for ZIRAQSPC specifically, the genuine Methods
  content turned out to live entirely in the paper's Science
  supplement (see the ZIRAQSPC supplement-attachment fix below), so the
  false-matched main-text isolation was simply disregarded in favor of
  `_methods_supp.txt` for stage 5 extraction rather than worked around
  with a manual override. Worth checking for on any other paper whose
  `_methods_main.txt` reads as narrative Results prose rather than
  procedural Methods text, per the sanity-check guidance in
  `prompts/pipeline_extraction.md`.
- **ZIRAQSPC's Science supplement (`science.aeh9302_sm.pdf`) was
  missing from the original stage 1/3 fetch** — not yet attached in
  Zotero at the time, so `methods_extracted_supp = 0` and stage 5 had
  to extract from Results-section technique descriptions instead of a
  real Methods section. Once the user attached the supplement in
  Zotero, fixing this required the same targeted-reset pattern as
  N9FA3VEL: `01_fetch_corpus.py` has no supplement-fetching capability
  at all (that logic lives only in `03_extract_methods.py`'s
  `process_paper()`/`extract_supplement_text()`), and neither stage 1
  nor stage 3 has a per-paper `--key` scoping flag, so picking up the
  new attachment meant manually resetting `methods_extracted_main` to
  0 for this one row and re-running stage 3 over the full corpus,
  relying on its own skip-gate (`is_methods_processed()`) to leave
  every other already-processed paper untouched. The supplement
  fetched cleanly and its Methods content isolated correctly (flagged
  `possible_trailing_contamination = 1`: the isolated
  `_methods_supp.txt` runs past the genuine "Statistics and
  reproducibility" close straight into the Fig. S1-S20 legends, since
  the paper has no distinct end-of-Methods header for
  `find_section_end()` to stop at — genuine Methods content ends at
  "Bar plots always represent mean and s.e.m unless specified
  otherwise." and everything after that in the file is figure-legend
  text, not usable for pipeline-step extraction).

## Current status

- [x] Zotero collection `pubmed-LosonczyA-set` populated (84 rows in
      manifest.db: the current 82-paper live collection plus two rows
      no longer present in the live collection — IBUNAE3Y, confirmed
      permanently removed from Zotero and marked `defunct_zotero_key`,
      and 2FGQLRRH, a pre-existing stale row already flagged by stage
      2's original coverage audit (see "zotero_keys are not guaranteed
      stable" above) — not a newly-discovered issue)
- [x] Target author list finalized in config/project_config.yaml
- [x] Coverage check run
- [x] Methods sections isolated (stage 3) — 65/84 rows recovered
      (header heuristic, manual override, or Science-supplement
      detection), 18 excluded as intentionally out of scope or defunct,
      1 genuinely still unresolved (2FGQLRRH — the stale row already
      flagged above, not a live-collection paper). Updated from an
      earlier 64/18/4 split: F3ELBLNY, GH4CWH6X, and KE4EERU8 were
      subsequently recovered and are no longer in the unresolved set.
- [x] Attribution scheme validated on a small batch (stage 4 — 7-paper
      test batch, bugs found and fixed during review)
- [x] Stage 4 full run — attribution extraction across all recovered,
      non-excluded papers (including LKVXCUIR, onboarded as the
      canonical record for the Kong et al. CA3 recurrent-connectivity
      study in place of 5TKU4RYR's preprint)
- [x] Extraction prompt validated on a small batch (stage 5)
- [x] Stage 5 full run — interactive pipeline-step extraction across
      the 59 recovered, target-author-matched papers, producing 163
      paper-author pipeline records (1013 total pipeline steps) in
      `data_root/fulltext_cache/`. The remaining 6 recovered papers
      (85Y2ALXZ, DNXQLFY7, HIMMYY8P, Q448AKAB, R6YFMBEN, TJR34ZYM) went
      through stage 4 and correctly matched no target author, so they
      have no pipeline file — not a gap.
- [x] Stage 6 full run — aggregation for all 27 target authors into
      `outputs/author_pipelines/`

**Pipeline complete end-to-end.** Of the 82 papers in the live Zotero
collection, 65 were recovered with usable Methods content and 17 were
excluded for documented reasons (review/commentary, software
description, retracted, not relevant, or superseded duplicate — see
"Exclusions" above); of those 65, 59 had at least one target-author
match and went through stage 5 extraction. That produced 163
paper-author pipeline records (1013 total pipeline steps), which stage
6 aggregated into 27 per-author files in `outputs/author_pipelines/` —
one per `config/project_config.yaml` target author. See "Known
limitations / follow-up items" below before treating this output as
fully clean for downstream (`lab3`) use.

## Known limitations / follow-up items

Issues surfaced during extraction and deliberately deferred rather than
fixed — one place to check before relying on `outputs/author_pipelines/`
downstream. Each is a pointer back to its full write-up, not a
re-diagnosis.

1. **YCFJ2N9Z / RBL2PSLI possible duplicate studies.** Both extracted
   and aggregated independently (not merged or excluded), flagged in
   every affected author's aggregated output — likely the same
   underlying study (preprint/published pair or closely related work).
   Needs manual review before downstream use.
2. **BIWVCPEH's `find_contributions_window()` anchor bug** (Grosmark/
   Sparks attribution) remains unfixed — an earlier false "author
   contributions" phrase match in the Discussion section stops the
   search window from ever reaching the real statement. See
   "Attribution notes flag contamination bleed-through" above.
3. **ETEC7ELI's Fraser T Sparks abbreviated-initials false negative**
   ("F.S." vs. the paper's expected "F.T.S.") in stage 4 — extracted
   correctly during stage 5 by direct text verification, but the
   underlying attribution record still shows Tier 3
   (`position_heuristic`)/low instead of the real Tier 1 evidence. Not
   corrected at the source.
4. **NYAYJKM8's stage-4 gap** — Tommy L Jr Lewis and Gergely F Turi
   both have real Tier-1-quality evidence in a reference-list-
   interleaved Author Contributions section that stage 4 never
   matched. Left unaddressed per user decision.
5. **6T8UW6LJ's word-gluing + Ack-demotion interaction bug** — Patrick
   Kaifosh's Tier 2/medium record is actually Acknowledgments-only text
   that the existing Ack-vs-task-verb heuristic should have demoted to
   low, but word-gluing broke the keyword match it relies on. Flagged,
   not fixed.
6. **ZIRAQSPC's main-text isolation is a false-positive Results
   paragraph, not real Methods** — a line-wrapped inline cross-
   reference evaded the header heuristic's guard (see
   "`materialsandmethods` header match can false-positive..." above).
   Cosmetic/inert since the supplement fix supplied the real 21-step
   Methods extraction, but the heuristic gap itself is unfixed and
   could recur on another paper.
7. **EV4PID4B has notably severe word-gluing** — one of the worst
   cases in the corpus-wide audit (`outputs/word_gluing_audit.md`).
   Extracted successfully; worth knowing if this paper is ever
   revisited.
8. **Corpus-wide word-gluing affects 53% of recovered papers** —
   documented, deliberately not fixed at the code level. See "Word-
   gluing is a common, corpus-wide extraction defect" above for the
   full write-up.

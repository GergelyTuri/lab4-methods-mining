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
  9U8LL9DE). `scripts/03_extract_methods.py` checks this file before
  falling back to the heuristic — check it before assuming a paper's
  `extraction_failed` status is final.
- **Exclusions.** `exclusion_reason` in manifest.db marks papers
  intentionally out of scope for stage 4/5 — review/commentary,
  software-description, retracted, or manually judged not_relevant.
  Currently 16 papers; the list is stable but may grow as more papers
  are reviewed.
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

## Current status
- [x] Zotero collection `pubmed-LosonczyA-set` populated (82 papers)
- [x] Target author list finalized in config/project_config.yaml
- [x] Coverage check run
- [x] Methods sections isolated (stage 3) — 66/82 papers recovered
      (header heuristic, manual override, or Science-supplement
      detection), 16 excluded as intentionally out of scope, 0
      genuinely still failing
- [x] Attribution scheme validated on a small batch (stage 4 — 7-paper
      test batch, bugs found and fixed during review)
- [ ] Stage 4 full run — attribution extraction across all 66 recovered
      papers
- [ ] Extraction prompt validated on a small batch (stage 5)
- [ ] Stage 5 full run — interactive pipeline-step extraction
- [ ] Stage 6 — per-author aggregation

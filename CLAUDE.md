# lab4-methods-mining

## Purpose
Extract structured, step-by-step data-analysis pipelines from the Methods
(and Methods-relevant Supplementary) sections of 83 papers from the
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

## Current status
- [ ] Zotero collection `pubmed-LosonczyA-set` populated (83 papers)
- [ ] Target author list finalized in config/project_config.yaml
- [ ] Coverage check run
- [ ] Attribution scheme validated on a small batch
- [ ] Extraction prompt validated on a small batch
- [ ] Full run

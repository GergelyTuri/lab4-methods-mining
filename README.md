# lab4-methods-mining

Extracts structured, step-by-step data-analysis pipelines from the
Methods (and Methods-relevant Supplementary) sections of Losonczy Lab
publications (2010–present), scoped to a specific list of target
authors. The output is a set of schema-conformant JSON documents — one
per (paper, target author) pair — describing what data-analysis steps
that author is attributable to, with what confidence, and traceable to
exact source text. This structured output feeds a downstream project
that matches these pipelines against actual analysis code in `lab3`.

## Background

Papers describe what a research group did; they don't say, in a
structured way, who on the group did what, or exactly what tools and
parameters they used. This project mines that information back out —
one paper at a time, one target author at a time — so it can later be
cross-referenced against the lab's actual code history. The result is
meant to help answer questions like "which analysis techniques has
author X actually used across their published work, and does that
match what's in `lab3`?"

## Repository structure

- **`config/`** — project configuration: `project_config.yaml` (data
  root path, Zotero collection name, target author list, extraction
  settings), `manual_methods_overrides.json` (human-verified Methods
  boundaries for papers the stage 3 heuristic can't handle), and
  `handcuration.md`.
- **`schema/`** — JSON Schema definitions that all structured output
  must conform to, notably `pipeline_step.schema.json`.
- **`scripts/`** — the numbered, scripted pipeline stages (01–04
  currently; see Usage below).
- **`prompts/`** — prompt templates for the interactive (non-scripted)
  stages, notably `pipeline_extraction.md` for stage 5.
- **`outputs/`** — committed, schema-conformant results: coverage
  reports and per-author pipeline rollups. Safe to commit because
  everything here is structured JSON/Markdown with only short (≤15
  word) quotes, never raw source text.
- **`data/`** (or wherever `config/project_config.yaml`'s `data_root`
  points) — raw PDFs, docx supplements, full extracted fulltext, and
  the SQLite manifest/pipeline databases. **This directory is
  gitignored and lives outside this repo** — it's never committed,
  since it holds copyrighted source documents. `data_root` is
  configurable precisely so this can point anywhere on disk (a local
  path today, a shared path on BioHPC later) without any script needing
  to change.

## Setup

### 1. Conda environment

This project targets native Windows with Python 3.11. Create and
activate the environment from the checked-in `environment.yml`:

```bash
conda env create -f environment.yml
conda activate lab4-methods
```

This installs the full pinned dependency set `environment.yml` lists,
including `pyzotero`, `pyyaml`, `jsonschema`, `pdfplumber`/`pypdf`, and
`zotero-mcp-server`.

### 2. Zotero desktop + zotero-mcp (local API mode)

The corpus is pulled from a Zotero library via its **local** HTTP API,
not the web API — no API key is required.

1. Install and run the Zotero desktop app, with the target papers in a
   collection (this project uses `pubmed-LosonczyA-set`; the name is
   configurable — see step 4).
2. In Zotero's preferences (Advanced tab), enable **"Allow other
   applications on this computer to communicate with Zotero"** so the
   local API server on `localhost:23119` accepts requests.
3. Confirm `zotero-mcp-server` (installed via `environment.yml`) can
   reach it — `scripts/01_fetch_corpus.py` and
   `scripts/04_extract_attribution.py` both connect via `pyzotero` in
   local mode (`library_id="0"`, `library_type="user"`), which is the
   standard placeholder convention for talking to the local API rather
   than the hosted one.

### 3. Claude Code MCP registration

This repo ships a project-scoped `.mcp.json` that registers the
`zotero` MCP server (`zotero-mcp serve`) automatically when you open
this project in Claude Code — no manual step needed in the common
case. If you need to register it manually (e.g. outside this repo, or
at user scope), the equivalent command is:

```bash
claude mcp add zotero -- zotero-mcp serve
```

### 4. Point the project at your data root

Edit `config/project_config.yaml` and set `data_root` to wherever you
want raw PDFs, extracted fulltext, and the SQLite databases stored on
your machine — a path outside this repo. Every script reads this value
at runtime rather than hardcoding a path, so this is the only line you
should need to change to relocate the data (e.g. moving from a local
machine to BioHPC later). The same file also holds the Zotero
collection name and the `target_authors` list.

## Usage

The pipeline runs in six stages, in order:

1. **`scripts/01_fetch_corpus.py`** — pulls the configured Zotero
   collection via the local API, populates `data_root`, and builds
   `manifest.db`.
2. **`scripts/02_check_coverage.py`** — gate: reports missing
   PDFs/supplements before proceeding further.
3. **`scripts/03_extract_methods.py`** — isolates Methods text (main
   text plus any Methods-relevant supplement) for each paper.
4. **`scripts/04_extract_attribution.py`** — determines which target
   author(s) are attributable to which paper, and with what tiered
   confidence, per the scheme documented in that script's module
   docstring.
5. **Stage 5 — interactive, not scripted.** There is no
   `05_extract_pipeline.py`. Pipeline-step extraction happens as an
   interactive Claude Code session per `prompts/pipeline_extraction.md`:
   load a paper's isolated Methods text and attribution record, extract
   pipeline steps against `schema/pipeline_step.schema.json`, and
   validate before moving on. See `CLAUDE.md`'s "Stage 5 is interactive,
   not scripted" section for the full workflow.
6. **`scripts/06_aggregate.py`** — rolls stage 5's per-(paper, author)
   output up into per-author timelines in
   `outputs/author_pipelines/`.

## Current status

See `CLAUDE.md`'s "Current status" section for up-to-date progress on
each stage — intentionally not duplicated here, so the two files can't
drift out of sync with each other.

## License

This project is licensed under the [PolyForm Noncommercial License
1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) (see
`LICENSE`). In plain language: you're free to use, modify, and share
this code for any noncommercial purpose — research, education,
personal projects, nonprofit or government use, and so on. Commercial
use requires separate permission from the copyright holder.

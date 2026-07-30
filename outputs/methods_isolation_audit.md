# Methods-isolation audit (read-only)

Scope: for all 66 recovered papers (`exclusion_reason IS NULL` and
`methods_extracted_main = 1` or `methods_extracted_supp = 1` in
`manifest.db`), compare each `{zotero_key}_methods_main.txt` word count
against its `{zotero_key}_main.txt` word count, and check whether stage
3's recognized Methods-header phrases appear in the isolated file. This
is a triage pass to scope how many papers are likely to need the
`_main.txt` fallback described in `prompts/pipeline_extraction.md`
before hitting them in a stage 5 batch. **No files were modified** —
this is a report only.

## Method

- Word counts computed via simple whitespace-splitting
  (`text.split()`). **Caveat**: PDF column-merge glues many words
  together with no separating space (documented in
  `scripts/03_extract_methods.py`'s own comments, e.g.
  `"EXPERIMENTALPROCEDURESwith10%FBS..."`), so absolute word counts are
  undercounts relative to true prose length in badly-glued papers. Both
  files for a given paper come from the same extraction pipeline, so
  the *ratio* between them is a more reliable signal than either
  absolute count on its own — but the ratio can still be skewed for
  papers whose non-Methods content (long Introduction/Discussion, or
  an unusually long reference list) is disproportionately large.
- Header-phrase check uses the same phrase list as
  `scripts/03_extract_methods.py`'s `START_PREFIX_PHRASES` (distinctive
  compound phrases: `materials and methods`, `methods and materials`,
  `methods and results`, `experimental procedures`, `STAR Methods` /
  `STAR+METHODS`, `online methods`, `supplemental(ary) experimental
  procedures`), tracked **separately** from bare `Methods`
  (`START_EQUALITY_PHRASES`), on a collapsed (letters-only, lowercased)
  version of the isolated file's full text.
- 63 of the 66 recovered papers have a `_methods_main.txt` file to
  compare; 3 (`F3ELBLNY`, `GH4CWH6X`, `KE4EERU8`) only have
  `_methods_supp.txt` (methods recovered from the supplement only,
  nothing to isolate from the main text) — these are a separate,
  already-understood category, not flagged here.

## Why the header-phrase check, taken literally, flags nothing

The task's second criterion — "none of the recognized Methods-header
phrases appear verbatim" — **fires for zero papers** in this corpus:
every single one of the 63 isolated files contains at least the bare
word "methods" somewhere. That's not a useful signal on its own; bare
"methods" is common enough in ordinary scientific prose (e.g. "...as
described in the materials and methods)." inside a Discussion-section
citation) that its presence proves nothing. Splitting it into
*distinctive compound phrase* vs. *bare "methods" only* is more
informative but still not reliable as a standalone flag: 21 of the 63
papers (a third of the corpus) are "bare-only," and that set spans the
**entire** ratio range — from confirmed failures (`NHDBUGYK` at
ratio 0.0024) to unambiguously fine, high-ratio captures (`9U8LL9DE` at
ratio 1.0, `RJ39DEXI`... not bare-only actually, but e.g. `Y9DG73R4` at
0.78, `UMWW7XYA` at 0.62). So the header-phrase criterion is reported
below for completeness, but **ratio is the signal actually doing the
flagging**.

## Threshold chosen

Sorting all 63 papers by ratio shows a clear natural gap: four papers
sit at **ratio < 0.04** (0.0024–0.0311), then a jump to the next
cluster starting at 0.0576. I used:

- **`ratio < 0.04` → SEVERE** (near-certain isolation failure)
- **`0.04 ≤ ratio < 0.10` → borderline** (worth a look before use, but
  not automatically broken — see individual notes below)

I did not pick these numbers from a formula; they're read directly off
where the distribution actually breaks in this dataset. Below 0.10 there
is no further natural gap — ratios climb smoothly from ~0.12 to 1.0 — so
I stopped flagging there rather than pick an arbitrary continuation.

**Known blind spot, stated plainly**: this numeric pre-check would
**not** have caught `YZESCRFK` (ratio 0.2142, comfortably inside the
"normal" range) — that failure (a Results-section fragment, not the
real Methods) was only found by actually reading the file. A paper
whose *wrong* content still happens to be a plausible *amount* of text
is invisible to a word-count-ratio check. That's exactly why
`prompts/pipeline_extraction.md`'s sanity-check subsection asks for a
qualitative skim regardless of what this audit flags — this audit
narrows down where to be extra careful, it doesn't replace that skim.

## Flagged: SEVERE (ratio < 0.04) — near-certain isolation failures

| zotero_key | title | mm_words | main_words | ratio | content peek |
|---|---|---:|---:|---:|---|
| `NHDBUGYK` | Local feedback inhibition tightly controls rapid formation of hippocampal place fields. | 12 | 5034 | 0.0024 | Confirmed TOC stub — literally just `STAR+METHODS` / `Detailed methods are provided in the online version...` / `KEY RESOURCES TABLE` / `RESOURCE AVAILABILITY`, then cuts off. Same shape as `ESKMCXTV`/`NAXFDQ32` (already documented in `CLAUDE.md`). |
| `N9FA3VEL` | Variable recruitment of distal tuft dendrites shapes new hippocampal place fields. | 156 | 21887 | 0.0071 | Confirmed wrong-section capture — content is Discussion/summary prose ("...distal tuft dendrites carry out diverse local operations...") that happens to open with a parenthetical `"(see materials and methods)"` citation. Same paper family as `EV4PID4B` (likely a preprint/published pair). |
| `ESKMCXTV` | Large-Scale 3D Two-Photon Imaging of Molecularly Identified CA1 Interneuron Dynamics in Behaving Mice. | 60 | 5270 | 0.0114 | Previously confirmed (batch 1): STAR★Methods TOC stub. |
| `NAXFDQ32` | Adult-born granule cells facilitate remapping of spatial and non-spatial representations in the dentate gyrus. | 153 | 4925 | 0.0311 | Previously confirmed (batch 1): STAR★Methods TOC stub. |

## Flagged: borderline (0.04 ≤ ratio < 0.10) — mixed; read individually

| zotero_key | title | mm_words | main_words | ratio | content peek |
|---|---|---:|---:|---:|---|
| `U7L3RARN` | Sublayer-Specific Coding Dynamics during Spatial Navigation and Learning in Hippocampal Area CA1. | 228 | 3955 | 0.0576 | Genuine methods content (SIMA signal extraction, ΔF/F, spatially-tuned-cell identification) — real, on-topic, but possibly a partial/incomplete capture rather than a stub. Uncertain; worth a look. |
| `XM7ZNKAL` | Direct cortical inputs to hippocampal area CA1 transmit complementary signals for goal-directed navigation. | 369 | 4694 | 0.0786 | **Confirmed wrong-section capture** — captured text is Discussion prose ("We also find that, when a clear navigational goal is established...") that opens with a `"STAR Methods)"` cross-reference citation, not the real STAR Methods section. Same failure shape as `N9FA3VEL`/`YZESCRFK`: a coincidental phrase match inside body prose. |
| `NYAYJKM8` | Progressive Decrease of Mitochondrial Motility during Maturation of Cortical Axons In Vitro and In Vivo. | 174 | 2174 | 0.0800 | Genuine, on-topic content (Animals, In Utero Cortical Electroporation), interleaved with bibliography (the recurring column-merge contamination shape). This paper also explicitly defers detail to a Supplemental Experimental Procedures section (and has a `_methods_supp.txt`) — the brevity may be intentional (main text is a stub-by-design pointing to the supplement), not a stage-3 failure. |
| `QQIBRBDR` | An Intranet of Things approach for adaptable control of behavioral and navigation-based experiments. | 1271 | 15544 | 0.0818 | Content looks genuine and properly captured from the real `Materials and methods` header. Low ratio likely just reflects a long overall document (this is the tools/methods paper "behaviorMate," probably with a long intro/discussion), not a truncation failure. |
| `N5AUJWGX` | Parvalbumin-positive basket cells differentiate among hippocampal pyramidal cells. | 469 | 5372 | 0.0873 | Content looks genuine and properly captured from the real `Materials and methods` header (Lead Contact, Animals, Surgical procedures...). Doesn't show the stub/wrong-section signature. |
| `R27V283A` | behaviorMate: An Intranet of Things Approach for Adaptable Control of Behavioral and Navigation-Based Experiments. | 1408 | 15805 | 0.0891 | Same underlying paper as `QQIBRBDR` (preprint/published pair) — same characterization: genuine content, low ratio likely just reflects document length. |

Of these six, I'd treat **`XM7ZNKAL`** as a near-certain failure (same
confirmed shape as two other papers), **`U7L3RARN`** as genuinely
uncertain, and the other four (`NYAYJKM8`, `QQIBRBDR`, `N5AUJWGX`,
`R27V283A`) as probably fine on closer look, despite the low ratio —
included here for completeness since they crossed the numeric
threshold, not because I believe they're broken.

## No `_methods_main.txt` file at all (not ratio-comparable)

| zotero_key | title |
|---|---|
| `F3ELBLNY` | Supramammillary regulation of locomotion and hippocampal activity. |
| `GH4CWH6X` | Compartment-specific tuning of dendritic feature selectivity by intracellular Ca(2+) release. |
| `KE4EERU8` | Brainstem nucleus incertus controls contextual memory formation. |

These recovered via supplement methods only (`methods_extracted_supp = 1`,
`methods_extracted_main = 0`) — an already-understood category, not new
findings.

## Summary

- **63/66** recovered papers have a `_methods_main.txt` to audit.
- **4 SEVERE** (near-certain failures, all independently confirmed by
  content): `NHDBUGYK`, `N9FA3VEL`, `ESKMCXTV`, `NAXFDQ32`.
- **6 borderline**, of which **1 more** (`XM7ZNKAL`) is a confirmed
  failure of the same "coincidental phrase match inside body prose"
  shape as `N9FA3VEL` and `YZESCRFK` — bringing the **confirmed or
  near-certain total to 5 of 63 (~8%)**.
- The remaining 5 borderline papers are probably fine despite the low
  ratio; flagged for completeness, not as predictions of failure.
- This audit **cannot** catch a `YZESCRFK`-shaped failure (wrong
  content, plausible amount of it) by ratio alone when the ratio isn't
  also low — `XM7ZNKAL` and `N9FA3VEL` were only caught here because
  their ratios *also* happened to be low; a wrong-section capture with
  a "normal" ratio needs the qualitative skim in
  `prompts/pipeline_extraction.md`, not this numeric check.

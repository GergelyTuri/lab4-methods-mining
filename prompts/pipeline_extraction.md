# Stage 5 prompt: pipeline-step extraction

This is the instruction set for stage 5 (see `CLAUDE.md`'s "Stage 5 is
interactive, not scripted"). It is followed once per
(paper, target author) pair — a single paper with three matched target
authors needs three passes through this prompt, each producing its own
output document.

Follow this prompt inside an interactive Claude Code session. It is not
a script and there is nothing to run — read it, then do what it says for
the one paper/author pair you're currently working.

## 1. Inputs to read first

Before extracting anything, read:

- `data_root/fulltext_cache/{zotero_key}_methods_main.txt` — the
  isolated main-text Methods content.
- `data_root/fulltext_cache/{zotero_key}_methods_supp.txt` — the
  isolated supplement Methods content, **if it exists**. Not every
  paper has one; don't treat its absence as an error.
- `data_root/fulltext_cache/{zotero_key}_attribution.json` — find the
  entry for the specific target author you're processing. Read its
  `attribution_source`, `attribution_confidence`, `evidence_quote`, and
  `notes` before doing anything else. This tells you *why* this author
  is believed to be connected to this paper, and how strong that belief
  is — it should shape how aggressively you extract (see §4).
- The paper's `manifest.db` row (or the Zotero record, if working from
  MCP directly) for `paper_id` (the `zotero_key` itself), `year`, and
  `journal` — these are required top-level schema fields but won't
  necessarily appear in the Methods text itself, so pull them from
  metadata rather than searching for them in the extracted text.
- The `manifest.db` row's `possible_trailing_contamination` flag — see
  §6 before trusting the tail of the methods text.

**Don't stop at the truncated `evidence_quote`.** Stage 4's evidentiary
window is intentionally narrow, and in practice the captured quote often
cuts off mid-sentence or mid-word (especially in column-merged PDF
extractions — see §6). Before extracting, search the main text yourself
for the complete Author Contributions statement (or equivalent CRediT
text) surrounding that quote. The full statement often says
considerably more than the truncated fragment shows — including,
sometimes, an explicit division of labor among co-authors that should
narrow your extraction (see §4).

### Sanity-check `_methods_main.txt` before trusting it

Stage 3's Methods isolation can fail in ways that don't look like an
error — the file exists, `manifest.db` shows `methods_extracted_main = 1`,
and it isn't flagged `possible_trailing_contamination`, yet its content
isn't the paper's real Methods section at all. Treat this as a live
possibility, not a hypothetical edge case: real batch-1 extractions hit
three different shapes of it —

- `ESKMCXTV`: the isolated file was only the STAR★Methods
  table-of-contents outline (13 lines), not the ~250 lines of actual
  method content that followed later in `_main.txt`.
- `NAXFDQ32`: the same TOC-stub shape, independently confirmed on a
  second, unrelated paper.
- `YZESCRFK`: the isolated file was a fragment of the *Results* section
  plus Acknowledgments — the real "2 Method" section was never captured
  at all, apparently because stage 3's header heuristic didn't
  recognize a bare, numbered, singular "Method" heading.

**When to suspect this** — before extracting, skim `_methods_main.txt`
against these rough signals:

- it's suspiciously short (rough guide: under ~500 words);
- it reads like a table of contents or a bare list of section headings
  rather than prose;
- it doesn't contain any recognizable methods-content markers — a named
  technique, a specific parameter or measurement, a piece of equipment
  or software.

Any one of these is reason enough to treat the file as a likely
isolation failure rather than a genuinely short Methods section — a
paper can genuinely have a short Methods section, but it still reads as
prose about techniques, not an outline or a Results fragment.

**What to do about it** — open
`data_root/fulltext_cache/{zotero_key}_main.txt` directly and search for
the real Methods section using the same header phrases stage 3's own
heuristic looks for (`materials and methods`, `methods and materials`,
`methods and results`, `experimental procedures`, `STAR Methods` /
`STAR+METHODS`, `online methods`, `supplemental`/`supplementary
experimental procedures`, or a bare `Methods` heading) — but be more
liberal than stage 3's strict, automatable heuristic, since you're doing
this by eye: also check for a numbered heading like `2 Method`
(singular, no "Materials and"), since that's exactly the form stage 3
missed in `YZESCRFK`. Once you find the real section, extract from there
instead of the isolated file — this is genuine paper text, just
mis-isolated upstream, not something you're fabricating by looking
elsewhere.

**Report it.** Whenever you use this fallback, say so explicitly in your
response for that paper — which failure shape it looked like, roughly —
not in the pipeline JSON itself (the schema has no field for this). This
is how the pattern gets tracked across batches without needing a
dedicated stage-3 audit script run for every one.

## 2. What counts as a pipeline step

A pipeline step is a **discrete data-analysis operation**, specific
enough that someone trying to match it against actual code in `lab3`
could plausibly recognize which function or script it corresponds to.
A restatement of the paper's abstract or a generic description of the
overall study is not a step.

Each step should isolate one operation: one imaging/acquisition step,
one preprocessing step, one statistical test, etc. If the text
describes a chain of operations ("raw video was motion-corrected, then
ROIs were extracted, then fluorescence traces were deconvolved"), that
is three steps, not one.

**Good extraction** — specific enough to be useful:
- "ROI extraction performed with suite2p, correlation threshold 0.7"
  (names the tool and the specific parameter that controlled it)
- "Place fields identified using a shuffled-spike-train null
  distribution, 1000 shuffles, p < 0.05 threshold" (names the method
  and the specific statistical parameters)
- "Motion correction via rigid-body registration in ScanImage,
  reference frame = median of first 500 frames" (names the technique,
  the tool, and a concrete parameter)

**Too vague** — do not extract steps like this:
- "Data was analyzed" (no technique, no tool, no specificity — this
  isn't a step, it's a placeholder)
- "Standard imaging preprocessing was performed" (names nothing
  concrete — if the paper truly gives no more detail than this, either
  skip it or record it with every optional field null rather than
  inventing specifics)
- "Statistics were computed using appropriate tests" (no test named, no
  parameters — not extractable as written)

If the paper's actual description is this vague, that's a signal to
record `extraction_confidence: "low"` and leave the optional fields
null (per §3) rather than to skip the step entirely — the vagueness
itself may be worth capturing if the *category* of operation
(e.g. "statistics") is clear even though the details aren't.

## 3. Non-fabrication rule

Every field in the output must be traceable to actual text in the
paper or supplement. This is the project's most important rule (see
CLAUDE.md's "Never fabricate").

Concretely:
- If the paper doesn't name a software tool, `software_tool` is
  `null`. Do not guess based on what's common in the lab or the field.
- If a version number isn't stated, `tool_version` is `null`, even if
  you happen to know (from general knowledge or `lab3`) which version
  the lab typically uses. What the lab usually does is not what this
  paper says it did.
- If `parameters` aren't reported for a step, use `{}`, not
  plausible-sounding placeholder values.
- If you are inferring a step's existence from an indirect statement
  (e.g. "cells were tracked across days" implies some registration
  step happened, but the paper never describes *how*), either don't
  extract it as a step, or extract it with `extraction_confidence: "low"`
  and every optional field `null` — never fill in a specific method
  because it's the "obvious" way someone would do it.

When in doubt, `null` and low confidence are always the safe choice.
An extraction that under-claims can be corrected later by a human
reviewer with the source text in front of them; one that over-claims
introduces a fabricated fact that may not get caught.

## 4. Handling attribution ambiguity

The attribution record you read in §1 sets the ceiling on how
confidently you should extract steps for this author on this paper.

- **Tier 1 (`author_contributions_statement`), high/medium
  confidence**: the paper has explicitly said this author did
  something. Extract normally — this is the strongest starting point.
- **Tier 2 (`methods_text_inference`), medium confidence**: the
  author's initials or name appear directly next to a technique
  description in the Methods text itself. Extract, but stay grounded
  in what's actually adjacent to their name — don't extend attribution
  to unrelated steps elsewhere in the Methods just because this author
  is known to be on the paper.
- **Tier 2, low confidence** (e.g. an Acknowledgments/funding-only
  match — see the `notes` field, which will say so explicitly per the
  stage-4 contamination-handling update): this evidence confirms
  authorship, not that this author performed any particular technique.
  Be very conservative — most papers in this state should yield few or
  zero extracted steps, because the Methods text gives no way to
  distinguish this author's specific contribution from the group's.
- **Tier 3 (`position_heuristic`), low confidence**: there is no
  textual evidence at all connecting this author to any specific
  technique, only their position in the byline. Default to extracting
  **zero steps** unless the Methods text separately and independently
  names this author next to a technique (in which case, re-examine
  whether stage 4 should have caught that as Tier 2 — flag it, don't
  silently upgrade the attribution yourself).

**It is correct and expected for a low-confidence author to produce an
empty `pipeline_steps` array.** Do not force steps onto a paper/author
pair just to produce non-empty output — an empty array with an honest
attribution record is a valid, useful result. Padding it out defeats
the purpose of the tiered attribution scheme.

**Explicit division of labor narrows what Tier 1 licenses.** A Tier 1
(`author_contributions_statement`) match means the paper explicitly
said this author did *something* — it does not automatically mean
every step in the Methods section belongs to them. If the full
statement you read per §1 divides labor among named co-authors — e.g.
"P.K. designed and built hardware and software for imaging, behavior
and motion-correction, and performed analyses. M.L.-B., G.F.T. and A.L.
performed experiments." — scope `pipeline_steps` specifically to what's
credited to your target author, and do not extend that Tier 1 license
into work the same statement explicitly credits to someone else, even
though it's described in the same Methods section. When the statement
doesn't divide labor this explicitly, the default (extract normally
across the section) still applies.

## 5. `source_location` and `source_quote`

For each step, record `source_location` as `"main_text"` or
`"supplement"` depending on which file the supporting text came from.

For `source_quote`:
- Keep any direct quote to **15 words or fewer** — this project's
  established copyright-safety convention (see CLAUDE.md).
- Prefer **paraphrasing** the technique description in `technique`,
  `parameters`, etc. over quoting it. The quote field exists to anchor
  the extraction to its source, not to reproduce the paper's prose.
- Reserve direct quotes for cases where the exact wording carries real
  information a paraphrase would lose — e.g. a specific stated
  parameter value or threshold ("bandpass filtered between 0.5 and
  8 Hz"), a specific software invocation, or an unusual technique name
  you want preserved verbatim rather than normalized.
- If no single short quote captures the step well, use `null` — don't
  stretch a quote past 15 words to make it more complete.

## 6. Contamination awareness

Check the paper's `possible_trailing_contamination` flag in
`manifest.db` before trusting the tail of `_methods_main.txt` or
`_methods_supp.txt`. When this flag is set, stage 3's Methods isolation
is known to sometimes have pulled in a trailing fragment of the
bibliography, Acknowledgments, or Author Contributions section along
with the genuine Methods content — see CLAUDE.md's "Attribution notes
flag contamination bleed-through" for the stage-4-side handling of the
same issue.

If text near the end of the Methods extraction looks like a citation
fragment (author-year lists, journal names, page ranges, reference
numbering) rather than a procedural description, **do not extract
pipeline steps from it** — it is very unlikely to be genuine Methods
content, however coincidentally close a name or initial might sit next
to it. If you're unsure whether a passage is genuine Methods text or
bleed-through, treat it the same way as any other ambiguity under this
project's rules: skip it, or extract it with low confidence and a note
in `parameters` or via omission — don't extract it at face value.

## 7. Output

Write the result to:

```
data_root/fulltext_cache/{zotero_key}_{target_author_slug}_pipeline.json
```

`target_author_slug` is a filesystem-safe version of the target
author's name: lowercase, whitespace collapsed to single underscores,
and any character that isn't a letter, digit, underscore, or hyphen
stripped (this removes apostrophes like in "O'Hare" without splitting
the name, and preserves hyphenated surnames like "Lovett-Barron").
Examples: `"Gergely F Turi"` → `gergely_f_turi`; `"Justin K O'Hare"` →
`justin_k_ohare`; `"Matthew Lovett-Barron"` → `matthew_lovett-barron`.

Before considering the extraction complete:
1. Validate the output against `schema/pipeline_step.schema.json`.
   `additionalProperties` is `false` throughout the schema, so an extra
   or misnamed field will fail validation, not silently pass through.
2. Confirm `target_author` matches the exact string used in
   `config/project_config.yaml`'s `target_authors` list, and that
   `attribution.source` / `attribution.confidence` exactly match the
   values already recorded in `{zotero_key}_attribution.json` for this
   author — this file should not re-derive or second-guess stage 4's
   attribution call, only consume it.
3. Re-read the `pipeline_steps` array once against the source text one
   more time, specifically checking for fabricated specifics (§3) —
   this is the single most important check before moving to the next
   paper.

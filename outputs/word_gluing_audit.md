# Word-gluing extraction defect audit (read-only)

Scope: for all 66 recovered papers (`exclusion_reason IS NULL` and
`methods_extracted_main = 1` or `methods_extracted_supp = 1` in
`manifest.db`, excluding the defunct `IBUNAE3Y` row), check whether the
PDF-extraction column-merge artifact first confirmed in `EV4PID4B` and
`D5I4EKZM` (no spaces between words across long stretches of text) is
present, and characterize how it correlates with journal/publisher and
with the two already-known defects (`possible_trailing_contamination`,
STAR★Methods TOC-stub isolation failures). **No files were modified** —
this is a report only.

## Headline result

**35 of 66 recovered papers (53%) show word-gluing** — this is a
corpus-wide, systematic issue, not the rare two-paper curiosity it
initially looked like. It correlates strongly with Cell Press's
STAR★Methods layout (86% of Cell Press papers affected, vs. 27-29% of
everyone else) and is **independent of** the existing
`possible_trailing_contamination` flag, though it partially overlaps
with the separately-documented TOC-stub isolation failures.

## Method

Getting a reliable automated detector took several failed attempts,
documented here because the failure modes are informative about what
*doesn't* work for this kind of check:

1. **Average token length / fraction of long tokens, naive
   whitespace-split.** Requiring a whitespace-delimited chunk to be
   entirely alphabetic before counting it systematically discards
   glued runs that happen to contain an embedded digit or parenthesis
   (`"(8-16weeks)"`, catalog numbers, coordinates) — extremely common
   in Methods prose. This made the metric blind to real gluing
   whenever the sampled window was number-dense, which is often.
2. **Pure letter-run extraction** (`[A-Za-z]+`, ignoring surrounding
   digits/punctuation) fixed the above but overcorrected: genuinely
   long single scientific words (`"electrophysiological"`,
   `"immunohistochemistry"`, `"photostimulation"`) are indistinguishable
   from several short words glued together by length alone, producing
   false positives on normal prose.
3. **Mid-token lowercase→uppercase transitions** (the task's suggested
   signature) had the same problem in reverse: normal neuroscience
   prose is saturated with capitalized abbreviations (CA1, GCaMP, ROI,
   NIH), so the transition rate varies more with how abbreviation-heavy
   a paper's writing style is than with whether it's actually glued.

**What worked**: searching for `was` or `were` immediately followed by
5+ more lowercase letters with no space — e.g. `wereconducted`,
`wasperformedusing`. This sequence essentially never occurs in properly
spaced English prose (verified against a manually excluded list of
genuine words: `wasteful`, `werewolf`, etc.), so it has had **zero false
positives** across every case spot-checked by direct reading during
calibration and the final pass (over 20 papers manually verified this
way). Critically, the regex must **not** require a word boundary before
`was`/`were` — in severely glued text, `was`/`were` is usually itself
glued to the *preceding* word too (`"Micewereconducted"`), and an
anchored pattern undercounts exactly the worst cases (verified directly
on `D5I4EKZM`: an anchored version found 2 hits in 27KB of near-total
gluing; the unanchored version found 40).

Classification is **binary**: any hit (after excluding the handful of
genuine `was-`/`were-` prefixed English words) marks a paper `glued`.
The corpus shows a clean split — 31 papers at exactly 0 hits, the rest
spread from ~2 to 129 hits — that doesn't cleanly separate into
"severe" vs. "mild" tiers, so severity is reported as a continuous
density value (hits per 10,000 characters) rather than forced into
buckets.

**Where the check was run**: primarily on each paper's
`{zotero_key}_methods_main.txt` (the file stage 5 extraction actually
reads), since that's what determines real-world impact. For four papers
already known this session to have a STAR★Methods **TOC-stub isolation
failure** (`ESKMCXTV`, `NAXFDQ32`, `LKVXCUIR`, `KZYI47XK` — where
`methods_main.txt` is mostly Discussion bleed-through or a bulleted
preview, not the real section), automated length-based heuristics
couldn't reliably tell "real content" from "long but unrepresentative
stub" apart, so these four use the verdict obtained by directly reading
the real STAR-Methods section (located via a `KEY RESOURCES
TABLE`+`REAGENT` anchor in `_main.txt`) during this session's earlier
manual work — three are glued (`ESKMCXTV`, `NAXFDQ32`, `KZYI47XK`), one
is not (`LKVXCUIR`). This is a real limitation: the same
unrepresentative-stub problem could in principle be hiding in other
papers beyond these four known cases, and a small-content paper's
"normal" verdict below should be read with that caveat. Three papers
(`F3ELBLNY`, `GH4CWH6X`, `KE4EERU8`) have no usable Methods text at all
(the pre-existing, already-documented extraction-failure category) and
are marked `insufficient_sample`.

## Results

| | Count | % of 66 |
|---|---|---|
| Glued | 35 | 53% |
| Normal | 28 | 42% |
| Insufficient sample (pre-existing extraction failures) | 3 | 5% |

### Journal/publisher correlation

| | Papers | Glued | % |
|---|---|---|---|
| Cell Press (Neuron, Cell Reports, Cell, Current Biology, Cell Reports Methods) | 29 | 25 | **86%** |
| Everything else (Nature Neuroscience, Nature-family, Nature Communications, Science, eLife, bioRxiv, Springer, other Elsevier) | 37 | 10 | 27% |

(`HIMMYY8P`, DOI `10.1016/j.crmeth...`, is Cell Reports Methods —
also Cell Press family, counted here even though the journal-mapping
script bucketed it as generic "Elsevier (other)".)

This is a strong, clear correlation, consistent with the original
observation that both known cases were Cell Press/Neuron
STAR★Methods-format papers. It's not exclusive to Cell Press, though —
`PSG2BFK8`/`UMWW7XYA` (Nature Communications), `V55JCIHQ`/`6T8UW6LJ`
(Science), and `LBKM3N66`/`R27V283A`/`R6YFMBEN` (bioRxiv) are all
glued too, so this reads as "the STAR★Methods-style Key-Resources-Table
layout is a strong risk factor," not "only Cell Press papers are
affected."

### Correlation with `possible_trailing_contamination`

| | Papers | Glued | % |
|---|---|---|---|
| `contamination_flag = True` | 27 | 13 | 48% |
| `contamination_flag = False` | 39 | 22 | 56% |

No meaningful correlation — glued and normal papers show up at
statistically indistinguishable rates regardless of the contamination
flag. This confirms word-gluing is an **independent failure mode**
from the trailing-bibliography-bleed-through issue the contamination
flag was built to catch; one doesn't predict the other.

### Correlation with the TOC-stub isolation failure

| Paper | TOC-stub? | Glued? |
|---|---|---|
| ESKMCXTV | yes | yes |
| NAXFDQ32 | yes | yes |
| KZYI47XK | yes | yes |
| LKVXCUIR | yes | **no** |

Partial overlap, not identity: 3 of the 4 known TOC-stub papers are
also glued, but `LKVXCUIR`'s real STAR-Methods content reads with
normal spacing despite its `methods_main.txt` being a stub. Both likely
share a root cause (a PDF layout complex enough to confuse pdfplumber's
text-extraction column-grouping), but they're separate failure shapes
requiring separate detection — a paper can have either, both, or
neither.

### The two originally-known cases in context

`D5I4EKZM` sits almost exactly at the corpus median density (14.7 hits
per 10K chars vs. a median of 14.7 among the 32 auto-scored glued
papers) — it's a *typical* case of this pattern, not an outlier.
`EV4PID4B` is more severe (33.9/10K, near the top of the observed
range: 1.9 to 38.0). The worst case in the corpus is `HAD4KPSV`
(38.0/10K, 129 hits) — nobody had previously flagged this paper as having
any extraction issue.

## Full paper-by-paper results

### Glued (35), sorted by severity (density per 10K characters; "manual" = TOC-stub override, see Method)

| Paper | Year | Journal | Density | Contamination flag |
|---|---|---|---|---|
| ESKMCXTV | 2020 | Neuron | manual | No |
| KZYI47XK | 2022 | Cell Reports | manual | No |
| NAXFDQ32 | 2023 | Neuron | manual | No |
| HAD4KPSV | 2021 | Neuron | 38.0 | No |
| U7L3RARN | 2016 | Neuron | 36.2 | No |
| EV4PID4B | 2025 | Neuron | 33.9 | No |
| ETEC7ELI | 2021 | Neuron | 28.1 | No |
| NHDBUGYK | 2022 | Neuron | 27.3 | No |
| IMM9NRV3 | 2017 | Neuron | 27.3 | No |
| LB5SX2W3 | 2020 | Neuron | 26.8 | No |
| DAVL9W98 | 2020 | Neuron | 26.5 | No |
| 8NDG4UEZ | 2021 | Cell Reports | 25.9 | No |
| 2LKHNA25 | 2016 | Neuron | 25.2 | Yes |
| RFR8U95R | 2019 | Cell Reports | 20.4 | No |
| WSSZP72M | 2022 | Neuron | 20.2 | Yes |
| H7G3J9VN | 2019 | Cell | 19.9 | No |
| PSG2BFK8 | 2020 | Nature Communications | 18.2 | Yes |
| V55JCIHQ | 2016 | Science | 16.5 | Yes |
| D5I4EKZM | 2022 | Neuron | 14.7 | No |
| UMWW7XYA | 2024 | Nature Communications | 14.5 | Yes |
| YU3INJP9 | 2016 | Neuron | 14.2 | Yes |
| NYAYJKM8 | 2016 | Current Biology | 11.9 | No |
| LBKM3N66 | 2024 | bioRxiv | 10.4 | Yes |
| SECXDAKS | 2024 | Nature Communications | 9.3 | Yes |
| SWL5RJLJ | 2021 | Neuron | 8.7 | Yes |
| N5AUJWGX | 2014 | Neuron | 7.8 | No |
| HIMMYY8P | 2021 | Cell Reports Methods | 7.6 | No |
| UF5YJ4HS | 2022 | Cell Reports | 6.4 | Yes |
| 3LEAWLYJ | 2016 | Neuron | 5.1 | No |
| Y9DG73R4 | 2023 | (IEEE conference) | 3.9 | No |
| RJ39DEXI | 2015 | Neuropharmacology (Elsevier) | 3.5 | No |
| R6YFMBEN | 2025 | bioRxiv | 3.4 | Yes |
| R27V283A | 2024 | bioRxiv | 3.2 | No |
| K433LNFA | 2019 | Neuron | 3.1 | Yes |
| 6T8UW6LJ | 2014 | Science | 1.9 | Yes |

### Normal (28)

4UQQ3CM5, 5TKU4RYR, 5ZPAWQC8, 85Y2ALXZ, 8SKA2ZZG, 9U8LL9DE, BIWVCPEH,
DNXQLFY7, EC39E7I5, FPF3RL6U, HU4D65CV, K3YPWJ6L, K73ERVAQ, KKAPPFAJ,
**LKVXCUIR** (manual override — TOC-stub, but confirmed non-glued),
N9FA3VEL, PNCRGARS, Q448AKAB, QQIBRBDR, RBL2PSLI, SZXKSZY9, TJR34ZYM,
UTJ3YW3R, VMWYLLBS, XM7ZNKAL, YCFJ2N9Z, YZESCRFK, ZIRAQSPC

### Insufficient sample (3, pre-existing extraction-failure category)

F3ELBLNY, GH4CWH6X, KE4EERU8 — all Science papers with no methods
content recovered at all (a separate, already-documented issue, not
new here).

## Practical implication for stage 5 (assessment only, no fix implied)

At 53% of the recovered corpus, this is common enough that treating it
purely as "handle it per-paper as encountered" (the current approach)
means roughly half of all remaining stage-5 extraction passes will hit
it. Whether that argues for a systematic fix (e.g., re-running
`pdfplumber` extraction with different column-detection settings
specifically for Cell Press-format PDFs, or a general de-gluing
preprocessing pass) versus continuing the current per-paper handling
is a judgment call outside this audit's scope — but the practical
severity varies a lot (1.9 to 38.0 hits/10K), and even the most severe
cases encountered so far (`EV4PID4B`, `D5I4EKZM`, `KZYI47XK`) were still
extractable by reading through the glued text directly, just more
slowly. No papers in this audit appear to be *blocked* by gluing alone
in the way the TOC-stub or missing-content failures are.

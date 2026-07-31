"""Stage 3: isolate Methods sections from main text and supplements.

For every paper in manifest.db with fulltext_status "ok" (same computation
scripts/02_check_coverage.py uses), this script:
  - fetches supplement attachment(s) via pyzotero (if any) and extracts
    their text, caching it to data_root/fulltext_cache/{zotero_key}_supp.txt
  - isolates the Methods section from the main text and, if present, the
    supplement text, using a header-based heuristic (no LLM/API calls)
  - writes isolated output to
    data_root/fulltext_cache/{zotero_key}_methods_main.txt and
    data_root/fulltext_cache/{zotero_key}_methods_supp.txt

It does not do attribution or pipeline-step extraction (stages 4/5's job).

Usage:
  python scripts/03_extract_methods.py              # full collection
  python scripts/03_extract_methods.py --limit 5     # first 5 items by zotero_key
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
import mammoth
import pdfplumber
import yaml
from pyzotero import zotero

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "project_config.yaml"
MANUAL_OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "config" / "manual_methods_overrides.json"

# Local Zotero API convention (see scripts/01_fetch_corpus.py for details):
# "0" / "user" is the standard placeholder pyzotero/zotero-mcp use to talk
# to the local desktop API at localhost:23119, which ignores real
# library_id/api_key.
LOCAL_LIBRARY_ID = "0"
LOCAL_LIBRARY_TYPE = "user"

# Same threshold scripts/02_check_coverage.py uses to distinguish real
# fulltext from a stray header/footer line pulled off an image-based PDF.
MIN_FULLTEXT_CHARS = 200

# Same supplement-filename heuristic as scripts/02_check_coverage.py (kept
# duplicated here rather than imported, matching that script's own
# duplication of scripts/01_fetch_corpus.py's Zotero-client helpers — each
# stage script in this pipeline is meant to run standalone).
SUPPLEMENT_FILENAME_MARKERS = ["supp", "supplementary", "appendix", "si", "s1", "s2", "mmc"]
SUPPLEMENT_ESM_PATTERN = re.compile(r"(?i)esm[-_]?\d|\d[-_]?esm|_esm(?:[^a-z0-9]|$)")
# Science's supplementary-materials naming convention — see
# scripts/02_check_coverage.py for the corpus-wide false-positive check
# that justifies anchoring to "_sm" right before the extension.
SUPPLEMENT_SM_PATTERN = re.compile(r"(?i)_sm\.[a-z0-9]+\Z")

# Manual curation, not something to infer automatically: papers confirmed
# (by reading the actual cached fulltext) to be intentionally out of scope
# for stage 4/5 rather than pending extraction failures — reviews/
# commentaries and software-description papers with no empirical Methods
# section, one retracted article whose cached fulltext is just the
# retraction notice, one paper manually reviewed and judged not
# relevant to this project's scope, one preprint superseded by its own
# published version (see "superseded_duplicate" below), and one dead
# Zotero key retained only as a historical record (see
# "defunct_zotero_key" below). See the stage-3 full-run reports for how
# each was confirmed.
# Tagged via exclusion_reason in manifest.db; their cached text and
# extraction_method are left untouched.
EXCLUSION_REASONS = {
    "4DHCXNES": "review_or_commentary",
    "642U6I8S": "review_or_commentary",
    "67UM7LCN": "review_or_commentary",
    "JH9IHBJH": "review_or_commentary",
    "I6IQRZTG": "software_description_no_methods",
    "F6JNRQZA": "software_description_no_methods",
    "MASWIV2A": "retracted",
    "UA49IT7M": "not_relevant",
    "NE32ANTQ": "review_or_commentary",
    "PG4C3BZX": "review_or_commentary",
    "WKXA9QZD": "review_or_commentary",
    "IKWDTTRN": "review_or_commentary",
    "YB9JEM6T": "software_description_no_methods",
    "H9K3DYM4": "not_relevant",
    "6NTFH4CJ": "not_relevant",
    "BTPMPI4L": "review_or_commentary",
    # IBUNAE3Y's Zotero metadata was the correct published-Neuron record
    # (DOI 10.1016/j.neuron.2026.06.010) for "Recurrent connectivity
    # shapes spatial coding in hippocampal CA3 subregions," but its PDF
    # attachment (key IFEIH9U9, titled "Accepted Version") actually
    # pointed to the bioRxiv preprint URL — the real Elsevier-published
    # PDF was never fetched under this key. During manual Zotero cleanup
    # of that wrong-attachment problem, IBUNAE3Y was trashed and then
    # permanently removed from the library (a direct key lookup now
    # 404s); a new item, LKVXCUIR, was created via the ScienceDirect
    # connector with the correct PDF and marked (via a `dc:replaces`
    # relation) as superseding IBUNAE3Y. IBUNAE3Y's manifest.db row is
    # kept only so this history isn't lost — it is not an active
    # exclusion decision, since the paper itself is not out of scope,
    # only this particular zotero_key is dead. See the "zotero_keys are
    # not guaranteed stable" note in CLAUDE.md's Known issues section.
    "IBUNAE3Y": (
        "defunct_zotero_key — item no longer exists in Zotero library "
        "(merged/replaced by LKVXCUIR during manual duplicate cleanup); "
        "row retained as historical record only, not an active exclusion "
        "decision"
    ),
    # 5TKU4RYR is the bioRxiv preprint of the same study now published as
    # LKVXCUIR (see IBUNAE3Y above for how LKVXCUIR came to be the
    # canonical Zotero record). Both versions were fully processed
    # through stage 4 with the same three matched target authors (Eunji
    # Kong, Zhenrui Liao, Tristan Geiller) at the same tier/confidence —
    # LKVXCUIR is canonical going forward; 5TKU4RYR is excluded from
    # stage 5 to avoid duplicate pipeline extractions of the same study.
    "5TKU4RYR": "superseded_duplicate",
}

# --- Methods-section header heuristic ---------------------------------
#
# Real extracted text from two-column journal PDFs is messy: a section
# header that spans the full page width extracts as its own clean line
# (e.g. "RESULTS", "REFERENCES"), but a header that sits at the top of a
# column often gets glued with no separating space to the first word of
# whatever text pdfplumber reads next from the adjacent column (e.g.
# "EXPERIMENTALPROCEDURES with10%FBSwithoutantibiotics..." or "oNLINe
# MetHods silencing in separate experiments..."). To catch both forms we
# compare a letters-only, lowercased, whitespace/punctuation-stripped
# ("collapsed") version of each line against known header phrases using
# startswith() rather than requiring the line to equal the phrase exactly.
#
# The plain section names below ("results", "discussion", ...) are common
# English words that could plausibly open an ordinary sentence at the top
# of a column, so — unlike the more distinctive multi-word/compound
# phrases — they're only accepted as an exact whole-line match (collapsed
# line has nothing else glued to it).
START_PREFIX_PHRASES = [
    "supplementalexperimentalprocedures",
    "supplementaryexperimentalprocedures",
    "materialsandmethods",
    "methodsandmaterials",
    "methodsandresults",
    "experimentalprocedures",
    "starmethods",
    "onlinemethods",
]
START_EQUALITY_PHRASES = {"methods"}

# Bare "Methods" gets a second chance beyond the whole-line equality check
# above: a real "Methods" header that sits at the top of a two-column-PDF
# column is routinely glued with no separating space to whatever the
# adjacent column's text happens to be at that point (see
# "Methods On day 1, RC was carried out..." in KKAPPFAJ, or "Methods step
# was necessary to obtain a reference z stack..." in RBL2PSLI — the latter
# glues onto a lowercase word mid-paragraph from elsewhere in the column,
# not a capitalized sentence start, so this pattern doesn't require the
# glued word itself to look like a sentence start).
#
# It DOES require "Methods"/"METHODS" itself to be capitalized, not plain
# lowercase "methods" — real section headers are stylistically capitalized
# in the source PDF, whereas plain "methods" starting a line is more often
# just the common noun starting a mid-column-wrap sentence. This is not a
# hypothetical: an earlier, case-insensitive version of this pattern
# false-matched "methods for histology and microscopy image alignment. Of
# [...]" in Y9DG73R4 — ordinary Related-Work prose that happened to wrap
# to the top of a column — before a genuine "2. METHODS" header later in
# the same document. Case is a cheap, effective discriminator between the
# two.
#
# The two known false-positive traps are rejected upstream regardless,
# before this pattern is ever consulted: is_candidate_header_line() rejects
# a leading "("/"[" (catches inline cross-references like "(see Methods)"
# or "(Online Methods, Fig. 3d...)"), and CITATION_TAIL_PATTERN rejects a
# trailing "(YEAR)." (catches bibliography lines like "Nat. Methods
# (2003)." — which this pattern also structurally can't match anyway,
# since the character right after "Methods " there is "(", not a letter).
#
# The optional leading "\d+[.)]?\s+" tolerates a numbered-section prefix
# (e.g. "2. METHODS Algorithm1:" in Y9DG73R4, a CS-style numbered paper),
# which the plain collapsed-equality check above can't match once the next
# subsection title is glued on with no space.
BARE_METHODS_GLUED_PATTERN = re.compile(r"^(?:\d+[.)]?\s+)?(?:Methods|METHODS)\s+[a-zA-Z]")

END_PREFIX_PHRASES = [
    "authorcontributions",
    "declarationofinterests",
    "conflictofinterest",
    "conflictsofinterest",
    "supplementalinformation",
    "supplementaryinformation",
    "supplementalreferences",
    "supplementaryreferences",
    "figurelegends",
]
END_EQUALITY_PHRASES = {"results", "discussion", "references", "acknowledgments", "acknowledgements"}

# Length threshold (in pages, or page-equivalents for docx text — see
# CHARS_PER_PAGE_ESTIMATE) under which a supplement with no internal
# header split is treated as methods-relevant in its entirety, per the
# stage-3 spec ("many supplement files are effectively all methods content
# already").
SUPP_WHOLE_DOC_PAGE_THRESHOLD = 15
CHARS_PER_PAGE_ESTIMATE = 3000


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_local_zotero_client() -> zotero.Zotero:
    """Build a pyzotero client against the local Zotero desktop API.

    Same HTTP/1.1 pin as scripts/01_fetch_corpus.py and
    scripts/02_check_coverage.py: Zotero 8's local server (port 23119)
    rejects httpx's default HTTP/2 negotiation attempt with a 502.
    """
    client = httpx.Client(
        transport=httpx.HTTPTransport(http1=True, http2=False),
        follow_redirects=True,
    )
    return zotero.Zotero(
        library_id=LOCAL_LIBRARY_ID,
        library_type=LOCAL_LIBRARY_TYPE,
        local=True,
        client=client,
    )


def find_collection_key(zot: zotero.Zotero, collection_name: str) -> str:
    collections = zot.everything(zot.collections())
    matches = [c for c in collections if c["data"]["name"] == collection_name]
    if not matches:
        available = ", ".join(c["data"]["name"] for c in collections)
        raise SystemExit(
            f"Collection '{collection_name}' not found in the active Zotero "
            f"library. Available collections: {available}"
        )
    return matches[0]["key"]


def filename_matches_supplement(filename: str) -> bool:
    lower = filename.lower()
    if any(marker in lower for marker in SUPPLEMENT_FILENAME_MARKERS):
        return True
    if SUPPLEMENT_SM_PATTERN.search(filename):
        return True
    return bool(SUPPLEMENT_ESM_PATTERN.search(filename))


def find_supplement_attachments(
    zot: zotero.Zotero, item_key: str, exclude_filename: str | None
) -> list[tuple[str, str, str]]:
    """Return (attachment_key, filename, kind) for supplementary attachments,
    where kind is "pdf" or "docx". Attachments that don't match the
    filename heuristic, or that match but aren't pdf/docx, are skipped —
    same scope as scripts/02_check_coverage.py's supplement_type enum."""
    children = zot.children(item_key)
    matches = []
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        filename = data.get("filename") or ""
        if not filename or filename == exclude_filename:
            continue
        if not filename_matches_supplement(filename):
            continue
        content_type = data.get("contentType", "")
        lower_name = filename.lower()
        if content_type == "application/pdf" or lower_name.endswith(".pdf"):
            kind = "pdf"
        elif content_type in (
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ) or lower_name.endswith((".doc", ".docx")):
            kind = "docx"
        else:
            continue
        matches.append((child["key"], filename, kind))
    return matches


def read_fulltext_char_count(fulltext_dir: Path, zotero_key: str) -> int:
    path = fulltext_dir / f"{zotero_key}_main.txt"
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").strip())


def compute_fulltext_status(has_main_pdf: bool, fulltext_extracted: bool, fulltext_chars: int) -> str:
    """Same logic as scripts/02_check_coverage.py's compute_fulltext_status."""
    if not has_main_pdf:
        return "missing"
    if fulltext_extracted and fulltext_chars > MIN_FULLTEXT_CHARS:
        return "ok"
    return "ocr_needed"


def ensure_methods_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(manifest)").fetchall()}
    additions = {
        "supplement_extracted": "INTEGER NOT NULL DEFAULT 0",
        "methods_extracted_main": "INTEGER NOT NULL DEFAULT 0",
        "methods_extracted_supp": "INTEGER NOT NULL DEFAULT 0",
        "extraction_method": "TEXT",
        "last_methods_check": "TEXT",
        "possible_trailing_contamination": "INTEGER NOT NULL DEFAULT 0",
        "exclusion_reason": "TEXT",
    }
    for col, decl in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE manifest ADD COLUMN {col} {decl}")
    conn.commit()


def apply_exclusion_reasons(conn: sqlite3.Connection) -> None:
    """Tag the manually curated EXCLUSION_REASONS papers as intentionally
    out of scope for stage 4/5. Only touches exclusion_reason — leaves
    cached fulltext and extraction_method as-is, so this can run every
    time without disturbing anything else."""
    conn.executemany(
        "UPDATE manifest SET exclusion_reason = ? WHERE zotero_key = ?",
        [(reason, key) for key, reason in EXCLUSION_REASONS.items()],
    )
    conn.commit()


def is_methods_processed(row: dict) -> bool:
    """A paper is done — skip it on rerun — once it's either manually
    excluded (see EXCLUSION_REASONS) or its main text has already yielded
    isolated Methods content.

    A paper whose main-text extraction previously failed is NOT considered
    done: it's retried every run. This heuristic is deterministic, so an
    unexcluded failure isn't noise to shrug off — it's a signal that the
    header heuristic itself has a gap (exactly how the two false-positive
    traps and the bare-"Methods" glued-header gap got found and fixed).
    Auto-retrying means a heuristic improvement lands on old failures
    immediately, without a manual manifest.db reset first."""
    if row.get("exclusion_reason") is not None:
        return True
    return bool(row.get("methods_extracted_main"))


def update_manifest_methods(
    conn: sqlite3.Connection,
    zotero_key: str,
    *,
    supplement_extracted: bool,
    methods_extracted_main: bool,
    methods_extracted_supp: bool,
    extraction_method: str,
    possible_trailing_contamination: bool,
) -> None:
    conn.execute(
        """
        UPDATE manifest SET
            supplement_extracted = ?,
            methods_extracted_main = ?,
            methods_extracted_supp = ?,
            extraction_method = ?,
            last_methods_check = ?,
            possible_trailing_contamination = ?
        WHERE zotero_key = ?
        """,
        (
            int(supplement_extracted),
            int(methods_extracted_main),
            int(methods_extracted_supp),
            extraction_method,
            datetime.now(timezone.utc).isoformat(),
            int(possible_trailing_contamination),
            zotero_key,
        ),
    )
    conn.commit()


def collapse(line: str) -> str:
    """Lowercase and strip everything but letters, so header phrases match
    regardless of spacing, numbering, or punctuation glued on by messy
    two-column PDF extraction."""
    return re.sub(r"[^a-z]", "", line.lower())


CITATION_TAIL_PATTERN = re.compile(r"\(\d{4}\)\.?\s*$")


def is_candidate_header_line(stripped_line: str) -> bool:
    """Reject lines that read like an inline cross-reference or a
    bibliography entry rather than an actual section header:
      - opens with a parenthesis/bracket — a real header is never wrapped
        in punctuation, but a cross-reference like "(see Experimental
        Procedures)" or "(Online Methods, Fig. 3d...)" reads exactly like
        one once collapse() strips the parenthesis away, and column-merged
        text frequently wraps such a reference to the very start of a
        line.
      - ends in a bare "(YEAR)." — a citation-list artifact. A reference
        list entry ending "... Nat. Methods (2003)." collapses to exactly
        "methods" once the year and punctuation are stripped, which is
        indistinguishable from a genuine bare "Methods" header without
        this check.
    """
    if stripped_line.startswith(("(", "[")):
        return False
    if CITATION_TAIL_PATTERN.search(stripped_line):
        return False
    return True


def find_section_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or not is_candidate_header_line(stripped):
            continue
        collapsed = collapse(stripped)
        if not collapsed:
            continue
        if collapsed in START_EQUALITY_PHRASES:
            return i
        if any(collapsed.startswith(p) for p in START_PREFIX_PHRASES):
            return i
        if BARE_METHODS_GLUED_PATTERN.match(stripped):
            return i
    return None


def find_section_end(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or not is_candidate_header_line(stripped):
            continue
        collapsed = collapse(stripped)
        if not collapsed:
            continue
        if collapsed in END_EQUALITY_PHRASES:
            return i
        if any(collapsed.startswith(p) for p in END_PREFIX_PHRASES):
            return i
    return len(lines)


# --- Trailing-contamination advisory flag ------------------------------
#
# The header/footer heuristic above isolates a Methods section by line,
# but on two-column source PDFs the columns can be interleaved within the
# same lines (see 03_extract_methods report from the 5-paper test batch:
# 2LKHNA25's isolated Methods text runs into bibliography entries with no
# clean header line to stop at). This is a purely advisory smoke-detector
# for that failure mode: it does not attempt to locate or trim the
# contamination, only to flag the paper for stage 5's human review.
TRAILING_CONTAMINATION_FRACTION = 0.15
TRAILING_CONTAMINATION_MIN_HITS = 3
ET_AL_PATTERN = re.compile(r"et al\.?", re.IGNORECASE)
YEAR_PAREN_PATTERN = re.compile(r"\(\d{4}[a-z]?\)")


def detect_trailing_contamination(text: str) -> bool:
    """Flag when the last ~15% of an isolated Methods text (by character
    count) contains several "et al." / "(YEAR)" citation-style patterns —
    a density that's atypical for methods prose but exactly what a
    bibliography looks like."""
    if not text:
        return False
    tail_len = max(1, int(len(text) * TRAILING_CONTAMINATION_FRACTION))
    tail = text[-tail_len:]
    hits = len(ET_AL_PATTERN.findall(tail)) + len(YEAR_PAREN_PATTERN.findall(tail))
    return hits >= TRAILING_CONTAMINATION_MIN_HITS


# --- Manual methods-boundary overrides ---------------------------------
#
# A small number of papers have a genuine Methods section that no
# reasonable header heuristic can isolate: a Nature Protocols paper whose
# entire body *is* the method, or a page whose layout got so scrambled by
# column-interleaving during stage-1 PDF extraction that no header line
# survives intact anywhere. For these, config/manual_methods_overrides.json
# records a human-verified start_marker/end_marker pair (exact substrings
# confirmed present in the cached fulltext) to slice directly, instead of
# running the heuristic at all.


def load_manual_overrides() -> dict:
    if not MANUAL_OVERRIDES_PATH.exists():
        return {}
    with MANUAL_OVERRIDES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def apply_manual_override(entry: dict, text: str) -> tuple[str | None, str]:
    """Slice text between entry's start_marker (inclusive) and end_marker
    (exclusive) via exact substring match. end_marker of None means "through
    the end of the text" (used for the whole-document override). Returns
    (isolated_text_or_None, extraction_method) — extraction_method is
    "manual_override" on success, "failed" if a marker isn't actually found
    in this text (a stale override shouldn't silently produce wrong output)."""
    start_marker = entry["start_marker"]
    end_marker = entry.get("end_marker")

    start_idx = text.find(start_marker)
    if start_idx == -1:
        return None, "failed"

    if end_marker is None:
        end_idx = len(text)
    else:
        end_idx = text.find(end_marker, start_idx + len(start_marker))
        if end_idx == -1:
            end_idx = len(text)

    isolated = text[start_idx:end_idx].strip()
    if not isolated:
        return None, "failed"
    return isolated, "manual_override"


def isolate_methods_main(text: str) -> tuple[str | None, str]:
    """Main text: header-only, never a whole-doc guess. Returns
    (isolated_text_or_None, extraction_method)."""
    lines = text.splitlines()
    start = find_section_start(lines)
    if start is None:
        return None, "failed"
    end = find_section_end(lines, start)
    isolated = "\n".join(lines[start:end]).strip()
    if not isolated:
        return None, "failed"
    return isolated, "heuristic_header"


def isolate_methods_supp(text: str, estimated_pages: float) -> tuple[str | None, str]:
    """Supplement text: try the header heuristic first; if that fails and
    the supplement is short, treat the whole document as methods-relevant
    content rather than discarding it for lack of a header match."""
    lines = text.splitlines()
    start = find_section_start(lines)
    if start is not None:
        end = find_section_end(lines, start)
        isolated = "\n".join(lines[start:end]).strip()
        if isolated:
            return isolated, "heuristic_header"
    if estimated_pages <= SUPP_WHOLE_DOC_PAGE_THRESHOLD:
        stripped = text.strip()
        if stripped:
            return stripped, "heuristic_whole_doc"
    return None, "failed"


def extract_pdf_text_and_pages(pdf_path: Path) -> tuple[str | None, int]:
    """Same approach as scripts/01_fetch_corpus.py's extract_fulltext, plus
    a page count (used to gauge supplement length for the whole-doc
    fallback)."""
    texts = []
    page_count = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_count += 1
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
    fulltext = "\n\n".join(texts).strip()
    return (fulltext or None), page_count


def extract_docx_text(docx_path: Path) -> str | None:
    """mammoth over python-docx: mammoth's extract_raw_text() is built to
    degrade gracefully on the kind of real-world messy docx these
    supplements are (tracked-changes artifacts, odd embedded styles,
    non-standard structure) and just pulls text, whereas python-docx's
    Document() model expects well-formed OOXML and is more likely to raise
    on a document that doesn't quite conform."""
    with docx_path.open("rb") as f:
        result = mammoth.extract_raw_text(f)
    text = (result.value or "").strip()
    return text or None


def extract_supplement_text(
    zot: zotero.Zotero,
    attachments: list[tuple[str, str, str]],
    supp_raw_dir: Path,
) -> tuple[str | None, float]:
    """Download and extract text for all supplement attachments for one
    paper. Multiple attachments (the "mixed" pdf+docx case, or simply
    several files of the same type) are concatenated with a separator
    naming the source file. Returns (concatenated_text_or_None,
    estimated_page_count)."""
    parts = []
    total_pages = 0.0
    supp_raw_dir.mkdir(parents=True, exist_ok=True)
    for attachment_key, filename, kind in attachments:
        local_path = supp_raw_dir / filename
        try:
            zot.dump(attachment_key, filename=filename, path=str(supp_raw_dir))
        except Exception as exc:  # noqa: BLE001 - report and continue with other attachments
            print(f"    supplement download failed for {filename}: {exc}")
            continue
        if not local_path.exists() or local_path.stat().st_size == 0:
            print(f"    supplement download produced an empty/missing file: {filename}")
            continue
        try:
            if kind == "pdf":
                text, pages = extract_pdf_text_and_pages(local_path)
                if text:
                    total_pages += pages
            else:
                text = extract_docx_text(local_path)
                if text:
                    total_pages += max(1, len(text) // CHARS_PER_PAGE_ESTIMATE)
        except Exception as exc:  # noqa: BLE001 - report and continue with other attachments
            print(f"    supplement text extraction failed for {filename}: {exc}")
            continue
        if text:
            parts.append(f"=== SOURCE: {filename} ===\n{text}")
    if not parts:
        return None, 0.0
    return "\n\n".join(parts), total_pages


def process_paper(
    zot: zotero.Zotero,
    zotero_key: str,
    manifest_row: dict,
    data_root: Path,
    fulltext_dir: Path,
    manual_overrides: dict,
) -> tuple[bool, bool, bool, str, bool]:
    """Returns (methods_extracted_main, supplement_extracted,
    methods_extracted_supp, overall_extraction_method,
    possible_trailing_contamination)."""
    main_text_path = fulltext_dir / f"{zotero_key}_main.txt"
    main_text = main_text_path.read_text(encoding="utf-8")
    if zotero_key in manual_overrides:
        isolated_main, main_method = apply_manual_override(manual_overrides[zotero_key], main_text)
    else:
        isolated_main, main_method = isolate_methods_main(main_text)
    methods_extracted_main = isolated_main is not None
    possible_trailing_contamination = False
    if methods_extracted_main:
        (fulltext_dir / f"{zotero_key}_methods_main.txt").write_text(isolated_main, encoding="utf-8")
        possible_trailing_contamination = detect_trailing_contamination(isolated_main)

    main_pdf_filename = manifest_row["main_pdf_filename"]
    attachments = find_supplement_attachments(zot, zotero_key, main_pdf_filename)

    supplement_extracted = False
    methods_extracted_supp = False
    supp_method = "failed"
    if attachments:
        supp_raw_dir = data_root / "supplements" / zotero_key
        supp_text, est_pages = extract_supplement_text(zot, attachments, supp_raw_dir)
        if supp_text:
            (fulltext_dir / f"{zotero_key}_supp.txt").write_text(supp_text, encoding="utf-8")
            supplement_extracted = True
            isolated_supp, supp_method = isolate_methods_supp(supp_text, est_pages)
            methods_extracted_supp = isolated_supp is not None
            if methods_extracted_supp:
                (fulltext_dir / f"{zotero_key}_methods_supp.txt").write_text(isolated_supp, encoding="utf-8")

    # extraction_method is a single column tracking whichever attempt
    # actually produced methods content: main takes priority (it's the
    # mandatory input), falling back to the supplement's method, then
    # "failed" if neither worked.
    if methods_extracted_main:
        overall_method = main_method
    elif methods_extracted_supp:
        overall_method = supp_method
    else:
        overall_method = "failed"

    return (
        methods_extracted_main,
        supplement_extracted,
        methods_extracted_supp,
        overall_method,
        possible_trailing_contamination,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only consider the first N collection items, sorted by zotero_key (for test runs).",
    )
    args = parser.parse_args()

    config = load_config()
    data_root = Path(config["data_root"])
    collection_name = config["zotero"]["collection_name"]
    fulltext_dir = data_root / "fulltext_cache"
    manifest_path = data_root / "manifest.db"

    print(f"Connecting to local Zotero API for collection '{collection_name}'...")
    zot = make_local_zotero_client()
    collection_key = find_collection_key(zot, collection_name)
    items = zot.everything(zot.collection_items_top(collection_key))
    items.sort(key=lambda it: it["key"])
    if args.limit is not None:
        items = items[: args.limit]

    print(f"Found {len(items)} item(s) to consider in '{collection_name}'.")

    conn = sqlite3.connect(manifest_path)
    conn.row_factory = sqlite3.Row
    ensure_methods_columns(conn)
    apply_exclusion_reasons(conn)
    manifest = {row["zotero_key"]: dict(row) for row in conn.execute("SELECT * FROM manifest").fetchall()}
    manual_overrides = load_manual_overrides()

    processed = skipped = ok_main = failed_main = 0
    for i, item in enumerate(items, start=1):
        zotero_key = item["key"]
        title = item["data"].get("title", "(untitled)")
        print(f"[{i}/{len(items)}] {zotero_key} — {title}")

        manifest_row = manifest.get(zotero_key)
        if manifest_row is None:
            print("    skipped — not in manifest.db, run 01_fetch_corpus.py first")
            skipped += 1
            continue

        has_main_pdf = bool(manifest_row["has_main_pdf"])
        fulltext_extracted = bool(manifest_row["fulltext_extracted"])
        fulltext_chars = read_fulltext_char_count(fulltext_dir, zotero_key) if has_main_pdf else 0
        status = compute_fulltext_status(has_main_pdf, fulltext_extracted, fulltext_chars)
        if status != "ok":
            print(f"    skipped — fulltext_status is '{status}', not 'ok'")
            skipped += 1
            continue

        if is_methods_processed(manifest_row):
            if manifest_row.get("exclusion_reason") is not None:
                print(f"    skipped — excluded ({manifest_row['exclusion_reason']})")
            else:
                print("    skipped — main methods already found")
            skipped += 1
            continue

        (
            methods_extracted_main,
            supplement_extracted,
            methods_extracted_supp,
            overall_method,
            possible_trailing_contamination,
        ) = process_paper(zot, zotero_key, manifest_row, data_root, fulltext_dir, manual_overrides)

        update_manifest_methods(
            conn,
            zotero_key,
            supplement_extracted=supplement_extracted,
            methods_extracted_main=methods_extracted_main,
            methods_extracted_supp=methods_extracted_supp,
            extraction_method=overall_method,
            possible_trailing_contamination=possible_trailing_contamination,
        )

        processed += 1
        if methods_extracted_main:
            ok_main += 1
        else:
            failed_main += 1

        print(
            f"    main methods: {'Y' if methods_extracted_main else 'N'} | "
            f"supplement extracted: {'Y' if supplement_extracted else 'n/a'} | "
            f"supplement methods: {'Y' if methods_extracted_supp else 'N'} | "
            f"method={overall_method} | "
            f"possible_trailing_contamination={'Y' if possible_trailing_contamination else 'N'}"
        )

    conn.close()
    print(
        f"\nDone. {processed} processed ({ok_main} main methods found, "
        f"{failed_main} main extraction_failed), {skipped} skipped, "
        f"out of {len(items)} considered."
    )


if __name__ == "__main__":
    main()

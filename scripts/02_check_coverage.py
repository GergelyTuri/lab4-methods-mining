"""Stage 2: coverage audit gate — outputs/coverage_report.md.

Reconciles data_root/manifest.db against the live Zotero collection named
in config/project_config.yaml (zotero.collection_name), inventories
supplementary attachments per item, and classifies fulltext extraction
status. This is a read-only report: it never modifies manifest.db, never
downloads anything, and never attempts OCR — it only flags what needs
attention before stage 3 (Methods-section isolation) begins.

Usage:
  python scripts/02_check_coverage.py
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import yaml
from jsonschema import validate
from pyzotero import zotero

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "project_config.yaml"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "coverage_manifest.schema.json"
REPORT_PATH = Path(__file__).resolve().parent.parent / "outputs" / "coverage_report.md"

# Local Zotero API convention (see scripts/01_fetch_corpus.py for details):
# "0" / "user" is the standard placeholder pyzotero/zotero-mcp use to talk
# to the local desktop API at localhost:23119, which ignores real
# library_id/api_key.
LOCAL_LIBRARY_ID = "0"
LOCAL_LIBRARY_TYPE = "user"

# A near-empty extracted-fulltext file (a stray header/footer line pulled
# off an otherwise-image-based PDF) should not count as "ok" fulltext.
MIN_FULLTEXT_CHARS = 200

# Case-insensitive filename substring markers used to recognize a
# non-main-PDF attachment as a supplement. Deliberately simple per the
# stage-2 spec ("don't over-engineer this"). "mmc" is Cell Press's
# "multimedia component" convention (mmc1.pdf, mmc2.xlsx...) and is safe
# as a bare substring — it's not an English word fragment like "si" is,
# so the false-positive risk is low.
SUPPLEMENT_FILENAME_MARKERS = ["supp", "supplementary", "appendix", "si", "s1", "s2", "mmc"]

# Springer/Nature's "Electronic Supplementary Material" convention (e.g.
# esm1.pdf, or the longer form Springer actually generates,
# "41593_2023_1234_MOESM1_ESM.pdf"). Unlike "mmc", bare "esm" is too
# risky as a substring — plausible words/tokens could contain it
# incidentally. Instead this requires "esm" to sit next to a digit
# (esm1, esm_2, 3esm — matching both esm1.pdf and the ESM1 inside
# MOESM1) or to appear as the literal "_esm" delimited suffix Springer's
# generator uses (matching the trailing "_ESM" in the long form above).
SUPPLEMENT_ESM_PATTERN = re.compile(r"(?i)esm[-_]?\d|\d[-_]?esm|_esm(?:[^a-z0-9]|$)")


def filename_matches_supplement(filename: str) -> bool:
    lower = filename.lower()
    if any(marker in lower for marker in SUPPLEMENT_FILENAME_MARKERS):
        return True
    return bool(SUPPLEMENT_ESM_PATTERN.search(filename))

# Journals known for extensive supplementary Methods sections. Cross-
# referenced (exact match, case-insensitive) against each paper's journal
# field only when no supplement was found locally, to distinguish "this
# paper likely has no supplement" from "nobody has attached it yet".
# Matched by exact name rather than substring: a naive substring check
# would make "Science" match journals like "Neuroscience" or "The European
# journal of neuroscience". Names below include the exact-casing variants
# PubMed/Zotero metadata actually uses in this collection (checked via a
# one-off scan of all 82 items' publicationTitle field) plus the baseline
# list from the stage-2 spec. Edit freely as the collection grows.
JOURNALS_WITH_SUPPLEMENTARY_METHODS = {
    "neuron",
    "cell",
    "cell reports",
    "cell reports methods",
    "elife",
    "nature",
    "nature neuroscience",
    "nature communications",
    "nature methods",
    "nature protocols",
    "science",
    "science (new york, n.y.)",
    "pnas",
    "proceedings of the national academy of sciences",
    "proceedings of the national academy of sciences of the united states of america",
    "current biology",
    "current biology : cb",
}

# Sort order for surfacing problems first in the report table.
FULLTEXT_STATUS_SEVERITY = {"paywalled": 0, "missing": 1, "ocr_needed": 2, "ok": 3}


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_local_zotero_client() -> zotero.Zotero:
    """Build a pyzotero client against the local Zotero desktop API.

    Same HTTP/1.1 pin as scripts/01_fetch_corpus.py: Zotero 8's local
    server (port 23119) rejects httpx's default HTTP/2 negotiation attempt
    with a 502, so the transport is forced to HTTP/1.1.
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


def parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", date_str)
    return int(match.group(1)) if match else None


def read_manifest(db_path: Path) -> dict[str, dict[str, Any]]:
    """Read all manifest.db rows, keyed by zotero_key. Read-only connection."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM manifest").fetchall()
    finally:
        conn.close()
    return {row["zotero_key"]: dict(row) for row in rows}


def read_fulltext_char_count(data_root: Path, zotero_key: str) -> int:
    path = data_root / "fulltext_cache" / f"{zotero_key}_main.txt"
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").strip())


def compute_fulltext_status(has_main_pdf: bool, fulltext_extracted: bool, fulltext_chars: int) -> str:
    if not has_main_pdf:
        return "missing"
    if fulltext_extracted and fulltext_chars > MIN_FULLTEXT_CHARS:
        return "ok"
    return "ocr_needed"


def classify_supplement(children: list[dict], exclude_filename: str | None) -> tuple[bool, str | None]:
    """Classify non-main-PDF attachments as supplementary material.

    Only attachments whose filename matches one of the supplement markers
    are considered. Among those, type is derived from contentType/
    extension; attachments matching neither pdf nor docx (e.g. a stray
    .xlsx or a Zotero web-page snapshot) are not counted — the
    coverage_manifest schema's supplement_type enum only supports
    pdf/docx/mixed/none, and in practice non-PDF/docx "supplements" have
    not shown up in this collection.
    """
    found_pdf = False
    found_docx = False
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
            found_pdf = True
        elif content_type in (
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ) or lower_name.endswith((".doc", ".docx")):
            found_docx = True

    if found_pdf and found_docx:
        return True, "mixed"
    if found_pdf:
        return True, "pdf"
    if found_docx:
        return True, "docx"
    return False, "none"


def find_main_pdf_attachment_key_and_filename(zot: zotero.Zotero, item_key: str) -> str | None:
    """Same largest-PDF heuristic as scripts/01_fetch_corpus.py, used only
    to identify (by filename) which attachment to exclude from supplement
    classification when manifest.db has no main_pdf_filename on record."""
    children = zot.children(item_key)
    pdf_children = [
        c
        for c in children
        if c["data"].get("itemType") == "attachment"
        and c["data"].get("contentType") == "application/pdf"
    ]
    if not pdf_children:
        return None
    pdf_children.sort(key=lambda c: len(c["data"].get("md5") or ""), reverse=True)
    return pdf_children[0]["data"].get("filename")


def build_flag(
    *,
    manifest_missing: bool,
    stale: bool,
    fulltext_status: str,
    supplement_type: str | None,
    journal: str | None,
) -> tuple[str | None, bool]:
    """Returns (flag_text, is_verify_manually_flag)."""
    parts = []
    if stale:
        parts.append("stale manifest row — item removed from collection")
    if manifest_missing:
        parts.append("missing from manifest — run 01_fetch_corpus.py")
    if fulltext_status == "ocr_needed":
        parts.append("near-empty fulltext — likely scanned, needs OCR")
    if fulltext_status == "missing" and not manifest_missing and not stale:
        parts.append("no main PDF in Zotero")

    is_verify_manually = False
    if supplement_type == "none" and not stale:
        normalized_journal = (journal or "").strip().lower()
        if normalized_journal in JOURNALS_WITH_SUPPLEMENTARY_METHODS:
            parts.append(
                "no supplement found — this journal commonly has supplementary "
                "methods, verify manually on publisher site"
            )
            is_verify_manually = True
        else:
            parts.append("no supplement found")

    return ("; ".join(parts) if parts else None), is_verify_manually


def truncate(text: str, length: int = 50) -> str:
    text = text or ""
    return text if len(text) <= length else text[: length - 1] + "…"


def main() -> None:
    config = load_config()
    data_root = Path(config["data_root"])
    collection_name = config["zotero"]["collection_name"]
    manifest_path = data_root / "manifest.db"

    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        import json

        schema = json.load(f)

    print(f"Connecting to local Zotero API for collection '{collection_name}'...")
    zot = make_local_zotero_client()
    collection_key = find_collection_key(zot, collection_name)
    items = zot.everything(zot.collection_items_top(collection_key))
    items_by_key = {item["key"]: item for item in items}

    manifest = read_manifest(manifest_path)

    collection_keys = set(items_by_key)
    manifest_keys = set(manifest)
    missing_from_manifest = sorted(collection_keys - manifest_keys)
    stale_manifest_rows = sorted(manifest_keys - collection_keys)

    table_entries: list[tuple[dict, str, bool]] = []  # (validated coverage row, title, is_current_collection_item)
    verify_manually_count = 0

    print(f"Auditing {len(items_by_key)} collection item(s)...")
    for i, (zotero_key, item) in enumerate(sorted(items_by_key.items()), start=1):
        data = item["data"]
        manifest_row = manifest.get(zotero_key)
        title = data.get("title") or (manifest_row["title"] if manifest_row else "(untitled)")
        year = parse_year(data.get("date"))
        if year is None and manifest_row:
            year = manifest_row["year"]
        doi = data.get("DOI") or None
        journal = data.get("publicationTitle") or data.get("repository") or None

        has_main_pdf = bool(manifest_row["has_main_pdf"]) if manifest_row else False
        fulltext_extracted = bool(manifest_row["fulltext_extracted"]) if manifest_row else False
        fulltext_chars = read_fulltext_char_count(data_root, zotero_key) if has_main_pdf else 0
        fulltext_status = compute_fulltext_status(has_main_pdf, fulltext_extracted, fulltext_chars)

        main_pdf_filename = manifest_row["main_pdf_filename"] if manifest_row else None
        if main_pdf_filename is None:
            main_pdf_filename = find_main_pdf_attachment_key_and_filename(zot, zotero_key)

        children = zot.children(zotero_key)
        has_supplement, supplement_type = classify_supplement(children, main_pdf_filename)

        flag, is_verify = build_flag(
            manifest_missing=manifest_row is None,
            stale=False,
            fulltext_status=fulltext_status,
            supplement_type=supplement_type,
            journal=journal,
        )
        if is_verify:
            verify_manually_count += 1

        row = {
            "zotero_key": zotero_key,
            "doi": doi,
            "year": year,
            "has_main_pdf": has_main_pdf,
            "has_supplement": has_supplement,
            "supplement_type": supplement_type,
            "fulltext_status": fulltext_status,
            "flag": flag,
        }
        try:
            validate(instance=row, schema=schema)
        except Exception as exc:
            raise SystemExit(f"Coverage row for {zotero_key} failed schema validation: {exc}") from exc

        table_entries.append((row, title, True))
        print(f"[{i}/{len(items_by_key)}] {zotero_key} — {fulltext_status}")

    # Stale manifest rows: no longer in the collection, but still worth surfacing.
    for zotero_key in stale_manifest_rows:
        manifest_row = manifest[zotero_key]
        title = manifest_row["title"] or "(untitled)"
        has_main_pdf = bool(manifest_row["has_main_pdf"])
        fulltext_extracted = bool(manifest_row["fulltext_extracted"])
        fulltext_chars = read_fulltext_char_count(data_root, zotero_key) if has_main_pdf else 0
        fulltext_status = compute_fulltext_status(has_main_pdf, fulltext_extracted, fulltext_chars)

        flag, _ = build_flag(
            manifest_missing=False,
            stale=True,
            fulltext_status=fulltext_status,
            supplement_type=None,
            journal=None,
        )

        row = {
            "zotero_key": zotero_key,
            "doi": manifest_row["doi"],
            "year": manifest_row["year"],
            "has_main_pdf": has_main_pdf,
            "has_supplement": False,
            "supplement_type": None,
            "fulltext_status": fulltext_status,
            "flag": flag,
        }
        try:
            validate(instance=row, schema=schema)
        except Exception as exc:
            raise SystemExit(f"Coverage row for stale {zotero_key} failed schema validation: {exc}") from exc

        table_entries.append((row, title, False))

    table_entries.sort(
        key=lambda entry: (FULLTEXT_STATUS_SEVERITY[entry[0]["fulltext_status"]], entry[0]["zotero_key"])
    )

    # Summary counts are scoped to current collection items only — stale
    # manifest rows are a separate concern (see the mismatches line below)
    # and would otherwise inflate these totals past len(items_by_key).
    status_counts = {"ok": 0, "ocr_needed": 0, "missing": 0, "paywalled": 0}
    supplement_found_count = 0
    for row, _, is_current in table_entries:
        if not is_current:
            continue
        status_counts[row["fulltext_status"]] += 1
        if row["supplement_type"] not in (None, "none"):
            supplement_found_count += 1

    lines = []
    lines.append("# Coverage Report")
    lines.append("")
    lines.append(f"- Total papers in collection: {len(items_by_key)}")
    lines.append(
        "- Fulltext status (current collection items): "
        f"ok={status_counts['ok']}, ocr_needed={status_counts['ocr_needed']}, "
        f"missing={status_counts['missing']}, paywalled={status_counts['paywalled']}"
    )
    lines.append(f"- Papers with a supplement found: {supplement_found_count}")
    lines.append(
        "- Manifest/collection mismatches: "
        f"{len(stale_manifest_rows)} stale manifest row(s) (in manifest.db but no longer in the "
        f"collection), {len(missing_from_manifest)} collection item(s) missing from manifest "
        "(run 01_fetch_corpus.py)"
    )
    lines.append(f"- \"Verify manually\" flags (supplement-heavy journal, none found locally): {verify_manually_count}")
    lines.append("")
    lines.append("| zotero_key | title | year | has_main_pdf | has_supplement | supplement_type | fulltext_status | flag |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row, title, _ in table_entries:
        lines.append(
            "| {key} | {title} | {year} | {has_main_pdf} | {has_supplement} | {supplement_type} | {status} | {flag} |".format(
                key=row["zotero_key"],
                title=truncate(title).replace("|", "/"),
                year=row["year"] if row["year"] is not None else "—",
                has_main_pdf="yes" if row["has_main_pdf"] else "no",
                has_supplement="yes" if row["has_supplement"] else "no",
                supplement_type=row["supplement_type"] or "—",
                status=row["fulltext_status"],
                flag=(row["flag"] or "").replace("|", "/"),
            )
        )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {REPORT_PATH} ({len(table_entries)} rows).")


if __name__ == "__main__":
    main()

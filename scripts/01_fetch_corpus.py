"""Stage 1: pull the Zotero collection into data_root and build manifest.db.

For each top-level item in the collection named in
config/project_config.yaml (zotero.collection_name), this script:
  - saves the main PDF attachment (if any) to data_root/pdfs/{zotero_key}/
  - extracts its fulltext to data_root/fulltext_cache/{zotero_key}_main.txt
  - records the outcome as a row in data_root/manifest.db

It does not touch supplementary materials (stage 2's job) or do any
Methods-section isolation or other content processing (stage 3's job).

Usage:
  python scripts/01_fetch_corpus.py              # full collection
  python scripts/01_fetch_corpus.py --limit 5     # first 5 items by zotero_key
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import yaml
from pyzotero import zotero

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "project_config.yaml"

# Local Zotero API convention (see pyzotero / zotero-mcp): the local desktop
# API at localhost:23119 does not check library_id/api_key, but pyzotero
# still requires *some* library_id/library_type to build request URLs.
# "0" / "user" is the standard placeholder used for this by every local-mode
# pyzotero client, including our zotero-mcp server.
LOCAL_LIBRARY_ID = "0"
LOCAL_LIBRARY_TYPE = "user"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_local_zotero_client() -> zotero.Zotero:
    """Build a pyzotero client against the local Zotero desktop API.

    Zotero 8's local server (port 23119) only speaks HTTP/1.1; httpx's
    default HTTP/2 negotiation attempt gets a 502 from it. Pinning the
    transport to HTTP/1.1 is the same fix zotero-mcp applies for the same
    server, and is required for any request (not just file downloads) to
    succeed.
    """
    import httpx

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


def find_pdf_attachment(zot: zotero.Zotero, item_key: str) -> dict | None:
    """Return the child attachment dict for this item's main PDF, if any."""
    children = zot.children(item_key)
    pdf_children = [
        c
        for c in children
        if c["data"].get("itemType") == "attachment"
        and c["data"].get("contentType") == "application/pdf"
    ]
    if not pdf_children:
        return None
    # If more than one PDF attachment exists, prefer the one with a larger
    # md5 hash string as a rough proxy for "more complete" (same heuristic
    # zotero-mcp uses locally, in the absence of a real size field on the
    # child item payload).
    pdf_children.sort(key=lambda c: len(c["data"].get("md5") or ""), reverse=True)
    return pdf_children[0]


def init_manifest_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manifest (
            zotero_key TEXT PRIMARY KEY,
            title TEXT,
            year INTEGER,
            doi TEXT,
            date_fetched TEXT,
            has_main_pdf INTEGER NOT NULL DEFAULT 0,
            main_pdf_filename TEXT,
            fulltext_extracted INTEGER NOT NULL DEFAULT 0,
            last_checked TEXT
        )
        """
    )
    conn.commit()
    return conn


def is_fully_fetched(conn: sqlite3.Connection, zotero_key: str) -> bool:
    row = conn.execute(
        "SELECT has_main_pdf, fulltext_extracted FROM manifest WHERE zotero_key = ?",
        (zotero_key,),
    ).fetchone()
    return row is not None and row[0] == 1 and row[1] == 1


def upsert_manifest_row(
    conn: sqlite3.Connection,
    *,
    zotero_key: str,
    title: str | None,
    year: int | None,
    doi: str | None,
    has_main_pdf: bool,
    main_pdf_filename: str | None,
    fulltext_extracted: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT date_fetched FROM manifest WHERE zotero_key = ?", (zotero_key,)
    ).fetchone()
    date_fetched = existing[0] if existing else now
    conn.execute(
        """
        INSERT INTO manifest (
            zotero_key, title, year, doi, date_fetched,
            has_main_pdf, main_pdf_filename, fulltext_extracted, last_checked
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(zotero_key) DO UPDATE SET
            title = excluded.title,
            year = excluded.year,
            doi = excluded.doi,
            has_main_pdf = excluded.has_main_pdf,
            main_pdf_filename = excluded.main_pdf_filename,
            fulltext_extracted = excluded.fulltext_extracted,
            last_checked = excluded.last_checked
        """,
        (
            zotero_key,
            title,
            year,
            doi,
            date_fetched,
            int(has_main_pdf),
            main_pdf_filename,
            int(fulltext_extracted),
            now,
        ),
    )
    conn.commit()


def touch_last_checked(conn: sqlite3.Connection, zotero_key: str) -> None:
    conn.execute(
        "UPDATE manifest SET last_checked = ? WHERE zotero_key = ?",
        (datetime.now(timezone.utc).isoformat(), zotero_key),
    )
    conn.commit()


def extract_fulltext(pdf_path: Path) -> str | None:
    """Extract fulltext from a PDF with pdfplumber. Returns None if nothing
    extractable came out (e.g. an image-based PDF that needs OCR)."""
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
    fulltext = "\n\n".join(texts).strip()
    return fulltext or None


def process_item(
    zot: zotero.Zotero,
    item: dict,
    pdfs_dir: Path,
    fulltext_dir: Path,
) -> tuple[bool, bool, str | None, str]:
    """Fetch + extract one item. Returns (has_main_pdf, fulltext_extracted,
    main_pdf_filename, status_message)."""
    zotero_key = item["key"]

    attachment = find_pdf_attachment(zot, zotero_key)
    if attachment is None:
        return False, False, None, "no PDF attachment found in Zotero"

    attachment_key = attachment["key"]
    filename = attachment["data"].get("filename") or f"{attachment_key}.pdf"
    item_pdf_dir = pdfs_dir / zotero_key
    item_pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = item_pdf_dir / filename

    try:
        zot.dump(attachment_key, filename=filename, path=str(item_pdf_dir))
    except Exception as exc:  # noqa: BLE001 - report any download failure per-item
        return False, False, None, f"PDF download failed: {exc}"

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return False, False, None, "PDF download produced an empty/missing file"

    try:
        fulltext = extract_fulltext(pdf_path)
    except Exception as exc:  # noqa: BLE001 - report any extraction failure per-item
        return True, False, filename, f"PDF saved, but fulltext extraction failed: {exc}"

    if fulltext is None:
        return True, False, filename, "PDF saved, but no extractable text (OCR likely needed)"

    fulltext_dir.mkdir(parents=True, exist_ok=True)
    out_path = fulltext_dir / f"{zotero_key}_main.txt"
    out_path.write_text(fulltext, encoding="utf-8")
    return True, True, filename, "OK"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N items, sorted by zotero_key (for test runs).",
    )
    args = parser.parse_args()

    config = load_config()
    data_root = Path(config["data_root"])
    collection_name = config["zotero"]["collection_name"]

    pdfs_dir = data_root / "pdfs"
    fulltext_dir = data_root / "fulltext_cache"
    manifest_path = data_root / "manifest.db"
    data_root.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to local Zotero API for collection '{collection_name}'...")
    zot = make_local_zotero_client()
    collection_key = find_collection_key(zot, collection_name)
    items = zot.everything(zot.collection_items_top(collection_key))
    items.sort(key=lambda it: it["key"])
    if args.limit is not None:
        items = items[: args.limit]

    print(f"Found {len(items)} item(s) to consider in '{collection_name}'.")

    conn = init_manifest_db(manifest_path)

    fetched = skipped = failed = 0
    for i, item in enumerate(items, start=1):
        zotero_key = item["key"]
        title = item["data"].get("title", "(untitled)")
        year = parse_year(item["data"].get("date"))
        doi = item["data"].get("DOI") or None

        print(f"[{i}/{len(items)}] {zotero_key} — {title}")

        if is_fully_fetched(conn, zotero_key):
            touch_last_checked(conn, zotero_key)
            skipped += 1
            print("    skipped (already fully fetched)")
            continue

        has_main_pdf, fulltext_extracted, main_pdf_filename, status = process_item(
            zot, item, pdfs_dir, fulltext_dir
        )
        upsert_manifest_row(
            conn,
            zotero_key=zotero_key,
            title=title,
            year=year,
            doi=doi,
            has_main_pdf=has_main_pdf,
            main_pdf_filename=main_pdf_filename,
            fulltext_extracted=fulltext_extracted,
        )

        if has_main_pdf and fulltext_extracted:
            fetched += 1
            print(f"    OK — PDF + fulltext saved ({main_pdf_filename})")
        else:
            failed += 1
            print(f"    FAILED — {status}")

    conn.close()
    print(
        f"\nDone. {fetched} fetched, {skipped} skipped (already complete), "
        f"{failed} failed, out of {len(items)} considered."
    )


if __name__ == "__main__":
    main()

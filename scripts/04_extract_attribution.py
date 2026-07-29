"""Stage 4: determine which target authors contributed to which papers.

For every paper in manifest.db that is not excluded (exclusion_reason IS
NULL) and has methods content available (methods_extracted_main or
methods_extracted_supp — the "recovered" set stage 3 produced), this
script:
  - pulls the paper's author list from Zotero metadata
  - matches it against config/project_config.yaml's target_authors list
    (normalized last-name + first-initial match)
  - for each matched author, determines attribution via the tiered scheme
    below and writes evidence to
    data_root/fulltext_cache/{zotero_key}_attribution.json

Papers with no target-author match are skipped entirely (no JSON file),
but are still marked attribution_extracted in manifest.db so they aren't
re-queried every run.

## Tiered attribution scheme

1. author_contributions_statement (highest confidence): the paper's main
   text has an "Author Contributions" / "CRediT" section, and the target
   author's name or initials appear in it.
2. methods_text_inference: no CRediT statement (or the author isn't
   named in it), but the isolated Methods text itself names the author
   or their initials next to a specific technique description.
3. position_heuristic (fallback, always "low" confidence): neither of
   the above found anything. Falls back to the author's position
   (first/last/middle) in the byline as a weak signal. This project's
   memory currently holds no verified technique associations for any
   target author, so this tier never fabricates a technique claim — it
   states the positional fact plainly and nothing more.

It does not do pipeline-step extraction (stage 5's job).

Usage:
  python scripts/04_extract_attribution.py              # full collection
  python scripts/04_extract_attribution.py --limit 5     # first 5 items by zotero_key
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from pyzotero import zotero

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "project_config.yaml"

# Local Zotero API convention (see scripts/01_fetch_corpus.py for details):
# "0" / "user" is the standard placeholder pyzotero/zotero-mcp use to talk
# to the local desktop API at localhost:23119, which ignores real
# library_id/api_key.
LOCAL_LIBRARY_ID = "0"
LOCAL_LIBRARY_TYPE = "user"

# Evidence quotes are kept short per this project's copyright-safety
# convention (CLAUDE.md: "short quotes <=15 words if any").
EVIDENCE_QUOTE_MAX_WORDS = 15

# How far to search past an "Author Contributions"-style header for the
# actual statement text. Generous on purpose — this is just an evidentiary
# search window, not a precise stage-3-grade section isolation — but
# capped so a missed end-of-section marker can't run the window into the
# reference list.
CONTRIBUTIONS_WINDOW_CHARS = 2500

# Section headers that plausibly terminate an Author Contributions
# statement, used to trim the search window if found before the char cap.
CONTRIBUTIONS_END_MARKERS = [
    "acknowledg",
    "declaration of interest",
    "competing interest",
    "conflict of interest",
    "supplemental information",
    "supplementary information",
    "references",
    "star methods",
    "data availability",
    "code availability",
]

CONTRIBUTIONS_HEADER_PATTERN = re.compile(
    r"(?i)author\s*contributions|credit\s*authorship\s*contribution\s*statement|\bcredit\s*statement\b"
)

# A last-name match is only real attribution evidence if it's not actually
# a citation to the author's own prior work — e.g. a narrative citation
# "as previously described (Turi et al., 2019)", or a reference-list entry
# "Kaifosh,P.,Lovett-Barron,M.,Turi,G.F.,Reardon,T.R.,Losonczy,A.,2013."
# (this exact string was caught misattributing RJ39DEXI's Methods content
# to Turi during test-batch review — a real bug, not a hypothetical).
#
# The general signal used for the reference-list shape: a surname is
# essentially never followed immediately (no space) by a comma in
# attribution prose — nobody writing "Turi performed the surgeries"
# writes "Turi, performed..." — but that's exactly how this corpus's
# bibliography entries always start ("Surname,Initials.,NextSurname,...").
# An earlier, narrower version of this guard only matched a comma
# introducing a strict run of "Initial." blocks, which a suffix in the
# middle of the run breaks: "Lewis,T.L.Jr.,Courchet,J.&Polleux,F...."
# (a real reference-list entry) slipped through and misattributed
# SECXDAKS's Methods content to Tommy Lewis during the same round of
# test-batch review that caught the Turi bug above — a second confirmed
# instance of the same underlying shape, not a hypothetical either.
# Checking for the bare immediate comma, with no assumption about what
# follows it, catches both.
CITATION_TAIL_PATTERN = re.compile(r"\A\s*(?:et\s*al\.?|\(?\s*\d{4}|,)")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_local_zotero_client() -> zotero.Zotero:
    """Build a pyzotero client against the local Zotero desktop API.

    Same HTTP/1.1 pin as every prior stage script: Zotero 8's local
    server (port 23119) rejects httpx's default HTTP/2 negotiation
    attempt with a 502.
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


# --- Name normalization and matching ------------------------------------


def normalize_name_part(s: str) -> str:
    """Strip diacritics and anything but letters, lowercase. Used to
    compare names that may be spelled with or without accents (e.g. a
    target_authors entry written in plain ASCII against a Zotero record
    that has the accented original)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


def parse_target_author(full_name: str) -> tuple[str, str]:
    """Split a config/project_config.yaml target_authors entry (e.g.
    "Gergely F Turi", "Justin K O'Hare") into (normalized_last_name,
    normalized_first_initial). Last name is simply the last
    whitespace-separated token — deliberately simple per this project's
    "don't try to be perfect here" guidance; a stray suffix like "Jr"
    sitting before the surname (as in the config's "Tommy L Jr Lewis")
    doesn't break this since it's just an extra middle token."""
    tokens = full_name.split()
    return normalize_name_part(tokens[-1]), normalize_name_part(tokens[0])[:1]


def creator_last_first_initial(creator: dict) -> tuple[str, str] | None:
    """Extract (normalized_last_name, normalized_first_initial) from a
    Zotero creator dict. Handles both the usual split firstName/lastName
    shape and the single-field "name" shape Zotero uses for creators
    entered as one block (e.g. group/consortium authors) — returns None
    if there's not enough information to split a first and last name."""
    last = creator.get("lastName")
    first = creator.get("firstName")
    if last and first:
        return normalize_name_part(last), normalize_name_part(first)[:1]
    name = creator.get("name")
    if name:
        parts = name.split()
        if len(parts) >= 2:
            return normalize_name_part(parts[-1]), normalize_name_part(parts[0])[:1]
    return None


def creator_display_name(creator: dict) -> str:
    if creator.get("lastName") or creator.get("firstName"):
        return f"{creator.get('firstName', '')} {creator.get('lastName', '')}".strip()
    return creator.get("name", "(unnamed)")


def match_target_authors(target_authors: list[str], author_creators: list[dict]) -> list[dict]:
    """Cross-reference target_authors against a paper's author-type
    creators. Returns one dict per match: {target_author, position
    (1-based), is_first, is_last, creator_name}. A creator whose last
    name matches a target but whose first initial does NOT is a near
    miss, not a match — per the "flag ambiguous matches rather than
    guessing" instruction, those are surfaced separately by
    find_near_miss_matches() rather than silently included here."""
    n = len(author_creators)
    matches = []
    for target in target_authors:
        target_last, target_initial = parse_target_author(target)
        for i, creator in enumerate(author_creators):
            parsed = creator_last_first_initial(creator)
            if parsed is None:
                continue
            creator_last, creator_initial = parsed
            if creator_last == target_last and creator_initial == target_initial:
                matches.append(
                    {
                        "target_author": target,
                        "position": i + 1,
                        "is_first": i == 0,
                        "is_last": i == n - 1,
                        "creator_name": creator_display_name(creator),
                    }
                )
    return matches


def find_near_miss_matches(target_authors: list[str], author_creators: list[dict]) -> list[str]:
    """Surface last-name-only matches (first initial differs) for manual
    review — e.g. two unrelated people who happen to share a surname.
    These are diagnostic only; they never produce an attribution record."""
    near_misses = []
    for target in target_authors:
        target_last, target_initial = parse_target_author(target)
        for creator in author_creators:
            parsed = creator_last_first_initial(creator)
            if parsed is None:
                continue
            creator_last, creator_initial = parsed
            if creator_last == target_last and creator_initial != target_initial:
                near_misses.append(
                    f"{target} vs. paper author {creator_display_name(creator)} "
                    f"(surname matches, first initial does not: '{target_initial}' vs '{creator_initial}')"
                )
    return near_misses


def generate_initials_variants(full_name: str) -> list[str]:
    """The complete (every name-part) initials for a name, dotted and
    bare — e.g. "J.O.H." / "JOH" for "Justin O'Hare", "M.L.-B." / "MLB"
    for "Matthew Lovett-Barron". Splits on whitespace, hyphens, AND
    apostrophes so a compound or apostrophe surname contributes one
    initial per part, and the dotted form preserves a hyphen where the
    split came from one. Both conventions are confirmed against real
    CRediT text found in this corpus: 9U8LL9DE literally writes O'Hare's
    initials as "J.O.H.", and F3ELBLNY writes Lovett-Barron's as
    "M.L.-B." (hyphen intact).

    Deliberately generates ONLY the complete form now, not a truncated
    first+last-only shorthand (e.g. the old "M.B." for "Matthew
    Lovett-Barron") — a short form can silently prefix-match a longer
    real initials run (e.g. "J.O." matching inside the real "J.O.H."),
    or worse, coincidentally collide with a completely unrelated name
    ("M.B." turned out to match an unrelated citation to "M.B. Moser"
    during test-batch review, not any evidence about Lovett-Barron)."""
    parts = re.split(r"([\s\-']+)", full_name)
    tokens: list[tuple[str, str]] = []  # (separator_before, initial_letter)
    sep = ""
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"[\s\-']+", part):
            sep = part
        else:
            tokens.append((sep, part[0].upper()))
            sep = ""
    if not tokens:
        return []

    bare = "".join(letter for _, letter in tokens)
    dotted_pieces = []
    for i, (sep_before, letter) in enumerate(tokens):
        if i > 0 and "-" in sep_before:
            dotted_pieces.append("-")
        dotted_pieces.append(letter)
        dotted_pieces.append(".")
    dotted = "".join(dotted_pieces)
    return [dotted, bare]


def truncate_to_words(text: str, max_words: int = EVIDENCE_QUOTE_MAX_WORDS) -> str:
    words = text.split()
    truncated = " ".join(words[:max_words])
    return truncated + ("…" if len(words) > max_words else "")


def classify_match_confidence(matched_form: str) -> str:
    """A short bare-initials match (e.g. "GT") is more prone to colliding
    with a different author who happens to share the same first-initial +
    last-initial pair than a longer initials form (e.g. "GFT") or an
    actual name-text match — so it gets "medium" instead of "high"."""
    stripped = matched_form.replace(".", "")
    if stripped.isupper() and len(stripped) <= 2:
        return "medium"
    return "high"


def find_name_evidence(text: str, full_name: str) -> tuple[str, str] | None:
    """Search text for either an initials shorthand or the bare last name
    for this author. Returns (matched_form, surrounding_clause) or None.
    A last-name hit immediately followed by "et al", a parenthetical year,
    or reference-list-style ", Initial." formatting is treated as a
    citation to the author's own prior work, not attribution evidence,
    and skipped — see CITATION_TAIL_PATTERN."""
    for variant in generate_initials_variants(full_name):
        if "." in variant:
            # Dotted forms (e.g. "P.K.", "J.O.H.") are self-delimiting —
            # every genuine Tier-1 match found during test-batch review
            # used this dotted form, and column-merge extraction routinely
            # glues it directly onto adjacent lowercase prose with no
            # separating space at all (e.g. "...expectationcanP.K.and
            # A.L.conceivedthestudy..." in 3LEAWLYJ's real Author
            # Contributions text). A hard non-letter boundary requirement
            # rejected that real match outright, so dotted variants are
            # searched as a plain substring instead.
            pattern = re.compile(re.escape(variant))
        else:
            # Bare undotted forms still need a boundary check — unlike
            # the dotted form they could otherwise match as a fragment of
            # an unrelated longer uppercase run (e.g. "GT" inside
            # "GTPase") — but only against another uppercase letter, not
            # against the glued lowercase prose that's normal for this
            # corpus.
            pattern = re.compile(r"(?<![A-Z])" + re.escape(variant) + r"(?![A-Z])")
        for m in pattern.finditer(text):
            # Defense in depth against exactly the bug that motivated
            # generating only the complete initials form above: if this
            # match is immediately followed by another "X." initial
            # block, it's a truncated prefix of a longer real sequence,
            # not the actual complete match — skip it and keep looking.
            if re.match(r"[A-Z]\.", text[m.end() : m.end() + 2]):
                continue
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 120)
            return variant, text[start:end].strip()

    # Bare last name, case-insensitive, word-bounded.
    for m in re.finditer(r"(?i)\b" + re.escape(full_name.split()[-1]) + r"\b", text):
        tail = text[m.end() : m.end() + 25]
        if CITATION_TAIL_PATTERN.match(tail):
            continue
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 120)
        return text[m.start() : m.end()], text[start:end].strip()

    return None


def find_contributions_window(main_text: str) -> str | None:
    """Locate a plausible Author Contributions / CRediT statement inside
    the main text and return a bounded window of text starting there.
    Loose on purpose (substring search, not a strict header isolation
    like stage 3 uses for Methods) — this is just meant to find a region
    to search for a name, not to cleanly delimit a section for its own
    sake."""
    m = CONTRIBUTIONS_HEADER_PATTERN.search(main_text)
    if m is None:
        return None
    window_end = min(len(main_text), m.start() + CONTRIBUTIONS_WINDOW_CHARS)
    window = main_text[m.start() : window_end]

    # Trim at the earliest end marker found after the header itself, if
    # one shows up before the char cap.
    lower = window.lower()
    earliest_end = None
    for marker in CONTRIBUTIONS_END_MARKERS:
        idx = lower.find(marker, 30)  # skip past the header phrase itself
        if idx != -1 and (earliest_end is None or idx < earliest_end):
            earliest_end = idx
    if earliest_end is not None:
        window = window[:earliest_end]
    return window


def read_methods_text(fulltext_dir: Path, zotero_key: str) -> str:
    parts = []
    for suffix in ("_methods_main.txt", "_methods_supp.txt"):
        path = fulltext_dir / f"{zotero_key}{suffix}"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def attribute_author(
    match: dict,
    contributions_window: str | None,
    methods_text: str,
    n_authors: int,
) -> dict:
    """Apply the three-tier attribution scheme for a single matched
    target author. Always returns a record — every matched author gets
    one, per the stage-4 spec — the tiers differ in evidence strength,
    not in whether a record exists at all."""
    target_author = match["target_author"]
    # Search using the author's name as Zotero has it FOR THIS PAPER, not
    # the config's canonical target_authors string — they can differ (the
    # config's "Justin K O'Hare" carries a middle initial this paper's own
    # Zotero record doesn't have), and the paper's text will only ever use
    # the form the paper itself credits the author with.
    search_name = match["creator_name"]

    # Tier 1: author contributions statement. Citation-guarded too, not
    # just Tier 2 — the same column-merge extraction artifacts that bleed
    # reference-list text into isolated Methods sections (see stage 3's
    # possible_trailing_contamination flag) can just as easily bleed a
    # citation into a captured contributions window.
    if contributions_window:
        evidence = find_name_evidence(contributions_window, search_name)
        if evidence:
            matched_form, clause = evidence
            return {
                "target_author": target_author,
                "attribution_source": "author_contributions_statement",
                "attribution_confidence": classify_match_confidence(matched_form),
                "evidence_quote": truncate_to_words(clause),
                "notes": f"matched via {matched_form!r} in the Author Contributions / CRediT statement",
            }

    # Tier 2: methods-text inference. Capped at "medium" even for a
    # longer/distinctive initials match — this tier is inherently less
    # formal evidence than a real contributions statement.
    if methods_text:
        evidence = find_name_evidence(methods_text, search_name)
        if evidence:
            matched_form, clause = evidence
            return {
                "target_author": target_author,
                "attribution_source": "methods_text_inference",
                "attribution_confidence": "medium",
                "evidence_quote": truncate_to_words(clause),
                "notes": f"matched via {matched_form!r} directly in the isolated Methods text",
            }

    # Tier 3: position heuristic. Always "low" confidence, never a
    # fabricated technique claim — this project's memory currently has no
    # verified per-author technique associations to draw on.
    if match["is_first"]:
        position_note = "first author — a weak signal they performed hands-on experimental/methods work, not verified against the text"
    elif match["is_last"]:
        position_note = "last/corresponding author — a weak signal of supervisory/design role rather than hands-on methods work, not verified against the text"
    else:
        position_note = (
            f"author #{match['position']} of {n_authors} (neither first nor last) — "
            "authorship position alone provides essentially no attribution signal here"
        )
    return {
        "target_author": target_author,
        "attribution_source": "position_heuristic",
        "attribution_confidence": "low",
        "evidence_quote": None,
        "notes": f"no direct textual evidence found; positional inference only ({position_note})",
    }


# --- manifest.db tracking -------------------------------------------------


def ensure_attribution_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(manifest)").fetchall()}
    additions = {
        "attribution_extracted": "INTEGER NOT NULL DEFAULT 0",
        "target_authors_matched": "TEXT",
        "last_attribution_check": "TEXT",
    }
    for col, decl in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE manifest ADD COLUMN {col} {decl}")
    conn.commit()


def is_attribution_processed(row: dict) -> bool:
    """A paper's author list and cached text don't change between runs,
    so — unlike stage 3's heuristic, which can improve — there is no
    reason to retry an already-attempted paper here, matched or not."""
    return bool(row.get("attribution_extracted"))


def update_manifest_attribution(
    conn: sqlite3.Connection,
    zotero_key: str,
    *,
    target_authors_matched: list[str],
) -> None:
    conn.execute(
        """
        UPDATE manifest SET
            attribution_extracted = 1,
            target_authors_matched = ?,
            last_attribution_check = ?
        WHERE zotero_key = ?
        """,
        (
            ", ".join(target_authors_matched) if target_authors_matched else None,
            datetime.now(timezone.utc).isoformat(),
            zotero_key,
        ),
    )
    conn.commit()


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
    target_authors = config["target_authors"]
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
    ensure_attribution_columns(conn)
    manifest = {row["zotero_key"]: dict(row) for row in conn.execute("SELECT * FROM manifest").fetchall()}

    processed = skipped = with_matches = without_matches = 0
    for i, item in enumerate(items, start=1):
        zotero_key = item["key"]
        title = item["data"].get("title", "(untitled)")
        print(f"[{i}/{len(items)}] {zotero_key} — {title}")

        manifest_row = manifest.get(zotero_key)
        if manifest_row is None:
            print("    skipped — not in manifest.db, run 01_fetch_corpus.py first")
            skipped += 1
            continue
        if manifest_row.get("exclusion_reason"):
            print(f"    skipped — excluded ({manifest_row['exclusion_reason']})")
            skipped += 1
            continue
        if not (manifest_row.get("methods_extracted_main") or manifest_row.get("methods_extracted_supp")):
            print("    skipped — no methods content available")
            skipped += 1
            continue
        if is_attribution_processed(manifest_row):
            print("    skipped — already processed")
            skipped += 1
            continue

        author_creators = [c for c in item["data"].get("creators", []) if c.get("creatorType") == "author"]
        matches = match_target_authors(target_authors, author_creators)
        near_misses = find_near_miss_matches(target_authors, author_creators)
        for nm in near_misses:
            print(f"    NOTE — near-miss (not matched): {nm}")

        if not matches:
            update_manifest_attribution(conn, zotero_key, target_authors_matched=[])
            processed += 1
            without_matches += 1
            print("    no target authors on this paper — skipped, marked processed")
            continue

        main_text_path = fulltext_dir / f"{zotero_key}_main.txt"
        main_text = main_text_path.read_text(encoding="utf-8") if main_text_path.exists() else ""
        contributions_window = find_contributions_window(main_text)
        methods_text = read_methods_text(fulltext_dir, zotero_key)

        records = [
            attribute_author(match, contributions_window, methods_text, len(author_creators)) for match in matches
        ]

        out_path = fulltext_dir / f"{zotero_key}_attribution.json"
        out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

        matched_names = [r["target_author"] for r in records]
        update_manifest_attribution(conn, zotero_key, target_authors_matched=matched_names)
        processed += 1
        with_matches += 1
        tiers = ", ".join(f"{r['target_author']}={r['attribution_source']}/{r['attribution_confidence']}" for r in records)
        print(f"    matched {len(records)}: {tiers}")

    conn.close()
    print(
        f"\nDone. {processed} processed ({with_matches} with matched target authors, "
        f"{without_matches} with none), {skipped} skipped, out of {len(items)} considered."
    )


if __name__ == "__main__":
    main()

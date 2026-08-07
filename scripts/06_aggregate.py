"""Stage 6: roll up per-paper pipeline extractions into per-author pipelines.

For every target author in config/project_config.yaml's target_authors
list, this script:
  - scans data_root/fulltext_cache for every *_pipeline.json file whose
    target_author field matches that author exactly (schema:
    schema/pipeline_step.schema.json)
  - validates each matching file against that schema before using it, as a
    sanity check that nothing got corrupted in the rollup
  - writes one aggregated file per author to
    outputs/author_pipelines/{author_slug}.json: target_author,
    total_papers, total_steps, a flags[] array surfacing known
    data-quality issues (see CLAUDE.md), and a papers[] array (one entry
    per paper this author has ANY record for, including zero-step ones),
    sorted chronologically by year

It does not deduplicate or merge pipeline_steps across papers by the same
author, even when they describe the same technique — parameters, software
versions, or context may differ subtly between papers, and collapsing them
risks losing real information. Every paper's full step list is preserved
independently.

Usage:
  python scripts/06_aggregate.py                      # full target_authors list
  python scripts/06_aggregate.py --authors "Gergely F Turi" "Eunji Kong"
                                                        # only these authors (test runs)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import jsonschema
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "project_config.yaml"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "pipeline_step.schema.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "author_pipelines"

# The two papers flagged as likely duplicates (preprint/published pair or
# otherwise closely related work covering the same underlying study) by
# the stage-5 extraction session — see CLAUDE.md. Not deduplicated or
# merged here (per this project's "never silently collapse" rule); both
# are included independently in every author's output they appear in, with
# a flag pointing at the other one for manual review.
LIKELY_DUPLICATE_PAPER_IDS = {"YCFJ2N9Z", "RBL2PSLI"}


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_schema() -> dict:
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def author_slug(full_name: str) -> str:
    """Match this project's existing per-paper pipeline filename convention
    (data_root/fulltext_cache/*_pipeline.json): lowercase, apostrophes
    dropped, everything else non-alphanumeric collapsed to a single
    underscore, hyphens preserved — e.g. "Matthew Lovett-Barron" ->
    "matthew_lovett-barron", "Justin K O'Hare" -> "justin_k_ohare"."""
    slug = full_name.lower().replace("'", "")
    slug = re.sub(r"[^a-z0-9\-]+", "_", slug)
    return re.sub(r"_+", "_", slug).strip("_")


def load_all_pipeline_records(fulltext_dir: Path) -> list[tuple[Path, dict]]:
    records = []
    for path in sorted(fulltext_dir.glob("*_pipeline.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records.append((path, data))
    return records


def get_excluded_keys(manifest_path: Path) -> set[str]:
    conn = sqlite3.connect(manifest_path)
    try:
        rows = conn.execute("SELECT zotero_key FROM manifest WHERE exclusion_reason IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def build_author_output(
    target_author: str,
    all_records: list[tuple[Path, dict]],
    validator: jsonschema.protocols.Validator,
    excluded_keys: set[str],
) -> dict:
    papers = []
    for path, data in all_records:
        if data.get("target_author") != target_author:
            continue

        errors = sorted(validator.iter_errors(data), key=str)
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise SystemExit(
                f"Schema validation failed for {path.name} (target_author={target_author!r}): {messages}"
            )

        steps = data["pipeline_steps"]
        papers.append(
            {
                "paper_id": data["paper_id"],
                "year": data["year"],
                "journal": data["journal"],
                "attribution": data["attribution"],
                "step_count": len(steps),
                "pipeline_steps": steps,
            }
        )

    papers.sort(key=lambda p: (p["year"], p["paper_id"]))

    flags = []
    for paper in papers:
        paper_id = paper["paper_id"]
        if paper_id in excluded_keys:
            flags.append(
                {
                    "paper_id": paper_id,
                    "flag": "excluded_paper_has_pipeline_file",
                    "note": (
                        "manifest.db marks this paper's exclusion_reason as set, but a "
                        "pipeline_step.json exists for this author anyway — this shouldn't "
                        "happen; investigate before using this paper's steps downstream."
                    ),
                }
            )
        if paper_id in LIKELY_DUPLICATE_PAPER_IDS:
            other = next(iter(LIKELY_DUPLICATE_PAPER_IDS - {paper_id}))
            flags.append(
                {
                    "paper_id": paper_id,
                    "flag": "possible_duplicate_study",
                    "note": (
                        f"Flagged during the stage-5 extraction session as possibly "
                        f"representing the same underlying study as {other} (preprint/"
                        "published pair or otherwise closely related work) — both are "
                        "included here independently, not merged; review manually before "
                        "downstream use."
                    ),
                }
            )

    return {
        "target_author": target_author,
        "total_papers": sum(1 for p in papers if p["step_count"] > 0),
        "total_steps": sum(p["step_count"] for p in papers),
        "flags": flags,
        "papers": papers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authors",
        nargs="+",
        default=None,
        metavar="TARGET_AUTHOR",
        help=(
            "Only aggregate these target_authors (must match project_config.yaml's "
            "target_authors entries verbatim) instead of the full list. Use this for "
            "small test runs before aggregating the whole corpus."
        ),
    )
    args = parser.parse_args()

    config = load_config()
    target_authors = config["target_authors"]

    if args.authors is not None:
        unknown = [a for a in args.authors if a not in target_authors]
        if unknown:
            raise SystemExit(
                f"--authors name(s) not found verbatim in project_config.yaml's target_authors: {unknown}"
            )
        target_authors = args.authors
        print(f"Test mode: restricting to {target_authors}.")

    data_root = Path(config["data_root"])
    fulltext_dir = data_root / "fulltext_cache"
    manifest_path = data_root / "manifest.db"

    schema = load_schema()
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)

    excluded_keys = get_excluded_keys(manifest_path)
    all_records = load_all_pipeline_records(fulltext_dir)
    print(f"Loaded {len(all_records)} pipeline_step.json file(s) from {fulltext_dir}")

    # Pre-flight sanity check across the WHOLE corpus, independent of which
    # authors this run targets: an excluded paper should never have a
    # pipeline_step.json at all. Per-author instances are still surfaced
    # individually via the excluded_paper_has_pipeline_file flag below.
    excluded_with_pipeline = sorted({data["paper_id"] for _, data in all_records if data.get("paper_id") in excluded_keys})
    if excluded_with_pipeline:
        print(
            f"WARNING: {len(excluded_with_pipeline)} excluded paper(s) unexpectedly have a "
            f"pipeline_step.json: {excluded_with_pipeline}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for author in target_authors:
        output = build_author_output(author, all_records, validator, excluded_keys)
        out_path = OUTPUT_DIR / f"{author_slug(author)}.json"
        out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(
            f"{author}: {output['total_papers']} paper(s) with steps, "
            f"{len(output['papers'])} paper record(s) total, {output['total_steps']} step(s) total, "
            f"{len(output['flags'])} flag(s) -> {out_path}"
        )


if __name__ == "__main__":
    main()

"""Build series-level documents from GEOmetadb.

Reads the pre-parsed GEOmetadb SQLite (see ``geo-metadata-source`` memory) and
emits one JSON record per GEO Series (GSE), aggregating its samples up to the
series level. Each record carries the series free text plus distinct sample
values, and a composed ``embed_text`` field — the whole-document text we embed
in v1 (see ``wiki/28-Embedding-Granularity.md``).

This is the source-agnostic hand-off between raw metadata and the index: it
feeds Postgres (pgvector + pg_search) next, but the JSONL stands alone.

Usage:
    uv run geo-build-series-docs
    uv run geo-build-series-docs --limit 500      # quick check

Series-aggregation caveat (wiki/24): the aggregated distinct values mean the
series *contains* these values, not that any one sample has all of them.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path("data/external/GEOmetadb.sqlite")
DEFAULT_OUTPUT = Path("data/processed/geo_series.jsonl")

# Cap the aggregated free-text blobs so a monster single-cell series (tens of
# thousands of samples) can't produce a multi-MB doc. text-embedding-3-small
# tops out ~8k tokens anyway; distinct-collapsing already removes most bulk.
MAX_BLOB_CHARS = 20_000

# One GROUP BY over the sample join, left-joined onto the series row. Indexes
# on gse_gsm / gsm make this stream efficiently.
QUERY = """
SELECT g.gse, g.title, g.summary, g.overall_design, g.type,
       g.pubmed_id, g.submission_date,
       agg.n_samples, agg.organisms, agg.molecules, agg.gpls,
       agg.source_names, agg.characteristics
FROM gse g
LEFT JOIN (
    SELECT gg.gse AS gse,
           count(*) AS n_samples,
           group_concat(DISTINCT gsm.organism_ch1)    AS organisms,
           group_concat(DISTINCT gsm.molecule_ch1)     AS molecules,
           group_concat(DISTINCT gsm.gpl)              AS gpls,
           group_concat(DISTINCT gsm.source_name_ch1)  AS source_names,
           group_concat(DISTINCT gsm.characteristics_ch1) AS characteristics
    FROM gse_gsm gg
    JOIN gsm ON gsm.gsm = gg.gsm
    GROUP BY gg.gse
) agg ON agg.gse = g.gse
"""


def _split(value: str | None) -> list[str]:
    """Split a group_concat blob of clean, comma-free values into a list."""
    if not value:
        return []
    seen: dict[str, None] = {}
    for part in value.split(","):
        p = part.strip()
        if p:
            seen.setdefault(p, None)
    return list(seen)


def _blob(value: str | None) -> str:
    """Normalize an aggregated free-text blob and cap its length."""
    if not value:
        return ""
    text = " | ".join(s.strip() for s in value.split(",") if s.strip())
    return text[:MAX_BLOB_CHARS]


def compose_embed_text(rec: dict) -> str:
    """Compose the whole-document text to embed (v1: one vector per series)."""
    parts = []
    if rec["title"]:
        parts.append(f"Title: {rec['title']}")
    if rec["type"]:
        parts.append(f"Study type: {rec['type']}")
    if rec["organisms"]:
        parts.append(f"Organisms: {', '.join(rec['organisms'])}")
    if rec["summary"]:
        parts.append(f"Summary: {rec['summary']}")
    if rec["overall_design"]:
        parts.append(f"Overall design: {rec['overall_design']}")
    if rec["molecules"]:
        parts.append(f"Molecules: {', '.join(rec['molecules'])}")
    if rec["source_names"]:
        parts.append(f"Sample sources: {rec['source_names']}")
    if rec["characteristics"]:
        parts.append(f"Sample characteristics: {rec['characteristics']}")
    return "\n".join(parts)


def build(
    *, db_path: Path = DEFAULT_DB, output: Path = DEFAULT_OUTPUT, limit: int | None = None
) -> int:
    if not db_path.exists():
        raise SystemExit(f"GEOmetadb not found: {db_path}")
    output.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    sql = QUERY + (f" LIMIT {int(limit)}" if limit else "")

    print("running aggregation query (materializing sample rollups)…", file=sys.stderr)
    t0 = time.time()
    cur = db.execute(sql)

    written = 0
    with output.open("w") as fh:
        for row in cur:
            rec = {
                "gse": row["gse"],
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "overall_design": row["overall_design"] or "",
                "type": row["type"] or "",
                "pubmed_id": row["pubmed_id"] or None,
                "submission_date": row["submission_date"] or None,
                "n_samples": row["n_samples"] or 0,
                "organisms": _split(row["organisms"]),
                "molecules": _split(row["molecules"]),
                "gpls": _split(row["gpls"]),
                "source_names": _blob(row["source_names"]),
                "characteristics": _blob(row["characteristics"]),
            }
            rec["embed_text"] = compose_embed_text(rec)
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            written += 1
            if written % 20_000 == 0:
                print(f"  {written:,} series written ({time.time()-t0:.0f}s)", file=sys.stderr)

    db.close()
    print(f"done: {written:,} series → {output} ({time.time()-t0:.0f}s)", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build series-level documents (JSONL) from GEOmetadb."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, dest="db_path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    build(db_path=args.db_path, output=args.output, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

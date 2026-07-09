"""Enumerate GEO Series (GSE) and land their ``esummary`` metadata locally.

This is stage 1 of the ingestion pipeline (see ``wiki/21-Ingestion-Pipeline.md``):
a fast, resumable crawl over the ``gds`` Entrez DB that writes one raw
``esummary`` record per line to a JSONL file. It's series-level (GSE) only, per
the v1 scope, and captures enough (title, summary, taxon, gdstype, sample count,
platforms) to stand up the search baseline before we fetch full MINiML/matrix
metadata from FTP in a later stage.

Idempotent & resumable: a ``<output>.progress.json`` checkpoint records how far
we got, and already-seen accessions are skipped, so re-running continues rather
than duplicating.

Usage:
    uv run geo-fetch-summaries --limit 2000
    uv run geo-fetch-summaries --term 'GSE[ETYP] AND "Homo sapiens"[Organism]'

Set NCBI_API_KEY / NCBI_EMAIL in the environment for 10 req/s and polite ID.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from geo_index.eutils import EutilsClient, SearchResult

# gds Entrez DB; ETYP=GSE restricts to Series records (our v1 unit).
DB = "gds"
DEFAULT_TERM = (
    'GSE[ETYP] AND ("Homo sapiens"[Organism] OR "Mus musculus"[Organism])'
)
DEFAULT_OUTPUT = Path("data/raw/geo_series_summaries.jsonl")
# esummary tolerates large pages; 500 keeps each request modest and resumable.
PAGE_SIZE = 500


def _checkpoint_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".progress.json")


def _load_checkpoint(output: Path, term: str) -> int:
    """Return the retstart to resume from (0 if fresh / term changed)."""
    path = _checkpoint_path(output)
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    if data.get("term") != term:
        # Different query — don't mix corpora in one file.
        print(
            f"warning: checkpoint term differs from --term; ignoring checkpoint.\n"
            f"  checkpoint: {data.get('term')!r}\n  requested:  {term!r}",
            file=sys.stderr,
        )
        return 0
    return int(data.get("next_retstart", 0))


def _save_checkpoint(
    output: Path, term: str, total: int, next_retstart: int
) -> None:
    _checkpoint_path(output).write_text(
        json.dumps(
            {"term": term, "total": total, "next_retstart": next_retstart},
            indent=2,
        )
    )


def _load_seen_accessions(output: Path) -> set[str]:
    """Accessions already written — so a resumed run never duplicates."""
    seen: set[str] = set()
    if not output.exists():
        return seen
    with output.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["accession"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seen


def _iter_records(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the per-uid records out of an esummary ``result`` mapping."""
    uids = page.get("uids", [])
    return [page[uid] for uid in uids if uid in page]


def crawl(
    *,
    term: str = DEFAULT_TERM,
    output: Path = DEFAULT_OUTPUT,
    limit: int | None = None,
    page_size: int = PAGE_SIZE,
    refresh: bool = False,
) -> int:
    """Run the crawl; return the number of new records written.

    ``refresh=True`` ignores the resume checkpoint and re-pages from the top.
    Because ``esummary`` returns newest-first, newly-released series appear at
    the front, so a refresh discovers them (the on-disk ``seen`` set skips
    everything already captured) and stops early once it reaches a full page of
    already-seen accessions. This is the incremental "pick up new series" path.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    with EutilsClient() as client:
        search: SearchResult = client.esearch(DB, term)
        total = search.count if limit is None else min(search.count, limit)
        print(
            f"esearch: {search.count:,} series match term; "
            f"targeting {total:,}"
            + (" (capped by --limit)" if limit is not None else ""),
            file=sys.stderr,
        )

        seen = _load_seen_accessions(output)
        start = 0 if refresh else _load_checkpoint(output, term)
        if refresh and seen:
            print(
                f"refresh: re-scanning newest-first, skipping {len(seen):,} "
                f"already on disk (stops at first all-seen page)",
                file=sys.stderr,
            )
        elif start or seen:
            print(
                f"resuming from retstart={start:,} "
                f"({len(seen):,} accessions already on disk)",
                file=sys.stderr,
            )

        written = 0
        fh: TextIO = output.open("a")
        try:
            retstart = start
            while retstart < total:
                this_page = min(page_size, total - retstart)
                page = client.esummary_page(DB, search, retstart, this_page)
                records = _iter_records(page)
                if not records:
                    print(
                        f"warning: empty page at retstart={retstart}; stopping.",
                        file=sys.stderr,
                    )
                    break
                new_in_page = 0
                for rec in records:
                    acc = rec.get("accession")
                    if acc and acc in seen:
                        continue
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                    if acc:
                        seen.add(acc)
                    written += 1
                    new_in_page += 1
                retstart += len(records)
                fh.flush()
                if not refresh:
                    _save_checkpoint(output, term, total, retstart)
                print(
                    f"  {min(retstart, total):,}/{total:,} fetched "
                    f"({written:,} new)",
                    file=sys.stderr,
                )
                # Refresh: newest-first means all-new series are contiguous at
                # the front, so a full page with nothing new = we've reached the
                # already-captured tail. Stop rather than re-page the corpus.
                if refresh and records and new_in_page == 0:
                    print(
                        f"refresh: reached already-captured series; stopping "
                        f"({written:,} new).",
                        file=sys.stderr,
                    )
                    break
        finally:
            fh.close()

    print(f"done: {written:,} new records → {output}", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download GEO Series (GSE) esummary metadata to local JSONL.",
    )
    parser.add_argument(
        "--term",
        default=DEFAULT_TERM,
        help=f"Entrez gds query term (default: {DEFAULT_TERM!r}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max series to fetch (default: all matching the term).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=PAGE_SIZE,
        help=f"esummary records per request (default: {PAGE_SIZE}).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the resume checkpoint and re-scan newest-first to pick up "
             "newly-released series (stops at the first all-seen page).",
    )
    args = parser.parse_args(argv)

    crawl(
        term=args.term,
        output=args.output,
        limit=args.limit,
        page_size=args.page_size,
        refresh=args.refresh,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

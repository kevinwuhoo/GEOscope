"""Strip data tables from raw SOFT family files → metadata-only SOFT.

Stage 2.5 of ingestion (see ``wiki/21-Ingestion-Pipeline.md``). The raw family
files we fetch (:mod:`geo_index.fetch_soft`) are kept verbatim, so for
**microarray** series they carry the full per-sample and per-platform data
tables — routinely >99% of the bytes (a 100 MB file can be ~500 KB of actual
metadata). Embedding and downstream parsing only need the metadata, so this
stage drops every block delimited by ``!*_table_begin`` … ``!*_table_end``
(covers ``sample``, ``platform``, and ``series_matrix`` tables) and rewrites a
metadata-only gz mirroring the raw tree.

Keeping this as its own stage (rather than stripping at download time) means the
raw pull stays dumb and re-runnable, and the strip is a pure, cheap
transformation we can re-run or tweak without re-fetching.

Files mirror the raw layout::

    data/raw/soft/GSE271nnn/GSE271800_family.soft.gz          (raw, with tables)
    data/processed/soft_meta/GSE271nnn/GSE271800_family.soft.gz  (metadata only)

Idempotent & resumable: an up-to-date output (newer than its input) is skipped
unless ``--force``.

Usage:
    uv run geo-strip-soft
    uv run geo-strip-soft --limit 100
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import os
import re
import sys
from pathlib import Path

DEFAULT_RAW_DIR = Path("data/raw/soft")
DEFAULT_OUT_DIR = Path("data/processed/soft_meta")
DEFAULT_CONCURRENCY = 8

# Strip only the BULK data tables: per-sample expression (!sample_table_*),
# per-platform probe annotation (!platform_table_*), and series-matrix
# (!series_matrix_table_*). These are the multi-MB payloads we don't want.
#
# We deliberately KEEP !series_table_* blocks: those are small "reused/
# reanalyzed data" provenance tables listing sample IDs + titles — that's
# metadata, not bulk data, and dropping it would lose information. A whitelist
# (rather than a generic `*_table`) is the metadata-safe failure mode: an
# unknown table type is kept, never silently lost.
#
# Begin markers may carry a "= <caption>" suffix (series-style); anchoring the
# rest of the line means an attribute *value* ending in "_table_begin" can
# never be mistaken for a delimiter.
_STRIP_TYPES = r"(?:sample|platform|series_matrix)"
_TABLE_BEGIN = re.compile(rf"^!{_STRIP_TYPES}_table_begin(?: = .*)?$")
_TABLE_END = re.compile(rf"^!{_STRIP_TYPES}_table_end$")


def _strip_lines(fin) -> list[str]:
    """Return the input lines with every ``!*_table_begin``…``!*_table_end``
    block (delimiter markers included) removed. Every other line — all ``^``
    record headers and ``!*`` metadata attributes — is preserved verbatim."""
    out: list[str] = []
    in_table = False
    for line in fin:
        stripped = line.strip()
        if in_table:
            if _TABLE_END.match(stripped):
                in_table = False
            continue
        if _TABLE_BEGIN.match(stripped):
            in_table = True
            continue
        out.append(line)
    return out


def validate_metadata(lines: list[str], accession: str) -> list[str]:
    """Check required SOFT structural fields survive in ``lines``.

    These fields all appear *before* any data table, so a correct strip can
    never drop them — this mainly catches truncated/corrupt downloads and any
    future regression in the strip logic. Returns a list of problem strings
    (empty == valid).

    Only series-level fields are unconditionally required. Sample/platform
    records are validated by *consistency*, not mere presence: a series that
    declares samples (``!Series_sample_id``) must actually carry that many
    ``^SAMPLE`` blocks (a mismatch means a truncated download), but a
    legitimately sample-less series — e.g. one whose data is provided only via
    ``!Series_supplementary_file`` — is valid and not flagged.
    """
    problems: list[str] = []

    def starts(prefix: str) -> int:
        return sum(1 for ln in lines if ln.startswith(prefix))

    series_accs = [
        ln.split("=", 1)[1].strip()
        for ln in lines
        if ln.startswith("!Series_geo_accession")
    ]
    if not series_accs:
        problems.append("missing !Series_geo_accession")
    elif accession and series_accs[0] != accession:
        problems.append(
            f"!Series_geo_accession {series_accs[0]!r} != filename {accession!r}"
        )
    if not starts("!Series_title"):
        problems.append("missing !Series_title")

    n_sample_hdr = starts("^SAMPLE")
    n_sample_acc = starts("!Sample_geo_accession")
    n_sample_title = starts("!Sample_title")
    n_series_sample_id = starts("!Series_sample_id")
    # Samples promised but missing → truncated/incomplete download.
    if n_series_sample_id and n_sample_hdr != n_series_sample_id:
        problems.append(
            f"declares {n_series_sample_id} !Series_sample_id but has "
            f"{n_sample_hdr} ^SAMPLE blocks (truncated?)"
        )
    # Whenever sample blocks exist, each must carry its accession + title.
    if n_sample_hdr and n_sample_acc != n_sample_hdr:
        problems.append(
            f"!Sample_geo_accession count {n_sample_acc} != ^SAMPLE {n_sample_hdr}"
        )
    if n_sample_hdr and n_sample_title != n_sample_hdr:
        problems.append(
            f"!Sample_title count {n_sample_title} != ^SAMPLE {n_sample_hdr}"
        )

    return problems


def _strip_one(raw: Path, dest: Path, *, force: bool) -> tuple[str, Path]:
    """Strip one file. Returns ``(status, dest)`` — status ok/skip/fail."""
    if (
        not force
        and dest.exists()
        and dest.stat().st_mtime >= raw.stat().st_mtime
    ):
        return ("skip", dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with gzip.open(raw, "rt", encoding="utf-8", errors="replace") as fin:
            lines = _strip_lines(fin)
        with gzip.open(tmp, "wt", encoding="utf-8") as fout:
            fout.writelines(lines)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return ("ok", dest)


def strip_all(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    force: bool = False,
) -> tuple[int, int, int]:
    """Strip data tables from every raw family SOFT under ``raw_dir``.

    Returns ``(stripped, skipped, failed)``.
    """
    raw_files = sorted(raw_dir.rglob("*_family.soft.gz"))
    if limit is not None:
        raw_files = raw_files[:limit]
    total = len(raw_files)
    if not total:
        raise SystemExit(f"no raw SOFT found under {raw_dir} (run geo-fetch-soft first)")

    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"stage 2.5: stripping tables from {total:,} files → {out_dir} "
        f"(concurrency={concurrency})",
        file=sys.stderr,
    )

    stripped = skipped = failed = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for raw in raw_files:
            dest = out_dir / raw.relative_to(raw_dir)
            futures[pool.submit(_strip_one, raw, dest, force=force)] = raw
        for i, fut in enumerate(cf.as_completed(futures), 1):
            raw = futures[fut]
            try:
                status, _ = fut.result()
                if status == "ok":
                    stripped += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001 — log and keep going
                failed += 1
                print(f"  FAIL {raw.name}: {exc}", file=sys.stderr)
            if i % 500 == 0 or i == total:
                print(
                    f"  {i:,}/{total:,}  stripped={stripped:,} "
                    f"skipped={skipped:,} failed={failed:,}",
                    file=sys.stderr,
                )

    print(
        f"done: stripped={stripped:,} skipped={skipped:,} failed={failed:,}",
        file=sys.stderr,
    )
    return stripped, skipped, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strip data tables from raw family SOFT (metadata-only output).",
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"Parallel strip workers (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-strip even if output is up to date."
    )
    args = parser.parse_args(argv)
    strip_all(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
        limit=args.limit,
        concurrency=args.concurrency,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

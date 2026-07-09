"""Validate stripped metadata SOFT — field integrity + lossless reconstruction.

Two independent checks (see ``wiki/21-Ingestion-Pipeline.md``):

1. **Field validation (100% coverage, cheap):** every stripped file must retain
   the required SOFT structural fields (:func:`geo_index.strip_soft_tables.validate_metadata`)
   — series/sample/platform accessions + titles and sample-count integrity. This
   catches truncated or corrupt downloads.

2. **Reconstruction diff (retained-raw subset):** wherever the *raw* family file
   is still on disk (the newest keep-raw batch, or ``--keep-raw-frac`` samples),
   independently strip it and assert the result is byte-identical to the stored
   metadata file. This proves *nothing but data tables* was removed — the direct
   answer to "is anything getting stripped that shouldn't be?".

Usage:
    uv run geo-validate-soft                     # validate everything stripped
    uv run geo-validate-soft --limit 5000
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

from geo_index.strip_soft_tables import (
    DEFAULT_OUT_DIR as DEFAULT_META_DIR,
    DEFAULT_RAW_DIR,
    _strip_lines,
    validate_metadata,
)


def _lines(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


def _accession(path: Path) -> str:
    return path.name.replace("_family.soft.gz", "")


def validate(
    *,
    meta_dir: Path = DEFAULT_META_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    limit: int | None = None,
) -> int:
    """Validate stripped files; return the number of problems found."""
    meta_files = sorted(meta_dir.rglob("*_family.soft.gz"))
    if limit is not None:
        meta_files = meta_files[:limit]
    total = len(meta_files)
    if not total:
        raise SystemExit(f"no stripped SOFT under {meta_dir} (run geo-fetch-soft --strip)")

    field_fail = recon_checked = recon_fail = 0
    fail_log = meta_dir / "_validate_report.log"
    fail_log.unlink(missing_ok=True)
    print(
        f"validating {total:,} stripped files (field checks; reconstruction "
        f"where raw kept in {raw_dir})",
        file=sys.stderr,
    )

    for i, meta in enumerate(meta_files, 1):
        acc = _accession(meta)
        meta_lines = _lines(meta)
        problems = validate_metadata(meta_lines, acc)
        if problems:
            field_fail += 1
            with fail_log.open("a") as fh:
                fh.write(f"{acc}\tFIELD\t{'; '.join(problems)}\n")

        raw = raw_dir / meta.relative_to(meta_dir)
        if raw.exists():
            recon_checked += 1
            recon = _strip_lines(iter(_lines(raw)))
            if recon != meta_lines:
                recon_fail += 1
                with fail_log.open("a") as fh:
                    fh.write(f"{acc}\tRECON\tstripped output != raw-minus-tables\n")

        if i % 2000 == 0 or i == total:
            print(
                f"  {i:,}/{total:,}  field_fail={field_fail:,} "
                f"recon_checked={recon_checked:,} recon_fail={recon_fail:,}",
                file=sys.stderr,
            )

    ok = field_fail == 0 and recon_fail == 0
    print(
        f"\n{'PASS' if ok else 'FAIL'}: {total:,} files · "
        f"field_fail={field_fail:,} · "
        f"reconstruction {recon_checked:,} checked / {recon_fail:,} mismatched"
        + ("" if ok else f"  (see {fail_log})"),
        file=sys.stderr,
    )
    return field_fail + recon_fail


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Validate stripped metadata SOFT (fields + reconstruction)."
    )
    p.add_argument("--meta-dir", type=Path, default=DEFAULT_META_DIR)
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args(argv)
    problems = validate(meta_dir=a.meta_dir, raw_dir=a.raw_dir, limit=a.limit)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

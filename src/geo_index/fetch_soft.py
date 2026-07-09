"""Download raw SOFT *family* files for each GEO Series from the FTP mirror.

Stage 2 of ingestion (see ``wiki/21-Ingestion-Pipeline.md``). Reads the series
catalog produced by stage 1 (:mod:`geo_index.fetch_summaries`) and, for each
accession, pulls ``GSExxx_family.soft.gz`` from NCBI's FTP HTTPS mirror.

Why FTP (not ``acc.cgi``)? The FTP mirror is NCBI's sanctioned *bulk* channel:
static files, gzipped at the source, and tolerant of parallel fetches — so a
20-wide crawl is fine here, whereas ``acc.cgi`` is a rate-limited query CGI. It
also avoids two ``acc.cgi?view=brief`` pathologies: for series on shared
sequencing platforms it bloats the response with the platform's *global*
``!Platform_sample_id`` cross-reference list (tens of MB of unrelated ids).

The family file carries the series + all sample metadata and — for **microarray**
series — the full per-sample / per-platform data tables (often >99% of the
bytes). Two modes:

* default (keep raw): write the gz verbatim; strip later with ``geo-strip-soft``.
* ``--strip`` (disk-safe bulk): download → strip data tables → write metadata-only
  gz, discarding the raw. The full corpus is ~3.5–7 TB raw but only ~2.6 GB
  stripped, so this is the only mode that fits a normal disk. ``--keep-raw-frac``
  retains a deterministic random subset of *raw* files for manual validation,
  and every stripped file is field-validated inline (see ``validate_metadata``).

Files mirror the FTP bucket layout so parsing later never re-downloads::

    data/raw/soft/GSE271nnn/GSE271800_family.soft.gz            (raw)
    data/processed/soft_meta/GSE271nnn/GSE271800_family.soft.gz (stripped)

Idempotent & resumable: an existing target is skipped. A series whose family file
doesn't exist yet (freshly released — GEO builds it within ~a day) returns 404
and is logged to ``_pending.log`` for a later re-run; other failures go to
``_failures.log``; inline validation problems go to ``_validation.log``.

Usage:
    uv run geo-fetch-soft                                   # keep raw (default)
    uv run geo-fetch-soft --strip --keep-raw-frac 0.01      # disk-safe full run
    uv run geo-fetch-soft --limit 50 --concurrency 20

Set NCBI_EMAIL to identify the crawler politely.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from geo_index.eutils import RETRY_STATUSES, TOOL_NAME
from geo_index.fetch_summaries import DEFAULT_OUTPUT as DEFAULT_CATALOG
from geo_index.strip_soft_tables import (
    DEFAULT_OUT_DIR as DEFAULT_STRIP_DIR,
    _strip_lines,
    validate_metadata,
)

FTP_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series"
DEFAULT_SOFT_DIR = Path("data/raw/soft")
DEFAULT_CONCURRENCY = 20
MAX_RETRIES = 5
# Family files range from ~10 KB (sequencing) to hundreds of MB (microarray), so
# allow a generous per-chunk read window; streaming resets it on each chunk.
TIMEOUT = httpx.Timeout(30.0, read=180.0)


def ftp_bucket(accession: str) -> str:
    """GEO's directory bucket for a GSE accession (GSE271800 → 'GSE271nnn')."""
    number = accession[3:]
    if len(number) <= 3:
        return "GSEnnn"
    return f"GSE{number[:-3]}nnn"


def soft_path(soft_dir: Path, accession: str) -> Path:
    """Local path for a series' family SOFT, mirroring the FTP layout."""
    return soft_dir / ftp_bucket(accession) / f"{accession}_family.soft.gz"


def ftp_url(accession: str) -> str:
    """FTP HTTPS URL for a series' raw family SOFT."""
    return f"{FTP_BASE}/{ftp_bucket(accession)}/{accession}/soft/{accession}_family.soft.gz"


@dataclass
class FetchOpts:
    soft_dir: Path = DEFAULT_SOFT_DIR
    strip: bool = False
    strip_out: Path = DEFAULT_STRIP_DIR
    keep_raw_frac: float = 0.0

    def target(self, acc: str) -> Path:
        """The file whose existence means this accession is already done."""
        return soft_path(self.strip_out, acc) if self.strip else soft_path(self.soft_dir, acc)

    def keep_raw(self, acc: str) -> bool:
        """Deterministically retain raw for ~keep_raw_frac of accessions.

        Stable per-accession hash → the same subset is kept across re-runs and
        is spread evenly over the corpus (not clustered by number/age)."""
        if self.keep_raw_frac <= 0:
            return False
        if self.keep_raw_frac >= 1:
            return True
        h = int(hashlib.md5(acc.encode()).hexdigest()[:8], 16)
        return (h % 1_000_000) < self.keep_raw_frac * 1_000_000


def _iter_accessions(catalog: Path) -> list[str]:
    if not catalog.exists():
        raise SystemExit(
            f"catalog not found: {catalog}\n"
            f"Run stage 1 first (geo-fetch-summaries), or pass --catalog."
        )
    accs: list[str] = []
    with catalog.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                acc = json.loads(line).get("accession")
            except json.JSONDecodeError:
                continue
            if acc:
                accs.append(acc)
    return accs


def _write_gz_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _fetch_one(
    client: httpx.Client, acc: str, opts: FetchOpts
) -> tuple[str, str, str | None, list[str]]:
    """Fetch (and, in strip mode, strip+validate) one series' family SOFT.

    Returns ``(status, acc, detail, problems)`` where ``status`` is
    ``ok`` / ``pending`` (404) / ``fail`` and ``problems`` are validation
    issues found in strip mode (empty otherwise).
    """
    url = ftp_url(acc)
    raw_dest = soft_path(opts.soft_dir, acc)
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    return ("pending", acc, "404 — family file not generated yet", [])
                if resp.status_code in RETRY_STATUSES:
                    resp.read()  # drain so the connection can be reused
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                # Stream to a temp .gz first (raw bytes), regardless of mode.
                raw_dest.parent.mkdir(parents=True, exist_ok=True)
                tmp_raw = raw_dest.with_suffix(raw_dest.suffix + ".tmp")
                try:
                    with tmp_raw.open("wb") as fh:
                        for chunk in resp.iter_bytes(1 << 16):
                            fh.write(chunk)
                except BaseException:
                    tmp_raw.unlink(missing_ok=True)
                    raise
        except (httpx.HTTPError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                threading.Event().wait(2.0**attempt)  # 1,2,4,8s; peers stay busy
            continue

        # Downloaded to tmp_raw. Publish per mode.
        try:
            if not opts.strip:
                os.replace(tmp_raw, raw_dest)  # keep raw verbatim
                return ("ok", acc, None, [])
            # Strip mode: read → strip → write metadata-only, validate inline.
            with gzip.open(tmp_raw, "rt", encoding="utf-8", errors="replace") as fin:
                stripped = _strip_lines(fin)
            _write_gz_lines(soft_path(opts.strip_out, acc), stripped)
            problems = validate_metadata(stripped, acc)
            if opts.keep_raw(acc):
                os.replace(tmp_raw, raw_dest)  # retained validation sample
            else:
                tmp_raw.unlink(missing_ok=True)
            return ("ok", acc, None, problems)
        except BaseException:
            tmp_raw.unlink(missing_ok=True)
            raise
    return ("fail", acc, str(last_exc), [])


def crawl(
    *,
    catalog: Path = DEFAULT_CATALOG,
    soft_dir: Path = DEFAULT_SOFT_DIR,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    strip: bool = False,
    strip_out: Path = DEFAULT_STRIP_DIR,
    keep_raw_frac: float = 0.0,
) -> tuple[int, int, int, int]:
    """Fetch family SOFT for each catalog accession, ``concurrency``-wide.

    Returns ``(fetched, skipped, pending, failed)``.
    """
    opts = FetchOpts(
        soft_dir=soft_dir, strip=strip, strip_out=strip_out, keep_raw_frac=keep_raw_frac
    )
    accessions = _iter_accessions(catalog)
    if limit is not None:
        accessions = accessions[:limit]

    soft_dir.mkdir(parents=True, exist_ok=True)
    # Idempotent resume: a series is "done" when its target (stripped in strip
    # mode, else raw) already exists.
    todo = [a for a in accessions if not opts.target(a).exists()]
    skipped = len(accessions) - len(todo)
    total = len(todo)

    logdir = strip_out if strip else soft_dir
    logdir.mkdir(parents=True, exist_ok=True)
    pending_log = logdir / "_pending.log"
    failures_log = logdir / "_failures.log"
    validation_log = logdir / "_validation.log"
    email = os.environ.get("NCBI_EMAIL")
    headers = {"User-Agent": f"{TOOL_NAME} ({email})" if email else TOOL_NAME}

    mode = (
        f"strip→{strip_out}"
        + (f", keep {keep_raw_frac:.0%} raw→{soft_dir}" if keep_raw_frac else ", raw discarded")
        if strip
        else f"keep raw→{soft_dir}"
    )
    print(
        f"stage 2: {len(accessions):,} in catalog · {skipped:,} done · "
        f"{total:,} to fetch [{mode}] (concurrency={concurrency})",
        file=sys.stderr,
    )
    if not total:
        return (0, skipped, 0, 0)

    fetched = pending = failed = invalid = 0
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    with httpx.Client(
        timeout=TIMEOUT, follow_redirects=True, limits=limits, headers=headers
    ) as client:
        with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_fetch_one, client, acc, opts) for acc in todo]
            for i, fut in enumerate(cf.as_completed(futures), 1):
                status, acc, detail, problems = fut.result()
                if status == "ok":
                    fetched += 1
                    if problems:
                        invalid += 1
                        with validation_log.open("a") as fh:
                            fh.write(f"{acc}\t{'; '.join(problems)}\n")
                        print(f"  INVALID {acc}: {'; '.join(problems)}", file=sys.stderr)
                elif status == "pending":
                    pending += 1
                    with pending_log.open("a") as fh:
                        fh.write(f"{acc}\t{detail}\n")
                else:
                    failed += 1
                    with failures_log.open("a") as fh:
                        fh.write(f"{acc}\t{detail}\n")
                    print(f"  FAIL {acc}: {detail}", file=sys.stderr)
                if i % 100 == 0 or i == total:
                    print(
                        f"  {i:,}/{total:,}  fetched={fetched:,} pending={pending:,} "
                        f"failed={failed:,}"
                        + (f" invalid={invalid:,}" if strip else ""),
                        file=sys.stderr,
                    )

    print(
        f"done: fetched={fetched:,} skipped={skipped:,} pending={pending:,} "
        f"failed={failed:,}"
        + (f" invalid={invalid:,} (see {validation_log})" if invalid else "")
        + (f"  (re-run to retry {pending_log})" if pending else "")
        + (f"  (see {failures_log})" if failed else ""),
        file=sys.stderr,
    )
    return fetched, skipped, pending, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download family SOFT per GEO series from the FTP mirror.",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--soft-dir", type=Path, default=DEFAULT_SOFT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"Parallel download workers (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--strip", action="store_true",
        help="Strip data tables and write metadata-only (disk-safe bulk mode).",
    )
    parser.add_argument(
        "--strip-out", type=Path, default=DEFAULT_STRIP_DIR,
        help=f"Metadata-only output dir in --strip mode (default: {DEFAULT_STRIP_DIR}).",
    )
    parser.add_argument(
        "--keep-raw-frac", type=float, default=0.0,
        help="In --strip mode, retain raw for this fraction of series (e.g. 0.01) "
             "for manual validation.",
    )
    args = parser.parse_args(argv)
    crawl(
        catalog=args.catalog,
        soft_dir=args.soft_dir,
        limit=args.limit,
        concurrency=args.concurrency,
        strip=args.strip,
        strip_out=args.strip_out,
        keep_raw_frac=args.keep_raw_frac,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

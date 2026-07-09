"""Minimal, polite NCBI E-utilities client.

Wraps the two calls we need for GEO series enumeration over the ``gds`` Entrez
database: ``esearch`` (with history) and ``esummary`` (JSON). Handles rate
limiting and retry/backoff so callers don't have to.

Rate limits (per NCBI usage guide): 3 req/s without an API key, 10 req/s with
one. Set ``NCBI_API_KEY`` and ``NCBI_EMAIL`` in the environment to get the
higher limit and to identify the crawler politely.

    https://www.ncbi.nlm.nih.gov/books/NBK25497/  (usage / rate limits)
    https://www.ncbi.nlm.nih.gov/books/NBK25499/  (JSON output)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "geo-metadata-index"

# Transient HTTP statuses worth retrying (NCBI throttling + upstream blips).
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class SearchResult:
    """Outcome of an ``esearch`` with ``usehistory=y``."""

    count: int
    web_env: str
    query_key: str


@dataclass
class EutilsClient:
    """A rate-limited E-utilities client.

    One instance == one crawl session. Reads ``NCBI_API_KEY`` / ``NCBI_EMAIL``
    from the environment on construction.
    """

    api_key: str | None = field(default_factory=lambda: os.environ.get("NCBI_API_KEY"))
    email: str | None = field(default_factory=lambda: os.environ.get("NCBI_EMAIL"))
    max_retries: int = 5
    timeout: float = 60.0

    _client: httpx.Client = field(init=False, repr=False)
    _min_interval: float = field(init=False)
    _last_request_at: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        # 10 req/s with a key, 3 without. Leave a little headroom.
        rate = 9.0 if self.api_key else 2.5
        self._min_interval = 1.0 / rate
        self._client = httpx.Client(base_url=BASE_URL, timeout=self.timeout)

    def __enter__(self) -> "EutilsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- internals ---------------------------------------------------------

    def _common_params(self) -> dict[str, str]:
        params = {"tool": TOOL_NAME}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        return params

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        merged = {**self._common_params(), **params}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._client.get(path, params=merged)
                if resp.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_exc = exc
                # Exponential backoff: 1, 2, 4, 8 ... seconds.
                if attempt < self.max_retries - 1:
                    time.sleep(2.0**attempt)
        raise RuntimeError(
            f"E-utilities request failed after {self.max_retries} attempts: {path}"
        ) from last_exc

    # -- public API --------------------------------------------------------

    def esearch(self, db: str, term: str) -> SearchResult:
        """Run ``esearch`` with history; return count + WebEnv/query_key.

        We use history rather than pulling the full UID list so we can page
        ``esummary`` server-side even for large result sets.
        """
        resp = self._get(
            "/esearch.fcgi",
            {"db": db, "term": term, "usehistory": "y", "retmode": "json"},
        )
        payload = resp.json()["esearchresult"]
        return SearchResult(
            count=int(payload["count"]),
            web_env=payload["webenv"],
            query_key=payload["querykey"],
        )

    def esearch_ids(self, db: str, term: str, retmax: int = 200) -> list[str]:
        """Run ``esearch`` and return the matching UID list (no history)."""
        resp = self._get(
            "/esearch.fcgi",
            {"db": db, "term": term, "retmode": "json", "retmax": str(retmax)},
        )
        return list(resp.json()["esearchresult"].get("idlist", []))

    def esummary_page(
        self, db: str, search: SearchResult, retstart: int, retmax: int
    ) -> dict[str, Any]:
        """Fetch one page of ``esummary`` docs (JSON) from a history result.

        Returns the raw ``result`` mapping: ``{"uids": [...], "<uid>": {...}}``.
        """
        params = {
            "db": db,
            "query_key": search.query_key,
            "WebEnv": search.web_env,
            "retstart": str(retstart),
            "retmax": str(retmax),
            "retmode": "json",
            "version": "2.0",
        }
        last: object = None
        for attempt in range(self.max_retries):
            payload = self._get("/esummary.fcgi", params).json()
            if isinstance(payload, dict) and "result" in payload:
                return payload["result"]
            err = ""
            if isinstance(payload, dict):
                err = str(payload.get("eutilsresult", {}).get("ERROR", "")) or str(payload)
            # Deterministic: the page's XML exceeds NCBI's 10MB JSON-conversion
            # cap. Retrying won't help — halve the window and merge the two
            # halves. Recurses until each sub-page fits.
            if "max size is 10MB" in err or "cannot be transformed to JSON" in err:
                if retmax <= 1:
                    print(
                        f"warning: skipping 1 esummary record too large for JSON "
                        f"(retstart={retstart})",
                        file=sys.stderr,
                    )
                    return {"uids": []}
                half = retmax // 2
                left = self.esummary_page(db, search, retstart, half)
                right = self.esummary_page(db, search, retstart + half, retmax - half)
                merged: dict[str, Any] = {
                    "uids": list(left.get("uids", [])) + list(right.get("uids", []))
                }
                for part in (left, right):
                    for k, v in part.items():
                        if k != "uids":
                            merged[k] = v
                return merged
            # Otherwise a transient error envelope — back off and retry.
            last = payload
            if attempt < self.max_retries - 1:
                time.sleep(2.0**attempt)
        raise RuntimeError(
            f"esummary returned no 'result' after {self.max_retries} attempts "
            f"(retstart={retstart}): {str(last)[:200]}"
        )

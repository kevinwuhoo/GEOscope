"""Polite HTTP helper for NCBI GEO's ``acc.cgi`` query endpoint.

Separate from :mod:`geo_index.eutils` (E-utilities) because ``acc.cgi`` is a
different service with its own host/path. Shares the same politeness posture:
rate limiting + retry/backoff on transient failures.

``acc.cgi`` is how we pull **metadata-only** SOFT for a series: the
``view=brief`` variant returns every ``!Series_*`` and ``!Sample_*`` attribute
(including the ``!Sample_characteristics_ch1`` goldmine) but *no* expression
data tables — and it works for freshly-released series whose FTP family files
haven't been generated yet.

    https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from geo_index.eutils import RETRY_STATUSES, TOOL_NAME

ACC_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"


@dataclass
class GeoWebClient:
    """Rate-limited client for ``acc.cgi``.

    acc.cgi is not an E-utility and there's no evidence it honors an API key,
    so we hold to the conservative unauthenticated posture (~3 req/s) regardless
    of ``NCBI_API_KEY``.
    """

    email: str | None = field(default_factory=lambda: os.environ.get("NCBI_EMAIL"))
    max_retries: int = 5
    timeout: float = 90.0
    # Conservative; acc.cgi is a heavier endpoint than eutils.
    requests_per_second: float = 2.5

    _client: httpx.Client = field(init=False, repr=False)
    _min_interval: float = field(init=False)
    _last_request_at: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._min_interval = 1.0 / self.requests_per_second
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)

    def __enter__(self) -> "GeoWebClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def fetch_brief_soft(self, accession: str, targ: str = "all") -> str:
        """Return the metadata-only (``view=brief``) SOFT text for a series.

        ``targ='all'`` includes the series + all its samples + platforms;
        ``view=brief`` omits data tables.
        """
        params = {
            "acc": accession,
            "targ": targ,
            "form": "text",
            "view": "brief",
        }
        if self.email:
            params["email"] = self.email
        params["tool"] = TOOL_NAME

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._client.get(ACC_URL, params=params)
                if resp.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                text = resp.text
                # acc.cgi returns HTTP 200 with an error body for bad/missing
                # accessions; treat a missing SOFT header as a hard failure.
                if not text.lstrip().startswith("^"):
                    raise ValueError(
                        f"acc.cgi returned non-SOFT body for {accession}: "
                        f"{text[:120]!r}"
                    )
                return text
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2.0**attempt)
        raise RuntimeError(
            f"acc.cgi request failed after {self.max_retries} attempts: {accession}"
        ) from last_exc

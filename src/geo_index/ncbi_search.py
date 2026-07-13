"""Live NCBI GEO candidate retrieval shared by every search transport."""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol

from .eutils import EutilsClient, SearchResult
from .normalize import map_assay, map_organisms
from .search_candidates import MAX_SOURCE_CANDIDATES, SearchCandidate


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
NativeError = Literal["ncbi_timeout", "ncbi_error"]


class EutilsProtocol(Protocol):
    def esearch(
        self, db: str, term: str, *, deadline: float | None = None
    ) -> SearchResult:
        raise NotImplementedError

    def esummary_page(
        self,
        db: str,
        search: SearchResult,
        retstart: int,
        retmax: int,
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class NativeSearchResult:
    count: int | None
    candidates: tuple[SearchCandidate, ...]
    error: NativeError | None = None

    @classmethod
    def unavailable(cls, error: NativeError) -> "NativeSearchResult":
        return cls(count=None, candidates=(), error=error)


class NcbiCandidateSource:
    def __init__(
        self,
        client: EutilsProtocol | None = None,
        *,
        timeout_seconds: float | None = None,
        max_concurrent_requests: int = 4,
        concurrency_gate: object | None = None,
        clock=time.monotonic,
    ) -> None:
        if max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be positive")
        self._client = client or EutilsClient()
        configured_timeout = timeout_seconds
        if configured_timeout is None:
            configured_timeout = float(getattr(self._client, "timeout", 5.0))
        if configured_timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = configured_timeout
        self._clock = clock
        self._gate = concurrency_gate or threading.BoundedSemaphore(
            max_concurrent_requests
        )
        self._state = threading.Condition()
        self._active_requests = 0
        self._closing = False
        self._closed = False

    def close(self) -> None:
        with self._state:
            while self._closing:
                self._state.wait()
            if self._closed:
                return
            self._closing = True
            while self._active_requests:
                self._state.wait()
        try:
            self._client.close()
        finally:
            with self._state:
                self._closed = True
                self._closing = False
                self._state.notify_all()

    @contextmanager
    def _operation(self, deadline: float) -> Iterator[None]:
        with self._state:
            if self._closing or self._closed:
                raise RuntimeError("NCBI candidate source is closed")
            self._active_requests += 1
        acquired = False
        try:
            remaining = deadline - self._clock()
            if remaining <= 0 or not self._gate.acquire(timeout=remaining):
                raise TimeoutError("NCBI concurrency gate acquisition timed out")
            acquired = True
            yield
        finally:
            if acquired:
                self._gate.release()
            with self._state:
                self._active_requests -= 1
                self._state.notify_all()

    @staticmethod
    def _candidate(raw: object, rank: int) -> SearchCandidate | None:
        if not isinstance(raw, dict):
            return None
        if str(raw.get("entrytype", "")).upper() != "GSE":
            return None
        gse = str(raw.get("accession") or "").upper()
        if not _GSE_RE.fullmatch(gse):
            return None
        title = str(raw.get("title") or "")[:500] or None
        summary = str(raw.get("summary") or "")[:1000] or None
        study_type = str(raw.get("gdstype") or "")[:200] or None
        taxon = str(raw.get("taxon") or "")[:256] or None
        organism_ids, organism_status = map_organisms(taxon)
        if taxon is None:
            organism_status = "unavailable"
        categories, labels, assay_status = map_assay(
            study_type or "",
            " ".join(value for value in (study_type, title, summary) if value),
        )
        return SearchCandidate(
            gse=gse,
            title=title,
            snippet=summary,
            study_type=study_type,
            n_samples=None,
            pubmed_id=None,
            organism_ids=tuple(organism_ids),
            organism_status=organism_status,
            sex_ids=(),
            sex_status="unavailable",
            assay_categories=tuple(categories),
            assay_labels=tuple(labels),
            assay_status=assay_status if categories or labels else "unavailable",
            source="ncbi",
            retrieval_score=None,
            original_rank=None,
            native_rank=rank,
            taxon=taxon,
        )

    def _search_term(self, term: str, limit: int) -> NativeSearchResult:
        deadline = self._clock() + self._timeout_seconds
        with self._operation(deadline):
            search = self._client.esearch("gds", term, deadline=deadline)
            if search.count == 0:
                return NativeSearchResult(count=0, candidates=())
            page = self._client.esummary_page(
                "gds",
                search,
                0,
                min(max(limit * 3, limit), MAX_SOURCE_CANDIDATES),
                deadline=deadline,
            )
        candidates: list[SearchCandidate] = []
        for uid in page.get("uids", []):
            candidate = self._candidate(page.get(str(uid)), len(candidates) + 1)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return NativeSearchResult(count=search.count, candidates=tuple(candidates))

    def search(
        self, query: str, limit: int = MAX_SOURCE_CANDIDATES
    ) -> NativeSearchResult:
        if not 1 <= limit <= MAX_SOURCE_CANDIDATES:
            raise ValueError(
                "NCBI candidate limit must be between "
                f"1 and {MAX_SOURCE_CANDIDATES}"
            )
        return self._search_term(f"({query}) AND gse[ETYP]", limit)

    def lookup(self, gse: str) -> SearchCandidate | None:
        if not _GSE_RE.fullmatch(gse):
            raise ValueError("lookup requires a normalized GSE accession")
        result = self._search_term(f"{gse}[ACCN] AND gse[ETYP]", 1)
        return next(
            (candidate for candidate in result.candidates if candidate.gse == gse),
            None,
        )

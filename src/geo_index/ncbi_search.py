"""Live NCBI GEO candidate retrieval shared by every search transport."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from .eutils import EutilsClient, SearchResult
from .normalize import map_assay, map_organisms
from .search_candidates import SearchCandidate


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
NativeError = Literal["ncbi_timeout", "ncbi_error"]


class EutilsProtocol(Protocol):
    def esearch(self, db: str, term: str) -> SearchResult:
        raise NotImplementedError

    def esummary_page(
        self, db: str, search: SearchResult, retstart: int, retmax: int
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
    def __init__(self, client: EutilsProtocol | None = None) -> None:
        self._client = client or EutilsClient()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._client.close()

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
        with self._lock:
            search = self._client.esearch("gds", term)
            if search.count == 0:
                return NativeSearchResult(count=0, candidates=())
            page = self._client.esummary_page(
                "gds", search, 0, min(max(limit * 3, limit), 100)
            )
        candidates: list[SearchCandidate] = []
        for uid in page.get("uids", []):
            candidate = self._candidate(page.get(str(uid)), len(candidates) + 1)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return NativeSearchResult(count=search.count, candidates=tuple(candidates))

    def search(self, query: str, limit: int = 20) -> NativeSearchResult:
        if not 1 <= limit <= 20:
            raise ValueError("NCBI candidate limit must be between 1 and 20")
        return self._search_term(f"({query}) AND gse[ETYP]", limit)

    def lookup(self, gse: str) -> SearchCandidate | None:
        if not _GSE_RE.fullmatch(gse):
            raise ValueError("lookup requires a normalized GSE accession")
        result = self._search_term(f"{gse}[ACCN] AND gse[ETYP]", 1)
        return next(
            (candidate for candidate in result.candidates if candidate.gse == gse),
            None,
        )

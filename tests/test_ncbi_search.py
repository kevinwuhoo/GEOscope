from types import SimpleNamespace

import pytest

from geo_index.ncbi_search import NativeSearchResult, NcbiCandidateSource


class Eutils:
    def __init__(self) -> None:
        self.terms: list[str] = []
        self.retmaxes: list[int] = []
        self.closed = False

    def esearch(self, db: str, term: str) -> SimpleNamespace:
        assert db == "gds"
        self.terms.append(term)
        return SimpleNamespace(count=2)

    def esummary_page(
        self, db: str, search: object, retstart: int, retmax: int
    ) -> dict[str, object]:
        assert (db, retstart) == ("gds", 0)
        self.retmaxes.append(retmax)
        return {
            "uids": ["1", "2"],
            "1": {
                "entrytype": "GSE",
                "accession": "GSE11803",
                "title": "Mouse exercise",
                "gdstype": "Expression profiling by array",
                "taxon": "Mus musculus",
                "summary": "Skeletal muscle after endurance exercise.",
            },
            "2": {"entrytype": "GPL", "accession": "GPL1"},
        }

    def close(self) -> None:
        self.closed = True


def test_search_returns_normalized_series_candidates() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)

    result = source.search("mouse exercise", limit=20)

    assert eutils.terms == ["(mouse exercise) AND gse[ETYP]"]
    assert eutils.retmaxes == [60]
    assert result.count == 2
    assert result.error is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.gse == "GSE11803"
    assert candidate.source == "ncbi"
    assert candidate.organism_ids == ("NCBITaxon:10090",)
    assert candidate.sex_status == "unavailable"
    assert candidate.assay_categories == ("expression (array)",)
    assert candidate.native_rank == 1


def test_search_preserves_query_and_caps_candidates_in_native_order() -> None:
    class ManyEutils(Eutils):
        def esearch(self, db: str, term: str) -> SimpleNamespace:
            assert db == "gds"
            self.terms.append(term)
            return SimpleNamespace(count=25)

        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            assert (db, retstart) == ("gds", 0)
            self.retmaxes.append(retmax)
            uids = [str(uid) for uid in range(25, 0, -1)]
            return {
                "uids": uids,
                **{
                    uid: {
                        "entrytype": "GSE",
                        "accession": f"GSE{1000 + int(uid)}",
                    }
                    for uid in uids
                },
            }

    eutils = ManyEutils()
    source = NcbiCandidateSource(eutils)

    result = source.search("  mouse:exercise?  ")

    assert eutils.terms == ["(  mouse:exercise?  ) AND gse[ETYP]"]
    assert eutils.retmaxes == [100]
    assert [candidate.gse for candidate in result.candidates] == [
        f"GSE{1000 + uid}" for uid in range(25, 0, -1)
    ]
    assert [candidate.native_rank for candidate in result.candidates] == list(
        range(1, 26)
    )


def test_search_rejects_non_series_and_malformed_accessions() -> None:
    class InvalidEutils(Eutils):
        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            return {
                "uids": ["1", "2", "3", "4", "5"],
                "1": {"entrytype": "GPL", "accession": "GSE1"},
                "2": {"entrytype": "GSE", "accession": "GSE0"},
                "3": {"entrytype": "GSE", "accession": "GSE-123"},
                "4": {"entrytype": "GSE", "accession": "GSE123extra"},
                "5": {"entrytype": "gse", "accession": "gse123"},
            }

    result = NcbiCandidateSource(InvalidEutils()).search("query")

    assert [candidate.gse for candidate in result.candidates] == ["GSE123"]


def test_unknown_esummary_metadata_is_unavailable_not_absent() -> None:
    class SparseEutils(Eutils):
        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            return {
                "uids": ["1"],
                "1": {"entrytype": "GSE", "accession": "GSE123"},
            }

    candidate = NcbiCandidateSource(SparseEutils()).search("query").candidates[0]

    assert candidate.organism_status == "unavailable"
    assert candidate.sex_status == "unavailable"
    assert candidate.assay_status == "unavailable"


@pytest.mark.parametrize("limit", [0, 101])
def test_search_rejects_limits_outside_the_bounded_pool(limit: int) -> None:
    eutils = Eutils()

    with pytest.raises(ValueError, match="between 1 and 100"):
        NcbiCandidateSource(eutils).search("query", limit=limit)

    assert eutils.terms == []


def test_search_with_no_native_results_skips_esummary() -> None:
    class EmptyEutils(Eutils):
        def esearch(self, db: str, term: str) -> SimpleNamespace:
            self.terms.append(term)
            return SimpleNamespace(count=0)

        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            raise AssertionError("esummary must not be called for an empty search")

    result = NcbiCandidateSource(EmptyEutils()).search("nothing")

    assert result == NativeSearchResult(count=0, candidates=())


def test_exact_lookup_requires_the_requested_accession() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)

    found = source.lookup("GSE11803")

    assert eutils.terms == ["GSE11803[ACCN] AND gse[ETYP]"]
    assert found is not None
    assert found.gse == "GSE11803"


def test_exact_lookup_does_not_accept_a_different_series() -> None:
    class MismatchedEutils(Eutils):
        def esummary_page(
            self, db: str, search: object, retstart: int, retmax: int
        ) -> dict[str, object]:
            return {
                "uids": ["1"],
                "1": {"entrytype": "GSE", "accession": "GSE999"},
            }

    eutils = MismatchedEutils()

    found = NcbiCandidateSource(eutils).lookup("GSE11803")

    assert eutils.terms == ["GSE11803[ACCN] AND gse[ETYP]"]
    assert found is None


@pytest.mark.parametrize("gse", ["gse11803", "GSE0", "GSE001", "GPL11803"])
def test_exact_lookup_rejects_non_normalized_accessions(gse: str) -> None:
    eutils = Eutils()

    with pytest.raises(ValueError, match="normalized GSE accession"):
        NcbiCandidateSource(eutils).lookup(gse)

    assert eutils.terms == []


def test_native_unavailable_result_records_the_error() -> None:
    assert NativeSearchResult.unavailable("ncbi_timeout") == NativeSearchResult(
        count=None, candidates=(), error="ncbi_timeout"
    )


def test_close_owns_the_eutils_client() -> None:
    eutils = Eutils()
    source = NcbiCandidateSource(eutils)
    source.close()
    assert eutils.closed

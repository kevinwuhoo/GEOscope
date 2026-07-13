from __future__ import annotations

import json
from pathlib import Path

import pytest

from geo_index.mcp_models import (
    DatasetSummary,
    FacetResultOutput,
    SearchDatasetsOutput,
    SearchFiltersInput,
    SearchLatencyOutput,
    SearchProvenanceOutput,
)
from geo_index.mcp_search_service import SearchExecution
from geo_index.ncbi_search import NativeSearchResult
from geo_index.search_candidates import SearchCandidate
from geo_index.search_eval import (
    estimated_cost,
    load_cases,
    ndcg_at,
    recall_at,
    reciprocal_rank,
    run_evaluation,
    write_report_atomic,
)
from geo_index.search_models import FACET_FIELDS, SearchFilters


def test_retrieval_metrics_are_bounded_and_deterministic() -> None:
    judgments = {"GSE1": 3, "GSE2": 2, "GSE3": 1}
    assert recall_at(["GSE1", "GSE9", "GSE2"], judgments, 3) == 2 / 3
    assert reciprocal_rank(["GSE9", "GSE2"], judgments) == 0.5
    assert ndcg_at(["GSE1", "GSE2", "GSE3"], judgments, 10) == 1.0


def test_cost_uses_explicit_current_prices_not_hard_coded_prices() -> None:
    assert estimated_cost(
        input_tokens=1_000_000,
        output_tokens=500_000,
        input_cost_per_million=0.25,
        output_cost_per_million=2.0,
    ) == 1.25


def _candidate(
    gse: str,
    *,
    source: str = "elasticsearch",
    original_rank: int | None = 1,
    native_rank: int | None = None,
    organism_ids: tuple[str, ...] = ("NCBITaxon:10090",),
) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Title {gse}",
        snippet=f"Summary {gse}",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=10,
        pubmed_id=None,
        organism_ids=organism_ids,
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (seq)",),
        assay_labels=("scRNA-seq",),
        assay_status="mapped",
        source=source,  # type: ignore[arg-type]
        retrieval_score=0.5 if original_rank is not None else None,
        original_rank=original_rank,
        native_rank=native_rank,
        taxon="Mus musculus",
    )


def _summary(
    gse: str,
    rank: int,
    *,
    source: str = "elasticsearch",
    organism_ids: list[str] | None = None,
) -> DatasetSummary:
    return DatasetSummary(
        gse=gse,
        rank=rank,
        score=90.0,
        title=f"Title {gse}",
        snippet=f"Summary {gse}",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=10,
        pubmed_id=None,
        organism_ids=organism_ids or ["NCBITaxon:10090"],
        organism_status="mapped",
        sex_ids=[],
        sex_status="absent",
        assay_categories=["expression (seq)"],
        assay_labels=["scRNA-seq"],
        assay_status="mapped",
        source=source,  # type: ignore[arg-type]
        retrieval_score=0.5,
        original_rank=rank if source != "ncbi" else None,
    )


def _execution(
    *,
    query: str,
    candidates: tuple[SearchCandidate, ...],
    results: list[DatasetSummary],
    native_count: int | None,
    degradation: list[str] | None = None,
    rerank_attempted: bool = False,
    rerank_applied: bool = False,
    tokens: tuple[int, int] = (0, 0),
    latency: tuple[int, int, int] = (10, 20, 0),
) -> SearchExecution:
    elasticsearch_candidates = sum(
        candidate.original_rank is not None for candidate in candidates
    )
    ncbi_candidates = sum(candidate.native_rank is not None for candidate in candidates)
    output = SearchDatasetsOutput(
        query=query,
        filters=SearchFiltersInput(),
        limit=10,
        retrieval_version="geo-series-v1:test",
        embedding_variant="gemini_embedding_2_3072_v1",
        results=results,
        facets={
            field: FacetResultOutput(
                field=field,
                buckets=[],
                scope="candidate_pool",
                candidate_count=elasticsearch_candidates,
            )
            for field in FACET_FIELDS
        },
        provenance=SearchProvenanceOutput(
            exact_accession=False,
            elasticsearch_candidates=elasticsearch_candidates,
            ncbi_candidates=ncbi_candidates,
            merged_candidates=len(candidates),
            rerank_attempted=rerank_attempted,
            rerank_applied=rerank_applied,
            rerank_model="gpt-5.6-luna" if rerank_attempted else None,
            rerank_reasoning_effort="low" if rerank_attempted else None,
            rerank_input_tokens=tokens[0],
            rerank_output_tokens=tokens[1],
            latency=SearchLatencyOutput(
                elasticsearch_ms=latency[0],
                ncbi_ms=latency[1],
                reranker_ms=latency[2],
            ),
            degradation=degradation or [],  # type: ignore[arg-type]
        ),
    )
    native_candidates = tuple(
        candidate for candidate in candidates if candidate.native_rank is not None
    )
    return SearchExecution(
        output=output,
        native=NativeSearchResult(
            count=native_count,
            candidates=native_candidates,
        ),
        candidates=candidates,
    )


class _Service:
    def __init__(self, executions: dict[str, SearchExecution]) -> None:
        self.executions = executions
        self.open_calls = 0
        self.close_calls = 0
        self.search_calls: list[tuple[str, SearchFilters, int]] = []

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def search_execution(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchExecution:
        self.search_calls.append((query, filters, limit))
        return self.executions[query]


def _write_cases(path: Path) -> None:
    cases = (
        {
            "query_id": "candidate_recall",
            "query": "candidate recall query",
            "filters": {},
            "judgments": {"GSE2": 3},
            "constraints": {"organism_ids": ["NCBITaxon:10090"]},
        },
        {
            "query_id": "native_count",
            "query": "native count query",
            "filters": {},
            "judgments": {"GSE3": 3},
            "constraints": {"organism_ids": ["NCBITaxon:10090"]},
            "expected_ncbi_count": 0,
        },
    )
    path.write_text(
        "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
    )


def test_evaluation_compares_baseline_and_luna_with_candidate_and_final_metrics(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    output_path = tmp_path / "report.json"
    _write_cases(cases_path)
    baseline = _Service(
        {
            "candidate recall query": _execution(
                query="candidate recall query",
                candidates=(_candidate("GSE1"), _candidate("GSE2", original_rank=2)),
                results=[_summary("GSE1", 1)],
                native_count=0,
            ),
            "native count query": _execution(
                query="native count query",
                candidates=(_candidate("GSE3"),),
                results=[_summary("GSE3", 1)],
                native_count=0,
            ),
        }
    )
    luna = _Service(
        {
            "candidate recall query": _execution(
                query="candidate recall query",
                candidates=(
                    _candidate("GSE1"),
                    _candidate(
                        "GSE2", source="ncbi", original_rank=None, native_rank=1
                    ),
                ),
                results=[
                    _summary("GSE2", 1, source="ncbi"),
                    _summary(
                        "GSE1",
                        2,
                        organism_ids=["NCBITaxon:9606"],
                    ),
                ],
                native_count=1,
                rerank_attempted=True,
                rerank_applied=True,
                tokens=(200, 50),
                latency=(10, 20, 30),
            ),
            "native count query": _execution(
                query="native count query",
                candidates=(_candidate("GSE3"),),
                results=[_summary("GSE3", 1)],
                native_count=1,
                degradation=["rerank_timeout"],
                rerank_attempted=True,
                rerank_applied=False,
                latency=(20, 10, 40),
            ),
        }
    )

    report = run_evaluation(
        cases_path=cases_path,
        output_path=output_path,
        compare_baseline=True,
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
        service_factories={"baseline": lambda: baseline, "luna": lambda: luna},
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["candidate_pool_bounds"] == {
        "elasticsearch": 100,
        "ncbi": 100,
        "merged": 200,
    }
    assert set(report["runs"]) == {"baseline", "luna"}
    baseline_case = report["runs"]["baseline"]["cases"][0]
    assert baseline_case["candidate_ids"] == ["GSE1", "GSE2"]
    assert baseline_case["final_ids"] == ["GSE1"]
    assert baseline_case["recall_at_40"] == 1.0
    assert baseline_case["ndcg_at_10"] == 0.0
    assert baseline_case["mrr"] == 0.0
    luna_aggregate = report["runs"]["luna"]["aggregate"]
    assert luna_aggregate["constraint_violations"] == 1
    assert luna_aggregate["ncbi_only_recovery"] == 1
    assert luna_aggregate["native_count_mismatches"] == 1
    assert luna_aggregate["fallback_rate"] == 0.5
    assert luna_aggregate["degradation_rate"] == 0.5
    assert luna_aggregate["rerank_input_tokens"] == 200
    assert luna_aggregate["rerank_output_tokens"] == 50
    assert luna_aggregate["estimated_cost"] == 0.0003
    # Elasticsearch and NCBI retrieval run concurrently, so wall latency is the
    # slower source plus reranking rather than the sum of both source timings.
    assert luna_aggregate["latency_ms"] == {"p50": 55.0, "p95": 59.5}
    assert baseline.open_calls == baseline.close_calls == 1
    assert luna.open_calls == luna.close_calls == 1
    assert all(call[2] == 10 for call in baseline.search_calls + luna.search_calls)


def test_atomic_report_write_preserves_existing_file_when_serialization_fails(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "report.json"
    output_path.write_text("previous\n", encoding="utf-8")

    with pytest.raises(TypeError):
        write_report_atomic(output_path, {"not_json": object()})

    assert output_path.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.glob(f".{output_path.name}.*.tmp")) == []


def test_case_parser_rejects_unbounded_or_unknown_input_before_opening_services(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "query_id": "bad",
                "query": "query",
                "filters": {},
                "judgments": {},
                "constraints": {"unknown": ["value"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    opened = False

    def factory() -> _Service:
        nonlocal opened
        opened = True
        return _Service({})

    with pytest.raises(ValueError, match="constraint"):
        run_evaluation(
            cases_path=cases_path,
            output_path=tmp_path / "report.json",
            compare_baseline=False,
            input_cost_per_million=0,
            output_cost_per_million=0,
            service_factories={"luna": factory},
        )

    assert opened is False


def test_versioned_evaluation_corpus_contains_the_required_cases() -> None:
    cases = load_cases(
        Path(__file__).parents[1] / "eval" / "unified_search_queries.jsonl"
    )

    assert {case.query_id for case in cases} == {
        "exact_gse_310900",
        "mouse_endurance_insulin",
        "human_breast_neoadjuvant",
        "control_childhood_malaria",
        "human_tumor_exhausted_t_cells",
        "mouse_brain_spatial_injury",
        "crispr_interferon_t_cells",
        "ncbi_zero_control",
    }
    exact = next(case for case in cases if case.query_id == "exact_gse_310900")
    assert exact.query == "GSE310900"
    assert exact.judgments == {"GSE310900": 3}
    sentinel = next(case for case in cases if case.query_id == "ncbi_zero_control")
    assert sentinel.expected_ncbi_count == 0

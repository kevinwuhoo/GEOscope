"""Repeatable quality, latency, fallback, and cost evaluation for unified search."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .elasticsearch_config import ElasticsearchSettings
from .mcp_models import DatasetSummary
from .mcp_search_service import McpSearchService, SearchExecution
from .mcp_settings import SearchQualitySettings
from .ncbi_search import NativeSearchResult
from .search_candidates import MAX_MERGED_CANDIDATES, MAX_SOURCE_CANDIDATES
from .search_models import FACET_FIELDS, SearchFilters


_GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")
_QUERY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CONSTRAINT_FIELDS = ("organism_ids", "assay_categories", "assay_labels")
_MAX_CASES = 100
_MAX_FILE_BYTES = 1_000_000
_MAX_LINE_BYTES = 65_536


class EvaluationService(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def search_execution(
        self, *, query: str, filters: SearchFilters, limit: int
    ) -> SearchExecution: ...


@dataclass(frozen=True)
class EvaluationCase:
    query_id: str
    query: str
    filters: SearchFilters
    judgments: dict[str, int]
    constraints: dict[str, tuple[str, ...]]
    expected_ncbi_count: int | None


class EmptyNativeSource:
    """NCBI source used to isolate the Elasticsearch-only baseline."""

    def search(self, query: str, limit: int = 100) -> NativeSearchResult:
        del query, limit
        return NativeSearchResult(count=0, candidates=())

    def lookup(self, gse: str):
        del gse
        return None

    def close(self) -> None:
        return None


def recall_at(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    relevant = {gse for gse, grade in judgments.items() if grade > 0}
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def reciprocal_rank(ranked: list[str], judgments: dict[str, int]) -> float:
    for rank, gse in enumerate(ranked, 1):
        if judgments.get(gse, 0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")

    def dcg(grades: list[int]) -> float:
        return sum(
            (2**grade - 1) / math.log2(index + 2)
            for index, grade in enumerate(grades)
        )

    actual = dcg([judgments.get(gse, 0) for gse in ranked[:k]])
    ideal = dcg(sorted(judgments.values(), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def estimated_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> float:
    values = (
        input_tokens,
        output_tokens,
        input_cost_per_million,
        output_cost_per_million,
    )
    if any(not math.isfinite(float(value)) or value < 0 for value in values):
        raise ValueError("token counts and prices must be finite and non-negative")
    return (
        input_tokens * input_cost_per_million
        + output_tokens * output_cost_per_million
    ) / 1_000_000


def _bounded_values(raw: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError(f"{field} must be a list with at most 20 values")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= 256:
            raise ValueError(f"{field} contains an invalid value")
        value = item.strip()
        if value not in values:
            values.append(value)
    return tuple(values)


def _case_from_json(raw: object, *, line_number: int) -> EvaluationCase:
    if not isinstance(raw, dict):
        raise ValueError(f"line {line_number} must contain a JSON object")
    allowed = {
        "query_id",
        "query",
        "filters",
        "judgments",
        "constraints",
        "expected_ncbi_count",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"line {line_number} has unknown fields: {', '.join(unknown)}")
    query_id = raw.get("query_id")
    if not isinstance(query_id, str) or not _QUERY_ID_RE.fullmatch(query_id):
        raise ValueError(f"line {line_number} has an invalid query_id")
    query = raw.get("query")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1_000:
        raise ValueError(f"line {line_number} has an invalid query")

    raw_filters = raw.get("filters")
    if not isinstance(raw_filters, dict):
        raise ValueError(f"line {line_number} filters must be an object")
    unknown_filters = sorted(set(raw_filters) - set(FACET_FIELDS))
    if unknown_filters:
        raise ValueError(f"line {line_number} has an unknown filter field")
    filters = SearchFilters.from_mapping(
        {
            field: _bounded_values(value, field=f"filters.{field}")
            for field, value in raw_filters.items()
        }
    )

    raw_judgments = raw.get("judgments")
    if not isinstance(raw_judgments, dict) or len(raw_judgments) > 200:
        raise ValueError(f"line {line_number} judgments must be a bounded object")
    judgments: dict[str, int] = {}
    for gse, grade in raw_judgments.items():
        if (
            not isinstance(gse, str)
            or not _GSE_RE.fullmatch(gse)
            or type(grade) is not int
            or not 0 <= grade <= 3
        ):
            raise ValueError(f"line {line_number} contains an invalid judgment")
        judgments[gse] = grade

    raw_constraints = raw.get("constraints")
    if not isinstance(raw_constraints, dict):
        raise ValueError(f"line {line_number} constraints must be an object")
    unknown_constraints = sorted(set(raw_constraints) - set(_CONSTRAINT_FIELDS))
    if unknown_constraints:
        raise ValueError(f"line {line_number} has an unknown constraint field")
    constraints = {
        field: _bounded_values(value, field=f"constraints.{field}")
        for field, value in raw_constraints.items()
    }

    expected = raw.get("expected_ncbi_count")
    if expected is not None and (
        type(expected) is not int or not 0 <= expected <= 100_000_000
    ):
        raise ValueError(f"line {line_number} has an invalid expected_ncbi_count")
    return EvaluationCase(
        query_id=query_id,
        query=query.strip(),
        filters=filters,
        judgments=judgments,
        constraints=constraints,
        expected_ncbi_count=expected,
    )


def load_cases(path: Path) -> tuple[EvaluationCase, ...]:
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("evaluation JSONL exceeds the 1 MB bound")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
                raise ValueError(f"line {line_number} exceeds the JSONL line bound")
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number} is not valid JSON") from exc
            case = _case_from_json(raw, line_number=line_number)
            if case.query_id in seen:
                raise ValueError(f"duplicate query_id: {case.query_id}")
            seen.add(case.query_id)
            cases.append(case)
            if len(cases) > _MAX_CASES:
                raise ValueError(f"evaluation is limited to {_MAX_CASES} cases")
    if not cases:
        raise ValueError("evaluation JSONL contains no cases")
    return tuple(cases)


def _constraint_violations(
    results: Sequence[DatasetSummary], constraints: Mapping[str, tuple[str, ...]]
) -> int:
    violations = 0
    for result in results:
        if any(
            requested
            and set(requested).isdisjoint(getattr(result, field))
            for field, requested in constraints.items()
        ):
            violations += 1
    return violations


def _percentile(values: Sequence[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _case_report(case: EvaluationCase, execution: SearchExecution) -> dict[str, Any]:
    provenance = execution.output.provenance
    candidate_ids = [candidate.gse for candidate in execution.candidates[:40]]
    final_ids = [result.gse for result in execution.output.results[:10]]
    expected_matches = (
        None
        if case.expected_ncbi_count is None
        else execution.native.count == case.expected_ncbi_count
    )
    return {
        "query_id": case.query_id,
        "candidate_ids": candidate_ids,
        "final_ids": final_ids,
        "recall_at_40": recall_at(candidate_ids, case.judgments, 40),
        "ndcg_at_10": ndcg_at(final_ids, case.judgments, 10),
        "mrr": reciprocal_rank(final_ids, case.judgments),
        "constraint_violations": _constraint_violations(
            execution.output.results, case.constraints
        ),
        "ncbi_only_recovery": sum(
            result.source == "ncbi" for result in execution.output.results
        ),
        "native_count": execution.native.count,
        "native_count_matches_expected": expected_matches,
        "candidate_counts": {
            "elasticsearch": provenance.elasticsearch_candidates,
            "ncbi": provenance.ncbi_candidates,
            "merged": provenance.merged_candidates,
        },
        "latency_ms": {
            "elasticsearch": provenance.latency.elasticsearch_ms,
            "ncbi": provenance.latency.ncbi_ms,
            "reranker": provenance.latency.reranker_ms,
            "total": (
                max(
                    provenance.latency.elasticsearch_ms,
                    provenance.latency.ncbi_ms,
                )
                + provenance.latency.reranker_ms
            ),
        },
        "degradation": list(provenance.degradation),
        "rerank_attempted": provenance.rerank_attempted,
        "rerank_applied": provenance.rerank_applied,
        "rerank_input_tokens": provenance.rerank_input_tokens,
        "rerank_output_tokens": provenance.rerank_output_tokens,
    }


def _aggregate(
    cases: Sequence[dict[str, Any]],
    *,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> dict[str, Any]:
    count = len(cases)
    input_tokens = sum(case["rerank_input_tokens"] for case in cases)
    output_tokens = sum(case["rerank_output_tokens"] for case in cases)
    latencies = [case["latency_ms"]["total"] for case in cases]
    candidate_fields = ("elasticsearch", "ncbi", "merged")
    return {
        "case_count": count,
        "mean_recall_at_40": sum(case["recall_at_40"] for case in cases) / count,
        "mean_ndcg_at_10": sum(case["ndcg_at_10"] for case in cases) / count,
        "mean_mrr": sum(case["mrr"] for case in cases) / count,
        "constraint_violations": sum(
            case["constraint_violations"] for case in cases
        ),
        "ncbi_only_recovery": sum(case["ncbi_only_recovery"] for case in cases),
        "native_count_mismatches": sum(
            case["native_count_matches_expected"] is False for case in cases
        ),
        "degradation_rate": sum(bool(case["degradation"]) for case in cases)
        / count,
        "fallback_rate": sum(
            case["rerank_attempted"] and not case["rerank_applied"]
            for case in cases
        )
        / count,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "candidate_pool": {
            field: {
                "mean": sum(case["candidate_counts"][field] for case in cases)
                / count,
                "max": max(case["candidate_counts"][field] for case in cases),
            }
            for field in candidate_fields
        },
        "rerank_input_tokens": input_tokens,
        "rerank_output_tokens": output_tokens,
        "estimated_cost": estimated_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        ),
    }


def write_report_atomic(path: Path, report: Mapping[str, object]) -> None:
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _default_service_factories(
    *, env: Mapping[str, str], compare_baseline: bool
) -> dict[str, Callable[[], EvaluationService]]:
    elasticsearch = ElasticsearchSettings.from_env(env)
    quality = SearchQualitySettings.from_env(env)
    factories: dict[str, Callable[[], EvaluationService]] = {
        "luna": lambda: McpSearchService(
            elasticsearch=elasticsearch,
            quality=quality,
        )
    }
    if compare_baseline:
        baseline_quality = replace(
            quality,
            rerank_enabled=False,
            openai_api_key=None,
        )
        factories["baseline"] = lambda: McpSearchService(
            elasticsearch=elasticsearch,
            quality=baseline_quality,
            ncbi_source_factory=lambda timeout: EmptyNativeSource(),
        )
    return factories


def run_evaluation(
    *,
    cases_path: Path,
    output_path: Path,
    compare_baseline: bool,
    input_cost_per_million: float,
    output_cost_per_million: float,
    service_factories: Mapping[str, Callable[[], EvaluationService]] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    estimated_cost(
        input_tokens=0,
        output_tokens=0,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )
    cases = load_cases(cases_path)
    factories = dict(
        service_factories
        or _default_service_factories(
            env=env or os.environ,
            compare_baseline=compare_baseline,
        )
    )
    labels = ("baseline", "luna") if compare_baseline else ("luna",)
    if set(factories) != set(labels):
        raise ValueError("service factories must match the requested evaluation runs")

    services = {label: factories[label]() for label in labels}
    opened: list[EvaluationService] = []
    runs: dict[str, dict[str, Any]] = {}
    try:
        for label in labels:
            services[label].open()
            opened.append(services[label])
        for label in labels:
            case_reports = [
                _case_report(
                    case,
                    services[label].search_execution(
                        query=case.query,
                        filters=case.filters,
                        limit=10,
                    ),
                )
                for case in cases
            ]
            runs[label] = {
                "aggregate": _aggregate(
                    case_reports,
                    input_cost_per_million=input_cost_per_million,
                    output_cost_per_million=output_cost_per_million,
                ),
                "cases": case_reports,
            }
    finally:
        for service in reversed(opened):
            service.close()

    report: dict[str, Any] = {
        "schema_version": "unified-search-eval-v1",
        "candidate_pool_bounds": {
            "elasticsearch": MAX_SOURCE_CANDIDATES,
            "ncbi": MAX_SOURCE_CANDIDATES,
            "merged": MAX_MERGED_CANDIDATES,
        },
        "pricing_per_million_tokens": {
            "input": input_cost_per_million,
            "output": output_cost_per_million,
        },
        "runs": runs,
    }
    write_report_atomic(output_path, report)
    return report


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("price must be finite and non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate unified GEO search quality, latency, fallback, and cost."
    )
    parser.add_argument("cases", type=Path, help="Versioned JSONL evaluation cases")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--compare-baseline", action="store_true")
    parser.add_argument(
        "--input-cost-per-million", type=_non_negative_float, required=True
    )
    parser.add_argument(
        "--output-cost-per-million", type=_non_negative_float, required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_evaluation(
        cases_path=args.cases,
        output_path=args.output,
        compare_baseline=args.compare_baseline,
        input_cost_per_million=args.input_cost_per_million,
        output_cost_per_million=args.output_cost_per_million,
    )


if __name__ == "__main__":
    main()

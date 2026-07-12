"""Read-only live comparison of full Elasticsearch retrieval paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .elasticsearch_config import (
    INDEX_NAME,
    VECTOR_FIELDS,
    ElasticsearchSettings,
    create_client,
    response_body,
)
from .elasticsearch_index import MAPPING_REVISION
from .elasticsearch_query_embeddings import (
    COMPARISON_MODEL_KEYS,
    QueryEncoder,
    QueryEncoderInfo,
    create_query_encoder,
)
from .elasticsearch_search import ElasticsearchSearchService
from .search_models import FACET_FIELDS, SearchFilters, SearchResponse


_QUERY_ID_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class LiveQueryCase:
    query_id: str
    query: str
    intent: str
    filters: SearchFilters


@dataclass(frozen=True)
class IndexSnapshot:
    server_version: str
    mapping_revision: str
    document_count: int
    vector_coverage: dict[str, int]


@dataclass(frozen=True)
class FeatureCheck:
    name: str
    passed: bool
    note: str


@dataclass(frozen=True)
class ModelComparison:
    info: QueryEncoderInfo
    dense_by_query: dict[str, SearchResponse]
    hybrid_by_query: dict[str, SearchResponse]


@dataclass(frozen=True)
class ComparisonRun:
    snapshot: IndexSnapshot
    cases: tuple[LiveQueryCase, ...]
    checks: tuple[FeatureCheck, ...]
    bm25_by_query: dict[str, SearchResponse]
    models: dict[str, ModelComparison]


def load_query_cases(path: Path) -> tuple[LiveQueryCase, ...]:
    """Load stable researcher query cases from a JSONL fixture."""

    cases: list[LiveQueryCase] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read query fixture {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid query JSON on line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"query line {line_number} must be an object")
        query_id = str(raw.get("query_id", "")).strip()
        query = str(raw.get("query", "")).strip()
        intent = str(raw.get("intent", "")).strip()
        if not _QUERY_ID_RE.fullmatch(query_id):
            raise ValueError(f"invalid query_id on line {line_number}: {query_id!r}")
        if query_id in seen:
            raise ValueError(f"duplicate query_id on line {line_number}: {query_id}")
        if not query:
            raise ValueError(f"blank query on line {line_number}")
        if not intent:
            raise ValueError(f"blank intent on line {line_number}")
        try:
            filters = SearchFilters.from_mapping(raw.get("filters"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid filters on line {line_number}: {exc}") from exc
        seen.add(query_id)
        cases.append(LiveQueryCase(query_id, query, intent, filters))
    if not cases:
        raise ValueError("query fixture is empty")
    return tuple(cases)


def inspect_index(client) -> IndexSnapshot:
    """Validate the live comparison index without mutating it."""

    info = response_body(client.info())
    try:
        server_version = str(info["version"]["number"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Elasticsearch info response is malformed") from exc
    if server_version != "9.4.2":
        raise ValueError(
            f"live comparison requires Elasticsearch 9.4.2, got {server_version}"
        )
    health = response_body(client.cluster.health(index=INDEX_NAME))
    if health.get("status") == "red":
        raise ValueError("Elasticsearch cluster health is red")

    raw_mapping = response_body(client.indices.get_mapping(index=INDEX_NAME))
    try:
        mapping = raw_mapping[INDEX_NAME]["mappings"]
        metadata = mapping["_meta"]
        properties = mapping["properties"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Elasticsearch mapping response is malformed") from exc
    revision = metadata.get("mapping_revision")
    if revision != MAPPING_REVISION:
        raise ValueError(
            f"mapping revision {revision!r} does not match {MAPPING_REVISION!r}"
        )
    for spec in VECTOR_FIELDS.values():
        vector_mapping = properties.get(spec.field, {})
        if vector_mapping.get("dims") != spec.dimensions:
            raise ValueError(
                f"mapping field {spec.field} expected {spec.dimensions} dimensions"
            )

    document_count = int(client.count(index=INDEX_NAME)["count"])
    if document_count < 1:
        raise ValueError("geo-series is empty")
    vector_coverage = {
        model_key: int(
            client.count(
                index=INDEX_NAME,
                query={"exists": {"field": spec.field}},
            )["count"]
        )
        for model_key, spec in VECTOR_FIELDS.items()
    }
    for model_key in COMPARISON_MODEL_KEYS:
        if vector_coverage[model_key] != document_count:
            raise ValueError(
                f"incomplete vector coverage for {model_key}: "
                f"{vector_coverage[model_key]}/{document_count}"
            )
    return IndexSnapshot(
        server_version=server_version,
        mapping_revision=str(revision),
        document_count=document_count,
        vector_coverage=vector_coverage,
    )


def _validate_hit_filters(
    response: SearchResponse, filters: SearchFilters, context: str
) -> None:
    for hit in response.hits:
        for field in FACET_FIELDS:
            selected = set(getattr(filters, field))
            if not selected:
                continue
            raw_values = hit.get(field, [])
            values = {str(value) for value in raw_values} if isinstance(raw_values, list) else set()
            if not selected & values:
                raise ValueError(f"{context}: hit {hit.get('gse')} leaked filter {field}")


def _validate_order(response: SearchResponse, context: str) -> None:
    pairs = [(float(hit.get("score", 0.0)), str(hit.get("gse", ""))) for hit in response.hits]
    expected = sorted(pairs, key=lambda pair: (-pair[0], pair[1]))
    if pairs != expected:
        raise ValueError(f"{context}: unstable score/GSE ordering")


def _validate_query_response(
    response: SearchResponse,
    case: LiveQueryCase,
    *,
    model_key: str,
    mode: str,
    topk: int,
    facet_pool: int,
) -> None:
    context = f"{model_key}/{case.query_id}/{mode}"
    if len(response.hits) != topk:
        raise ValueError(f"{context}: expected {topk} hits, got {len(response.hits)}")
    _validate_hit_filters(response, case.filters, context)
    _validate_order(response, context)
    for field, facet in response.facets.items():
        if facet.scope != "candidate_pool":
            raise ValueError(f"{context}: facet {field} has scope {facet.scope}")
        if facet.candidate_count is None or not 0 < facet.candidate_count <= facet_pool:
            raise ValueError(f"{context}: facet {field} has invalid candidate count")
    provenance = response.provenance
    spec = VECTOR_FIELDS[model_key]
    if provenance is None or (
        provenance.backend != "elasticsearch"
        or provenance.mapping_revision != MAPPING_REVISION
        or provenance.active_model_key != model_key
        or provenance.vector_field != spec.field
        or provenance.dimensions != spec.dimensions
        or provenance.mode != mode
    ):
        raise ValueError(f"{context}: provenance does not match retrieval path")


def run_comparison(
    client: Any,
    cases: tuple[LiveQueryCase, ...],
    *,
    encoder_factory: Callable[[str], QueryEncoder] = create_query_encoder,
    service_factory: Callable[..., Any] = ElasticsearchSearchService,
    topk: int = 5,
) -> ComparisonRun:
    """Run read-only full-featured and diagnostic searches for every model."""

    snapshot = inspect_index(client)
    common = {
        "topk": topk,
        "deep": 100,
        "num_candidates": 500,
        "k0": 60,
        "facet_pool": 100,
        "bucket_limit": 10,
    }
    base_service = service_factory(
        client,
        active_model_key="bge_small_v15",
        encode_query=lambda _query: [0.0] * 383 + [1.0],
    )
    exact = base_service.get_dataset("gse1124")
    if exact is None or exact.get("gse") != "GSE1124":
        raise ValueError("exact lookup preflight did not return GSE1124")

    filter_probe = SearchFilters(
        organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
        assay_categories=("expression (array)",),
    )
    blank_probe = base_service.search("", mode="bm25", filters=filter_probe, **common)
    _validate_hit_filters(blank_probe, filter_probe, "blank filter preflight")
    _validate_order(blank_probe, "blank filter preflight")
    if any(facet.scope != "all_matches" for facet in blank_probe.facets.values()):
        raise ValueError("blank facet preflight did not use all_matches scope")

    own_filter = SearchFilters(
        organism_ids=("NCBITaxon:9606",),
        assay_categories=("expression (array)",),
    )
    own_probe = base_service.search("", mode="bm25", filters=own_filter, **common)
    organism_values = {
        bucket.value for bucket in own_probe.facets["organism_ids"].buckets
    }
    if "NCBITaxon:10090" not in organism_values:
        raise ValueError("organism facet did not omit its own filter")

    bm25_by_query: dict[str, SearchResponse] = {}
    for case in cases:
        response = base_service.search(
            case.query, mode="bm25", filters=case.filters, **common
        )
        _validate_query_response(
            response,
            case,
            model_key="bge_small_v15",
            mode="bm25",
            topk=topk,
            facet_pool=100,
        )
        bm25_by_query[case.query_id] = response

    models: dict[str, ModelComparison] = {}
    for model_key in COMPARISON_MODEL_KEYS:
        encoder = encoder_factory(model_key)
        try:
            service = service_factory(
                client,
                active_model_key=model_key,
                encode_query=encoder.encode,
            )
            dense_by_query: dict[str, SearchResponse] = {}
            hybrid_by_query: dict[str, SearchResponse] = {}
            for case in cases:
                dense = service.search(
                    case.query, mode="dense", filters=case.filters, **common
                )
                hybrid = service.search(
                    case.query, mode="hybrid", filters=case.filters, **common
                )
                _validate_query_response(
                    dense,
                    case,
                    model_key=model_key,
                    mode="dense",
                    topk=topk,
                    facet_pool=100,
                )
                _validate_query_response(
                    hybrid,
                    case,
                    model_key=model_key,
                    mode="hybrid",
                    topk=topk,
                    facet_pool=100,
                )
                dense_by_query[case.query_id] = dense
                hybrid_by_query[case.query_id] = hybrid
            models[model_key] = ModelComparison(
                encoder.info, dense_by_query, hybrid_by_query
            )
        finally:
            encoder.close()

    checks = (
        FeatureCheck("index_preflight", True, "Elasticsearch 9.4.2 mapping and coverage"),
        FeatureCheck("exact_lookup", True, "lowercase gse1124 resolved to GSE1124"),
        FeatureCheck("filters", True, "OR-within and AND-across filters held"),
        FeatureCheck("blank_facets", True, "all_matches and own-filter omission held"),
        FeatureCheck("full_hybrid", True, "BM25+dense native RRF passed for all cases"),
        FeatureCheck("provenance", True, "model field, dimensions, mapping, and mode matched"),
    )
    return ComparisonRun(
        snapshot=snapshot,
        cases=cases,
        checks=checks,
        bm25_by_query=bm25_by_query,
        models=models,
    )


def overlap_at_five(left: SearchResponse, right: SearchResponse) -> int:
    """Count shared GSE accessions in two top-five result sets."""

    left_gses = {str(hit["gse"]) for hit in left.hits[:5]}
    right_gses = {str(hit["gse"]) for hit in right.hits[:5]}
    return len(left_gses & right_gses)


def _markdown_text(value: object, *, limit: int | None = None) -> str:
    text = " ".join(str(value).split()).replace("|", r"\|")
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _hit_cell(hit: dict[str, object]) -> str:
    return f"{hit['gse']} — {_markdown_text(hit.get('title', ''), limit=100)}"


def _filters_text(filters: SearchFilters) -> str:
    active = {key: value for key, value in filters.as_dict().items() if value}
    return _markdown_text(json.dumps(active, sort_keys=True, separators=(",", ":")))


def render_markdown(
    run: ComparisonRun,
    *,
    source_revision: str,
    query_digest: str,
) -> str:
    """Render one deterministic, diff-friendly comparison report."""

    lines = [
        "# Elasticsearch Live Search Comparison",
        "",
        "## Run provenance",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| Source revision | `{_markdown_text(source_revision)}` |",
        f"| Query fixture SHA-256 | `{_markdown_text(query_digest)}` |",
        f"| Elasticsearch | `{run.snapshot.server_version}` |",
        f"| Index | `{INDEX_NAME}` |",
        f"| Mapping | `{run.snapshot.mapping_revision}` |",
        f"| Documents | {run.snapshot.document_count} |",
        "| Retrieval | topk=5, deep=100, candidates=500, RRF k0=60, facet pool=100 |",
        "",
        "## Model readiness",
        "",
        "| Model | Query model | Revision | Vector field | Dimensions | Coverage |",
        "|---|---|---|---|---:|---:|",
    ]
    for model_key in COMPARISON_MODEL_KEYS:
        model = run.models[model_key]
        spec = VECTOR_FIELDS[model_key]
        lines.append(
            f"| `{model_key}` | `{model.info.model_id}` | `{model.info.revision}` | "
            f"`{spec.field}` | {spec.dimensions} | "
            f"{run.snapshot.vector_coverage[model_key]} |"
        )
    gemini = VECTOR_FIELDS["gemini_embedding_2_3072_v1"]
    lines.append(
        f"| `gemini_embedding_2_3072_v1` (context only) | `gemini-embedding-2` | — | "
        f"`{gemini.field}` | {gemini.dimensions} | "
        f"{run.snapshot.vector_coverage['gemini_embedding_2_3072_v1']} |"
    )
    lines.extend(
        [
            "",
            "## Feature proof",
            "",
            "| Feature | Status | Evidence |",
            "|---|---|---|",
        ]
    )
    for check in run.checks:
        label = check.name.replace("_", " ").capitalize()
        lines.append(
            f"| {label} | {'PASS' if check.passed else 'FAIL'} | "
            f"{_markdown_text(check.note)} |"
        )

    for case in run.cases:
        lines.extend(
            [
                "",
                f"## Query: {case.query_id}",
                "",
                f"**Search:** {_markdown_text(case.query)}  ",
                f"**Intent:** {_markdown_text(case.intent)}  ",
                f"**Filters:** `{_filters_text(case.filters)}`",
                "",
                "### Full hybrid: native RRF (BM25 + dense)",
                "",
                "| Rank | BGE | MedCPT | Qwen |",
                "|---:|---|---|---|",
            ]
        )
        hybrid = {
            key: run.models[key].hybrid_by_query[case.query_id]
            for key in COMPARISON_MODEL_KEYS
        }
        for rank in range(5):
            lines.append(
                f"| {rank + 1} | {_hit_cell(hybrid['bge_small_v15'].hits[rank])} | "
                f"{_hit_cell(hybrid['medcpt_v1'].hits[rank])} | "
                f"{_hit_cell(hybrid['qwen3_06b_1024_v1'].hits[rank])} |"
            )
        lines.extend(
            [
                "",
                "### Diagnostic components",
                "",
                "| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |",
                "|---:|---|---|---|---|",
            ]
        )
        bm25 = run.bm25_by_query[case.query_id]
        dense = {
            key: run.models[key].dense_by_query[case.query_id]
            for key in COMPARISON_MODEL_KEYS
        }
        for rank in range(5):
            lines.append(
                f"| {rank + 1} | {_hit_cell(bm25.hits[rank])} | "
                f"{_hit_cell(dense['bge_small_v15'].hits[rank])} | "
                f"{_hit_cell(dense['medcpt_v1'].hits[rank])} | "
                f"{_hit_cell(dense['qwen3_06b_1024_v1'].hits[rank])} |"
            )
        lines.extend(
            [
                "",
                "### Hybrid facet evidence",
                "",
                "| Model | Facet | Scope | Candidates | Top buckets |",
                "|---|---|---|---:|---|",
            ]
        )
        for model_key in COMPARISON_MODEL_KEYS:
            response = hybrid[model_key]
            for field in FACET_FIELDS:
                facet = response.facets[field]
                buckets = ", ".join(
                    f"{bucket.value} ({bucket.count})" for bucket in facet.buckets[:3]
                )
                lines.append(
                    f"| `{model_key}` | `{field}` | `{facet.scope}` | "
                    f"{facet.candidate_count} | {_markdown_text(buckets)} |"
                )

    lines.extend(
        [
            "",
            "## Pairwise overlap@5",
            "",
            "| Query | Mode | BGE/MedCPT | BGE/Qwen | MedCPT/Qwen |",
            "|---|---|---:|---:|---:|",
        ]
    )
    pairs = (
        ("bge_small_v15", "medcpt_v1"),
        ("bge_small_v15", "qwen3_06b_1024_v1"),
        ("medcpt_v1", "qwen3_06b_1024_v1"),
    )
    for case in run.cases:
        for mode in ("dense", "hybrid"):
            responses = {
                key: (
                    run.models[key].dense_by_query[case.query_id]
                    if mode == "dense"
                    else run.models[key].hybrid_by_query[case.query_id]
                )
                for key in COMPARISON_MODEL_KEYS
            }
            overlaps = [overlap_at_five(responses[left], responses[right]) for left, right in pairs]
            lines.append(
                f"| `{case.query_id}` | {mode} | {overlaps[0]} | {overlaps[1]} | {overlaps[2]} |"
            )
    lines.extend(
        [
            "",
            "> This is a qualitative live smoke comparison, not a relevance judgment or model-selection result.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_atomic(path: Path, content: str) -> None:
    """Replace a report only after its complete content is available."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare full Elasticsearch search with three fixed query models"
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("eval/elasticsearch_live_queries.jsonl"),
    )
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/elasticsearch-live-comparison.md"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("GEO_TEST_ELASTIC") != "1":
        print("set GEO_TEST_ELASTIC=1 to run the live comparison", file=sys.stderr)
        return 2
    args = _parser().parse_args(argv)
    client = None
    try:
        if args.topk != 5:
            raise ValueError("the reviewable comparison requires --topk 5")
        settings = ElasticsearchSettings.from_env()
        client = create_client(settings)
        cases = load_query_cases(args.queries)
        run = run_comparison(client, cases, topk=args.topk)
        source_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        query_digest = hashlib.sha256(args.queries.read_bytes()).hexdigest()
        report = render_markdown(
            run,
            source_revision=source_revision,
            query_digest=query_digest,
        )
        write_report_atomic(args.output, report)
        print(f"wrote {args.output} with {len(cases)} queries and 3 models")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"comparison failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())

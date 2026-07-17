from __future__ import annotations

import os
import time
from dataclasses import replace

import pytest

from geo_index.reranker import AnthropicReranker
from geo_index.search_candidates import SearchCandidate
from geo_index.search_eval import estimated_cost


def _candidate(gse: str, rank: int, taxon: str, organism_id: str) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Provider schema smoke {gse}",
        snippet="Skeletal muscle gene expression after endurance exercise.",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=10,
        pubmed_id=None,
        organism_ids=(organism_id,),
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (seq)",),
        assay_labels=(),
        assay_status="category",
        source="elasticsearch",
        retrieval_score=1.0 / rank,
        original_rank=rank,
        native_rank=None,
        taxon=taxon,
    )


@pytest.mark.provider_integration
def test_live_haiku_accepts_the_strict_complete_ranking_schema() -> None:
    if os.environ.get("GEO_TEST_ANTHROPIC") != "1":
        pytest.skip("set GEO_TEST_ANTHROPIC=1 to permit the live provider call")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is not configured")
    reranker = AnthropicReranker(
        api_key=api_key,
        model="claude-haiku-4-5",
        thinking="disabled",
        timeout_seconds=30,
    )
    candidates = (
        _candidate("GSE11803", 1, "Mus musculus", "NCBITaxon:10090"),
        _candidate("GSE310900", 2, "Homo sapiens", "NCBITaxon:9606"),
    )
    try:
        result = reranker.rerank(
            "mouse skeletal muscle after endurance exercise",
            candidates,
            limit=2,
        )
    finally:
        reranker.close()

    assert set(result.scores) == {"GSE11803", "GSE310900"}


@pytest.mark.provider_integration
def test_live_haiku_reranks_maximum_two_hundred_candidate_pool(
    record_property,
) -> None:
    if os.environ.get("GEO_TEST_ANTHROPIC") != "1":
        pytest.skip("set GEO_TEST_ANTHROPIC=1 to permit the live provider call")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is not configured")
    input_price = float(
        os.environ.get("GEO_TEST_ANTHROPIC_INPUT_COST_PER_MILLION", "0")
    )
    output_price = float(
        os.environ.get("GEO_TEST_ANTHROPIC_OUTPUT_COST_PER_MILLION", "0")
    )
    max_latency_seconds = float(
        os.environ.get("GEO_TEST_ANTHROPIC_MAX_LATENCY_SECONDS", "120")
    )
    reranker = AnthropicReranker(
        api_key=api_key,
        model="claude-haiku-4-5",
        thinking="disabled",
        timeout_seconds=max_latency_seconds,
    )
    local = tuple(
        _candidate(
            f"GSE{100_000 + rank}",
            rank,
            "Mus musculus",
            "NCBITaxon:10090",
        )
        for rank in range(1, 101)
    )
    native = tuple(
        replace(
            _candidate(
                f"GSE{200_000 + rank}",
                rank,
                "Homo sapiens",
                "NCBITaxon:9606",
            ),
            source="ncbi",
            retrieval_score=None,
            original_rank=None,
            native_rank=rank,
        )
        for rank in range(1, 101)
    )
    candidates = (*local, *native)

    started = time.perf_counter()
    try:
        result = reranker.rerank(
            "mouse skeletal muscle gene expression after endurance exercise",
            candidates,
            limit=50,
        )
    finally:
        elapsed_seconds = time.perf_counter() - started
        reranker.close()

    cost = estimated_cost(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        input_cost_per_million=input_price,
        output_cost_per_million=output_price,
    )
    record_property("candidate_count", len(candidates))
    record_property("latency_seconds", elapsed_seconds)
    record_property("input_tokens", result.input_tokens)
    record_property("output_tokens", result.output_tokens)
    record_property("estimated_cost", cost)

    assert len(candidates) == 200
    assert set(result.scores) == {candidate.gse for candidate in candidates}
    assert result.input_tokens > 0
    assert 0 < result.output_tokens < 8_000
    assert elapsed_seconds <= max_latency_seconds
    assert cost >= 0

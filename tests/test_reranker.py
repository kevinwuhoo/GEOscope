import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

import geo_index.reranker as reranker_module
from geo_index.reranker import (
    InvalidRerankOutputError,
    OpenAIReranker,
    RerankRefusalError,
    RerankResult,
    rank_candidates,
)
from geo_index.search_candidates import SearchCandidate


def candidate(gse: str, original_rank: int) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Title {gse}",
        snippet="Mouse skeletal muscle after endurance exercise.",
        study_type="Expression profiling by array",
        n_samples=10,
        pubmed_id=None,
        organism_ids=("NCBITaxon:10090",),
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (array)",),
        assay_labels=(),
        assay_status="category",
        source="elasticsearch",
        retrieval_score=0.2,
        original_rank=original_rank,
        native_rank=None,
        taxon="Mus musculus",
    )


def ncbi_candidate(gse: str, native_rank: int) -> SearchCandidate:
    return replace(
        candidate(gse, 1),
        source="ncbi",
        original_rank=None,
        native_rank=native_rank,
    )


class Responses:
    def __init__(
        self,
        output: object,
        *,
        response_output: tuple[object, ...] = (),
    ) -> None:
        self.output = output
        self.response_output = response_output
        self.kwargs: dict[str, object] | None = None
        self.call_count = 0

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.call_count += 1
        self.kwargs = kwargs
        output_text = self.output if isinstance(self.output, str) else json.dumps(self.output)
        return SimpleNamespace(
            output_text=output_text,
            output=self.response_output,
            usage=SimpleNamespace(input_tokens=120, output_tokens=30),
        )


class Client:
    def __init__(
        self,
        output: object,
        *,
        response_output: tuple[object, ...] = (),
    ) -> None:
        self.responses = Responses(output, response_output=response_output)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_reranker(client: Client) -> OpenAIReranker:
    return OpenAIReranker(
        api_key="secret",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=8,
        client=client,
    )


def test_reranker_uses_luna_low_reasoning_and_strict_schema_once() -> None:
    client = Client(
        {
            "rankings": [
                {"gse": "GSE2", "relevance_score": 95},
                {"gse": "GSE1", "relevance_score": 80},
            ]
        }
    )
    reranker = make_reranker(client)

    result = reranker.rerank(
        "mouse exercise",
        (candidate("GSE1", 1), candidate("GSE2", 2)),
        limit=10,
    )

    assert result.scores == {"GSE2": 95, "GSE1": 80}
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    assert client.responses.call_count == 1
    assert client.responses.kwargs is not None
    assert client.responses.kwargs["model"] == "gpt-5.6-luna"
    assert client.responses.kwargs["reasoning"] == {"effort": "low"}
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["max_output_tokens"] == 1_000
    text = client.responses.kwargs["text"]
    assert isinstance(text, dict)
    assert text["format"]["strict"] is True
    assert text["format"]["schema"]["properties"]["rankings"]["items"][
        "properties"
    ]["gse"] == {"type": "string", "enum": ["GSE1", "GSE2"]}
    payload = json.loads(client.responses.kwargs["input"])
    assert payload["query"] == "mouse exercise"
    assert payload["requested_results"] == 10
    assert [item["gse"] for item in payload["candidates"]] == ["GSE1", "GSE2"]


@pytest.mark.parametrize(
    "rankings",
    [
        [{"gse": "GSE1", "relevance_score": 90}],
        [
            {"gse": "GSE1", "relevance_score": 90},
            {"gse": "GSE1", "relevance_score": 80},
        ],
        [
            {"gse": "GSE1", "relevance_score": 90},
            {"gse": "GSE9", "relevance_score": 80},
        ],
    ],
    ids=["missing", "duplicate", "unknown"],
)
def test_reranker_rejects_incomplete_candidate_id_sets(
    rankings: list[dict[str, object]],
) -> None:
    reranker = make_reranker(Client({"rankings": rankings}))

    with pytest.raises(InvalidRerankOutputError) as captured:
        reranker.rerank(
            "query",
            (candidate("GSE1", 1), candidate("GSE2", 2)),
            limit=10,
        )

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 30


@pytest.mark.parametrize("score", [-1, 101, "90", 90.5, True])
def test_reranker_rejects_non_integer_or_out_of_range_scores(score: object) -> None:
    reranker = make_reranker(
        Client({"rankings": [{"gse": "GSE1", "relevance_score": score}]})
    )

    with pytest.raises(InvalidRerankOutputError):
        reranker.rerank("query", (candidate("GSE1", 1),), limit=1)


def test_reranker_raises_distinct_error_for_refusal() -> None:
    refusal = SimpleNamespace(type="refusal", refusal="Cannot comply")
    response_item = SimpleNamespace(content=(refusal,))
    reranker = make_reranker(
        Client("not structured JSON", response_output=(response_item,))
    )

    with pytest.raises(RerankRefusalError) as captured:
        reranker.rerank("query", (candidate("GSE1", 1),), limit=1)

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 30


def test_reranker_raises_invalid_output_for_malformed_json() -> None:
    reranker = make_reranker(Client("not structured JSON"))

    with pytest.raises(InvalidRerankOutputError) as captured:
        reranker.rerank("query", (candidate("GSE1", 1),), limit=1)

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 30


def test_reranker_bounds_candidate_text_before_the_provider_call() -> None:
    client = Client({"rankings": [{"gse": "GSE1", "relevance_score": 50}]})
    reranker = make_reranker(client)

    reranker.rerank(
        "query", (replace(candidate("GSE1", 1), snippet="x" * 5_000),), limit=1
    )

    assert client.responses.kwargs is not None
    payload = json.loads(client.responses.kwargs["input"])
    assert len(payload["candidates"][0]["summary"]) == 800


def test_rank_candidates_uses_score_then_source_ranks_then_gse() -> None:
    candidates = (
        ncbi_candidate("GSE8", 2),
        candidate("GSE2", 1),
        ncbi_candidate("GSE7", 1),
        candidate("GSE1", 2),
        ncbi_candidate("GSE6", 2),
        ncbi_candidate("GSE9", 3),
    )
    ordered = rank_candidates(
        candidates,
        RerankResult(
            scores={
                "GSE1": 80,
                "GSE2": 80,
                "GSE6": 80,
                "GSE7": 80,
                "GSE8": 80,
                "GSE9": 90,
            },
            input_tokens=10,
            output_tokens=5,
        ),
    )

    assert [item.gse for item in ordered] == [
        "GSE9",
        "GSE2",
        "GSE1",
        "GSE7",
        "GSE6",
        "GSE8",
    ]


def test_empty_candidate_set_skips_provider_call() -> None:
    client = Client({"rankings": []})
    reranker = make_reranker(client)

    result = reranker.rerank("query", (), limit=10)

    assert result == RerankResult(scores={}, input_tokens=0, output_tokens=0)
    assert client.responses.call_count == 0


@pytest.mark.parametrize("limit", [0, 51])
def test_reranker_rejects_out_of_range_limits(limit: int) -> None:
    client = Client({"rankings": []})
    reranker = make_reranker(client)

    with pytest.raises(ValueError, match="between 1 and 50"):
        reranker.rerank("query", (candidate("GSE1", 1),), limit=limit)

    assert client.responses.call_count == 0


def test_reranker_closes_its_client() -> None:
    client = Client({"rankings": []})
    reranker = make_reranker(client)

    reranker.close()

    assert client.closed is True


def test_reranker_configures_sdk_transport_for_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = Client({"rankings": []})

    def openai_factory(**kwargs: Any) -> Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(reranker_module, "OpenAI", openai_factory)

    OpenAIReranker(
        api_key="secret",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=8,
    )

    assert captured == {"api_key": "secret", "timeout": 8, "max_retries": 1}

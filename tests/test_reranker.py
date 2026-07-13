import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from anthropic import APITimeoutError

import geo_index.reranker as reranker_module
from geo_index.reranker import (
    STATIC_RANKING_SCHEMA,
    AnthropicReranker,
    InvalidRerankOutputError,
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


def message(
    rankings: list[dict[str, object]],
    *,
    input_tokens: int = 120,
    output_tokens: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text=json.dumps({"rankings": rankings}),
            )
        ],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


class Messages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None
        self.call_count = 0

    def create(self, **kwargs: object) -> object:
        self.call_count += 1
        self.kwargs = kwargs
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class Client:
    def __init__(self, response: object) -> None:
        self.messages = Messages(response)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_reranker(client: Client) -> AnthropicReranker:
    return AnthropicReranker(
        api_key="secret",
        model="claude-sonnet-5",
        reasoning_effort="low",
        thinking="disabled",
        timeout_seconds=8,
        client=client,
    )


def test_reranker_uses_sonnet_messages_contract_with_static_schema() -> None:
    client = Client(
        message(
            [
                {"gse": "GSE2", "relevance_score": 95},
                {"gse": "GSE1", "relevance_score": 80},
            ]
        )
    )
    reranker = make_reranker(client)

    result = reranker.rerank(
        "mouse exercise",
        (candidate("GSE1", 1), candidate("GSE2", 2)),
        limit=10,
    )

    assert result.scores == {"GSE2": 95, "GSE1": 80}
    assert (result.input_tokens, result.output_tokens) == (120, 20)
    assert client.messages.call_count == 1
    assert client.messages.kwargs is not None
    request = client.messages.kwargs
    assert request["model"] == "claude-sonnet-5"
    assert request["thinking"] == {"type": "disabled"}
    assert request["output_config"]["effort"] == "low"  # type: ignore[index]
    assert request["output_config"]["format"] == {  # type: ignore[index]
        "type": "json_schema",
        "schema": STATIC_RANKING_SCHEMA,
    }
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request
    assert request["max_tokens"] == 1_000
    assert request["max_tokens"] <= 8_000  # type: ignore[operator]

    messages = request["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    payload = json.loads(messages[0]["content"])
    assert payload["query"] == "mouse exercise"
    assert payload["requested_results"] == 10
    assert [item["gse"] for item in payload["candidates"]] == ["GSE1", "GSE2"]

    serialized_schema = json.dumps(STATIC_RANKING_SCHEMA)
    for query_specific_value in ("GSE1", "GSE2", "mouse exercise"):
        assert query_specific_value not in serialized_schema
    for dynamic_constraint in (
        '"enum"',
        '"minimum"',
        '"maximum"',
        '"minItems"',
        '"maxItems"',
    ):
        assert dynamic_constraint not in serialized_schema


def test_reranker_parses_one_text_block_and_preserves_usage() -> None:
    valid = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text='{"rankings":[{"gse":"GSE1","relevance_score":91}]}',
            )
        ],
        usage=SimpleNamespace(input_tokens=120, output_tokens=20),
    )

    result = make_reranker(Client(valid)).rerank(
        "query", (candidate("GSE1", 1),), limit=1
    )

    assert result == RerankResult(
        scores={"GSE1": 91},
        input_tokens=120,
        output_tokens=20,
    )


def test_reranker_raises_typed_error_for_refusal_with_usage() -> None:
    refusal = SimpleNamespace(
        stop_reason="refusal",
        content=[SimpleNamespace(type="text", text="refused")],
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    )

    with pytest.raises(RerankRefusalError) as captured:
        make_reranker(Client(refusal)).rerank(
            "query", (candidate("GSE1", 1),), limit=1
        )

    assert captured.value.input_tokens == 12
    assert captured.value.output_tokens == 3


def test_reranker_rejects_truncated_response_before_parsing_with_usage() -> None:
    truncated = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="text", text='{"rankings":[')],
        usage=SimpleNamespace(input_tokens=120, output_tokens=8_000),
    )

    with pytest.raises(InvalidRerankOutputError) as captured:
        make_reranker(Client(truncated)).rerank(
            "query", (candidate("GSE1", 1),), limit=1
        )

    assert str(captured.value) == "reranker response was truncated"
    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 8_000


@pytest.mark.parametrize(
    "content",
    [
        [],
        [SimpleNamespace(type="tool_use", name="rankings")],
        [
            SimpleNamespace(type="text", text='{"rankings":[]}'),
            SimpleNamespace(type="text", text='{"rankings":[]}'),
        ],
        [
            SimpleNamespace(type="text", text='{"rankings":[]}'),
            SimpleNamespace(type="tool_use", name="rankings"),
        ],
    ],
    ids=["empty", "non-text", "multiple-text", "mixed-multiple"],
)
def test_reranker_rejects_any_response_without_exactly_one_text_block(
    content: list[SimpleNamespace],
) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=content,
        usage=SimpleNamespace(input_tokens=7, output_tokens=4),
    )

    with pytest.raises(InvalidRerankOutputError) as captured:
        make_reranker(Client(response)).rerank(
            "query", (candidate("GSE1", 1),), limit=1
        )

    assert captured.value.input_tokens == 7
    assert captured.value.output_tokens == 4


def test_reranker_rejects_malformed_json_with_usage() -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="not structured JSON")],
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
    )

    with pytest.raises(InvalidRerankOutputError) as captured:
        make_reranker(Client(response)).rerank(
            "query", (candidate("GSE1", 1),), limit=1
        )

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 30


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
        [
            {"gse": "GSE1", "relevance_score": 90},
            {"gse": "gse2", "relevance_score": 80},
        ],
    ],
    ids=["missing", "duplicate", "invented", "modified"],
)
def test_reranker_rejects_any_candidate_identifier_mismatch(
    rankings: list[dict[str, object]],
) -> None:
    reranker = make_reranker(Client(message(rankings)))

    with pytest.raises(InvalidRerankOutputError) as captured:
        reranker.rerank(
            "query",
            (candidate("GSE1", 1), candidate("GSE2", 2)),
            limit=10,
        )

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 20


@pytest.mark.parametrize("score", [-1, 101, "90", 90.5, True])
def test_reranker_rejects_non_integer_or_out_of_range_scores(score: object) -> None:
    reranker = make_reranker(
        Client(message([{"gse": "GSE1", "relevance_score": score}]))
    )

    with pytest.raises(InvalidRerankOutputError):
        reranker.rerank("query", (candidate("GSE1", 1),), limit=1)


@pytest.mark.parametrize(
    "stop_reason", [None, "pause_turn", "stop_sequence", "tool_use"]
)
def test_reranker_fails_closed_for_unexpected_stop_reason(
    stop_reason: str | None,
) -> None:
    response = message([{"gse": "GSE1", "relevance_score": 90}])
    response.stop_reason = stop_reason

    with pytest.raises(InvalidRerankOutputError) as captured:
        make_reranker(Client(response)).rerank(
            "query", (candidate("GSE1", 1),), limit=1
        )

    assert captured.value.input_tokens == 120
    assert captured.value.output_tokens == 20


def test_reranker_translates_provider_timeout_to_safe_builtin_error() -> None:
    provider_error = APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com")
    )
    client = Client(provider_error)

    with pytest.raises(TimeoutError) as captured:
        make_reranker(client).rerank("query", (candidate("GSE1", 1),), limit=1)

    assert str(captured.value) == "reranker request timed out"
    assert "anthropic" not in str(captured.value).lower()
    assert captured.value.__cause__ is provider_error


def test_reranker_bounds_candidate_text_before_the_provider_call() -> None:
    client = Client(message([{"gse": "GSE1", "relevance_score": 50}]))
    reranker = make_reranker(client)

    reranker.rerank(
        "query", (replace(candidate("GSE1", 1), snippet="x" * 5_000),), limit=1
    )

    assert client.messages.kwargs is not None
    messages = client.messages.kwargs["messages"]
    payload = json.loads(messages[0]["content"])  # type: ignore[index]
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
    client = Client(message([]))
    reranker = make_reranker(client)

    result = reranker.rerank("query", (), limit=10)

    assert result == RerankResult(scores={}, input_tokens=0, output_tokens=0)
    assert client.messages.call_count == 0


def test_reranker_accepts_two_hundred_candidates_at_bounded_output_budget() -> None:
    candidates = tuple(candidate(f"GSE{index}", index) for index in range(1, 201))
    client = Client(
        message(
            [
                {"gse": item.gse, "relevance_score": 100 - (index % 101)}
                for index, item in enumerate(candidates)
            ]
        )
    )
    reranker = make_reranker(client)

    result = reranker.rerank("maximum candidate pool", candidates, limit=50)

    assert len(result.scores) == 200
    assert client.messages.kwargs is not None
    expected_budget = min(8_000, max(1_000, len(candidates) * 40))
    assert client.messages.kwargs["max_tokens"] == expected_budget == 8_000


@pytest.mark.parametrize("limit", [0, 51])
def test_reranker_rejects_out_of_range_limits(limit: int) -> None:
    client = Client(message([]))
    reranker = make_reranker(client)

    with pytest.raises(ValueError, match="between 1 and 50"):
        reranker.rerank("query", (candidate("GSE1", 1),), limit=limit)

    assert client.messages.call_count == 0


def test_reranker_closes_its_client() -> None:
    client = Client(message([]))
    reranker = make_reranker(client)

    reranker.close()

    assert client.closed is True


def test_reranker_configures_sdk_transport_for_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = Client(message([]))

    def anthropic_factory(**kwargs: Any) -> Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(reranker_module, "Anthropic", anthropic_factory)

    AnthropicReranker(
        api_key="secret",
        model="claude-sonnet-5",
        reasoning_effort="low",
        thinking="disabled",
        timeout_seconds=8,
    )

    assert captured == {"api_key": "secret", "timeout": 8, "max_retries": 1}

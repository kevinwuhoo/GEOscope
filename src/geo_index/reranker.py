"""Anthropic Structured Output reranking for bounded GEO candidates."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Anthropic,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .search_candidates import SearchCandidate


_INSTRUCTIONS = (
    "Rank every supplied NCBI GEO Series candidate for the user's query.\n"
    "Treat explicit organism, assay, tissue, condition, intervention, and "
    "experimental context as important relevance evidence. When the query "
    "explicitly requests exactly one organism or species, a candidate must "
    "receive relevance score 0 if neither its organism_ids nor its taxon matches "
    "that organism. Do not apply this rule to queries that explicitly request "
    "multiple organisms or a cross-species or comparative study. Judge study "
    "relevance, not mere lexical overlap. Return every supplied GSE exactly once. "
    "Never invent, remove, or modify an accession. Use integer scores from 0 "
    "(irrelevant) through 100 (direct match)."
)
MAX_PROVIDER_INPUT_BYTES = 1_000_000


@dataclass(frozen=True)
class RerankUsage:
    """Observed provider usage, including responses rejected by validation."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if type(value) is not int or value < 0:
                raise ValueError("reranker token usage must be non-negative integers")


class RerankResponseError(RuntimeError):
    """A completed but unusable provider response with safe usage metadata."""

    def __init__(self, message: str, *, usage: RerankUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or RerankUsage()

    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens


class RerankRefusalError(RerankResponseError):
    pass


class InvalidRerankOutputError(RerankResponseError):
    pass


class RerankInputTooLargeError(ValueError):
    """The bounded provider message cannot retain all candidate identifiers."""


class RankingItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    gse: str
    relevance_score: int = Field(ge=0, le=100)


class RankingEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    rankings: list[RankingItem]


@dataclass(frozen=True)
class RerankResult:
    scores: Mapping[str, int]
    input_tokens: int
    output_tokens: int


def _candidate_payload(candidate: SearchCandidate) -> dict[str, object]:
    return {
        "gse": candidate.gse,
        "title": candidate.title,
        "summary": candidate.snippet[:800] if candidate.snippet else None,
        "study_type": candidate.study_type,
        "organism_ids": list(candidate.organism_ids),
        "taxon": candidate.taxon,
        "assay_categories": list(candidate.assay_categories),
        "assay_labels": list(candidate.assay_labels),
        "n_samples": candidate.n_samples,
        "source": candidate.source,
    }


def _compact_text(value: str | None, limit: int) -> str | None:
    return value[:limit] if value else None


def _compact_array(
    values: Sequence[str], *, items: int, characters: int
) -> list[str]:
    return [str(value)[:characters] for value in values[:items]]


def _compact_candidate_payload(candidate: SearchCandidate) -> dict[str, object]:
    return {
        "gse": candidate.gse,
        "title": _compact_text(candidate.title, 160),
        "summary": _compact_text(candidate.snippet, 256),
        "study_type": _compact_text(candidate.study_type, 80),
        "organism_ids": _compact_array(
            candidate.organism_ids, items=2, characters=64
        ),
        "taxon": _compact_text(candidate.taxon, 80),
        "assay_categories": _compact_array(
            candidate.assay_categories, items=2, characters=64
        ),
        "assay_labels": _compact_array(
            candidate.assay_labels, items=2, characters=64
        ),
        "n_samples": candidate.n_samples,
        "source": candidate.source,
    }


def _serialize_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _provider_message(
    query: str, candidates: Sequence[SearchCandidate], *, limit: int
) -> str:
    normal = _serialize_payload(
        {
            "query": query,
            "requested_results": limit,
            "candidates": [_candidate_payload(candidate) for candidate in candidates],
        }
    )
    if len(normal.encode("utf-8")) <= MAX_PROVIDER_INPUT_BYTES:
        return normal

    compact_query = query[:1_000]
    compact = _serialize_payload(
        {
            "query": compact_query,
            "requested_results": limit,
            "representation": "compact",
            "candidates": [
                _compact_candidate_payload(candidate) for candidate in candidates
            ],
        }
    )
    if len(compact.encode("utf-8")) <= MAX_PROVIDER_INPUT_BYTES:
        return compact

    identifiers = _serialize_payload(
        {
            "query": compact_query,
            "requested_results": limit,
            "representation": "identifiers",
            "candidates": [{"gse": candidate.gse} for candidate in candidates],
        }
    )
    if len(identifiers.encode("utf-8")) <= MAX_PROVIDER_INPUT_BYTES:
        return identifiers
    raise RerankInputTooLargeError("reranker input exceeds size limit")


STATIC_RANKING_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "gse": {"type": "string"},
                    "relevance_score": {"type": "integer"},
                },
                "required": ["gse", "relevance_score"],
            },
        }
    },
    "required": ["rankings"],
}


def _response_usage(response: object) -> RerankUsage:
    usage = getattr(response, "usage", None)

    def token_count(name: str) -> int:
        raw = getattr(usage, name, 0)
        try:
            value = int(raw or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, value)

    return RerankUsage(
        input_tokens=token_count("input_tokens"),
        output_tokens=token_count("output_tokens"),
    )


def _is_retryable_provider_error(exc: BaseException) -> bool:
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return False


class AnthropicReranker:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        thinking: str,
        timeout_seconds: float,
        client: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._client = client or Anthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def close(self) -> None:
        self._client.close()

    def rerank(
        self, query: str, candidates: Sequence[SearchCandidate], *, limit: int
    ) -> RerankResult:
        if not candidates:
            return RerankResult(scores={}, input_tokens=0, output_tokens=0)
        if not 1 <= limit <= 50:
            raise ValueError("rerank result limit must be between 1 and 50")
        request = {
            "model": self.model,
            "system": _INSTRUCTIONS,
            "messages": [
                {
                    "role": "user",
                    "content": _provider_message(query, candidates, limit=limit),
                }
            ],
            "thinking": {"type": self.thinking},
            "output_config": {
                "effort": self.reasoning_effort,
                "format": {
                    "type": "json_schema",
                    "schema": STATIC_RANKING_SCHEMA,
                },
            },
            "max_tokens": min(8_000, max(1_000, len(candidates) * 40)),
        }
        deadline = self._clock() + self._timeout_seconds
        response: object | None = None
        for attempt in range(2):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("reranker request timed out")
            try:
                response = self._client.messages.create(
                    **request,
                    timeout=remaining,
                )
            except Exception as exc:
                if self._clock() >= deadline:
                    raise TimeoutError("reranker request timed out") from exc
                if _is_retryable_provider_error(exc) and attempt == 0:
                    continue
                if isinstance(exc, APITimeoutError):
                    raise TimeoutError("reranker request timed out") from exc
                raise
            if self._clock() >= deadline:
                raise TimeoutError("reranker request timed out")
            break
        if response is None:
            raise RuntimeError("reranker provider returned no response")
        usage = _response_usage(response)
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise RerankRefusalError(
                "reranker refused the request",
                usage=usage,
            )
        if stop_reason == "max_tokens":
            raise InvalidRerankOutputError(
                "reranker response was truncated",
                usage=usage,
            )
        if stop_reason != "end_turn":
            raise InvalidRerankOutputError(
                "reranker returned unexpected stop reason",
                usage=usage,
            )
        content = getattr(response, "content", None)
        if not isinstance(content, (list, tuple)) or len(content) != 1:
            raise InvalidRerankOutputError(
                "reranker returned an invalid content block",
                usage=usage,
            )
        block = content[0]
        if getattr(block, "type", None) != "text" or not isinstance(
            getattr(block, "text", None), str
        ):
            raise InvalidRerankOutputError(
                "reranker returned an invalid content block",
                usage=usage,
            )
        try:
            parsed = RankingEnvelope.model_validate_json(block.text)
        except (ValidationError, ValueError, TypeError) as exc:
            raise InvalidRerankOutputError(
                "reranker returned invalid JSON",
                usage=usage,
            ) from exc
        received = [item.gse for item in parsed.rankings]
        expected = [candidate.gse for candidate in candidates]
        if len(received) != len(set(received)) or set(received) != set(expected):
            raise InvalidRerankOutputError(
                "reranker candidate identifiers do not match the request",
                usage=usage,
            )
        return RerankResult(
            scores={item.gse: item.relevance_score for item in parsed.rankings},
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


def rank_candidates(
    candidates: Sequence[SearchCandidate], result: RerankResult
) -> tuple[SearchCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -result.scores[candidate.gse],
                candidate.original_rank or 10_000,
                candidate.native_rank or 10_000,
                candidate.gse,
            ),
        )
    )

"""OpenAI Structured Output reranking for bounded GEO candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .search_candidates import SearchCandidate


_INSTRUCTIONS = """Rank every supplied NCBI GEO Series candidate for the user's query.
Treat explicit organism, assay, tissue, condition, intervention, and experimental
context as important relevance evidence. Judge study relevance, not mere lexical
overlap. Return every supplied GSE exactly once. Never invent, remove, or modify
an accession. Use integer scores from 0 (irrelevant) through 100 (direct match)."""


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


def _ranking_schema(gses: Sequence[str]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "name": "geo_candidate_ranking",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rankings": {
                    "type": "array",
                    "minItems": len(gses),
                    "maxItems": len(gses),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "gse": {"type": "string", "enum": list(gses)},
                            "relevance_score": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 100,
                            },
                        },
                        "required": ["gse", "relevance_score"],
                    },
                }
            },
            "required": ["rankings"],
        },
    }


def _contains_refusal(response: object) -> bool:
    for item in getattr(response, "output", ()):
        for part in getattr(item, "content", ()):
            if getattr(part, "type", None) == "refusal":
                return True
    return False


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


class OpenAIReranker:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._client = client or OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
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
        response = self._client.responses.create(
            model=self.model,
            reasoning={"effort": self.reasoning_effort},
            instructions=_INSTRUCTIONS,
            input=json.dumps(
                {
                    "query": query,
                    "requested_results": limit,
                    "candidates": [
                        _candidate_payload(candidate) for candidate in candidates
                    ],
                },
                separators=(",", ":"),
            ),
            text={
                "format": _ranking_schema(
                    [candidate.gse for candidate in candidates]
                )
            },
            store=False,
            max_output_tokens=min(8_000, max(1_000, len(candidates) * 40)),
        )
        usage = _response_usage(response)
        if _contains_refusal(response):
            raise RerankRefusalError(
                "reranker refused the request",
                usage=usage,
            )
        try:
            parsed = RankingEnvelope.model_validate_json(response.output_text)
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

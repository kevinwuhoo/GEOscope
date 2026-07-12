"""Read-only live comparison of full Elasticsearch retrieval paths."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .search_models import SearchFilters


_QUERY_ID_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class LiveQueryCase:
    query_id: str
    query: str
    intent: str
    filters: SearchFilters


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


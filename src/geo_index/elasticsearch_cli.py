"""Primary command-line search interface backed by Elasticsearch."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .elasticsearch_runtime import ElasticsearchRuntime
from .search_models import SearchFilters


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search canonical GEO metadata through Elasticsearch"
    )
    parser.add_argument("query")
    parser.add_argument(
        "--mode", choices=("hybrid", "dense", "bm25"), default="hybrid"
    )
    parser.add_argument("--topk", type=int, default=15)
    parser.add_argument("--organism-id", action="append", default=[])
    parser.add_argument("--sex-id", action="append", default=[])
    parser.add_argument("--assay-category", action="append", default=[])
    parser.add_argument("--assay-label", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    filters = SearchFilters.from_mapping(
        {
            "organism_ids": args.organism_id,
            "sex_ids": args.sex_id,
            "assay_categories": args.assay_category,
            "assay_labels": args.assay_label,
        }
    )
    runtime = ElasticsearchRuntime()
    try:
        response = runtime.search(
            args.query,
            mode=args.mode,
            topk=args.topk,
            filters=filters,
        )
        print(json.dumps(asdict(response), sort_keys=True))
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

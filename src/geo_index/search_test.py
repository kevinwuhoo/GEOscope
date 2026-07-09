"""In-memory semantic search over the embedded series — a retrieval sanity test.

Loads the vectors from ``geo-embed`` and does brute-force cosine top-k (fine at
223k × 384). This is the *test* harness for the core premise — that a query
like "single cell RNA" pulls back studies using 10x / Drop-seq / Smart-seq2 /
SPLiT-seq even when they never say "single cell" — BEFORE we invest in Postgres
+ pgvector + pg_search. It is not the production retrieval path.

Usage:
    uv run geo-search "single cell RNA of human immune cells"
    uv run geo-search "spatial transcriptomics of brain" --topk 15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

DEFAULT_PREFIX = Path("data/processed/embeddings")
DEFAULT_DOCS = Path("data/processed/geo_series.jsonl")
# bge-*-en-v1.5 retrieval: prepend this instruction to the QUERY only.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# Single-cell technology markers — used only to annotate results so we can see
# semantic recall at a glance (does an sc study surface without saying "single cell"?).
SC_TECH = re.compile(
    r"10x|chromium|drop-?seq|smart-?seq|split-?seq|cel-?seq|indrop|"
    r"single[- ]cell|scrna|snrna|sci-?seq|microwell|10X Genomics",
    re.I,
)


def _load_light_docs(docs_path: Path) -> dict[str, dict]:
    """gse → small display record (title/type/n_samples/organisms + sc hint)."""
    out: dict[str, dict] = {}
    with docs_path.open() as fh:
        for line in fh:
            r = json.loads(line)
            blob = f"{r['title']} {r['type']} {r['overall_design']} {r['summary']}"
            out[r["gse"]] = {
                "title": r["title"],
                "type": r["type"],
                "n_samples": r["n_samples"],
                "organisms": r["organisms"],
                "sc_hint": bool(SC_TECH.search(blob)),
            }
    return out


def search(query: str, topk: int, prefix: Path, docs_path: Path) -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    emb = np.load(prefix.with_suffix(".npy"))
    meta = json.loads(prefix.with_suffix(".ids.json").read_text())
    ids, model_name = meta["ids"], meta["meta"]["model"]
    print(f"loaded {emb.shape[0]:,} vectors (dim {emb.shape[1]}, model {model_name})",
          file=sys.stderr)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(model_name, device=device)
    q = model.encode(
        [BGE_QUERY_INSTRUCTION + query], normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)[0]

    scores = emb @ q  # cosine (all normalized)
    top = np.argpartition(-scores, topk)[:topk]
    top = top[np.argsort(-scores[top])]

    docs = _load_light_docs(docs_path)
    print(f'\nTop {topk} for: "{query}"\n' + "=" * 72)
    sc_count = 0
    for rank, i in enumerate(top, 1):
        gse = ids[i]
        d = docs.get(gse, {})
        sc = "🧬sc" if d.get("sc_hint") else "   "
        if d.get("sc_hint"):
            sc_count += 1
        title = (d.get("title") or "")[:66]
        print(
            f"{rank:2}. {scores[i]:.3f} {sc} {gse:11} "
            f"n={d.get('n_samples', '?'):<6} {d.get('type', '')[:34]:34} {title}"
        )
    print("=" * 72)
    print(f"{sc_count}/{topk} results carry a single-cell technology marker.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="In-memory semantic search test.")
    p.add_argument("query")
    p.add_argument("--topk", type=int, default=15)
    p.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    p.add_argument("--docs", type=Path, default=DEFAULT_DOCS, dest="docs_path")
    a = p.parse_args(argv)
    search(a.query, a.topk, a.prefix, a.docs_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Embed the series documents with a local open model (v1 test baseline).

Reads ``data/processed/geo_series.jsonl`` (from ``geo-build-series-docs``) and
encodes each series' ``embed_text`` with a sentence-transformers model — default
``BAAI/bge-small-en-v1.5`` (384-dim), running locally on MPS/CPU so there's no
API key or per-token cost. Writes a float32 matrix + aligned id list.

Whole-document embedding, one vector per series (v1 — see
``wiki/28-Embedding-Granularity.md``). The model choice is a *test* baseline;
swap ``--model`` and re-run to A/B against MedCPT / OpenAI once we have an eval.

Usage:
    uv run geo-embed
    uv run geo-embed --limit 20000 --out-prefix data/processed/embed_slice
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

DEFAULT_INPUT = Path("data/processed/geo_series.jsonl")
DEFAULT_PREFIX = Path("data/processed/embeddings")
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def build(
    *,
    input_path: Path = DEFAULT_INPUT,
    out_prefix: Path = DEFAULT_PREFIX,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 64,
    limit: int | None = None,
) -> int:
    import torch
    from sentence_transformers import SentenceTransformer

    ids: list[str] = []
    texts: list[str] = []
    with input_path.open() as fh:
        for line in fh:
            rec = json.loads(line)
            ids.append(rec["gse"])
            texts.append(rec["embed_text"])
            if limit and len(ids) >= limit:
                break

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"embedding {len(texts):,} docs with {model_name} on {device}", flush=True)
    model = SentenceTransformer(model_name, device=device)

    t0 = time.time()
    emb = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,  # cosine == dot product downstream
        show_progress_bar=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    print(f"encoded in {time.time()-t0:.0f}s → shape {emb.shape}", flush=True)

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_prefix.with_suffix(".npy"), emb)
    meta = {"model": model_name, "dim": int(emb.shape[1]), "count": len(ids)}
    out_prefix.with_suffix(".ids.json").write_text(json.dumps({"meta": meta, "ids": ids}))
    print(
        f"saved {out_prefix.with_suffix('.npy')} + .ids.json "
        f"({emb.nbytes/1e6:.0f}MB)",
        flush=True,
    )
    return len(ids)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Embed series docs with a local model.")
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT, dest="input_path")
    p.add_argument("--out-prefix", type=Path, default=DEFAULT_PREFIX)
    p.add_argument("--model", default=DEFAULT_MODEL, dest="model_name")
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args(argv)
    build(
        input_path=a.input_path,
        out_prefix=a.out_prefix,
        model_name=a.model_name,
        batch_size=a.batch_size,
        limit=a.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

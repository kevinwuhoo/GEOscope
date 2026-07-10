"""Postgres hybrid retrieval: pg_search (BM25) + pgvector (dense) fused with RRF.

The production retrieval path (v2), replacing the in-memory numpy test harness.
Runs against a ParadeDB container (Postgres 18 + pg_search + pgvector):

    docker run -d --name geo-paradedb \
      -e POSTGRES_PASSWORD=geo -e POSTGRES_USER=postgres -e POSTGRES_DB=geo \
      -p 5433:5432 -v geo_pg:/var/lib/postgresql paradedb/paradedb:latest

Usage:
    uv run python -m geo_index.pg_hybrid init
    uv run python -m geo_index.pg_hybrid load
    uv run python -m geo_index.pg_hybrid search "drug that suppresses mTOR signaling"
    uv run python -m geo_index.pg_hybrid search "spatial transcriptomics" --mode bm25
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from .facets import build_filter_clause
from .search_models import SearchFilters

DSN = os.environ.get("GEO_PG_DSN", "postgresql://postgres:geo@localhost:5433/geo")
ROOT = Path(__file__).resolve().parents[2]
PREFIX = ROOT / "data/processed/embeddings"
DOCS = ROOT / "data/processed/geo_series.jsonl"
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

# The text BM25 indexes — must match what the dense model embedded, so the two
# retrievers see the same document surface (parity with the in-memory eval).
SEARCH_FIELDS = ("title", "summary", "overall_design", "characteristics", "source_names", "type")


def _connect():
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(DSN)
    register_vector(conn)
    return conn


def _search_text(rec: dict) -> str:
    return " ".join(str(rec.get(k) or "") for k in SEARCH_FIELDS)


def init() -> int:
    """Create extensions, table, and indexes (drops any existing table)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_search;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("DROP TABLE IF EXISTS series CASCADE;")
        cur.execute(
            f"""
            CREATE TABLE series (
                id             BIGINT PRIMARY KEY,
                gse            TEXT UNIQUE NOT NULL,
                title          TEXT,
                summary        TEXT,
                overall_design TEXT,
                type           TEXT,
                characteristics TEXT,
                source_names   TEXT,
                organisms      TEXT,
                n_samples      INT,
                pubmed_id      BIGINT,
                search_text    TEXT,
                embedding      vector({DIM})
            );
            """
        )
        conn.commit()
    print("created table `series`", flush=True)
    return 0


def load(limit: int | None = None, batch: int = 5000) -> int:
    """Stream geo_series.jsonl + embeddings.npy into the table via COPY."""
    emb = np.load(PREFIX.with_suffix(".npy"), mmap_mode="r")
    ids = json.loads(PREFIX.with_suffix(".ids.json").read_text())["ids"]
    if emb.shape[1] != DIM:
        raise SystemExit(f"embedding dim {emb.shape[1]} != expected {DIM}")

    with _connect() as conn, conn.cursor() as cur:
        t0 = time.time()
        n = 0
        copy_sql = (
            "COPY series (id, gse, title, summary, overall_design, type, "
            "characteristics, source_names, organisms, n_samples, pubmed_id, "
            "search_text, embedding) FROM STDIN"
        )
        with DOCS.open() as fh, cur.copy(copy_sql) as cp:
            for i, line in enumerate(fh):
                r = json.loads(line)
                gse = r["gse"]
                if gse != ids[i]:
                    raise SystemExit(f"row {i}: jsonl gse {gse} != npy id {ids[i]}")
                vec = "[" + ",".join(f"{x:.6f}" for x in emb[i]) + "]"
                cp.write_row(
                    (
                        int(gse[3:]),
                        gse,
                        r.get("title"),
                        r.get("summary"),
                        r.get("overall_design"),
                        r.get("type"),
                        r.get("characteristics"),
                        r.get("source_names"),
                        ", ".join(r.get("organisms") or []),
                        r.get("n_samples"),
                        r.get("pubmed_id"),
                        _search_text(r),
                        vec,
                    )
                )
                n += 1
                if n % 20000 == 0:
                    print(f"  copied {n:,} rows ({time.time()-t0:.0f}s)", flush=True)
                if limit and n >= limit:
                    break
        conn.commit()
        print(f"loaded {n:,} rows in {time.time()-t0:.0f}s", flush=True)
    return n


def build_indexes() -> int:
    """Create the BM25 (pg_search) and HNSW (pgvector) indexes."""
    with _connect() as conn, conn.cursor() as cur:
        t0 = time.time()
        print("building BM25 index (pg_search)...", flush=True)
        cur.execute("DROP INDEX IF EXISTS series_bm25;")
        cur.execute(
            "CREATE INDEX series_bm25 ON series USING bm25 (id, search_text) "
            "WITH (key_field='id');"
        )
        conn.commit()
        print(f"  BM25 done ({time.time()-t0:.0f}s)", flush=True)
        t0 = time.time()
        print("building HNSW index (pgvector, cosine)...", flush=True)
        # Container /dev/shm is small (64MB); avoid the parallel-worker DSM
        # segment and keep the build in local memory instead.
        cur.execute("SET max_parallel_maintenance_workers = 0;")
        cur.execute("SET maintenance_work_mem = '1GB';")
        cur.execute("DROP INDEX IF EXISTS series_hnsw;")
        cur.execute(
            "CREATE INDEX series_hnsw ON series USING hnsw (embedding vector_cosine_ops);"
        )
        conn.commit()
        print(f"  HNSW done ({time.time()-t0:.0f}s)", flush=True)
    return 0


def load_model():
    """Load the query-embedding model once (reuse across many searches)."""
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return SentenceTransformer(EMBED_MODEL, device=device)


def embed_query(model, text: str) -> np.ndarray:
    return model.encode(
        [BGE_QUERY_INSTRUCTION + text], normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)[0]


def _search_statement(mode: str, predicate: str) -> str:
    """Return retrieval SQL with a prevalidated filter predicate in each branch."""

    if mode == "bm25":
        return f"""
            SELECT s.gse, s.title, s.type, paradedb.score(s.id) AS score,
                   NULL::bigint AS bm25_rank, NULL::bigint AS dense_rank
            FROM series AS s
            WHERE s.search_text @@@ %(q)s AND ({predicate})
            ORDER BY score DESC
            LIMIT %(topk)s
        """
    if mode == "dense":
        return f"""
            SELECT s.gse, s.title, s.type,
                   1 - (s.embedding <=> %(qv)s) AS score,
                   NULL::bigint AS bm25_rank, NULL::bigint AS dense_rank
            FROM series AS s
            WHERE {predicate}
            ORDER BY s.embedding <=> %(qv)s
            LIMIT %(topk)s
        """
    if mode == "hybrid":
        return f"""
            WITH bm25 AS (
                SELECT s.id,
                       RANK() OVER (ORDER BY paradedb.score(s.id) DESC) AS rank
                FROM series AS s
                WHERE s.search_text @@@ %(q)s AND ({predicate})
                ORDER BY paradedb.score(s.id) DESC
                LIMIT %(deep)s
            ),
            dense AS (
                SELECT s.id,
                       RANK() OVER (ORDER BY s.embedding <=> %(qv)s) AS rank
                FROM series AS s
                WHERE {predicate}
                ORDER BY s.embedding <=> %(qv)s
                LIMIT %(deep)s
            ),
            fused AS (
                SELECT COALESCE(b.id, d.id) AS id,
                       COALESCE(1.0 / (%(k0)s + b.rank), 0) +
                       COALESCE(1.0 / (%(k0)s + d.rank), 0) AS rrf,
                       b.rank AS bm25_rank,
                       d.rank AS dense_rank
                FROM bm25 AS b
                FULL OUTER JOIN dense AS d USING (id)
            )
            SELECT s.gse, s.title, s.type, f.rrf AS score,
                   f.bm25_rank, f.dense_rank
            FROM fused AS f
            JOIN series AS s USING (id)
            ORDER BY f.rrf DESC
            LIMIT %(topk)s
        """
    raise ValueError(f"unsupported search mode: {mode}")


def search_rows(
    conn,
    query: str,
    *,
    model=None,
    qv: np.ndarray | None = None,
    topk: int = 15,
    deep: int = 200,
    mode: str = "hybrid",
    k0: int = 60,
    filters: SearchFilters | None = None,
) -> list[dict]:
    """Run a search and return result rows as dicts. mode: hybrid | bm25 | dense.

    Pass a preloaded ``model`` (or a precomputed ``qv``) to avoid reloading the
    embedding model per call — the web server does this.
    """
    if mode not in {"bm25", "dense", "hybrid"}:
        raise ValueError(f"unsupported search mode: {mode}")
    if topk < 1 or deep < topk or k0 < 1:
        raise ValueError("require topk >= 1, deep >= topk, and k0 >= 1")
    active_filters = filters or SearchFilters()
    if mode != "bm25" and qv is None:
        if model is None:
            model = load_model()
        qv = embed_query(model, query)
    predicate, filter_params = build_filter_clause(active_filters, alias="s")
    params: dict[str, object] = {
        "q": query,
        "qv": qv,
        "topk": topk,
        "deep": deep,
        "k0": k0,
        **filter_params,
    }
    with conn.cursor() as cur:
        if mode in {"dense", "hybrid"}:
            cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        cur.execute(_search_statement(mode, predicate), params)
        return [
            {
                "gse": gse,
                "title": title,
                "type": study_type,
                "score": float(score) if score is not None else None,
                "bm25_rank": int(bm25_rank) if bm25_rank is not None else None,
                "dense_rank": int(dense_rank) if dense_rank is not None else None,
            }
            for gse, title, study_type, score, bm25_rank, dense_rank in cur.fetchall()
        ]


def search(query: str, topk: int = 15, deep: int = 200, mode: str = "hybrid", k0: int = 60) -> int:
    with _connect() as conn:
        rows = search_rows(conn, query, topk=topk, deep=deep, mode=mode, k0=k0)
    print(f'\n[{mode}] Top {len(rows)} for: "{query}"\n' + "=" * 78)
    for rank, r in enumerate(rows, 1):
        extra = f" (bm25#{r['bm25_rank'] or '-'}, dense#{r['dense_rank'] or '-'})" if mode == "hybrid" else ""
        print(f"{rank:2}. {r['score']:.4f} {r['gse']:11} {(r['title'] or '')[:70]}{extra}")
    print("=" * 78)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Postgres hybrid retrieval (pg_search + pgvector).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    lp = sub.add_parser("load")
    lp.add_argument("--limit", type=int, default=None)
    sub.add_parser("index")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--topk", type=int, default=15)
    sp.add_argument("--deep", type=int, default=200)
    sp.add_argument("--mode", choices=["hybrid", "bm25", "dense"], default="hybrid")
    a = p.parse_args(argv)

    if a.cmd == "init":
        return init()
    if a.cmd == "load":
        return load(limit=a.limit)
    if a.cmd == "index":
        return build_indexes()
    if a.cmd == "search":
        return search(a.query, topk=a.topk, deep=a.deep, mode=a.mode)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

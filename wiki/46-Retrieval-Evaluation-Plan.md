---
title: Retrieval Evaluation Plan
tags: [retrieval, evaluation, metrics, qrels, plan, v1]
status: implementation-plan
created: 2026-07-10
---

# 46 · Retrieval Evaluation Implementation Plan

← [[Home]] · operationalizes [[25-Embeddings-and-Cost#Eval]] · uses filtered
retrieval from [[45-Normalized-Filters-and-Facets-Plan]]

> **Follow-on:** land the single-BGE baseline and reviewed qrels here first.
> [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]] then reuses the same
> harness, re-pools across BGE/MedCPT/Qwen, and compares seven fixed systems.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small, repeatable evaluation that tells us whether BM25, dense,
or hybrid search is actually better for this prototype and exposes regressions
before model or query changes are adopted.

**Architecture:** Keep judgments as human-readable JSONL and metrics as pure
Python. Pool the union of results from the three existing retrieval modes, review
that bounded pool on a 0/1/2 scale, then refuse to score a run whose top results
are unjudged. The live database is needed to pool/run retrieval, but loaders,
validation, metrics, and orchestration tests stay offline.

**Tech Stack:** Python 3.11+, stdlib `argparse`, `dataclasses`, `hashlib`, `json`,
and `math`; the existing Postgres retrieval path; pytest. No new ML or evaluation
framework dependency.

## Global Constraints

- Use exactly 16 versioned seed queries and three fixed slices.
- Pool BM25, dense, and hybrid to depth 20.
- Accept only integer relevance grades 0, 1, and 2.
- Do not score unjudged top-20 results as irrelevant.
- Human review is required before a judgment is committed.
- Report Recall@20, NDCG@10, and MRR@20 overall and per slice.
- Reuse one embedding per query across dense and hybrid.
- Do not train or add a ranking/regression model.

---

## Scope decisions

- This is a **prototype harness**, not a general benchmarking platform.
- Start with 16 fixed queries: 10 conceptual, 3 normalized-filter, and 3 exact
  accession probes.
- Pool depth is 20 per BM25/dense/hybrid mode, at most 60 unique candidates per
  query before overlap.
- Human-reviewed judgments are authoritative. An LLM or subagent may propose a
  grade and evidence, but a person accepts/changes it before it enters committed
  qrels.
- Report Recall@20, NDCG@10, and MRR@20 overall and by slice.
- “Recall” means recall over the judged pool; this design cannot establish
  absolute recall over all 222,961 series.
- A measured baseline is success. Do not encode “hybrid must win” as a test.

## Known behavior this evaluation should expose

The database contains `GSE1`, `GSE2`, and `GSE4`, but the current BM25
`search_text` does not include the `gse` accession. The exact-accession slice may
therefore score poorly. Record that baseline rather than silently removing the
cases; it tells us whether accession routing belongs in the search service or
whether callers must use `get_dataset`.

The three filtered cases require Track 2. Metrics, file validation, conceptual
cases, exact probes, and fake-retriever tests can land first. The human
`assay_labels=["scRNA-seq"]` case also requires Track 1's assay refresh.

## File structure

| Path | Responsibility |
|---|---|
| `src/geo_index/retrieval_eval.py` | JSONL loaders, pooling, validation, metrics, CLI |
| `tests/test_retrieval_eval.py` | Pure metric, validation, pooling, and fake-runner tests |
| `eval/retrieval_queries.jsonl` | Versioned query cases and intent notes |
| `eval/retrieval_qrels.jsonl` | Versioned human judgments after pool review |
| `eval/README.md` | Review rubric and reproducible commands |
| `eval/retrieval_pool.jsonl` | Generated review queue; not committed |
| `eval/results/` | Generated run reports; not committed by default |
| `.gitignore` | Generated pool/report exclusions |
| `pyproject.toml` | `geo-eval-retrieval` entry point |

## Versioned file formats

One query per JSONL line:

```json
{"query_id":"concept_individual_cells","query":"transcriptomes of individual cells","slice":"conceptual","filters":{},"intent":"single-cell transcriptome studies despite vocabulary differences"}
```

One judgment per JSONL line:

```json
{"query_id":"concept_individual_cells","gse":"GSE12345","relevance":2,"note":"Single-cell transcriptome profiling is the study's main objective"}
```

Generated pool rows include the query ID, GSE, title, study type, sample count,
and a short summary/design excerpt. They do **not** reveal which mode retrieved
the candidate or at what rank, making review reasonably blind.

Relevance rubric:

| Grade | Meaning |
|---:|---|
| 0 | Irrelevant, an incidental term match, or the wrong scientific intent |
| 1 | Relevant but partial, indirect, or a secondary part of the study |
| 2 | Directly and centrally relevant to the query intent |

For filtered cases, grade scientific relevance only after the program has
verified the normalized filter. A female-filter case means the GSE contains at
least one female sample; it does not imply sample-level co-occurrence with every
other study attribute.

### Task 1: Add seed queries and strict loaders

**Files:**
- Create: `eval/retrieval_queries.jsonl`
- Create: `eval/README.md`
- Create: `src/geo_index/retrieval_eval.py`
- Create: `tests/test_retrieval_eval.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `QueryCase`, `Judgment`, `load_queries(path)`, `load_qrels(path)`.

- [ ] **Step 1: Commit these 16 query cases**

Use the exact IDs, text, slices, filters, and intents below:

| Query ID | Query | Slice | Filters | Intent |
|---|---|---|---|---|
| `concept_individual_cells` | `transcriptomes of individual cells` | conceptual | none | single-cell transcriptomes despite missing “single cell” wording |
| `concept_spatial_expression` | `spatial location of gene expression in tissue sections` | conceptual | none | spatial-transcriptomics studies across technology names |
| `concept_mtor_drug` | `drug that suppresses mTOR signaling` | conceptual | none | perturbation studies involving pharmacologic mTOR inhibition |
| `concept_crispr_t_cells` | `CRISPR screen in T cells` | conceptual | none | pooled or arrayed CRISPR screens in T-cell systems |
| `concept_single_cell_atac` | `chromatin accessibility in individual cells` | conceptual | none | single-cell ATAC/accessibility studies |
| `concept_ribosome_stress` | `ribosome profiling during cellular stress` | conceptual | none | Ribo-seq/ribosome-footprinting stress studies |
| `concept_airway_virus` | `airway epithelium viral infection` | conceptual | none | airway epithelial response to viral infection |
| `concept_tumor_deconvolution` | `reference datasets for tumor deconvolution` | conceptual | none | reference expression datasets useful for tumor mixture deconvolution |
| `concept_rare_fibroblast` | `rare disease fibroblast transcriptomes` | conceptual | none | fibroblast transcriptomes from rare genetic disease |
| `concept_nonmodel_toxicogenomics` | `non-model organism toxicogenomics` | conceptual | none | toxicant-response transcriptomics outside standard model organisms |
| `filtered_mouse_spatial` | `spatial transcriptomics brain` | filtered | `organism_ids=[NCBITaxon:10090]` | mouse brain spatial-expression studies |
| `filtered_human_scrna` | `single-cell RNA studies` | filtered | `organism_ids=[NCBITaxon:9606]`, `assay_labels=[scRNA-seq]` | human GSEs normalized as scRNA-seq |
| `filtered_female_liver` | `liver gene expression` | filtered | `sex_ids=[PATO:0000383]` | liver-expression GSEs containing female samples |
| `exact_gse1` | `GSE1` | exact | none | return GSE1 at rank 1 |
| `exact_gse2` | `GSE2` | exact | none | return GSE2 at rank 1 |
| `exact_gse4` | `GSE4` | exact | none | return GSE4 at rank 1 |

Write them as these exact JSONL records:

```jsonl
{"query_id":"concept_individual_cells","query":"transcriptomes of individual cells","slice":"conceptual","filters":{},"intent":"single-cell transcriptome studies despite vocabulary differences"}
{"query_id":"concept_spatial_expression","query":"spatial location of gene expression in tissue sections","slice":"conceptual","filters":{},"intent":"spatial-transcriptomics studies across technology names"}
{"query_id":"concept_mtor_drug","query":"drug that suppresses mTOR signaling","slice":"conceptual","filters":{},"intent":"perturbation studies involving pharmacologic mTOR inhibition"}
{"query_id":"concept_crispr_t_cells","query":"CRISPR screen in T cells","slice":"conceptual","filters":{},"intent":"pooled or arrayed CRISPR screens in T-cell systems"}
{"query_id":"concept_single_cell_atac","query":"chromatin accessibility in individual cells","slice":"conceptual","filters":{},"intent":"single-cell ATAC or accessibility studies"}
{"query_id":"concept_ribosome_stress","query":"ribosome profiling during cellular stress","slice":"conceptual","filters":{},"intent":"Ribo-seq or ribosome-footprinting stress studies"}
{"query_id":"concept_airway_virus","query":"airway epithelium viral infection","slice":"conceptual","filters":{},"intent":"airway epithelial response to viral infection"}
{"query_id":"concept_tumor_deconvolution","query":"reference datasets for tumor deconvolution","slice":"conceptual","filters":{},"intent":"reference expression datasets useful for tumor mixture deconvolution"}
{"query_id":"concept_rare_fibroblast","query":"rare disease fibroblast transcriptomes","slice":"conceptual","filters":{},"intent":"fibroblast transcriptomes from rare genetic disease"}
{"query_id":"concept_nonmodel_toxicogenomics","query":"non-model organism toxicogenomics","slice":"conceptual","filters":{},"intent":"toxicant-response transcriptomics outside standard model organisms"}
{"query_id":"filtered_mouse_spatial","query":"spatial transcriptomics brain","slice":"filtered","filters":{"organism_ids":["NCBITaxon:10090"]},"intent":"mouse brain spatial-expression studies"}
{"query_id":"filtered_human_scrna","query":"single-cell RNA studies","slice":"filtered","filters":{"organism_ids":["NCBITaxon:9606"],"assay_labels":["scRNA-seq"]},"intent":"human GSEs normalized as scRNA-seq"}
{"query_id":"filtered_female_liver","query":"liver gene expression","slice":"filtered","filters":{"sex_ids":["PATO:0000383"]},"intent":"liver-expression GSEs containing female samples"}
{"query_id":"exact_gse1","query":"GSE1","slice":"exact","filters":{},"intent":"return GSE1 at rank 1"}
{"query_id":"exact_gse2","query":"GSE2","slice":"exact","filters":{},"intent":"return GSE2 at rank 1"}
{"query_id":"exact_gse4","query":"GSE4","slice":"exact","filters":{},"intent":"return GSE4 at rank 1"}
```

- [ ] **Step 2: Write failing loader/validation tests**

Tests must reject:

- duplicate query IDs;
- an unknown slice;
- unknown normalized filter fields;
- blank query text or intent;
- malformed GSE accessions;
- duplicate `(query_id, gse)` judgments;
- relevance outside `0, 1, 2`;
- qrels that reference an unknown query.

- [ ] **Step 3: Implement immutable records and JSONL loaders**

Use:

```python
@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    slice: Literal["conceptual", "filtered", "exact"]
    filters: SearchFilters
    intent: str


@dataclass(frozen=True)
class Judgment:
    query_id: str
    gse: str
    relevance: int
    note: str
```

Normalize GSEs to uppercase, but reject rather than auto-correct malformed query
IDs, filter fields, or grades.

Implement the loaders with these concrete validation helpers:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .search_models import SearchFilters


Slice = Literal["conceptual", "filtered", "exact"]
SLICES = {"conceptual", "filtered", "exact"}
GSE_RE = re.compile(r"^GSE[1-9][0-9]*$")


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    slice: Slice
    filters: SearchFilters
    intent: str


@dataclass(frozen=True)
class Judgment:
    query_id: str
    gse: str
    relevance: int
    note: str


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    return rows


def load_queries(path: Path) -> list[QueryCase]:
    cases: list[QueryCase] = []
    seen: set[str] = set()
    for row in _jsonl(path):
        query_id = str(row.get("query_id", ""))
        query = str(row.get("query", "")).strip()
        slice_name = str(row.get("slice", ""))
        intent = str(row.get("intent", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]+", query_id):
            raise ValueError(f"invalid query_id: {query_id}")
        if query_id in seen:
            raise ValueError(f"duplicate query_id: {query_id}")
        if not query or not intent:
            raise ValueError(f"blank query or intent: {query_id}")
        if slice_name not in SLICES:
            raise ValueError(f"unknown slice: {slice_name}")
        seen.add(query_id)
        cases.append(
            QueryCase(
                query_id=query_id,
                query=query,
                slice=slice_name,
                filters=SearchFilters.from_mapping(row.get("filters", {})),
                intent=intent,
            )
        )
    return cases


def load_qrels(path: Path, valid_query_ids: set[str]) -> list[Judgment]:
    judgments: list[Judgment] = []
    seen: set[tuple[str, str]] = set()
    for row in _jsonl(path):
        query_id = str(row.get("query_id", ""))
        gse = str(row.get("gse", "")).strip().upper()
        relevance = row.get("relevance")
        note = str(row.get("note", "")).strip()
        key = (query_id, gse)
        if query_id not in valid_query_ids:
            raise ValueError(f"qrel references unknown query: {query_id}")
        if not GSE_RE.fullmatch(gse):
            raise ValueError(f"malformed GSE: {gse}")
        if key in seen:
            raise ValueError(f"duplicate qrel: {query_id}/{gse}")
        if type(relevance) is not int or relevance not in {0, 1, 2}:
            raise ValueError(f"invalid relevance for {query_id}/{gse}")
        if not note:
            raise ValueError(f"blank judgment note for {query_id}/{gse}")
        seen.add(key)
        judgments.append(Judgment(query_id, gse, relevance, note))
    return judgments
```

Register the script now so every later task's documented command exists:

```toml
[project.scripts]
geo-eval-retrieval = "geo_index.retrieval_eval:main"
```

- [ ] **Step 4: Document the rubric and run loader tests**

```bash
uv run pytest tests/test_retrieval_eval.py -k "load or validate" -v
```

- [ ] **Step 5: Commit queries and loaders**

```bash
git add eval/retrieval_queries.jsonl eval/README.md pyproject.toml src/geo_index/retrieval_eval.py tests/test_retrieval_eval.py
git commit -m "feat: define retrieval evaluation cases"
```

### Task 2: Implement metrics as pure functions

**Files:**
- Modify: `src/geo_index/retrieval_eval.py`
- Modify: `tests/test_retrieval_eval.py`

**Interfaces:**
- Produces: `recall_at_k(ranked, qrels, k=20) -> float`.
- Produces: `ndcg_at_k(ranked, qrels, k=10) -> float`.
- Produces: `reciprocal_rank(ranked, qrels, k=20) -> float`.
- Produces: `evaluate_rankings(cases: list[QueryCase],
  rankings: dict[str, list[str]], judgments: list[Judgment]) -> dict[str, object]`.

- [ ] **Step 1: Write exact metric fixtures**

For ranking `[C, B, A]` with relevance `A=2`, `B=1`, `C=0`, assert:

- Recall@2 is `0.5` when grades 1 and 2 are relevant.
- NDCG@3 is approximately `0.586883` with gains `2**grade - 1`.
- MRR@3 is `0.5` because the first positive is at rank 2.

Also test no positives, a perfect ranking, duplicate ranked accessions, and a
ranking shorter than `k`. Validation—not silent coercion—handles undefined cases.

- [ ] **Step 2: Implement and run metric tests**

Add these pure functions to `retrieval_eval.py`:

```python
import math
from collections.abc import Sequence


def _validate_ranking(ranked: Sequence[str]) -> None:
    if len(set(ranked)) != len(ranked):
        raise ValueError("ranking contains duplicate accessions")


def recall_at_k(
    ranked: Sequence[str], qrels: dict[str, int], k: int = 20
) -> float:
    _validate_ranking(ranked)
    relevant = {gse for gse, grade in qrels.items() if grade >= 1}
    if not relevant:
        raise ValueError("recall is undefined without a positive judgment")
    hits = sum(gse in relevant for gse in ranked[:k])
    return hits / len(relevant)


def ndcg_at_k(
    ranked: Sequence[str], qrels: dict[str, int], k: int = 10
) -> float:
    _validate_ranking(ranked)

    def dcg(grades: Sequence[int]) -> float:
        return sum(
            ((2**grade) - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(grades, 1)
        )

    actual = [qrels.get(gse, 0) for gse in ranked[:k]]
    ideal = sorted(qrels.values(), reverse=True)[:k]
    ideal_score = dcg(ideal)
    if ideal_score == 0:
        raise ValueError("NDCG is undefined without a positive judgment")
    return dcg(actual) / ideal_score


def reciprocal_rank(
    ranked: Sequence[str], qrels: dict[str, int], k: int = 20
) -> float:
    _validate_ranking(ranked)
    if not any(grade >= 1 for grade in qrels.values()):
        raise ValueError("MRR is undefined without a positive judgment")
    for rank, gse in enumerate(ranked[:k], 1):
        if qrels.get(gse, 0) >= 1:
            return 1.0 / rank
    return 0.0


def metrics_for_ranking(
    ranked: Sequence[str], qrels: dict[str, int]
) -> dict[str, float]:
    return {
        "recall_at_20": recall_at_k(ranked, qrels, 20),
        "ndcg_at_10": ndcg_at_k(ranked, qrels, 10),
        "mrr_at_20": reciprocal_rank(ranked, qrels, 20),
    }
```

```bash
uv run pytest tests/test_retrieval_eval.py -k "recall or ndcg or reciprocal or metric" -v
```

- [ ] **Step 3: Add aggregate reporting**

Return per-query metrics, macro means overall, and macro means for
`conceptual`, `filtered`, and `exact`. Include the number of evaluated queries in
every aggregate so a missing slice cannot resemble a zero score.

```python
def macro_average(rows: list[dict[str, float]]) -> dict[str, float | int]:
    if not rows:
        return {"query_count": 0}
    names = ("recall_at_20", "ndcg_at_10", "mrr_at_20")
    return {
        "query_count": len(rows),
        **{
            name: sum(float(row[name]) for row in rows) / len(rows)
            for name in names
        },
    }


def evaluate_rankings(
    cases: list[QueryCase],
    rankings: dict[str, list[str]],
    judgments: list[Judgment],
) -> dict[str, object]:
    qrels: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        qrels.setdefault(judgment.query_id, {})[judgment.gse] = judgment.relevance
    per_query: dict[str, dict[str, float]] = {}
    by_slice: dict[str, list[dict[str, float]]] = {
        "conceptual": [],
        "filtered": [],
        "exact": [],
    }
    for case in cases:
        metrics = metrics_for_ranking(
            rankings[case.query_id], qrels.get(case.query_id, {})
        )
        per_query[case.query_id] = metrics
        by_slice[case.slice].append(metrics)
    return {
        "per_query": per_query,
        "overall": macro_average(list(per_query.values())),
        "by_slice": {
            slice_name: macro_average(rows)
            for slice_name, rows in by_slice.items()
        },
    }
```

- [ ] **Step 4: Commit metrics**

```bash
git add src/geo_index/retrieval_eval.py tests/test_retrieval_eval.py
git commit -m "feat: add retrieval evaluation metrics"
```

### Task 3: Pool candidates without leaking rank to reviewers

**Files:**
- Modify: `src/geo_index/retrieval_eval.py`
- Modify: `tests/test_retrieval_eval.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `pool_candidates(cases: list[QueryCase], retrieve: Retriever,
  embed: Embedder, *, judged_keys: set[tuple[str, str]] | None = None,
  modes: tuple[str, ...] = ("bm25", "dense", "hybrid"),
  depth: int = 20) -> list[Candidate]`.
- Produces CLI: `geo-eval-retrieval pool`.

- [ ] **Step 1: Test pooling with a fake retriever**

Assert that pooling:

- unions all three modes at depth 20;
- deduplicates by `(query_id, gse)`;
- produces stable query-ID/GSE ordering independent of retrieval completion order;
- calls the embedding hook only once per query and reuses the vector for dense
  and hybrid;
- keeps title, type, sample count, and a bounded excerpt;
- omits mode, rank, and score from the review file;
- marks already-judged candidates when an existing qrels file is supplied.

- [ ] **Step 2: Implement the live retrieval adapter**

Open one database connection for a pool run, load the embedding model once, embed
each query once, and pass its vector to dense and hybrid calls. Forward the exact
`SearchFilters` from each query case to Track 2's `search_rows()`.

For the exact slice, still run all three retrieval modes: the purpose is to
measure the current search behavior, not silently route around it in the harness.

Add the following records and pooling functions:

```python
from collections.abc import Callable


@dataclass(frozen=True)
class Candidate:
    query_id: str
    gse: str
    title: str
    study_type: str
    n_samples: int | None
    excerpt: str
    judged: bool


Retriever = Callable[[QueryCase, str, object | None, int], list[dict]]
Embedder = Callable[[str], object]


def _excerpt(row: dict, limit: int = 500) -> str:
    text = " ".join(
        str(row.get(key) or "") for key in ("summary", "overall_design")
    )
    return " ".join(text.split())[:limit]


def pool_candidates(
    cases: list[QueryCase],
    retrieve: Retriever,
    embed: Embedder,
    *,
    judged_keys: set[tuple[str, str]] | None = None,
    modes: tuple[str, ...] = ("bm25", "dense", "hybrid"),
    depth: int = 20,
) -> list[Candidate]:
    judged = judged_keys or set()
    pooled: list[Candidate] = []
    for case in cases:
        qv = embed(case.query)
        by_gse: dict[str, dict] = {}
        for mode in modes:
            rows = retrieve(case, mode, None if mode == "bm25" else qv, depth)
            for row in rows:
                gse = str(row["gse"]).upper()
                by_gse.setdefault(gse, row)
        for gse in sorted(by_gse, key=lambda value: int(value[3:])):
            row = by_gse[gse]
            pooled.append(
                Candidate(
                    query_id=case.query_id,
                    gse=gse,
                    title=str(row.get("title") or ""),
                    study_type=str(row.get("type") or ""),
                    n_samples=row.get("n_samples"),
                    excerpt=_excerpt(row),
                    judged=(case.query_id, gse) in judged,
                )
            )
    return pooled


def write_pool(path: Path, candidates: list[Candidate]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate.__dict__, sort_keys=True) + "\n")
```

Add a single-query hydration query so reviewers see useful context without
changing the production result schema:

```python
def _hydrate_eval_rows(conn, rows: list[dict]) -> list[dict]:
    gses = [str(row["gse"]) for row in rows]
    if not gses:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT gse, title, type, n_samples, summary, overall_design
            FROM series
            WHERE gse = ANY(%s::text[])
            """,
            (gses,),
        )
        details = {
            gse: {
                "gse": gse,
                "title": title,
                "type": study_type,
                "n_samples": n_samples,
                "summary": summary,
                "overall_design": overall_design,
            }
            for gse, title, study_type, n_samples, summary, overall_design
            in cur.fetchall()
        }
    return [details[gse] for gse in gses if gse in details]
```

The `pool` command opens one connection and constructs these closures:

```python
model = pg_hybrid.load_model()


def embed(text: str):
    return pg_hybrid.embed_query(model, text)


def retrieve(case: QueryCase, mode: str, qv, depth: int) -> list[dict]:
    rows = pg_hybrid.search_rows(
        conn,
        case.query,
        qv=qv,
        mode=mode,
        topk=depth,
        deep=max(200, depth),
        filters=case.filters,
    )
    return _hydrate_eval_rows(conn, rows)
```

- [ ] **Step 3: Add generated paths to `.gitignore`**

```gitignore
eval/retrieval_pool.jsonl
eval/results/
```

Keep the reviewed `eval/retrieval_qrels.jsonl` versioned.

- [ ] **Step 4: Add the pool command**

```bash
uv run geo-eval-retrieval pool \
  --queries eval/retrieval_queries.jsonl \
  --qrels eval/retrieval_qrels.jsonl \
  --output eval/retrieval_pool.jsonl \
  --depth 20
```

If qrels does not exist on the first run, treat it as empty. Never overwrite the
qrels file from the pooling command.

- [ ] **Step 5: Run tests and commit pooling**

```bash
uv run pytest tests/test_retrieval_eval.py -k pool -v
git add .gitignore src/geo_index/retrieval_eval.py tests/test_retrieval_eval.py
git commit -m "feat: pool retrieval evaluation candidates"
```

### Task 4: Review the pool and commit complete qrels

**Files:**
- Create: `eval/retrieval_qrels.jsonl`
- Modify: `eval/README.md`

- [ ] **Step 1: Generate the review pool**

Run the command from Task 3 after Tracks 1 and 2 are live. The maximum first-pass
pool is 960 rows (16 queries × 3 modes × 20), and overlap should make it smaller.

- [ ] **Step 2: Review every candidate**

For each row, read the title and excerpt, inspect the GEO page when evidence is
ambiguous, then add exactly one 0/1/2 judgment plus a short evidence note to
`eval/retrieval_qrels.jsonl`. Seed the matching GSE as grade 2 for each exact
query; grade every other pooled exact-query candidate 0.

An LLM can draft judgments in batches, but the reviewer must confirm them before
commit. Do not let a relevance model judge the same retrieval system it is meant
to evaluate without human review.

- [ ] **Step 3: Validate completeness and positive coverage**

```bash
uv run geo-eval-retrieval validate \
  --queries eval/retrieval_queries.jsonl \
  --pool eval/retrieval_pool.jsonl \
  --qrels eval/retrieval_qrels.jsonl
```

Validation fails if a pooled row is unjudged, a query has no positive judgment,
or any duplicate/schema error exists.

- [ ] **Step 4: Commit the reviewed set**

```bash
git add eval/retrieval_qrels.jsonl eval/README.md
git commit -m "data: add retrieval relevance judgments"
```

### Task 5: Run and record reproducible comparisons

**Files:**
- Modify: `src/geo_index/retrieval_eval.py`
- Modify: `tests/test_retrieval_eval.py`
- Modify: `wiki/42-Build-Log.md`

**Interfaces:**
- Produces CLI: `geo-eval-retrieval validate` and `geo-eval-retrieval run`.
- Consumes script registered in Task 1:
  `geo-eval-retrieval = "geo_index.retrieval_eval:main"`.

- [ ] **Step 1: Test run orchestration and judgment coverage**

Using a fake retriever, verify that `run`:

- evaluates BM25, dense, and hybrid with identical query/filter inputs;
- reuses one query vector for dense and hybrid;
- aborts scoring if any returned top-20 accession lacks a judgment;
- writes the unjudged cases to a separate review queue;
- includes per-query, per-slice, and overall metrics;
- never changes query or qrels inputs.

- [ ] **Step 2: Include reproducibility metadata**

Each report records run ID, UTC timestamp, Git commit, query-file SHA-256,
qrels-file SHA-256, embedding model, modes, `topk`, `deep`, and `k0`. This makes a
later model or rule change comparable without a training experiment.

Implement validation and the live runner with these functions:

```python
import hashlib
import subprocess
from datetime import datetime, timezone

from . import pg_hybrid


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def validate_complete(
    cases: list[QueryCase],
    pool_path: Path,
    judgments: list[Judgment],
) -> None:
    pool_keys = {
        (str(row["query_id"]), str(row["gse"]).upper())
        for row in _jsonl(pool_path)
    }
    judged_keys = {(row.query_id, row.gse) for row in judgments}
    missing = sorted(pool_keys - judged_keys)
    if missing:
        raise ValueError(f"{len(missing)} pooled candidates are unjudged")
    by_query: dict[str, list[int]] = {}
    for judgment in judgments:
        by_query.setdefault(judgment.query_id, []).append(judgment.relevance)
    no_positive = [
        case.query_id
        for case in cases
        if not any(grade >= 1 for grade in by_query.get(case.query_id, []))
    ]
    if no_positive:
        raise ValueError(f"queries without a positive judgment: {no_positive}")


def run_live_evaluation(
    cases: list[QueryCase],
    judgments: list[Judgment],
    *,
    modes: tuple[str, ...],
    topk: int,
    deep: int,
    k0: int,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    judged = {(row.query_id, row.gse) for row in judgments}
    needs_vectors = any(mode in {"dense", "hybrid"} for mode in modes)
    model = pg_hybrid.load_model() if needs_vectors else None
    vectors = {
        case.query_id: pg_hybrid.embed_query(model, case.query)
        for case in cases
    } if model is not None else {}
    rankings: dict[str, dict[str, list[str]]] = {
        mode: {} for mode in modes
    }
    unjudged: list[dict[str, str]] = []
    with pg_hybrid._connect() as conn:
        for case in cases:
            for mode in modes:
                rows = pg_hybrid.search_rows(
                    conn,
                    case.query,
                    qv=vectors.get(case.query_id),
                    mode=mode,
                    topk=topk,
                    deep=deep,
                    k0=k0,
                    filters=case.filters,
                )
                gses = [str(row["gse"]) for row in rows]
                rankings[mode][case.query_id] = gses
                unjudged.extend(
                    {"query_id": case.query_id, "gse": gse}
                    for gse in gses
                    if (case.query_id, gse) not in judged
                )
    if unjudged:
        unique = {
            (row["query_id"], row["gse"]): row for row in unjudged
        }
        return {}, [unique[key] for key in sorted(unique)]
    return {
        mode: evaluate_rankings(cases, rankings[mode], judgments)
        for mode in modes
    }, []


def write_unjudged(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
```

The `run` command calls `run_live_evaluation()`. If `unjudged` is nonempty, write
`<output-stem>.unjudged.jsonl` and exit nonzero without a metrics file. Otherwise
write:

```python
report = {
    "run_id": args.run_id,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "git_commit": _git_commit(),
    "queries_sha256": _sha256(args.queries),
    "qrels_sha256": _sha256(args.qrels),
    "embedding_model": pg_hybrid.EMBED_MODEL,
    "settings": {
        "modes": list(modes),
        "topk": args.topk,
        "deep": args.deep,
        "k0": args.k0,
    },
    "metrics": metrics,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
```

- [ ] **Step 3: Add the CLI dispatcher and run the complete suite**

Implement `main(argv: list[str] | None = None) -> int` with three required
subcommands and these exact arguments:

```python
import argparse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GEO retrieval evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    pool = sub.add_parser("pool")
    pool.add_argument("--queries", type=Path, required=True)
    pool.add_argument("--qrels", type=Path, required=True)
    pool.add_argument("--output", type=Path, required=True)
    pool.add_argument("--depth", type=int, default=20)

    validate = sub.add_parser("validate")
    validate.add_argument("--queries", type=Path, required=True)
    validate.add_argument("--pool", type=Path, required=True)
    validate.add_argument("--qrels", type=Path, required=True)

    run = sub.add_parser("run")
    run.add_argument("--queries", type=Path, required=True)
    run.add_argument("--qrels", type=Path, required=True)
    run.add_argument("--modes", default="bm25,dense,hybrid")
    run.add_argument("--topk", type=int, default=20)
    run.add_argument("--deep", type=int, default=200)
    run.add_argument("--k0", type=int, default=60)
    run.add_argument("--run-id", required=True)
    run.add_argument("--output", type=Path, required=True)
    return parser
```

Add this live pool wrapper and dispatcher:

```python
def pool_live(
    cases: list[QueryCase],
    judgments: list[Judgment],
    depth: int,
) -> list[Candidate]:
    model = pg_hybrid.load_model()
    with pg_hybrid._connect() as conn:
        def embed(text: str):
            return pg_hybrid.embed_query(model, text)

        def retrieve(
            case: QueryCase, mode: str, qv, requested_depth: int
        ) -> list[dict]:
            rows = pg_hybrid.search_rows(
                conn,
                case.query,
                qv=qv,
                mode=mode,
                topk=requested_depth,
                deep=max(200, requested_depth),
                filters=case.filters,
            )
            return _hydrate_eval_rows(conn, rows)

        return pool_candidates(
            cases,
            retrieve,
            embed,
            judged_keys={(row.query_id, row.gse) for row in judgments},
            depth=depth,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cases = load_queries(args.queries)
    query_ids = {case.query_id for case in cases}
    if args.command == "pool":
        if args.depth < 1:
            raise ValueError("pool depth must be positive")
        judgments = (
            load_qrels(args.qrels, query_ids) if args.qrels.exists() else []
        )
        candidates = pool_live(cases, judgments, args.depth)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_pool(args.output, candidates)
        return 0

    judgments = load_qrels(args.qrels, query_ids)
    if args.command == "validate":
        validate_complete(cases, args.pool, judgments)
        return 0

    modes = tuple(value.strip() for value in args.modes.split(",") if value.strip())
    allowed_modes = {"bm25", "dense", "hybrid"}
    if not modes or any(mode not in allowed_modes for mode in modes):
        raise ValueError(f"invalid modes: {modes}")
    if args.topk < 1 or args.deep < args.topk or args.k0 < 1:
        raise ValueError("require topk >= 1, deep >= topk, and k0 >= 1")
    metrics, unjudged = run_live_evaluation(
        cases,
        judgments,
        modes=modes,
        topk=args.topk,
        deep=args.deep,
        k0=args.k0,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if unjudged:
        unjudged_path = args.output.with_name(
            f"{args.output.stem}.unjudged.jsonl"
        )
        write_unjudged(unjudged_path, unjudged)
        return 2
    report = {
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "queries_sha256": _sha256(args.queries),
        "qrels_sha256": _sha256(args.qrels),
        "embedding_model": pg_hybrid.EMBED_MODEL,
        "settings": {
            "modes": list(modes),
            "topk": args.topk,
            "deep": args.deep,
            "k0": args.k0,
        },
        "metrics": metrics,
    }
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0
```

End the module with:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

```bash
uv run pytest tests/test_retrieval_eval.py -v
uv run pytest -v
```

- [ ] **Step 4: Produce the first measured baseline**

```bash
uv run geo-eval-retrieval run \
  --queries eval/retrieval_queries.jsonl \
  --qrels eval/retrieval_qrels.jsonl \
  --modes bm25,dense,hybrid \
  --topk 20 \
  --deep 200 \
  --run-id bge-small-v1 \
  --output eval/results/bge-small-v1.json
```

If new top-20 results are unjudged, review the emitted queue, append judgments,
validate again, and rerun. Never score unjudged results as zero merely to finish.

- [ ] **Step 5: Summarize decisions in the build log**

Record the three aggregate metrics by mode and slice, the query-level failures,
and any retrieval change justified by the evidence. In particular, record what
the exact slice says about accession routing.

- [ ] **Step 6: Commit the runner and baseline summary**

```bash
git add src/geo_index/retrieval_eval.py tests/test_retrieval_eval.py wiki/42-Build-Log.md
git commit -m "feat: run reproducible retrieval evaluation"
```

## Definition of done

- Sixteen fixed cases and their reviewed pooled judgments are versioned.
- Every scored top-20 result has an explicit 0/1/2 judgment.
- Recall@20, NDCG@10, and MRR@20 are tested with exact fixtures.
- Reports show overall and conceptual/filtered/exact slice results.
- Query vectors are reused across dense and hybrid modes.
- Results include enough hashes/settings to reproduce a comparison.
- The build log records the baseline without assuming which mode should win.

## Explicitly deferred

- Training a regression/ranking model.
- Large-scale assessor tooling or inter-annotator agreement analysis.
- Automatic LLM judgments without human confirmation.
- Absolute corpus recall claims.
- Cross-encoder reranking evaluation until the baseline is stable.

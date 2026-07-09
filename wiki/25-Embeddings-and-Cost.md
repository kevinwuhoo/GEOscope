---
title: Embeddings & Cost
tags: [embeddings, cost, eval, models]
---

# 25 · Embeddings & Cost

← [[Home]] · feeds [[26-Datastore-Postgres]], [[23-Search-and-Retrieval]]

## TL;DR

> Embedding **all of GEO once costs single-digit dollars** with a cheap managed model, and is essentially **free** on a self-hosted open model. Cost is *not* your constraint. Spend the effort on an **eval set** and pick the model on measured quality.

## The document we embed (series-level)

Concatenate into one string per GSE:
`title` + `summary` + `overall_design` + platform title(s) + **normalized values** (`organism`, `assay`, `tissue`, `disease` labels from [[22-Ontology-Normalization]]) + distinct sample `source_name`/`characteristics`.

Folding normalized labels into the text is deliberate — it injects clean vocabulary the embedder can latch onto. Estimated **~1,000 tokens/doc average** (summaries + design run long); some hit 2–3k.

> **One embedding per document, not one per field.** Categorical fields (organism, sex, assay…) are served by facets/filters, not embeddings; only the narrative bucket gets embedded, and one concatenated vector is the v1 default. The full reasoning + when to split into multi-vector is [[28-Embedding-Granularity]].

## Cost to embed the full corpus once

Assume **289k series × ~1,000 tokens ≈ 289M tokens** (call it 300–450M with headroom).

| Model | $/1M tokens | Dims | Est. one-time cost (≈289M tok) | Host |
|---|---|---|---|---|
| **OpenAI `text-embedding-3-small`** | $0.02 | 1536 (truncatable) | **~$6** (≤$9 with headroom) | API |
| OpenAI `text-embedding-3-large` | $0.13 | 3072 (trunc.) | ~$38 | API |
| Voyage `voyage-3-lite` | $0.02 | 512/1024 | ~$6 | API |
| Voyage `voyage-3-large` | ~$0.18 | 256–2048 | ~$52 | API |
| Cohere `embed-v4.0` | ~$0.12 | 256–1536 | ~$35 | API |
| **MedCPT** (NCBI) | $0 (self-host) | 768 | ~a few $ of GPU time | GPU |
| **BGE-M3 / Qwen3-Embedding** | $0 (self-host) | 1024 / up to 4096 | ~a few $ of GPU time | GPU |

Notes:
- **Re-embedding is cheap**, so experimenting is cheap — that's the whole argument for A/B'ing models instead of guessing.
- **Storage/index is trivial:** 289k × 1536 × 4B ≈ **1.7 GB** float32 (use pgvector `halfvec` → ~0.9 GB). MedCPT's 768-dim → ~0.9 GB. Nothing for Postgres.
- Sample-level someday (8.6M × ~150 tok ≈ 1.3B tok): ~$26 on `-3-small`. Still cheap; the cost there is *infra/complexity*, not tokens. → [[40-Roadmap]]

## Model candidates & when each wins

**Document retrieval (query → experiment summaries):**
- **MedCPT** (NCBI) — the domain-native default: Query + Article bi-encoders trained on **255M PubMed query→abstract click pairs**, same 768-dim space, plus a matching Cross-Encoder reranker. Literally built for "query vs. biomedical abstract". Caveat: 768-dim, ~512-token article cap → chunk long summaries.
- **Strong general models** — `text-embedding-3-small/large`, `voyage-3-large` (32k context = no chunking), open `Qwen3-Embedding`/`BGE-M3`/`NV-Embed-v2`. Often match or beat MedCPT on out-of-distribution queries thanks to scale + long context.

**Ontology-term / entity matching (for [[22-Ontology-Normalization]], NOT document search):**
- **SapBERT** / **BioLORD-2023** — tuned for short biomedical entity/synonym matching. Do **not** use these for document retrieval, and don't use document embedders for fine-grained ontology matching — opposite optimization targets.

## Recommended path

1. **Ship v1 on `text-embedding-3-small`** — cheapest, zero infra, instantly available, strong baseline. ~$6 to embed everything.
2. **Benchmark against MedCPT** (free, domain-native) and one open general model (`BGE-M3` or `Qwen3-Embedding`) on the eval set below. If MedCPT/open wins meaningfully, switch — the re-embed is a coffee break and a few dollars.
3. Keep the embedder behind an interface so swapping is one config change.

## Eval — the thing worth building

Small, honest, reusable. ~50–100 labeled queries with known-relevant GSEs.

- **Seed queries from the pain:** "single cell RNA" (must return 10x/Drop-seq/SPLiT-seq), "CRISPR screen in T cells", "spatial transcriptomics mouse brain", accession/gene-symbol exact hits, etc.
- **Metrics:** Recall@20, NDCG@10, MRR. For normalization: precision + coverage per field vs. a hand-labeled sample (MetaSRA-style; expect high precision, watch recall).
- **What it decides:** embedding model; hybrid fuse-vs-route ([[23-Search-and-Retrieval]]); whether reranking earns its latency; the normalization confidence threshold `τ` ([[22-Ontology-Normalization]]).
- Build it in week 1 and never argue about model choice again. → [[40-Roadmap]]

## Sources

- OpenAI text-embedding-3 (dims, pricing) — https://platform.openai.com/docs/models/text-embedding-3-large · https://openai.com/index/new-embedding-models-and-api-updates/
- Voyage voyage-3-large — https://blog.voyageai.com/2025/01/07/voyage-3-large/ · Cohere Embed v4 — https://docs.cohere.com/docs/cohere-embed
- MedCPT (Query / Article / Cross-Encoder) — https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842 · https://huggingface.co/ncbi/MedCPT-Query-Encoder
- Open models / MTEB (Qwen3-Embedding, BGE, NV-Embed) — https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/
- Entity-matching embedders (SapBERT / BioLORD, for normalization not doc search) — https://aclanthology.org/2021.naacl-main.334/ · https://huggingface.co/FremyCompany/BioLORD-2023
- text2term TF-IDF benchmark — https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353

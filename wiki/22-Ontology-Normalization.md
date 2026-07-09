---
title: Ontology Normalization
tags: [ontology, normalization, harmonization]
---

# 22 · Ontology Normalization

← [[Home]] · the heart of the project · pairs with [[24-Faceted-Search]]

> This is where `sex = M/F/0/1` becomes one thing, and where free-text tissue/disease/assay become facetable. It's **not** "just embeddings" — read [[#RAG vs. IDs|the framing]] below.

## The target: which ontology per field

The field→ontology mapping below is the **de-facto standard**, consistent across CELLxGENE, HCA, Sfaira, and MetaSRA (evidence in [[30-Prior-Art]]). Emulate CELLxGENE's schema — it's the strictest and best-documented.

| Field | Ontology | Prefix / IRI example | Notes |
|---|---|---|---|
| Organism | **NCBITaxon** | `NCBITaxon:9606` (human), `10090` (mouse) | Easiest; near-deterministic |
| Tissue / anatomy | **UBERON** | `UBERON:0002107` (liver) | Cross-species |
| Cell type | **Cell Ontology (CL)** | `CL:0000236` (B cell) | DAG (multiple parents) |
| Disease | **MONDO** | `MONDO:0007254` (breast cancer); `PATO:0000461` = "normal" | MONDO harmonizes DOID/OMIM/Orphanet/NCIT |
| Assay / method | **EFO** (⊇ OBI) | `EFO:...` (child of `EFO:0002772`/`EFO:0010183`) | Where 10x 3′/5′/Drop-seq/SPLiT-seq live |
| Sex | **PATO** | `PATO:0000384` (male), `PATO:0000383` (female), `PATO:0001340` (hermaphrodite) | CELLxGENE convention |
| Ethnicity / ancestry | **HANCESTRO** | `HANCESTRO:0005` (European) | human only |
| Developmental stage | **HsapDv / MmusDv** | `HsapDv:…` / `MmusDv:…` | species-specific |
| Cell line | **Cellosaurus** | `CVCL_…` | if samples are cell lines |

**EFO is the hub.** It's an EBI application ontology (~93k terms) that *imports* UBERON, CL, ChEBI, and MONDO, purpose-built to annotate expression experiments — so one ontology covers most fields. Note EFO uses the EBI IRI base `http://www.ebi.ac.uk/efo/EFO_…`, **not** the OBO PURL. ([EFO](https://www.ebi.ac.uk/efo/))

> ⚠️ **Sex has two conventions.** Single-cell world (CELLxGENE) uses **PATO** (`0000383`/`0000384`). GA4GH/clinical (Phenopackets) uses **NCIT** (`C46112`/`C46113`). Pick PATO to match the expression-data ecosystem; keep NCIT as an xref. (Aside: the `NCIT:C46109/C46110` pair sometimes cited for sex is *wrong* — the phenopacket codes are C46112/C46113.)

## The mapping cascade (cheap-first)

The literature is unanimous on the shape: **exact/curated lookup → similarity → LLM only for the residual, always grounded to a real ID.** Never let an LLM emit ontology IDs freely (it hallucinates them — GO grounding was **3/100 correct** direct vs **98/100** when grounded via a lookup step; [SPIRES](https://pmc.ncbi.nlm.nih.gov/articles/PMC10924283/)).

```mermaid
flowchart LR
  V[raw value\ne.g. 'breast tumour'] --> R{small rules\nsex/age/units}
  R -->|handled| OUT[ontology ID + confidence]
  R -->|no| X[exact synonym match\nOLS/ontology synonym sets]
  X -->|hit| OUT
  X -->|miss| T[similarity\ntext2term TF-IDF / SapBERT-BioLORD]
  T -->|score ≥ τ| OUT
  T -->|low| L[LLM extract label\n→ ground to ID via OAK/OLS]
  L --> OUT
  L -->|still none| U[unmapped\nkeep raw, flag for review]
```

### Tiers, with tools

1. **Hand rules for the trivial-but-messy fields.** `sex`, `age`, units. A 20-line dictionary collapses `M/male/1/XY → PATO:0000384`. Highest precision, zero dependencies. (MetaSRA does exactly this + a "maximal phrase" rule so *"breast"* isn't wrongly pulled out of *"breast cancer"*.)
2. **Exact synonym lookup** against ontology label+synonym sets. Pull terms via **OLS4** (`https://www.ebi.ac.uk/ols4/api/search?q=…&ontology=efo&exact=true`) or load OWL locally with **OAK** (`oaklib`). Deterministic, can't hallucinate an ID.
3. **Lexical/semantic similarity** for variants/misspellings:
   - **text2term** (`pip install text2term`) — default **TF-IDF** mapper is fast (<1 min for 10k terms) and, in its own UK-Biobank→EFO benchmark, **most accurate (73.3%)** *and* fastest (4s) vs Zooma (65%, 687s) / Levenshtein / BioPortal. ([Database 2024](https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353))
   - **SapBERT / BioLORD-2023** embeddings for entity linking when strings diverge semantically ("mammary carcinoma" ↔ "breast cancer"). SapBERT is UMLS-synonym-tuned; BioLORD adds definition/sentence grounding. Nearest-neighbor over term-label embeddings → every candidate is a **real** ontology term (no hallucination). ([SapBERT](https://github.com/cambridgeltl/sapbert), [BioLORD](https://huggingface.co/FremyCompany/BioLORD-2023))
   - **Zooma** (EBI) — precedent-first: reuses prior curated annotations, falls back to OLS; returns HIGH/GOOD/MEDIUM confidence you can route on. Great as a *second opinion* / cross-check.
4. **LLM extraction for the messy residual** — long prose where the value is buried (e.g. teasing "10x 5′" out of an extract protocol). Use the LLM to propose a **label/span**, then **ground** it to an ID with OAK/OLS. Pattern: [OntoGPT/SPIRES](https://github.com/monarch-initiative/ontogpt). Highest coverage, highest cost/latency, so it's last.

Route by **confidence**: tier 1–2 auto-accept; tier 3 accept above a threshold `τ`; tier 4 / low-confidence → `unmapped`, keep raw, flag. Store per-field confidence so facets can distinguish "known female" from "guessed".

## RAG vs. IDs — answering "isn't this just embeddings?"

| | Embeddings/RAG | Ontology IDs |
|---|---|---|
| Good for | fuzzy **recall** in search | discrete **facets**, filters, counts, roll-ups |
| Can you `GROUP BY` it? | No | Yes |
| Hallucination risk | n/a (retrieval) | none if grounded via lookup |
| Handles new phrasings | yes, natively | only via the cascade |

**You need both, and they compound:** normalized values (`assay: 10x 3′ scRNA-seq`, `organism: Homo sapiens`) get **written back into the text you embed** — so retrieval gets a cleaner, richer signal, *and* you get clean facets. Normalization is not replaced by embeddings; it's upstream of them.

## Ontology-hierarchy note (for facets)

CL and EFO are **DAGs** (a term has multiple parents). To support "pick T cell → get all subtypes", precompute a **multi-valued transitive-ancestor array** per record (the `is_a`/`subClassOf` closure). This is what makes hierarchical facets a plain `terms` aggregation. Mechanics in [[24-Faceted-Search]].

## Spike scope

Prove the cascade on **3 fields**: `sex` (tier-1 rules, trivial win), `organism` (tiers 1–2, near-deterministic), and **one hard one** — `assay` (EFO; directly serves the single-cell story) or `tissue` (UBERON). Measure precision/coverage against a hand-labeled sample. → [[25-Embeddings-and-Cost#Eval]], [[40-Roadmap]]

## Sources

- **Field→ontology schema:** CELLxGENE — https://github.com/chanzuckerberg/single-cell-curation/blob/main/schema/4.0.0/schema.md · latest — https://chanzuckerberg.github.io/single-cell-curation/latest-schema.html
- **Ontologies:** EFO — https://www.ebi.ac.uk/efo/ · NCBITaxon — https://obofoundry.org/ontology/ncbitaxon.html · UBERON — https://obofoundry.org/ontology/uberon.html · CL — https://obofoundry.org/ontology/cl.html · MONDO — https://obofoundry.org/ontology/mondo.html · DOID — https://obofoundry.org/ontology/doid.html · OBI — https://obofoundry.org/ontology/obi.html · PATO — https://obofoundry.org/ontology/pato.html · HANCESTRO — https://obofoundry.org/ontology/hancestro.html · HsapDv/MmusDv — https://obofoundry.org/ontology/hsapdv.html
- **Sex codes (PATO vs NCIT):** phenopacket schema — https://github.com/phenopackets/phenopacket-schema/blob/master/docs/sex.rst
- **Mapping tools:** OLS4 — https://www.ebi.ac.uk/ols4/ · Zooma — https://www.ebi.ac.uk/spot/zooma/ · text2term (+ TF-IDF benchmark) — https://github.com/ccb-hms/ontology-mapper · https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353 · OntoGPT/SPIRES — https://github.com/monarch-initiative/ontogpt · OAK — https://github.com/INCATools/ontology-access-kit
- **Methods / tradeoffs:** MetaSRA — https://academic.oup.com/bioinformatics/article/33/18/2914/3848915 · SapBERT — https://aclanthology.org/2021.naacl-main.334/ · BioLORD-2023 — https://huggingface.co/FremyCompany/BioLORD-2023 · LLM ID-grounding / hallucination — https://pmc.ncbi.nlm.nih.gov/articles/PMC10924283/ · https://arxiv.org/pdf/2503.21813

---
title: Sources
tags: [sources, references]
---

# 99 · Sources

← [[Home]]

Citations gathered during the [[Home|research pass]] (2026-07). Grouped by topic.

## GEO data model, formats, access
- GEO overview — https://www.ncbi.nlm.nih.gov/geo/info/overview.html
- SOFT format — https://www.ncbi.nlm.nih.gov/geo/info/soft.html
- MINiML format — https://www.ncbi.nlm.nih.gov/geo/info/MINiML.html
- Download / FTP layout — https://www.ncbi.nlm.nih.gov/geo/info/download.html
- Programmatic access — https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html
- Private-accession example (GSE335901) — https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE335901
- HTS/SRA linkage — https://www.ncbi.nlm.nih.gov/geo/info/seq.html
- Homepage counts — https://www.ncbi.nlm.nih.gov/geo/
- E-utilities book — https://www.ncbi.nlm.nih.gov/books/NBK25501/ · usage/rate limits — https://www.ncbi.nlm.nih.gov/books/NBK25497/ · API-key account — https://www.ncbi.nlm.nih.gov/account/ · JSON params — https://www.ncbi.nlm.nih.gov/books/NBK25499/
- GEOparse — https://github.com/guma44/GEOparse · pysradb — https://github.com/saketkc/pysradb
- GEOmetadb (Bioconductor) — https://www.bioconductor.org/packages/release/bioc/html/GEOmetadb.html · paper — https://pmc.ncbi.nlm.nih.gov/articles/PMC2639278/ · staleness — https://support.bioconductor.org/p/9149627/ · GitHub repo (client only, no ETL — verified directly) — https://github.com/zhujack/GEOmetadb
- No single-cell field in GEO — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8121533/ · metadata mining — https://www.elucidata.io/blog/mining-data-and-metadata-from-geo-datasets

## Ontologies (scope)
- EFO — https://www.ebi.ac.uk/efo/ · about — https://www.ebi.ac.uk/efo/about.html
- NCBITaxon — https://obofoundry.org/ontology/ncbitaxon.html
- UBERON — https://obofoundry.org/ontology/uberon.html
- Plant Ontology — https://obofoundry.org/ontology/po.html
- Cell Ontology — https://obofoundry.org/ontology/cl.html
- MONDO — https://obofoundry.org/ontology/mondo.html · DOID — https://obofoundry.org/ontology/doid.html
- OBI — https://obofoundry.org/ontology/obi.html
- PATO (sex terms confirmed via OLS) — https://obofoundry.org/ontology/pato.html
- HANCESTRO — https://obofoundry.org/ontology/hancestro.html · HsapDv / MmusDv — https://obofoundry.org/ontology/hsapdv.html
- Phenopacket sex codes (NCIT C46112/C46113) — https://github.com/phenopackets/phenopacket-schema/blob/master/docs/sex.rst

## Ontology mapping tools
- OLS4 — https://www.ebi.ac.uk/ols4/ · API `…/api/search?q=` · GitHub — https://github.com/EBISPOT/ols4 · paper — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12094816/
- BioPortal REST — https://www.bioontology.org/wiki/BioPortal_REST_services
- Zooma — https://www.ebi.ac.uk/spot/zooma/ · GitHub — https://github.com/EBISPOT/zooma
- text2term — https://text2term.readthedocs.io/ · GitHub — https://github.com/ccb-hms/ontology-mapper · paper — https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353
- OntoGPT / SPIRES — https://github.com/monarch-initiative/ontogpt · paper — https://academic.oup.com/bioinformatics/article/40/3/btae104/7612230 · full text — https://pmc.ncbi.nlm.nih.gov/articles/PMC10924283/
- OAK (oaklib) — https://github.com/INCATools/ontology-access-kit

## Normalization method tradeoffs
- MetaSRA — https://academic.oup.com/bioinformatics/article/33/18/2914/3848915 · full text — https://pmc.ncbi.nlm.nih.gov/articles/PMC5870770/ · pipeline — https://github.com/deweylab/metasra-pipeline
- SapBERT — https://aclanthology.org/2021.naacl-main.334/ · GitHub — https://github.com/cambridgeltl/sapbert
- BioLORD-2023 — https://huggingface.co/FremyCompany/BioLORD-2023 · paper — https://academic.oup.com/jamia/article/31/9/1844/7614965
- OAEI-LLM-T (LLM ontology-matching hallucination) — https://arxiv.org/pdf/2503.21813

## Prior art (harmonized corpora)
- CELLxGENE schema — https://github.com/chanzuckerberg/single-cell-curation/blob/main/schema/4.0.0/schema.md · latest — https://chanzuckerberg.github.io/single-cell-curation/latest-schema.html · NAR 2025 — https://academic.oup.com/nar/article/53/D1/D886/7912032
- HCA metadata ontologies — https://ebi-ait.github.io/hca-metadata-community/ontologies/ontologies.html
- Sfaira — https://pmc.ncbi.nlm.nih.gov/articles/PMC8386039/
- ARCHS4 — https://pmc.ncbi.nlm.nih.gov/articles/PMC5893633/
- recount3 — https://pmc.ncbi.nlm.nih.gov/articles/PMC8628444/
- STARGEO — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5604135/
- ALE — https://pmc.ncbi.nlm.nih.gov/articles/PMC5751806/
- DISCO — https://academic.oup.com/nar/article/53/D1/D932/7899529
- OmicIDX — https://github.com/omicidx/omicidx-api

## Embedding models
- Hugging Face Hub revisions/cache/`local_files_only` — https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download
- BGE small v1.5 — https://huggingface.co/BAAI/bge-small-en-v1.5
- Pinned BGE small v1.5 snapshot used by the Track 4 baseline image — https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
- OpenAI embeddings — https://platform.openai.com/docs/models/text-embedding-3-large · announcement — https://openai.com/index/new-embedding-models-and-api-updates/
- Voyage voyage-3-large — https://blog.voyageai.com/2025/01/07/voyage-3-large/
- Cohere Embed v4 — https://docs.cohere.com/docs/cohere-embed
- MedCPT — https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842 · Query Encoder — https://huggingface.co/ncbi/MedCPT-Query-Encoder · Article Encoder — https://huggingface.co/ncbi/MedCPT-Article-Encoder · Cross-Encoder — https://huggingface.co/ncbi/MedCPT-Cross-Encoder
- Qwen3-Embedding-0.6B — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B · paper — https://arxiv.org/abs/2506.05176
- Qwen3-Embedding / MTEB — https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/

## Retrieval / RAG patterns
- BMQExpander (ontology-grounded expansion) — https://arxiv.org/abs/2508.11784
- LLM query understanding for live RAG — https://arxiv.org/pdf/2506.21384
- RRF hybrid dense-sparse — https://ceur-ws.org/Vol-4173/T3-7.pdf
- Rerankers 2026 comparison — https://futureagi.com/blog/best-rerankers-for-rag-2026/
- Retrieval vs RAG coverage (list vs summary) — https://arxiv.org/pdf/2603.08819

## Datastores / search engines
- PostgreSQL advisory-lock semantics — https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
- Psycopg safe SQL composition — https://www.psycopg.org/psycopg3/docs/api/sql.html
- PostgreSQL `CREATE INDEX` / concurrent builds — https://www.postgresql.org/docs/current/sql-createindex.html
- pgvector 0.8.0 — https://www.postgresql.org/about/news/pgvector-080-released-2952/ · GitHub — https://github.com/pgvector/pgvector
- pgvector mixed dimensions — https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column · HNSW — https://github.com/pgvector/pgvector#hnsw · vector storage — https://github.com/pgvector/pgvector#vector-type
- pgvector iterative index scans — https://github.com/pgvector/pgvector#iterative-index-scans
- pgvector 0.8 filtering on Aurora — https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/
- ParadeDB pg_search — https://www.paradedb.com/blog/introducing-search · hybrid RRF recipe — https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual · faceting — https://www.paradedb.com/blog/faceting
- Timescale pg_textsearch — https://github.com/timescale/pg_textsearch
- pgvectorscale — https://github.com/timescale/pgvectorscale
- OpenSearch RRF — https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/ · efficient kNN filters — https://opensearch.org/blog/efficient-filters-in-knn/
- Elasticsearch retrievers/RRF — https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever
- Qdrant Facet API — https://qdrant.tech/blog/qdrant-1.12.x/ · hybrid queries — https://qdrant.tech/documentation/search/hybrid-queries/
- Weaviate hybrid — https://docs.weaviate.io/weaviate/concepts/search/hybrid-search
- Milvus 2.5 full-text — https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md
- Vespa grouping/facets — https://docs.vespa.ai/en/querying/grouping.html
- Typesense faceting — https://typesense.org/docs/30.2/api/search.html
- LanceDB reranking — https://docs.lancedb.com/reranking · no-facet issue — https://github.com/lancedb/lancedb/issues/1348

## Faceted search design
- Solr multi-select faceting (tag/exclude) — https://yonik.com/multi-select-faceting/
- ES path_hierarchy tokenizer — https://www.elastic.co/docs/reference/text-analysis/analysis-pathhierarchy-tokenizer
- Ontology-enhanced faceted search (VLDB) — https://link.springer.com/article/10.1007/s00778-022-00735-3
- Algolia hierarchicalMenu — https://www.algolia.com/doc/api-reference/widgets/hierarchical-menu/js

## Model Context Protocol
- Model Context Protocol — https://modelcontextprotocol.io/
- Official Python SDK v1 branch — https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
- MCP Python package/release history — https://pypi.org/project/mcp/
- Official in-memory MCP server testing — https://py.sdk.modelcontextprotocol.io/testing/
- Standalone FastMCP package — https://pypi.org/project/fastmcp/
- FastMCP v3 upgrade guide (`on_duplicate` constructor migration) — https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2
- FastMCP running servers / Streamable HTTP — https://gofastmcp.com/deployment/running-server
- FastMCP HTTP and ASGI deployment — https://gofastmcp.com/deployment/http
- FastMCP 3.4.4 Host/Origin guard default and explicit opt-in — https://github.com/PrefectHQ/fastmcp/pull/4472
- FastMCP JWT/JWKS token verification — https://gofastmcp.com/servers/auth/token-verification
- FastMCP remote OAuth discovery — https://gofastmcp.com/servers/auth/remote-oauth
- FastMCP server-wide authorization — https://gofastmcp.com/servers/authorization
- FastMCP lifespan-managed resources — https://gofastmcp.com/servers/lifespan
- FastMCP middleware and rate limiting — https://gofastmcp.com/servers/middleware
- FastMCP tools: strict validation, structured output, timeouts, and annotations — https://gofastmcp.com/servers/tools
- Psycopg connection-pool startup, acquisition timeout, and waiting bounds — https://www.psycopg.org/psycopg3/docs/api/pool.html
- uv project and dependency-group behavior — https://docs.astral.sh/uv/concepts/projects/dependencies/
- Uvicorn application-factory and deployment options — https://www.uvicorn.org/deployment/
- FastMCP server testing — https://gofastmcp.com/servers/testing
- FastMCP `run_server_async` HTTP test utility — https://gofastmcp.com/python-sdk/fastmcp-utilities-tests
- FastMCP bearer-token clients — https://gofastmcp.com/clients/auth/bearer

> Full per-topic research notes (with the deeper comparisons) were produced by the research agents during this session; this file is the distilled link index.

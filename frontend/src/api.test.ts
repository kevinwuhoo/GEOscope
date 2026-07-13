import { afterEach, expect, test, vi } from "vitest";

import { searchDemo } from "./api";


function responseWithCounts(ncbiCandidates: number, mergedCandidates: number) {
  return {
    query: "mouse exercise",
    geo: { count: 100, results: [] },
    geoscope: {
      query: "mouse exercise",
      filters: {
        organism_ids: [],
        sex_ids: [],
        assay_categories: [],
        assay_labels: [],
      },
      limit: 10,
      retrieval_version: "geo-series-v1:test",
      embedding_variant: "gemini_embedding_2_3072_v1",
      results: [],
      facets: {},
      provenance: {
        exact_accession: false,
        elasticsearch_candidates: 100,
        ncbi_candidates: ncbiCandidates,
        merged_candidates: mergedCandidates,
        rerank_attempted: true,
        rerank_applied: true,
        rerank_model: "gpt-5.6-luna",
        rerank_reasoning_effort: "low",
        rerank_input_tokens: 1000,
        rerank_output_tokens: 200,
        latency: { elasticsearch_ms: 10, ncbi_ms: 20, reranker_ms: 30 },
        degradation: [],
      },
    },
    membership: {},
  };
}


afterEach(() => {
  vi.restoreAllMocks();
});


test("accepts the shared 100 NCBI and 200 merged candidate maxima", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(responseWithCounts(100, 200)), { status: 200 }),
  );

  const response = await searchDemo("mouse exercise");

  expect(response.geoscope.provenance.ncbi_candidates).toBe(100);
  expect(response.geoscope.provenance.merged_candidates).toBe(200);
});


test.each([
  [101, 200],
  [100, 201],
])("rejects provenance above the shared bounds", async (ncbi, merged) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(responseWithCounts(ncbi, merged)), { status: 200 }),
  );

  await expect(searchDemo("mouse exercise")).rejects.toThrow();
});

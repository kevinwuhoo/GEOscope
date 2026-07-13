import { z } from "zod";


const nativeResultSchema = z.object({
  gse: z.string(),
  title: z.string().nullable().optional(),
  study_type: z.string().nullable().optional(),
  taxon: z.string().nullable().optional(),
  summary: z.string().nullable().optional(),
});

const geoscopeResultSchema = z.object({
  gse: z.string(),
  rank: z.number(),
  score: z.number().nullable(),
  title: z.string().nullable(),
  snippet: z.string().nullable(),
  study_type: z.string().nullable(),
  n_samples: z.number().nullable(),
  pubmed_id: z.number().nullable(),
  organism_ids: z.array(z.string()),
  organism_labels: z.array(z.string()),
  organism_status: z.string().nullable(),
  sex_ids: z.array(z.string()),
  sex_status: z.string().nullable(),
  assay_categories: z.array(z.string()),
  assay_labels: z.array(z.string()),
  assay_status: z.string().nullable(),
  truncated_fields: z.array(z.string()).default([]),
  source: z.enum(["elasticsearch", "ncbi", "both"]),
  retrieval_score: z.number().nullable(),
  original_rank: z.number().int().positive().nullable(),
});

const provenanceSchema = z.object({
  exact_accession: z.boolean(),
  elasticsearch_candidates: z.number().int().min(0).max(100),
  ncbi_candidates: z.number().int().min(0).max(100),
  merged_candidates: z.number().int().min(0).max(200),
  rerank_attempted: z.boolean(),
  rerank_applied: z.boolean(),
  rerank_model: z.string().min(1).max(256).nullable(),
  rerank_reasoning_effort: z.literal("low").nullable(),
  rerank_thinking: z.literal("disabled").nullable(),
  rerank_input_tokens: z.number().int().nonnegative(),
  rerank_output_tokens: z.number().int().nonnegative(),
  latency: z.object({
    elasticsearch_ms: z.number().int().nonnegative(),
    ncbi_ms: z.number().int().nonnegative(),
    reranker_ms: z.number().int().nonnegative(),
  }),
  degradation: z.array(z.enum([
    "ncbi_timeout",
    "ncbi_error",
    "rerank_timeout",
    "rerank_refusal",
    "rerank_invalid",
    "rerank_error",
  ])).max(6),
});

const demoResponseSchema = z.object({
  query: z.string(),
  geo: z.object({
    count: z.number().nullable(),
    results: z.array(nativeResultSchema),
    error: z.string().optional(),
  }),
  geoscope: z.object({
    query: z.string(),
    retrieval_version: z.string(),
    embedding_variant: z.string().nullable(),
    results: z.array(geoscopeResultSchema),
    facets: z.record(z.string(), z.unknown()),
    provenance: provenanceSchema,
  }).passthrough(),
  membership: z.record(z.string(), z.boolean()).nullable(),
});

export type DemoResponse = z.infer<typeof demoResponseSchema>;
export type NativeResult = z.infer<typeof nativeResultSchema>;
export type GEOscopeResult = z.infer<typeof geoscopeResultSchema>;


export async function searchDemo(
  query: string,
  signal?: AbortSignal,
): Promise<DemoResponse> {
  const params = new URLSearchParams({ q: query, limit: "10" });
  const response = await fetch(`/api/demo/search?${params}`, { signal });
  if (!response.ok) {
    throw new Error(
      response.status === 422
        ? "Enter a specific study, mechanism, assay, or perturbation."
        : "The live comparison could not be loaded. Check the backend and try again.",
    );
  }
  return demoResponseSchema.parse(await response.json());
}

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
  organism_status: z.string().nullable(),
  sex_ids: z.array(z.string()),
  sex_status: z.string().nullable(),
  assay_categories: z.array(z.string()),
  assay_labels: z.array(z.string()),
  assay_status: z.string().nullable(),
  truncated_fields: z.array(z.string()).default([]),
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
  const params = new URLSearchParams({ q: query, limit: "8" });
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

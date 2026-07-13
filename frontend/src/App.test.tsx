import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";


const demoResponse = {
  query: "transcriptomes of individual cells",
  mode: "hybrid",
  geo: {
    count: 14,
    results: [
      {
        gse: "GSE999",
        title: "Literal keyword match",
        study_type: "Expression profiling",
        taxon: "Homo sapiens",
        summary: "A literal query match.",
      },
    ],
  },
  geoscope: {
    query: "transcriptomes of individual cells",
    filters: {
      organism_ids: [],
      sex_ids: [],
      assay_categories: [],
      assay_labels: [],
    },
    mode: "hybrid",
    limit: 8,
    retrieval_version: "geo-series-v1:gemini:embedding:hybrid",
    embedding_variant: "gemini_embedding_2_3072_v1",
    results: [
      {
        gse: "GSE123",
        rank: 1,
        score: 0.91,
        title: "Chromium single-cell study",
        snippet: "Profiles individual immune cells using 10x Chromium.",
        study_type: "Expression profiling by high throughput sequencing",
        n_samples: 12,
        pubmed_id: 12345678,
        organism_ids: ["NCBITaxon:9606"],
        organism_status: "mapped",
        sex_ids: [],
        sex_status: null,
        assay_categories: ["transcriptomics"],
        assay_labels: ["scRNA-seq"],
        assay_status: "mapped",
        truncated_fields: [],
      },
    ],
    facets: {},
  },
  membership: { GSE123: false },
};


afterEach(() => {
  vi.restoreAllMocks();
});


test("explains the thesis and turns a query into a live GEO comparison", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(demoResponse), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const user = userEvent.setup();
  render(<App />);

  expect(
    screen.getByRole("heading", { name: /see what geo search misses/i }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("NCBITaxon:9606").length).toBeGreaterThan(0);

  const query = screen.getByRole("searchbox", { name: /describe the studies/i });
  await user.clear(query);
  await user.type(query, "transcriptomes of individual cells");
  await user.click(screen.getByRole("button", { name: /compare results/i }));

  expect(await screen.findByText("GSE123")).toBeInTheDocument();
  expect(screen.getByText("GSE999")).toBeInTheDocument();
  expect(
    screen.getByText(/not returned by geo keyword search/i),
  ).toBeInTheDocument();
});

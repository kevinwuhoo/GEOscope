import { cleanup, render, screen } from "@testing-library/react";
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


const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);


afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalClipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor);
  } else {
    Reflect.deleteProperty(navigator, "clipboard");
  }
});


test("presents the focused NCBI GEO marketing story", () => {
  render(<App />);

  expect(
    screen.getByRole("heading", { name: /see what ncbi geo search misses/i }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByText(/hybrid bm25 \+ embedding retrieval/i).length,
  ).toBeGreaterThan(0);
  expect(
    screen.getAllByText(/structured metadata extraction and normalization/i).length,
  ).toBeGreaterThan(0);
  expect(screen.queryByText(/geo \/ metadata discovery/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/not just vector search/i)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: /messy in/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /neoadjuvant chemotherapy/i }),
  ).toBeInTheDocument();
});


test("explains the thesis and turns a query into a live GEO comparison", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(demoResponse), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const user = userEvent.setup();
  render(<App />);

  expect(
    screen.getByRole("heading", { name: /see what ncbi geo search misses/i }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("NCBITaxon:9606").length).toBeGreaterThan(0);
  expect(
    screen.queryByRole("combobox", { name: /retrieval mode/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /human breast cancer transcriptomics/i }),
  ).toBeInTheDocument();

  const query = screen.getByRole("searchbox", { name: /describe the studies/i });
  await user.clear(query);
  await user.type(query, "transcriptomes of individual cells");
  await user.click(screen.getByRole("button", { name: /compare results/i }));

  expect(fetchMock.mock.calls[0]?.[0]).toEqual(
    expect.stringContaining("mode=hybrid"),
  );
  expect(await screen.findByText("GSE123")).toBeInTheDocument();
  expect(screen.getByText("GSE999")).toBeInTheDocument();
  const pair = screen.getByText("GSE123").closest(".comparison-row");
  expect(pair).not.toBeNull();
  expect(pair?.querySelector(".result-card--scope")?.textContent).toContain("GSE123");
  expect(pair?.querySelector(".result-card--native")?.textContent).toContain("GSE999");
  expect(
    screen.getByRole("article", { name: /geoscope result 1: chromium single-cell study/i }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: /ncbi geo result 1: literal keyword match/i }),
  ).toBeInTheDocument();
});


test("preserves paired ranks when source result counts differ", async () => {
  const secondResult = {
    ...demoResponse.geoscope.results[0],
    gse: "GSE456",
    rank: 2,
    title: "A second GEOscope result",
  };
  const unequalResponse = {
    ...demoResponse,
    geoscope: {
      ...demoResponse.geoscope,
      results: [...demoResponse.geoscope.results, secondResult],
    },
    membership: { ...demoResponse.membership, GSE456: false },
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(unequalResponse), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: /compare results/i }));
  await screen.findByText("GSE456");

  const rows = document.querySelectorAll(".comparison-row");
  expect(rows).toHaveLength(2);
  expect(rows[1]?.querySelector(".result-card--scope")?.textContent).toContain("GSE456");
  expect(rows[1]?.querySelector(".comparison-cell--native")).toHaveTextContent(
    /no ncbi geo result at this rank/i,
  );
});


test("copies the production MCP endpoint", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy mcp url/i }));

  expect(writeText).toHaveBeenCalledWith("https://geoscope.kevinformatics.com/mcp");
  expect(screen.getByRole("status")).toHaveTextContent(/copied/i);
});


test("keeps manual MCP copy guidance when clipboard access fails", async () => {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) },
  });
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy mcp url/i }));

  expect(screen.getByRole("status")).toHaveTextContent(
    /select the url and copy it manually/i,
  );
});

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";


const demoResponse = {
  query: "transcriptomes of individual cells",
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
    limit: 10,
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
        organism_labels: ["Homo sapiens"],
        organism_status: "mapped",
        sex_ids: [],
        sex_status: null,
        assay_categories: ["transcriptomics"],
        assay_labels: ["scRNA-seq"],
        assay_status: "mapped",
        truncated_fields: [],
        source: "elasticsearch",
        retrieval_score: 0.91,
        original_rank: 1,
      },
      {
        gse: "GSE888",
        rank: 7,
        score: null,
        title: "Fresh live NCBI series",
        snippet: "A live NCBI candidate with partial metadata.",
        study_type: "Expression profiling",
        n_samples: null,
        pubmed_id: null,
        organism_ids: [],
        organism_labels: [],
        organism_status: "unavailable",
        sex_ids: [],
        sex_status: "unavailable",
        assay_categories: [],
        assay_labels: [],
        assay_status: "unavailable",
        truncated_fields: [],
        source: "ncbi",
        retrieval_score: null,
        original_rank: null,
      },
      {
        gse: "GSE777",
        rank: 9,
        score: 0.72,
        title: "Series found by both sources",
        snippet: "A merged local and live NCBI candidate.",
        study_type: "Expression profiling",
        n_samples: 4,
        pubmed_id: null,
        organism_ids: ["NCBITaxon:9606"],
        organism_labels: ["Homo sapiens"],
        organism_status: "mapped",
        sex_ids: [],
        sex_status: "absent",
        assay_categories: ["transcriptomics"],
        assay_labels: [],
        assay_status: "mapped",
        truncated_fields: [],
        source: "both",
        retrieval_score: 0.72,
        original_rank: 4,
      },
    ],
    facets: {},
    provenance: {
      exact_accession: false,
      elasticsearch_candidates: 100,
      ncbi_candidates: 100,
      merged_candidates: 200,
      rerank_attempted: true,
      rerank_applied: true,
      rerank_model: "claude-sonnet-5",
      rerank_reasoning_effort: "low",
      rerank_thinking: "disabled",
      rerank_input_tokens: 123,
      rerank_output_tokens: 45,
      latency: {
        elasticsearch_ms: 12,
        ncbi_ms: 20,
        reranker_ms: 31,
      },
      degradation: [],
    },
  },
  membership: { GSE123: false, GSE888: true, GSE777: true },
};


const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);

const CLAUDE_COMMAND =
  "claude mcp add --scope user --transport http geoscope https://geoscope.kevinformatics.com/mcp";
const CODEX_COMMAND =
  "codex mcp add geoscope --url https://geoscope.kevinformatics.com/mcp";
const GEMINI_COMMAND =
  "gemini mcp add --scope user --transport http geoscope https://geoscope.kevinformatics.com/mcp";


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
    screen.getByRole("heading", {
      name: /see what searching ncbi geo misses/i,
      level: 1,
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      /finds the geo studies you need by understanding the biological meaning/i,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "GEOscope finds the GEO studies you need by understanding the biological meaning of your question, not just the exact words used in a submission.",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: /primary navigation/i })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /open live demo/i })).toHaveAttribute(
    "href",
    "#live-demo",
  );
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


test("offers the requested MCP clients with official local logos", () => {
  render(<App />);

  const hero = document.querySelector(".hero");
  expect(hero?.querySelector(".mcp-install")).not.toBeNull();
  expect(
    screen.getByRole("heading", { name: /bring geoscope to your agent/i, level: 3 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /try a live comparison/i })).toHaveAttribute(
    "href",
    "#live-demo",
  );
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent?.trim())).toEqual([
    "Claude Code",
    "Codex",
    "Gemini CLI",
  ]);
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  expect(tabs[0]).toHaveAttribute("tabindex", "0");
  expect(screen.getByText(CLAUDE_COMMAND)).toBeVisible();
  const logoSources = tabs.map((tab) =>
    tab.querySelector("img")?.getAttribute("src"),
  );
  expect(logoSources.every(Boolean)).toBe(true);
  expect(logoSources[0]).toContain("claude-code");
  expect(logoSources[1]).toContain("codex");
  expect(logoSources[2]).toMatch(/(?:gemini-cli|^data:image\/svg\+xml)/);
  expect(
    screen.queryByRole("list", { name: /compatible mcp clients/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", {
      name: /search the same research question two ways/i,
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/hybrid metadata search with the literal keyword results/i),
  ).toBeInTheDocument();
});


test("switches MCP instructions with pointer and keyboard input", async () => {
  const user = userEvent.setup();
  render(<App />);
  const tabs = screen.getAllByRole("tab");

  await user.click(tabs[1]);
  expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  expect(tabs[1]).toHaveAttribute("tabindex", "0");
  expect(tabs[0]).toHaveAttribute("tabindex", "-1");
  expect(screen.getByText(CODEX_COMMAND)).toBeVisible();

  tabs[1].focus();
  await user.keyboard("{ArrowRight}");
  expect(tabs[2]).toHaveFocus();
  expect(tabs[2]).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(GEMINI_COMMAND)).toBeVisible();

  await user.keyboard("{ArrowRight}");
  expect(tabs[0]).toHaveFocus();
  await user.keyboard("{End}");
  expect(tabs[2]).toHaveFocus();
  await user.keyboard("{Home}");
  expect(tabs[0]).toHaveFocus();
});


test("offers concise species-neutral example queries", () => {
  render(<App />);

  const examples = [
    "breast tumors before and after neoadjuvant chemotherapy",
    "NASH liver transcriptomes compared with healthy controls",
    "PI3K signaling in insulin-resistant skeletal muscle",
  ];
  for (const example of examples) {
    expect(screen.getByRole("button", { name: example })).toBeInTheDocument();
  }
  expect(
    screen.getByRole("searchbox", { name: /describe the studies/i }),
  ).toHaveValue("");
  expect(
    screen.getByRole("searchbox", { name: /describe the studies/i }),
  ).toHaveAttribute(
    "placeholder",
    "Describe a disease, treatment, pathway, assay, or comparison",
  );
});


test("runs an example query when selected", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(demoResponse), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const user = userEvent.setup();
  render(<App />);

  const example = "PI3K signaling in insulin-resistant skeletal muscle";
  await user.click(screen.getByRole("button", { name: example }));

  expect(
    screen.getByRole("searchbox", { name: /describe the studies/i }),
  ).toHaveValue(example);
  expect(await screen.findByText("GSE123")).toBeInTheDocument();
  const requestUrl = new URL(
    String(fetchMock.mock.calls[0]?.[0]),
    window.location.origin,
  );
  expect(requestUrl.searchParams.get("q")).toBe(example);
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
    screen.getByRole("heading", {
      name: /see what searching ncbi geo misses/i,
      level: 1,
    }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("NCBITaxon:9606").length).toBeGreaterThan(0);
  expect(
    screen.queryByRole("combobox", { name: /retrieval mode/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "breast tumors before and after neoadjuvant chemotherapy",
    }),
  ).toBeInTheDocument();

  const query = screen.getByRole("searchbox", { name: /describe the studies/i });
  await user.clear(query);
  await user.type(query, "transcriptomes of individual cells");
  await user.click(screen.getByRole("button", { name: /compare results/i }));

  const requestUrl = new URL(
    String(fetchMock.mock.calls[0]?.[0]),
    window.location.origin,
  );
  expect(requestUrl.searchParams.get("q")).toBe(
    "transcriptomes of individual cells",
  );
  expect(requestUrl.searchParams.get("limit")).toBe("10");
  expect(requestUrl.searchParams.has("mode")).toBe(false);
  expect(await screen.findByText("GSE123")).toBeInTheDocument();
  expect(screen.getByText("GSE999")).toBeInTheDocument();
  expect(
    screen.getByText(/live ncbi result · not yet indexed/i),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/found by both geoscope and displayed ncbi results/i),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/not in this ncbi candidate set \(up to 100\)/i),
  ).toBeInTheDocument();
  const pair = screen.getByText("GSE123").closest(".comparison-row");
  expect(pair).not.toBeNull();
  expect(pair?.querySelector(".result-card--scope")?.textContent).toContain("GSE123");
  expect(pair?.querySelector(".result-card--native")?.textContent).toContain("GSE999");
  const scopeTags = pair?.querySelectorAll(".result-card--scope .result-tag");
  expect(Array.from(scopeTags ?? [], (tag) => tag.textContent)).toEqual([
    "Homo sapiens",
    "scRNA-seq",
    "Expression profiling by high throughput sequencing",
  ]);
  expect(scopeTags?.[0]).toHaveClass("result-tag--normalized");
  expect(scopeTags?.[0]).toHaveAttribute("title", "NCBITaxon:9606");
  expect(scopeTags?.[1]).toHaveClass("result-tag--normalized");
  expect(scopeTags?.[1]).not.toHaveAttribute("title");
  expect(scopeTags?.[2]).toHaveClass("result-tag--source");
  expect(
    screen.getByRole("article", { name: /geoscope result 1: chromium single-cell study/i }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: /ncbi geo result 1: literal keyword match/i }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: /geoscope result 7: fresh live ncbi series/i }),
  ).toHaveTextContent("07");
  const nativeSearchLink = screen.getByRole("link", {
    name: /open this search on ncbi geo/i,
  });
  const nativeSearchUrl = new URL(nativeSearchLink.getAttribute("href") ?? "");
  expect(nativeSearchLink).toHaveAttribute("target", "_blank");
  expect(nativeSearchLink).toHaveAttribute("rel", "noopener noreferrer");
  expect(nativeSearchUrl.origin).toBe("https://www.ncbi.nlm.nih.gov");
  expect(nativeSearchUrl.pathname).toBe("/gds/");
  expect(nativeSearchUrl.searchParams.get("term")).toBe(
    "transcriptomes of individual cells AND gse[ETYP]",
  );
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
      results: [demoResponse.geoscope.results[0], secondResult],
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

  await user.type(
    screen.getByRole("searchbox", { name: /describe the studies/i }),
    "transcriptomes of individual cells",
  );
  await user.click(screen.getByRole("button", { name: /compare results/i }));
  await screen.findByText("GSE456");

  const rows = document.querySelectorAll(".comparison-row");
  expect(rows).toHaveLength(2);
  expect(rows[1]?.querySelector(".result-card--scope")?.textContent).toContain("GSE456");
  expect(rows[1]?.querySelector(".comparison-cell--native")).toHaveTextContent(
    /no ncbi geo result at this rank/i,
  );
});


test("summarizes multi-organism tags and omits unavailable tag categories", async () => {
  const multiOrganism = {
    ...demoResponse.geoscope.results[0],
    organism_ids: [
      "NCBITaxon:10090",
      "NCBITaxon:10116",
      "NCBITaxon:9606",
    ],
    organism_labels: ["Mus musculus", "Rattus norvegicus", "Homo sapiens"],
    assay_labels: ["RNA-seq", "ChIP-seq"],
  };
  const noAssay = {
    ...demoResponse.geoscope.results[0],
    gse: "GSE456",
    rank: 2,
    title: "Study without normalized assay metadata",
    organism_ids: ["NCBITaxon:9606"],
    organism_labels: ["Homo sapiens"],
    assay_labels: [],
  };
  const noTags = {
    ...demoResponse.geoscope.results[0],
    gse: "GSE789",
    rank: 3,
    title: "Study without tag metadata",
    study_type: null,
    organism_ids: [],
    organism_labels: [],
    assay_labels: [],
  };
  const response = {
    ...demoResponse,
    geoscope: {
      ...demoResponse.geoscope,
      results: [multiOrganism, noAssay, noTags],
    },
    membership: { GSE123: false, GSE456: false, GSE789: false },
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(response), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const user = userEvent.setup();
  render(<App />);

  await user.type(
    screen.getByRole("searchbox", { name: /describe the studies/i }),
    "transcriptomes of individual cells",
  );
  await user.click(screen.getByRole("button", { name: /compare results/i }));

  const multiCard = await screen.findByRole("article", {
    name: /geoscope result 1: chromium single-cell study/i,
  });
  expect(
    Array.from(multiCard.querySelectorAll(".result-tag"), (tag) => tag.textContent),
  ).toEqual([
    "Mus musculus · Rattus norvegicus +1",
    "RNA-seq",
    "Expression profiling by high throughput sequencing",
  ]);
  const noAssayCard = screen.getByRole("article", {
    name: /geoscope result 2: study without normalized assay metadata/i,
  });
  expect(
    Array.from(noAssayCard.querySelectorAll(".result-tag"), (tag) => tag.textContent),
  ).toEqual([
    "Homo sapiens",
    "Expression profiling by high throughput sequencing",
  ]);
  const noTagsCard = screen.getByRole("article", {
    name: /geoscope result 3: study without tag metadata/i,
  });
  expect(noTagsCard.querySelector(".result-tags")).toBeNull();
});


test("copies each selected MCP command and clears stale feedback", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy command/i }));
  expect(writeText).toHaveBeenLastCalledWith(CLAUDE_COMMAND);
  expect(screen.getByRole("status")).toHaveTextContent(
    /copied claude code command/i,
  );

  await user.click(screen.getByRole("tab", { name: /^codex$/i }));
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  await user.click(screen.getByRole("button", { name: /copy command/i }));
  expect(writeText).toHaveBeenLastCalledWith(CODEX_COMMAND);

  await user.click(screen.getByRole("tab", { name: /gemini cli/i }));
  await user.click(screen.getByRole("button", { name: /copy command/i }));
  expect(writeText).toHaveBeenLastCalledWith(GEMINI_COMMAND);
});


test("keeps manual MCP copy guidance when clipboard access fails", async () => {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) },
  });
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy command/i }));

  expect(screen.getByRole("status")).toHaveTextContent(
    /select the command and copy it manually/i,
  );
});

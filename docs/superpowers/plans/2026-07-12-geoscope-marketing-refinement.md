# GEOscope Marketing Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Refine the GEOscope marketing site around a clearer NCBI GEO comparison, fixed hybrid search, realistic researcher queries, an elevated and aligned GEOscope result presentation, and a copyable MCP endpoint.

**Architecture:** Keep the existing React component boundaries and browser-safe API contract. `App` owns the page narrative and marquee, `LiveComparison` owns fixed-hybrid search and paired results, `CapabilityFlow` owns the retrieval story, and a new `McpInstall` component owns clipboard behavior independently.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, CSS

## Global Constraints

- Refer to the source repository as **NCBI GEO** in all visitor-facing source copy.
- Expose only hybrid BM25 + embedding retrieval in the marketing UI; do not change backend search modes.
- Do not claim that structured extraction or normalization is comprehensive.
- Use `https://geoscope.kevinformatics.com/mcp` as the exact MCP endpoint.
- Do not add product-specific ChatGPT or Claude installation flows.
- Do not add new runtime dependencies.
- Preserve the unrelated user change in `wiki/Home.md`.

---

### Task 1: Marketing narrative, feature marquee, and researcher example

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/ResearcherExample.tsx`
- Delete: `frontend/src/components/NormalizationProof.tsx`

**Interfaces:**
- Consumes: Existing `AccessionScope`, `LiveComparison`, `CapabilityFlow`, and `ResearcherExample` React components.
- Produces: Static page narrative with NCBI GEO naming and two `.signal-strip__track` sequences, the second marked `aria-hidden="true"`.

- [x] **Step 1: Add a failing static-content test**

Add this test in `frontend/src/App.test.tsx`:

```tsx
test("presents the focused NCBI GEO marketing story", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: /see what ncbi geo search misses/i })).toBeInTheDocument();
  expect(screen.getAllByText(/hybrid bm25 \+ embedding retrieval/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/structured metadata extraction and normalization/i).length).toBeGreaterThan(0);
  expect(screen.queryByText(/geo \/ metadata discovery/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/not just vector search/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /messy in/i })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /neoadjuvant chemotherapy/i })).toBeInTheDocument();
});
```

- [x] **Step 2: Run the static-content test and confirm RED**

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "presents the focused"`

Expected: FAIL because the NCBI GEO thesis and revised content are absent.

- [x] **Step 3: Update the App narrative and marquee**

In `frontend/src/App.tsx`, remove the `NormalizationProof` import and rendered section, remove `.hero-index`, all `.section-kicker` and `.footer-note` elements owned by `App`, and change the hero heading to:

```tsx
<h1 id="hero-title">See what NCBI GEO search misses.</h1>
```

Add this feature data above `App`:

```tsx
const features = [
  "Hybrid BM25 + embedding retrieval",
  "Semantic similarity search",
  "Structured metadata extraction and normalization",
  "Exact filters and facets",
  "An MCP server for your agent",
];
```

Replace the static signal strip with:

```tsx
<div className="signal-strip" aria-label="GEOscope capabilities">
  {[false, true].map((duplicate) => (
    <div className="signal-strip__track" aria-hidden={duplicate || undefined} key={String(duplicate)}>
      {features.map((feature) => (
        <span className="signal-strip__item" key={feature}>
          {feature}<i aria-hidden="true" />
        </span>
      ))}
    </div>
  ))}
</div>
```

Update the hero proof to `GSE / series-level metadata`, `HYBRID / BM25 + embeddings`, and `MCP / agent-ready retrieval`. Update the footer sentence to `Hybrid, semantic discovery for NCBI GEO metadata.`

- [x] **Step 4: Revise the researcher example**

In `frontend/src/components/ResearcherExample.tsx`, remove the kicker and receipt header, and use:

```tsx
<h2 id="researcher-title">
  “Find human breast cancer transcriptomics before and after neoadjuvant chemotherapy, including treatment response.”
</h2>
```

Set the structured rows to semantic text `neoadjuvant chemotherapy and treatment response`, organism `NCBITaxon:9606`, disease `breast cancer`, and result `ranked NCBI GEO series + facets`.

- [x] **Step 5: Remove the obsolete normalization component and verify GREEN**

Delete `frontend/src/components/NormalizationProof.tsx` after removing its only import.

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "presents the focused"`

Expected: PASS.

---

### Task 2: Fixed hybrid search and aligned source comparison

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/LiveComparison.tsx`

**Interfaces:**
- Consumes: `searchDemo(query: string, mode: SearchMode, signal?: AbortSignal)` from `frontend/src/api.ts`.
- Produces: Search requests with literal mode `"hybrid"` and `.comparison-row` elements containing GEOscope first and NCBI GEO second.

- [x] **Step 1: Add failing hybrid and alignment assertions**

Extend the live comparison test to include:

```tsx
expect(screen.queryByRole("combobox", { name: /retrieval mode/i })).not.toBeInTheDocument();
expect(screen.getByRole("button", { name: /human breast cancer transcriptomics/i })).toBeInTheDocument();

await user.click(screen.getByRole("button", { name: /compare results/i }));

expect(vi.mocked(globalThis.fetch).mock.calls[0]?.[0]).toEqual(
  expect.stringContaining("mode=hybrid"),
);
const pair = (await screen.findByText("GSE123")).closest(".comparison-row");
expect(pair).not.toBeNull();
expect(pair?.querySelector(".result-card--scope")?.textContent).toContain("GSE123");
expect(pair?.querySelector(".result-card--native")?.textContent).toContain("GSE999");
```

Change the miss-label assertion to `/not returned by ncbi geo keyword search/i`.

- [x] **Step 2: Run the live comparison test and confirm RED**

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "turns a query"`

Expected: FAIL because a mode selector is present and the result sources are separate columns in the opposite order.

- [x] **Step 3: Fix the query examples and hybrid behavior**

In `frontend/src/components/LiveComparison.tsx`, use:

```tsx
const examples = [
  "human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data",
  "liver transcriptomics comparing nonalcoholic steatohepatitis with healthy human controls",
  "mouse skeletal muscle gene expression after endurance exercise in insulin resistance",
];
```

Remove `SearchMode`, the `mode` state, and the `<select>`. Call:

```tsx
const response = await searchDemo(normalized, "hybrid", controller.signal);
params.set("mode", "hybrid");
```

Use the new placeholder `e.g. breast cancer before and after neoadjuvant chemotherapy`.

- [x] **Step 4: Render shared paired result rows**

Add:

```tsx
const rowCount = Math.max(data.geoscope.results.length, data.geo.results.length, 1);
const rows = Array.from({ length: rowCount }, (_, index) => ({
  geoscope: data.geoscope.results[index],
  native: data.geo.results[index],
}));
```

Render one `.comparison-header` followed by:

```tsx
<div className="comparison-results">
  {rows.map((row, index) => (
    <div className="comparison-row" key={row.geoscope?.gse ?? row.native?.gse ?? index}>
      <div className="comparison-cell comparison-cell--scope">
        {row.geoscope ? (
          <GEOscopeCard result={row.geoscope} inNative={data.membership?.[row.geoscope.gse]} />
        ) : <div className="result-empty">No GEOscope result at this rank.</div>}
      </div>
      <div className="comparison-cell comparison-cell--native">
        {row.native ? (
          <NativeCard result={row.native} rank={index + 1} />
        ) : <div className="result-empty">No NCBI GEO result at this rank.</div>}
      </div>
    </div>
  ))}
</div>
```

Put the GEOscope header before the NCBI GEO header and change all native source copy, loading copy, empty states, and miss labels to `NCBI GEO`.

- [x] **Step 5: Run the focused test and full frontend suite**

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "turns a query"`

Expected: PASS.

Run: `pnpm --dir frontend test`

Expected: all frontend tests PASS.

---

### Task 3: Standalone MCP installation panel

**Files:**
- Create: `frontend/src/components/McpInstall.tsx`
- Modify: `frontend/src/components/CapabilityFlow.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Browser `navigator.clipboard.writeText(value)` when available.
- Produces: `MCP_URL`, an accessible copy button, status text, and manual-copy fallback.

- [x] **Step 1: Add failing copy success and failure tests**

Add tests in `frontend/src/App.test.tsx`:

```tsx
test("copies the production MCP endpoint", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy mcp url/i }));

  expect(writeText).toHaveBeenCalledWith("https://geoscope.kevinformatics.com/mcp");
  expect(screen.getByRole("status")).toHaveTextContent(/copied/i);
});

test("keeps manual MCP copy guidance when clipboard access fails", async () => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) },
  });
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy mcp url/i }));

  expect(screen.getByRole("status")).toHaveTextContent(/select the url and copy it manually/i);
});
```

- [x] **Step 2: Run the MCP tests and confirm RED**

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "MCP"`

Expected: FAIL because the MCP copy button is absent.

- [x] **Step 3: Implement the focused MCP component**

Create `frontend/src/components/McpInstall.tsx`:

```tsx
import { useState } from "react";

export const MCP_URL = "https://geoscope.kevinformatics.com/mcp";

export function McpInstall() {
  const [status, setStatus] = useState("");

  async function copyUrl() {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(MCP_URL);
      setStatus("Copied MCP URL.");
    } catch {
      setStatus("Select the URL and copy it manually.");
    }
  }

  return (
    <aside className="mcp-install" aria-labelledby="mcp-title">
      <div>
        <h3 id="mcp-title">Bring GEOscope to your agent.</h3>
        <p>Add this URL as a custom MCP server in ChatGPT, Claude, or another MCP-compatible client.</p>
      </div>
      <div className="mcp-install__action">
        <label htmlFor="mcp-url">MCP server URL</label>
        <div className="mcp-install__copy-row">
          <input id="mcp-url" value={MCP_URL} readOnly onFocus={(event) => event.currentTarget.select()} />
          <button type="button" onClick={copyUrl}>{status.startsWith("Copied") ? "Copied" : "Copy MCP URL"}</button>
        </div>
        <p className="mcp-install__status" role="status" aria-live="polite">{status}</p>
      </div>
    </aside>
  );
}
```

- [x] **Step 4: Separate the retrieval flow from MCP**

In `frontend/src/components/CapabilityFlow.tsx`, import and render `<McpInstall />` after `.capability-flow`. Update the capability data to:

```tsx
const capabilities = [
  {
    verb: "Search language and meaning",
    detail: "Combine BM25 keyword precision with embedding-based similarity across NCBI GEO titles, summaries, designs, and sample metadata.",
    signal: "BM25 + embeddings",
  },
  {
    verb: "Structure the metadata",
    detail: "Extract and normalize useful biological concepts from submitter-authored metadata so relevant studies are easier to compare.",
    signal: "extraction + normalization",
  },
  {
    verb: "Narrow to evidence",
    detail: "Use exact filters and facets to turn a broad similarity search into a ranked, inspectable set of NCBI GEO series.",
    signal: "filters + facets",
  },
];
```

Remove the section kicker and use the heading `From a specific question to inspectable evidence.`

- [x] **Step 5: Run the MCP tests and full frontend suite**

Run: `pnpm --dir frontend exec vitest run src/App.test.tsx -t "MCP"`

Expected: both MCP tests PASS.

Run: `pnpm --dir frontend test`

Expected: all frontend tests PASS.

---

### Task 4: Visual hierarchy, responsive alignment, and verification

**Files:**
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Class names introduced by Tasks 1–3.
- Produces: Continuous marquee motion, paired equal-height result rows, elevated GEOscope cards, a distinct MCP panel, mobile stacking, and reduced-motion fallback.

- [x] **Step 1: Implement marquee and comparison styles**

Replace the old signal-strip, comparison-grid, normalization, and mode-select styles with rules that:

```css
.signal-strip { display: flex; min-height: 76px; overflow: hidden; padding: 0; }
.signal-strip__track { flex: none; display: flex; align-items: center; width: max-content; animation: marquee 34s linear infinite; }
.signal-strip__item { display: inline-flex; align-items: center; gap: 30px; padding-left: 30px; font: 650 14px/1 var(--mono); text-transform: uppercase; }
.signal-strip__item i { width: 7px; height: 7px; background: var(--carbon); transform: rotate(45deg); }
.signal-strip:hover .signal-strip__track, .signal-strip:focus-within .signal-strip__track { animation-play-state: paused; }
@keyframes marquee { to { transform: translateX(-100%); } }

.comparison-header, .comparison-row { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 28px; }
.comparison-row { align-items: stretch; }
.comparison-cell { min-width: 0; display: flex; }
.comparison-cell > * { width: 100%; }
.result-card--scope { z-index: 2; margin: -4px 0 4px; border: 2px solid var(--cobalt); background: var(--white); box-shadow: 10px 10px 0 rgb(49 92 255 / .18), 4px 4px 0 var(--cobalt); transform: scale(1.012); }
.result-column__header--scope { position: relative; z-index: 3; padding-inline: 18px; background: var(--cobalt); color: white; border-color: var(--cobalt); }
.result-column__header--native { color: var(--muted); border-color: var(--rule); }
.result-card--native { background: rgb(255 255 255 / .35); color: #344743; }
```

Keep every comparison row equal-height by allowing both flex children to stretch. Do not use fixed card heights.

- [x] **Step 2: Implement capability, MCP, and cleanup styles**

Delete `.hero-index`, `.section-kicker`, normalization/mapping, query-receipt header, footer-note, and obsolete select rules. Add `.mcp-install`, `.mcp-install__action`, `.mcp-install__copy-row`, and status styles using the lime panel treatment against the dark capability section. Keep the MCP input selectable and give the copy button a minimum 48-pixel target.

- [x] **Step 3: Implement mobile and reduced-motion behavior**

At `max-width: 760px`, stack each `.comparison-header` and `.comparison-row` into one column with GEOscope first, remove scale from scope cards, and stack the MCP panel and copy controls. Keep the marquee clipped to the viewport.

In the existing reduced-motion query, add:

```css
.signal-strip__track { animation: none !important; }
.signal-strip__track[aria-hidden="true"] { display: none; }
```

- [x] **Step 4: Run automated verification**

Run: `pnpm --dir frontend test`

Expected: all frontend tests PASS with no warnings.

Run: `pnpm --dir frontend build`

Expected: TypeScript and Vite build complete successfully.

Run: `git diff --check`

Expected: no whitespace errors.

- [x] **Step 5: Review scope and working tree**

Run: `git status --short`

Expected: only the marketing implementation, its tests, this plan, and the pre-existing `wiki/Home.md` user edit are present. Do not stage or modify `wiki/Home.md`.

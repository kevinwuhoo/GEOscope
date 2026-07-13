# Marketing Query Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the marketing site's verbose species-specific search examples with the three approved, live-validated natural-language queries.

**Architecture:** Keep the change inside the existing `LiveComparison` presentation boundary. Update its static `examples` array and assert the rendered button labels and initial search value through the existing React Testing Library suite; do not alter request construction or shared search behavior.

**Tech Stack:** React, TypeScript, Vitest, React Testing Library, pnpm, Vite

## Global Constraints

- Use exactly these examples: `breast tumors before and after neoadjuvant chemotherapy`, `NASH liver transcriptomes compared with healthy controls`, and `PI3K signaling in insulin-resistant skeletal muscle`.
- Do not add an explicit species to any example.
- Do not change the placeholder, API contract, query parsing, Elasticsearch retrieval, MCP server, or result rendering.
- Search correctness and relevance behavior must remain in the shared MCP/Elasticsearch layer.
- Preserve unrelated working-tree changes and stage only files from this task.
- The user approved committing directly to `main` and pushing the eight commits already ahead of `origin/main` with this change.

---

### Task 1: Replace and verify the marketing query examples

**Files:**
- Modify: `frontend/src/App.test.tsx:149-174`
- Modify: `frontend/src/components/LiveComparison.tsx:6-10`
- Create: `docs/superpowers/plans/2026-07-13-marketing-query-examples.md`

**Interfaces:**
- Consumes: `LiveComparison`, whose `examples: string[]` drives the initial search-field value and the `Try:` buttons.
- Produces: Three exact example-query buttons and an initial search-field value equal to the first example; no exported interface changes.

- [ ] **Step 1: Write the failing frontend test**

Add this focused test before the existing live-comparison test in `frontend/src/App.test.tsx`:

```tsx
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
  ).toHaveValue(examples[0]);
});
```

In the existing `explains the thesis and turns a query into a live GEO comparison` test, replace the old example-button expectation with:

```tsx
expect(
  screen.getByRole("button", {
    name: "breast tumors before and after neoadjuvant chemotherapy",
  }),
).toBeInTheDocument();
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
pnpm --dir frontend exec vitest run src/App.test.tsx -t "offers concise species-neutral example queries"
```

Expected: FAIL because no button named `breast tumors before and after neoadjuvant chemotherapy` exists yet.

- [ ] **Step 3: Make the minimal production change**

Replace the `examples` array in `frontend/src/components/LiveComparison.tsx` with:

```tsx
const examples = [
  "breast tumors before and after neoadjuvant chemotherapy",
  "NASH liver transcriptomes compared with healthy controls",
  "PI3K signaling in insulin-resistant skeletal muscle",
];
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
pnpm --dir frontend exec vitest run src/App.test.tsx -t "offers concise species-neutral example queries"
```

Expected: PASS with one matching test and no failures.

- [ ] **Step 5: Run full frontend verification**

Run:

```bash
pnpm --dir frontend test
pnpm --dir frontend build
```

Expected: all Vitest tests pass and the Vite production build exits successfully.

- [ ] **Step 6: Review and commit only task files**

Run:

```bash
git diff --check
git diff -- frontend/src/App.test.tsx frontend/src/components/LiveComparison.tsx docs/superpowers/plans/2026-07-13-marketing-query-examples.md
git add frontend/src/App.test.tsx frontend/src/components/LiveComparison.tsx docs/superpowers/plans/2026-07-13-marketing-query-examples.md
git commit -m "Refine marketing query examples"
```

Expected: the commit includes only the test, component, and implementation plan.

- [ ] **Step 7: Push the approved `main` history**

Run:

```bash
git push origin main
```

Expected: `origin/main` advances to the new implementation commit, including the eight commits the user confirmed were already ready to push.

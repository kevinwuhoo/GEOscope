# GEOscope Marketing Refinement Design

## Purpose

Refine the existing GEOscope marketing page so its primary comparison is easier
to understand, visually favors GEOscope, and speaks to realistic researcher
workflows. The page remains a hackathon prototype backed by the existing live
comparison API and production MCP endpoint.

The core story is: GEOscope uses hybrid lexical and embedding retrieval to find
relevant studies across inconsistent NCBI GEO metadata, then makes the same
search capability available to researchers and MCP-compatible agents.

## Naming and copy

- Refer to the source repository as **NCBI GEO** in marketing copy, comparison
  labels, loading states, and miss labels. Preserve the GEOscope product name.
- Update the hero thesis to **“See what NCBI GEO search misses.”**
- Remove ornamental microcopy that does not help a visitor understand or use
  the product, including the hero version line, section kickers, query-receipt
  header labels, and footer prototype label. Keep functional labels such as
  form labels, result ranks, accessions, and controlled values.
- Replace the live-comparison introduction with: **“See the difference for
  yourself. GEOscope results on the left, NCBI GEO results on the right.”**
- Describe planned and implemented metadata work collectively as structured
  metadata extraction and normalization. Do not make taxon mapping the primary
  marketing proof and do not claim complete normalization coverage.

## Information architecture

### Hero and feature marquee

Keep the existing hero and accession-scope visual, but remove the version-style
index and update the NCBI GEO wording. Replace the static green strip with a
larger, continuously scrolling marquee. Its repeating feature sequence is:

- hybrid BM25 + embedding retrieval;
- semantic similarity search;
- structured metadata extraction and normalization;
- exact filters and facets; and
- an MCP server for agents.

These are polished versions of the requested rough feature ideas, not a claim
that every metadata field has already been extracted or normalized. Duplicate
the sequence in the DOM to make the marquee loop continuously, and hide the
duplicate sequence from assistive technology. Pause the marquee on hover or
keyboard focus and disable its movement when reduced motion is requested.

### Live comparison

The search interface exposes only hybrid retrieval. Remove the mode selector
and always send `mode=hybrid` through the existing frontend API call. Keep the
backend contract unchanged because non-marketing clients may still use its
other bounded modes.

Replace the short examples with specific, researcher-style searches spanning
multiple experimental domains rather than focusing only on single-cell data:

1. `human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data`
2. `liver transcriptomics comparing nonalcoholic steatohepatitis with healthy human controls`
3. `mouse skeletal muscle gene expression after endurance exercise in insulin resistance`

Render GEOscope on the left and NCBI GEO on the right. The two result lists must
share paired rank rows so the first result on each side occupies the same row,
the second result occupies the next row, and so on. A missing result on either
side leaves a deliberately empty cell rather than collapsing the alignment.
On narrow screens, each paired row stacks with its GEOscope result first.

The GEOscope side is the visual hero: use a higher stacking level, a slightly
larger card treatment, cobalt edge and shadow, bright normalized-data chips,
and a more colorful header. The NCBI GEO side remains legible but visually
quieter. Elevation must not obscure content, break focus outlines, or cause
horizontal overflow.

### Retrieval capabilities and MCP

Remove the standalone normalization proof section. Replace the existing
three-step “one retrieval loop” story with a focused workflow covering:

1. hybrid BM25 and embedding retrieval;
2. structured metadata extraction and normalization; and
3. precise filters, facets, and ranked evidence.

MCP is not shown as a pipeline stage. Give it a separate agent-facing panel
after the retrieval workflow with the headline **“Bring GEOscope to your
agent.”** The panel explains that an MCP-compatible client can call the same
bounded NCBI GEO metadata search operations.

The panel contains a read-only field showing the canonical production endpoint:

```text
https://geoscope.kevinformatics.com/mcp
```

A **Copy MCP URL** button writes that exact value to the clipboard. After a
successful copy it changes to **Copied** and exposes the confirmation through a
polite live region. If clipboard access is unavailable or fails, the field
remains selectable and the page shows a concise instruction to copy it
manually. Supporting copy tells the visitor to add the URL as a custom MCP
server in ChatGPT, Claude, or another MCP-compatible client without presenting
product-specific setup steps that may become stale.

### Researcher example and closing

Keep one structured-query example, but update it to a specific non-single-cell
workflow consistent with the example searches. Remove its decorative receipt
micro-labels while preserving the inspectable semantic text and filters. The
closing call to action continues to point to the live comparison and uses NCBI
GEO naming.

## Component boundaries

- `App` composes the revised narrative, removes `NormalizationProof`, and owns
  the accessible scrolling feature marquee.
- `LiveComparison` owns the fixed hybrid query, researcher examples, paired
  result-row rendering, and source-specific cards.
- `CapabilityFlow` owns the three-stage retrieval workflow and the separate MCP
  installation panel.
- `McpInstall` is a focused component for the canonical endpoint, clipboard
  behavior, success status, and failure fallback.
- `ResearcherExample` presents the revised structured researcher request.
- `styles.css` owns visual hierarchy, row alignment, marquee motion, responsive
  stacking, and reduced-motion behavior.

The obsolete `NormalizationProof` component is removed after `App` no longer
imports it.

## Testing and verification

Frontend behavior tests must establish that:

- visible source copy says NCBI GEO and removed ornamental labels are absent;
- no retrieval-mode selector is rendered and a search requests hybrid mode;
- GEOscope is rendered before NCBI GEO in each comparison row;
- realistic example queries are available;
- the standalone normalization proof section is absent;
- the MCP endpoint is visible and the copy button writes the canonical URL;
- copy success is announced and clipboard failure leaves manual-copy guidance;
  and
- the MCP panel is separate from the three-stage retrieval workflow.

Run the Vitest suite and the TypeScript/Vite production build. Visually inspect
desktop and mobile layouts, checking paired result alignment, GEOscope
elevation, marquee looping, no horizontal overflow, keyboard focus, and reduced
motion.

## Scope boundaries

- Do not change the marketing API or MCP server protocol.
- Do not add product-specific ChatGPT or Claude installation flows.
- Do not claim that structured extraction or normalization is comprehensive.
- Do not add unrelated navigation, backend search features, or new dependencies.

# Marketing Query Examples Design

## Goal

Replace the marketing site's long, species-specific example searches with
shorter natural-language queries that resemble real researcher requests. The
examples should demonstrate treatment comparison, case/control comparison, and
pathway-level retrieval without asking the search layer to enforce a species.

## Approved query set

1. `breast tumors before and after neoadjuvant chemotherapy`
2. `NASH liver transcriptomes compared with healthy controls`
3. `PI3K signaling in insulin-resistant skeletal muscle`

The first query becomes the live comparison's initial value because the
component initializes from the first example.

## Live relevance validation

The three queries were run against the deployed hybrid search endpoint on
2026-07-13.

- The breast-tumor query returned eight directly relevant GEOscope results. The
  highest-ranked studies explicitly described breast tumor samples collected
  before and after neoadjuvant chemotherapy.
- The NASH query ranked liver transcriptome studies comparing NASH or NAFLD
  with healthy controls at the top. Lower ranks included multiple organisms,
  which is valid because the query intentionally omits a species.
- A longer pathway candidate that also required exercise produced partial
  matches across its concepts. Removing the exercise clause made the top result
  directly match PI3K signaling, skeletal muscle, and insulin resistance.

## Implementation scope

Update the `examples` array in
`frontend/src/components/LiveComparison.tsx`. Update the frontend assertion
that currently locates the first example by its old species-specific wording.

Do not change the placeholder, API contract, query parsing, Elasticsearch
retrieval, MCP server, or result rendering. This is a marketing-copy change;
search correctness remains in the shared MCP/Elasticsearch layer.

## Verification

Run the focused `App.test.tsx` test that covers the example-query button, then
run the full frontend test suite and production build. Confirm that all three
example buttons render and that selecting one updates the search field without
automatically submitting it.

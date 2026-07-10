---
title: Normalization Tests and Assay Hardening Plan
tags: [normalization, assay, testing, plan, v1]
status: implementation-plan
created: 2026-07-10
---

# 44 · Normalization Tests and Assay Hardening Implementation Plan

← [[Home]] · hardens [[22-Ontology-Normalization]] · precedes [[45-Normalized-Filters-and-Facets-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small automated test foundation and remove the known false-positive
assay behavior where microscopy magnification or chromium exposure is labeled
`10x Chromium`.

**Architecture:** Keep normalization pure and deterministic. Move reusable assay
detection into one focused module shared by normalization and the in-memory search
harness; retain `normalize.py` as orchestration and field aggregation. Tests cover
positive, negative, and status behavior without requiring Postgres or model files.

**Tech Stack:** Python 3.11+, stdlib `re`, pytest, existing `uv` project.

## Global Constraints

- **v1:** harden existing organism, sex, and assay behavior only.
- Do not add EFO grounding, tissue candidate generation, or an LLM dependency.
- `10x` or `chromium` alone is insufficient evidence for `10x Chromium`.
- Preserve the existing `map_assay(type_text, free_text)` return contract.
- The in-memory retrieval display hint and normalizer must use the same assay rules.
- Refresh only the three persisted assay columns; do not reload raw data, change
  organism/sex columns, or re-embed documents.
- Every behavior change starts with a failing test and ends with a focused commit.

---

## Current state

- No committed test suite or pytest dependency exists.
- `normalize.py` uses `r"10x|chromium|10 ?x genomics"`, so both `10X
  magnification` and `hexavalent chromium exposure` become `10x Chromium`.
- `search_test.py` has a second, independent broad `SC_TECH` regex containing the
  same terms, so evaluation hints repeat the false-positive behavior.
- The pure normalization functions are already easy to test without a database.

## File structure

| Path | Responsibility |
|---|---|
| `src/geo_index/assay_rules.py` | Canonical fine-assay and single-cell detection rules |
| `src/geo_index/normalize.py` | Coarse assay mapping and row-level normalization orchestration |
| `src/geo_index/search_test.py` | In-memory retrieval display harness using shared rules |
| `tests/test_normalize.py` | Baseline tests for pure normalization behavior |
| `tests/test_assay_rules.py` | Positive/negative assay regression tests |
| `pyproject.toml` | pytest development dependency and test configuration |

### Task 1: Establish the test runner and baseline normalization fixtures

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Consumes: existing pure functions from `geo_index.normalize`.
- Produces: `uv run pytest` as the project test command.

- [ ] **Step 1: Add pytest as a development dependency**

Run:

```bash
uv add --dev pytest
```

Expected: `pyproject.toml` gains a dev dependency group and `uv.lock` is updated.

- [ ] **Step 2: Add pytest configuration**

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 3: Create baseline pure-function tests**

Create `tests/test_normalize.py`:

```python
from geo_index.normalize import map_organisms, map_sex_value, normalize_row


def test_map_organisms_maps_human_and_mouse() -> None:
    ids, status = map_organisms("Homo sapiens, Mus musculus")
    assert ids == ["NCBITaxon:9606", "NCBITaxon:10090"]
    assert status == "mapped"


def test_map_sex_rejects_numeric_study_code() -> None:
    ids, reason, confidence = map_sex_value("1")
    assert ids == []
    assert reason == "numeric_code"
    assert confidence == 0.0


def test_normalize_row_keeps_absent_distinct_from_unmapped() -> None:
    result = normalize_row(
        {
            "organisms": "Homo sapiens",
            "characteristics": "",
            "title": "",
            "summary": "",
            "overall_design": "",
            "type": "Expression profiling by high throughput sequencing",
        }
    )
    assert result["organism_status"] == "mapped"
    assert result["sex_status"] == "absent"
    assert result["tissue_status"] == "absent"
```

- [ ] **Step 4: Run the baseline suite**

Run:

```bash
uv run pytest tests/test_normalize.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit the test foundation**

```bash
git add pyproject.toml uv.lock tests/test_normalize.py
git commit -m "test: add normalization test foundation"
```

### Task 2: Capture the assay false positives before fixing them

**Files:**
- Create: `tests/test_assay_rules.py`

**Interfaces:**
- Consumes: existing `map_assay(type_text, free_text)`.
- Produces: failing regression tests for the two measured false-positive classes.

- [ ] **Step 1: Write negative and positive 10x tests**

Create `tests/test_assay_rules.py`:

```python
import pytest

from geo_index.normalize import map_assay


@pytest.mark.parametrize(
    "text",
    [
        "Images were acquired at 10X magnification.",
        "Effects of hexavalent chromium exposure in fish liver.",
        "Cells were treated with chromium chloride.",
    ],
)
def test_non_assay_10x_and_chromium_are_not_10x_genomics(text: str) -> None:
    _, labels, _ = map_assay("", text)
    assert "10x Chromium" not in labels


@pytest.mark.parametrize(
    "text",
    [
        "10x Genomics Chromium Single Cell 3' Gene Expression",
        "Libraries were prepared on the Chromium Controller.",
        "10x Chromium 5' v2 chemistry",
    ],
)
def test_contextual_10x_genomics_phrases_are_detected(text: str) -> None:
    _, labels, status = map_assay("", text)
    assert "10x Chromium" in labels
    assert status == "detailed"
```

- [ ] **Step 2: Verify the negative tests fail for the current bug**

Run:

```bash
uv run pytest tests/test_assay_rules.py::test_non_assay_10x_and_chromium_are_not_10x_genomics -v
```

Expected: all three cases fail because the current regex emits `10x Chromium`.

- [ ] **Step 3: Commit only after the implementation task turns the tests green**

Do not commit a permanently red suite; continue directly to Task 3.

### Task 3: Centralize contextual assay detection

**Files:**
- Create: `src/geo_index/assay_rules.py`
- Modify: `src/geo_index/normalize.py`
- Test: `tests/test_assay_rules.py`

**Interfaces:**
- Produces: `detect_fine_assays(text: str) -> list[str]`.
- Produces: `has_single_cell_technology(text: str) -> bool`.
- Preserves: `normalize.map_assay(type_text, free_text)`.

- [ ] **Step 1: Implement focused contextual rules**

Create `src/geo_index/assay_rules.py`:

```python
from __future__ import annotations

import re


_TEN_X_CHROMIUM = re.compile(
    r"\b(?:"
    r"10\s*x\s+genomics|"
    r"10x\s+chromium|"
    r"chromium\s+(?:controller|single[- ]cell|next\s+gem|3[\'’′]?|5[\'’′]?|v[234]\b)"
    r")",
    re.I,
)

_FINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"drop-?seq", re.I), "Drop-seq"),
    (re.compile(r"smart-?seq ?2|smartseq2", re.I), "Smart-seq2"),
    (re.compile(r"split-?seq", re.I), "SPLiT-seq"),
    (re.compile(r"cel-?seq", re.I), "CEL-seq"),
    (re.compile(r"\bscrna|single[ -]cell rna", re.I), "scRNA-seq"),
    (re.compile(r"\bsnrna|single[ -]nucleus", re.I), "snRNA-seq"),
    (re.compile(r"chip-?seq", re.I), "ChIP-seq"),
    (re.compile(r"cut ?& ?run|cut and run", re.I), "CUT&RUN"),
    (re.compile(r"cut ?& ?tag|cut and tag", re.I), "CUT&Tag"),
    (re.compile(r"atac-?seq", re.I), "ATAC-seq"),
    (re.compile(r"bisulfite|wgbs|\brrbs\b|methyl-?seq", re.I), "bisulfite-seq"),
    (re.compile(r"ribo-?seq|ribosome profiling", re.I), "Ribo-seq"),
    (re.compile(r"clip-?seq|hits-?clip|par-?clip|iclip", re.I), "CLIP-seq"),
    (re.compile(r"\bhi-?c\b", re.I), "Hi-C"),
    (re.compile(r"visium|slide-?seq|merfish|spatial transcriptom", re.I), "spatial transcriptomics"),
    (re.compile(r"nanopore", re.I), "Nanopore"),
    (re.compile(r"pacbio|\bsmrt\b", re.I), "PacBio"),
)

_SINGLE_CELL_LABELS = {
    "10x Chromium",
    "Drop-seq",
    "Smart-seq2",
    "SPLiT-seq",
    "CEL-seq",
    "scRNA-seq",
    "snRNA-seq",
}


def detect_fine_assays(text: str) -> list[str]:
    labels: list[str] = []
    if _TEN_X_CHROMIUM.search(text):
        labels.append("10x Chromium")
    for pattern, label in _FINE_PATTERNS:
        if pattern.search(text) and label not in labels:
            labels.append(label)
    return labels


def has_single_cell_technology(text: str) -> bool:
    return bool(_SINGLE_CELL_LABELS.intersection(detect_fine_assays(text)))
```

- [ ] **Step 2: Make `map_assay` use the shared detector**

In `src/geo_index/normalize.py`, import:

```python
from .assay_rules import detect_fine_assays
```

Remove `_ASSAY_FINE` and replace its loop in `map_assay` with:

```python
fine = detect_fine_assays(free_text) if free_text else []
```

- [ ] **Step 3: Run the assay tests**

```bash
uv run pytest tests/test_assay_rules.py -v
```

Expected: 6 tests pass.

- [ ] **Step 4: Run all normalization tests**

```bash
uv run pytest tests/test_normalize.py tests/test_assay_rules.py -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Commit the assay fix**

```bash
git add src/geo_index/assay_rules.py src/geo_index/normalize.py tests/test_assay_rules.py
git commit -m "fix: require context for 10x assay detection"
```

### Task 4: Remove the duplicate search-harness oracle

**Files:**
- Modify: `src/geo_index/search_test.py`
- Modify: `tests/test_assay_rules.py`

**Interfaces:**
- Consumes: `has_single_cell_technology(text)` from Task 3.
- Removes: the duplicate `SC_TECH` regex.

- [ ] **Step 1: Add a shared-oracle regression test**

Append to `tests/test_assay_rules.py`:

```python
from geo_index.assay_rules import has_single_cell_technology


def test_single_cell_hint_uses_contextual_rules() -> None:
    assert has_single_cell_technology("10x Genomics Chromium libraries") is True
    assert has_single_cell_technology("10X magnification of chromium-treated fish") is False
```

- [ ] **Step 2: Replace `SC_TECH` in the harness**

In `src/geo_index/search_test.py`:

```python
from geo_index.assay_rules import has_single_cell_technology
```

Delete `import re`, delete the local `SC_TECH` constant, and change the display
record to:

```python
"sc_hint": has_single_cell_technology(blob),
```

- [ ] **Step 3: Run the focused and full suites**

```bash
uv run pytest tests/test_assay_rules.py -v
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Run the existing normalization demo**

```bash
uv run geo-normalize demo
```

Expected: the command exits 0 and prints the existing field examples.

- [ ] **Step 5: Commit the shared rule path**

```bash
git add src/geo_index/search_test.py tests/test_assay_rules.py
git commit -m "refactor: share assay detection with search harness"
```

### Task 5: Refresh only the persisted assay columns

**Files:**
- Modify: `src/geo_index/normalize.py`
- Modify: `tests/test_assay_rules.py`
- Modify: `wiki/42-Build-Log.md`

**Interfaces:**
- Produces: `normalize_assay_fields(row: dict) -> dict[str, object]`.
- Produces: `refresh_assays(limit: int | None = None, batch: int = 5000) -> int`.
- Produces CLI command: `geo-normalize assay-refresh [--limit N]`.

- [ ] **Step 1: Add a pure persisted-field regression test**

Append to `tests/test_assay_rules.py`:

```python
from geo_index.normalize import normalize_assay_fields


def test_normalize_assay_fields_returns_only_persisted_assay_columns() -> None:
    result = normalize_assay_fields(
        {
            "title": "Chromium exposure at 10X magnification",
            "summary": "",
            "overall_design": "",
            "type": "Expression profiling by high throughput sequencing",
        }
    )
    assert result == {
        "assay_categories": ["expression (seq)"],
        "assay_labels": [],
        "assay_status": "category",
    }
```

- [ ] **Step 2: Run the focused test and verify it fails**

```bash
uv run pytest tests/test_assay_rules.py::test_normalize_assay_fields_returns_only_persisted_assay_columns -v
```

Expected: collection fails because `normalize_assay_fields` is not defined.

- [ ] **Step 3: Extract the pure helper and reuse it in `normalize_row()`**

Add above `normalize_row()` in `src/geo_index/normalize.py`:

```python
def normalize_assay_fields(row: dict) -> dict[str, object]:
    free_text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "summary", "overall_design")
    )
    categories, labels, status = map_assay(
        row.get("type") or "",
        f"{row.get('type') or ''} {free_text}",
    )
    return {
        "assay_categories": categories,
        "assay_labels": labels,
        "assay_status": status,
    }
```

Replace the existing assay block in `normalize_row()` with:

```python
out.update(normalize_assay_fields(row))
```

- [ ] **Step 4: Add the targeted database refresh**

Add this function beside `run()` in `src/geo_index/normalize.py`:

```python
def refresh_assays(limit: int | None = None, batch: int = 5000) -> int:
    """Recompute only assay_categories, assay_labels, and assay_status."""
    import time

    migrate()
    read = _connect()
    write = _connect()
    n = 0
    started = time.time()
    update_sql = (
        "UPDATE series SET assay_categories=%s, assay_labels=%s, "
        "assay_status=%s WHERE id=%s"
    )
    try:
        with read.cursor(name="assay_refresh_scan") as scan, write.cursor() as cur:
            scan.itersize = batch
            sql = (
                "SELECT id, title, summary, overall_design, type "
                "FROM series ORDER BY id"
            )
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            scan.execute(sql)
            pending: list[tuple[object, ...]] = []
            for sid, title, summary, design, type_text in scan:
                fields = normalize_assay_fields(
                    {
                        "title": title,
                        "summary": summary,
                        "overall_design": design,
                        "type": type_text,
                    }
                )
                pending.append(
                    (
                        fields["assay_categories"] or None,
                        fields["assay_labels"] or None,
                        fields["assay_status"],
                        sid,
                    )
                )
                n += 1
                if len(pending) >= batch:
                    cur.executemany(update_sql, pending)
                    write.commit()
                    pending.clear()
            if pending:
                cur.executemany(update_sql, pending)
                write.commit()
    finally:
        read.close()
        write.close()
    print(f"refreshed assay fields for {n:,} rows in {time.time() - started:.0f}s")
    return n
```

Add the parser/dispatch branches in `main()`:

```python
    ap = sub.add_parser("assay-refresh")
    ap.add_argument("--limit", type=int, default=None)
```

```python
    if a.cmd == "assay-refresh":
        return refresh_assays(limit=a.limit)
```

- [ ] **Step 5: Run the offline suite**

```bash
uv run pytest tests/test_assay_rules.py tests/test_normalize.py -v
uv run pytest -v
```

Expected: all tests pass without connecting to Postgres.

- [ ] **Step 6: Refresh the live assay arrays once**

```bash
uv run geo-normalize assay-refresh
uv run geo-normalize report
```

Expected: exactly 222,961 rows are refreshed in the 2026-07-10 corpus; organism,
sex, embeddings, and raw columns are untouched. If the corpus row count has
intentionally changed, the refreshed count must equal `SELECT count(*) FROM
series` at the start of the run.

- [ ] **Step 7: Record before/after evidence and commit**

In `wiki/42-Build-Log.md`, record the old and new `10x Chromium` counts, the
three negative examples from Task 2, total refreshed rows, elapsed time, and the
test command. Then commit:

```bash
git add src/geo_index/normalize.py tests/test_assay_rules.py wiki/42-Build-Log.md
git commit -m "fix: refresh hardened assay labels"
```

## Definition of done

- `uv run pytest -v` passes.
- The two known false-positive classes do not produce `10x Chromium`.
- Contextual 10x Genomics phrases still produce `10x Chromium`.
- Normalization and the in-memory display harness share one assay-rule module.
- No Postgres, model download, or external API is required by the test suite.
- The live database's three assay columns have been refreshed without reloading
  raw data, recomputing organism/sex, or re-embedding.
- Track 2 can rely on assay facets without knowingly propagating the broad
  `10x|chromium` bug.

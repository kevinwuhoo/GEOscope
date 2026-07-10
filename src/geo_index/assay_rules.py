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
    (
        re.compile(r"visium|slide-?seq|merfish|spatial transcriptom", re.I),
        "spatial transcriptomics",
    ),
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

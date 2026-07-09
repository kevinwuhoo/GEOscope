"""Tier-1/2 ontology normalization across the fields the data makes tractable.

The cheap, high-precision tiers of the cascade in ``wiki/22-Ontology-Normalization.md``
— hand rules + curated exact-lookup — applied to every field a compact curated
head-of-distribution table can reach without tier-3 similarity or tier-4 LLM
extraction. It reads the already-loaded ``series`` table (see ``pg_hybrid.py``),
maps each field, and writes back ontology-ID (or controlled-label) arrays plus a
per-field *status*.

Three statuses are kept distinct on purpose — they look identical if you only
count IDs, and conflating them makes coverage lie:

    absent    — the field was never reported for this series
    unmapped  — a value was reported but nothing in the cascade claimed it
    mapped    — at least one value grounded to a real ontology ID / label

Field → ontology (per the committed CELLxGENE schema):
    organism   → NCBITaxon      sex        → PATO
    tissue     → UBERON         cell_type  → Cell Ontology (CL)
    disease    → MONDO (+PATO 'normal')     ethnicity → HANCESTRO
    cell_line  → Cellosaurus (CVCL)
    assay      → controlled labels (EFO grounding deferred — it needs extraction)
    dev_stage  → controlled coarse labels (HsapDv/MmusDv grounding deferred)
    age        → numeric normalization (not an ontology)

Two data-driven design choices this spike proved out (see the wiki):
  * **Value-driven, with an explicit reject path.** Keys are polluted with the
    wrong concept (``sex: C57BL/6``, ``dev_stage: IV`` = tumor stage, ``tissue:
    breast cancer``). We validate each *value* and reject the rest to ``unmapped``.
  * **Never mint an ontology ID we're unsure of.** Curated tables hold only IDs
    verified against OLS4/Cellosaurus; the long tail stays ``unmapped`` for the
    v2 tier-2 OLS/OAK lookup + tier-3 similarity to reach.

⚠️ The curated ID tables are a **prototype head** — verify against OLS4 before
production; tier-2 (OLS/OAK exact lookup) should replace the hardcoding.

Usage:
    uv run geo-normalize migrate          # add columns (idempotent)
    uv run geo-normalize run              # map + write back all rows
    uv run geo-normalize run --limit 5000
    uv run geo-normalize report           # coverage stats over the table
    uv run geo-normalize demo             # show mapping on tricky real values
"""
from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict

DSN = os.environ.get("GEO_PG_DSN", "postgresql://postgres:geo@localhost:5433/geo")

# ---------------------------------------------------------------------------
# characteristics parsing
# ---------------------------------------------------------------------------
# The stored blob is samples joined by " | ", tag fields within a sample by ";",
# each tag "key: value". Curators use several spellings per concept, so map keys
# onto a canonical field. (Values are folded per field; a series *contains* them.)
KEY_SYNONYMS = {
    "sex": "sex", "gender": "sex",
    "tissue": "tissue", "organ/tissue": "tissue", "organ": "tissue",
    "tissue type": "tissue", "source tissue": "tissue", "anatomical site": "tissue",
    "tissue source": "tissue", "body site": "tissue",
    "cell type": "cell_type", "cell-type": "cell_type", "celltype": "cell_type",
    "cell types": "cell_type", "cell subtype": "cell_type",
    "cell line": "cell_line", "cell-line": "cell_line", "cell line name": "cell_line",
    "cell_line": "cell_line",
    "disease": "disease", "disease state": "disease", "diagnosis": "disease",
    "disease status": "disease", "tumor type": "disease", "cancer type": "disease",
    "disease type": "disease",
    "ethnicity": "ethnicity", "race": "ethnicity", "ancestry": "ethnicity",
    "ethnic group": "ethnicity", "race/ethnicity": "ethnicity",
    "developmental stage": "dev_stage", "development stage": "dev_stage",
    "stage": "dev_stage", "life stage": "dev_stage", "age stage": "dev_stage",
    "age": "age", "age (years)": "age", "donor age": "age", "patient age": "age",
    "age(years)": "age",
}


def parse_characteristics(blob: str | None) -> dict[str, set[str]]:
    """Parse an aggregated characteristics blob into {canonical_field: {values}}."""
    out: dict[str, set[str]] = defaultdict(set)
    if not blob:
        return out
    for sample in blob.split("|"):
        for field in sample.split(";"):
            key, sep, value = field.partition(":")
            if not sep:
                continue
            canon = KEY_SYNONYMS.get(key.strip().lower())
            value = value.strip()
            if canon and value:
                out[canon].add(value)
    return out


def _norm_token(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip().strip("\"'").rstrip(" .").lower())


# Shared "explicitly unknown" tokens (distinct from a value we failed to map).
_UNKNOWN = {
    "", "unknown", "unk", "n/a", "na", "not applicable", "not collected",
    "not available", "not determined", "not recorded", "not reported",
    "undetermined", "unspecified", "none", "not known", "missing", "nd",
    "?", "--", "-", ".", "other",
}


def _map_curated(
    values: set[str], table: dict[str, str], *, unknown=_UNKNOWN
) -> tuple[list[str], str, list[str]]:
    """Exact curated lookup with paren-stripping retry. Value-driven: a value
    that isn't in the table (e.g. a disease term in the tissue field) simply
    misses → the field lands in ``unmapped``, never a wrong ID."""
    if not values:
        return [], "absent", []
    ids: list[str] = []
    reasons: list[str] = []
    saw_unknown = False
    for v in values:
        t = _norm_token(v)
        if t in unknown:
            saw_unknown = True
            reasons.append("unknown")
            continue
        hit = table.get(t)
        if not hit and "(" in t:
            hit = table.get(re.sub(r"\s*\(.*?\)\s*", " ", t).strip())
        if hit:
            if hit not in ids:
                ids.append(hit)
            reasons.append("exact")
        else:
            reasons.append("miss")
    if ids:
        return ids, "mapped", reasons
    if saw_unknown:
        return [], "unknown", reasons
    return [], "unmapped", reasons


# ---------------------------------------------------------------------------
# Sex → PATO (tier-1 hand rules, value-driven with rejection)
# ---------------------------------------------------------------------------
PATO_MALE = "PATO:0000384"
PATO_FEMALE = "PATO:0000383"
PATO_HERMAPHRODITE = "PATO:0001340"
PATO_NORMAL = "PATO:0000461"

_SEX_EXACT = {
    "m": PATO_MALE, "male": PATO_MALE, "males": PATO_MALE, "man": PATO_MALE,
    "men": PATO_MALE, "boy": PATO_MALE, "xy": PATO_MALE, "♂": PATO_MALE,
    "f": PATO_FEMALE, "female": PATO_FEMALE, "females": PATO_FEMALE,
    "woman": PATO_FEMALE, "women": PATO_FEMALE, "girl": PATO_FEMALE,
    "fem": PATO_FEMALE, "xx": PATO_FEMALE, "♀": PATO_FEMALE,
    "hermaphrodite": PATO_HERMAPHRODITE, "hermaphrodites": PATO_HERMAPHRODITE,
    "herm": PATO_HERMAPHRODITE,
}
_SEX_MIXED = {
    "both", "both sexes", "mixed", "mix", "mixed sex", "male and female",
    "female and male", "male & female", "m/f", "f/m", "m+f", "mf",
    "male/female", "female/male", "pooled", "pool",
}
_SEX_FUZZY = {"male": PATO_MALE, "female": PATO_FEMALE}
_DEV_STAGE_WORDS = {"adult", "fetal", "fetus", "embryo", "embryonic", "larva",
                    "larval", "pupa", "juvenile", "neonate", "newborn"}


def _levenshtein_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b = b, a
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def map_sex_value(raw: str) -> tuple[list[str], str, float]:
    """Map one raw sex value → (pato_ids, reason, confidence)."""
    t = _norm_token(raw)
    if t in _UNKNOWN:
        return [], "unknown", 0.0
    if t in _SEX_EXACT:
        return [_SEX_EXACT[t]], "exact", 1.0
    if t in _SEX_MIXED:
        return [PATO_MALE, PATO_FEMALE], "mixed", 0.9
    if t in _DEV_STAGE_WORDS:
        return [], "leaked_stage", 0.0
    if "/" in t or re.search(r"bl/?6|balb|c57|c3h|129|cd-?1|nod|wistar|sprague", t):
        return [], "leaked_strain", 0.0
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return [], "numeric_code", 0.0  # ambiguous code — never guess sex from a number
    if re.search(r"\d", t):
        return [], "leaked_age", 0.0
    if len(t) >= 4 and t.isalpha():
        for target, pid in _SEX_FUZZY.items():
            if _levenshtein_le1(t, target):
                return [pid], "fuzzy", 0.75
    return [], "unrecognized", 0.0


def map_sex_field(values: set[str]) -> tuple[list[str], str, list[str]]:
    if not values:
        return [], "absent", []
    ids: set[str] = set()
    reasons: list[str] = []
    saw_unknown = False
    for v in values:
        vids, reason, _ = map_sex_value(v)
        reasons.append(reason)
        if vids:
            ids.update(vids)
        elif reason == "unknown":
            saw_unknown = True
    if ids:
        return sorted(ids), "mapped", reasons
    if saw_unknown:
        return [], "unknown", reasons
    return [], "unmapped", reasons


# ---------------------------------------------------------------------------
# Organism → NCBITaxon (curated head)
# ---------------------------------------------------------------------------
NCBITAXON = {
    "homo sapiens": "NCBITaxon:9606", "mus musculus": "NCBITaxon:10090",
    "rattus norvegicus": "NCBITaxon:10116", "arabidopsis thaliana": "NCBITaxon:3702",
    "drosophila melanogaster": "NCBITaxon:7227", "saccharomyces cerevisiae": "NCBITaxon:4932",
    "caenorhabditis elegans": "NCBITaxon:6239", "danio rerio": "NCBITaxon:7955",
    "sus scrofa": "NCBITaxon:9823", "bos taurus": "NCBITaxon:9913",
    "oryza sativa": "NCBITaxon:4530", "gallus gallus": "NCBITaxon:9031",
    "escherichia coli": "NCBITaxon:562", "schizosaccharomyces pombe": "NCBITaxon:4896",
    "zea mays": "NCBITaxon:4577", "macaca mulatta": "NCBITaxon:9544",
    "canis lupus familiaris": "NCBITaxon:9615", "pan troglodytes": "NCBITaxon:9598",
    "xenopus laevis": "NCBITaxon:8355", "xenopus tropicalis": "NCBITaxon:8364",
    "ovis aries": "NCBITaxon:9940", "equus caballus": "NCBITaxon:9796",
    "oryctolagus cuniculus": "NCBITaxon:9986", "plasmodium falciparum": "NCBITaxon:5833",
    "mycobacterium tuberculosis": "NCBITaxon:1773", "pseudomonas aeruginosa": "NCBITaxon:287",
    "bacillus subtilis": "NCBITaxon:1423", "solanum lycopersicum": "NCBITaxon:4081",
    "solanum tuberosum": "NCBITaxon:4113", "glycine max": "NCBITaxon:3847",
    "triticum aestivum": "NCBITaxon:4565", "hordeum vulgare": "NCBITaxon:4513",
    "medicago truncatula": "NCBITaxon:3880", "nicotiana tabacum": "NCBITaxon:4097",
    "chlamydomonas reinhardtii": "NCBITaxon:3055", "dictyostelium discoideum": "NCBITaxon:44689",
    "candida albicans": "NCBITaxon:5476", "macaca fascicularis": "NCBITaxon:9541",
    "cricetulus griseus": "NCBITaxon:10029", "mesocricetus auratus": "NCBITaxon:10036",
    "staphylococcus aureus": "NCBITaxon:1280", "vitis vinifera": "NCBITaxon:29760",
    "capra hircus": "NCBITaxon:9925", "apis mellifera": "NCBITaxon:7460",
    "gossypium hirsutum": "NCBITaxon:3635", "neurospora crassa": "NCBITaxon:5141",
}


def map_organisms(raw: str | None) -> tuple[list[str], str]:
    """Map the comma/semicolon-joined ``organisms`` column → (ids, status)."""
    if not raw or not raw.strip():
        return [], "absent"
    names = [n.strip() for n in re.split(r"[,;]", raw) if n.strip()]
    if not names:
        return [], "absent"
    ids: list[str] = []
    for n in names:
        tid = NCBITAXON.get(n.lower())
        if tid and tid not in ids:
            ids.append(tid)
    return (ids, "mapped") if ids else ([], "unmapped")


# ---------------------------------------------------------------------------
# Tissue → UBERON (curated head; verified sample against OLS4)
# ---------------------------------------------------------------------------
UBERON = {
    "liver": "UBERON:0002107",
    "blood": "UBERON:0000178", "whole blood": "UBERON:0000178",
    "peripheral blood": "UBERON:0000178",
    "bone marrow": "UBERON:0002371", "brain": "UBERON:0000955",
    "lung": "UBERON:0002048", "spleen": "UBERON:0002106", "skin": "UBERON:0002097",
    "kidney": "UBERON:0002113", "heart": "UBERON:0000948",
    "hippocampus": "UBERON:0002421", "colon": "UBERON:0001155",
    "breast": "UBERON:0001911", "mammary gland": "UBERON:0001911",
    "plasma": "UBERON:0001969", "blood plasma": "UBERON:0001969",
    "serum": "UBERON:0001977", "blood serum": "UBERON:0001977",
    "cerebellum": "UBERON:0002037", "ovary": "UBERON:0000992",
    "placenta": "UBERON:0001987", "prostate": "UBERON:0002367",
    "prostate gland": "UBERON:0002367", "skeletal muscle": "UBERON:0001134",
    "cerebral cortex": "UBERON:0000956", "cortex": "UBERON:0000956",
    "muscle": "UBERON:0002385", "pancreas": "UBERON:0001264",
    "stomach": "UBERON:0000945", "intestine": "UBERON:0000160",
    "small intestine": "UBERON:0002108", "large intestine": "UBERON:0000059",
    "testis": "UBERON:0000473", "testes": "UBERON:0000473",
    "adipose": "UBERON:0001013", "adipose tissue": "UBERON:0001013", "fat": "UBERON:0001013",
    "lymph node": "UBERON:0000029", "thymus": "UBERON:0002370",
    "retina": "UBERON:0000966", "uterus": "UBERON:0000995",
    "esophagus": "UBERON:0001043", "bladder": "UBERON:0001255",
    "urinary bladder": "UBERON:0001255", "embryo": "UBERON:0000922",
}


# ---------------------------------------------------------------------------
# Cell type → Cell Ontology (curated head; verified sample)
# ---------------------------------------------------------------------------
CL = {
    "macrophage": "CL:0000235", "macrophages": "CL:0000235",
    "monocyte": "CL:0000576", "monocytes": "CL:0000576",
    "cd14+ monocytes": "CL:0000576",
    "fibroblast": "CL:0000057", "fibroblasts": "CL:0000057",
    "mouse embryonic fibroblasts": "CL:0000057",
    "t cell": "CL:0000084", "t cells": "CL:0000084",
    "cd4+ t cells": "CL:0000624", "cd4 t cells": "CL:0000624", "cd4+ t cell": "CL:0000624",
    "cd8+ t cells": "CL:0000625", "cd8 t cells": "CL:0000625", "cd8+ t cell": "CL:0000625",
    "b cell": "CL:0000236", "b cells": "CL:0000236",
    "neuron": "CL:0000540", "neurons": "CL:0000540",
    "microglia": "CL:0000129", "microglial cell": "CL:0000129",
    "epithelial": "CL:0000066", "epithelial cell": "CL:0000066", "epithelial cells": "CL:0000066",
    "embryonic stem cell": "CL:0002322", "embryonic stem cells": "CL:0002322",
    "mouse embryonic stem cells": "CL:0002322", "esc": "CL:0002322", "mesc": "CL:0002322",
    "leukocyte": "CL:0000738", "leukocytes": "CL:0000738",
    "hepatocyte": "CL:0000182", "keratinocyte": "CL:0000312",
    "nk cell": "CL:0000623", "natural killer cell": "CL:0000623",
    "dendritic cell": "CL:0000451",
}


# ---------------------------------------------------------------------------
# Cell line → Cellosaurus (curated head; famous lines, verified sample)
# ---------------------------------------------------------------------------
CELLOSAURUS = {
    "mcf7": "CVCL_0031", "mcf-7": "CVCL_0031", "hela": "CVCL_0030",
    "k562": "CVCL_0004", "hct116": "CVCL_0291", "a549": "CVCL_0023",
    "hek293t": "CVCL_0063", "293t": "CVCL_0063", "hek293": "CVCL_0045", "293": "CVCL_0045",
    "hepg2": "CVCL_0027", "lncap": "CVCL_0395", "mda-mb-231": "CVCL_0062",
    "u2os": "CVCL_0042", "thp-1": "CVCL_0006", "thp1": "CVCL_0006",
    "mcf10a": "CVCL_0598", "mcf-10a": "CVCL_0598", "t47d": "CVCL_0553", "t-47d": "CVCL_0553",
    "c2c12": "CVCL_0188", "imr90": "CVCL_0347", "imr-90": "CVCL_0347",
    "jurkat": "CVCL_0065", "a375": "CVCL_0132", "gm12878": "CVCL_7526",
    "3t3-l1": "CVCL_0123", "huh7": "CVCL_0336", "huh-7": "CVCL_0336",
    "h9": "CVCL_9773", "wa09": "CVCL_9773", "h1": "CVCL_9771", "wa01": "CVCL_9771",
}


# ---------------------------------------------------------------------------
# Disease → MONDO (+ PATO 'normal'); ambiguous 2-letter abbrevs deliberately
# omitted (ad/uc/cd/ms/ra collide across diseases). Curated head, verified sample.
# ---------------------------------------------------------------------------
MONDO = {
    "healthy": PATO_NORMAL, "normal": PATO_NORMAL, "control": PATO_NORMAL,
    "healthy control": PATO_NORMAL, "normal control": PATO_NORMAL, "control group": PATO_NORMAL,
    "hepatocellular carcinoma": "MONDO:0007256", "hcc": "MONDO:0007256",
    "chronic myelogenous leukemia": "MONDO:0011996", "cml": "MONDO:0011996",
    "chronic myeloid leukemia": "MONDO:0011996",
    "breast cancer": "MONDO:0007254", "breast carcinoma": "MONDO:0007254",
    "acute myeloid leukemia": "MONDO:0018874", "acute myelogenous leukemia": "MONDO:0018874",
    "aml": "MONDO:0018874",
    "prostate cancer": "MONDO:0008315", "multiple myeloma": "MONDO:0009693",
    "rheumatoid arthritis": "MONDO:0008383", "multiple sclerosis": "MONDO:0005301",
    "covid-19": "MONDO:0100096", "covid19": "MONDO:0100096",
    "crohn's disease": "MONDO:0005011", "crohns disease": "MONDO:0005011",
    "lung cancer": "MONDO:0008903", "glioblastoma": "MONDO:0018177",
    "colorectal cancer": "MONDO:0005575", "asthma": "MONDO:0004979",
    "ulcerative colitis": "MONDO:0005101", "obesity": "MONDO:0011122", "obese": "MONDO:0011122",
    "type 2 diabetes": "MONDO:0005148", "type 2 diabetes mellitus": "MONDO:0005148",
    "alzheimer's disease": "MONDO:0004975", "alzheimer disease": "MONDO:0004975",
    "melanoma": "MONDO:0005105", "adenocarcinoma": "MONDO:0004970",
    "lung adenocarcinoma": "MONDO:0005097",
}


# ---------------------------------------------------------------------------
# Ethnicity / ancestry → HANCESTRO (human only; curated, verified sample)
# ---------------------------------------------------------------------------
HANCESTRO = {
    "european": "HANCESTRO:0005", "white": "HANCESTRO:0005", "caucasian": "HANCESTRO:0005",
    "cau": "HANCESTRO:0005", "cauc": "HANCESTRO:0005", "eur": "HANCESTRO:0005",
    "w": "HANCESTRO:0005", "european american": "HANCESTRO:0005",
    "european-american": "HANCESTRO:0005", "western european": "HANCESTRO:0005",
    "african": "HANCESTRO:0010", "afr": "HANCESTRO:0010",
    "black": "HANCESTRO:0016", "african american": "HANCESTRO:0016",
    "african-american": "HANCESTRO:0016", "aa": "HANCESTRO:0016",
    "black or african american": "HANCESTRO:0016",
    "asian": "HANCESTRO:0008",
    "east asian": "HANCESTRO:0009", "chinese": "HANCESTRO:0009", "japanese": "HANCESTRO:0009",
    "korean": "HANCESTRO:0009", "korean-asian": "HANCESTRO:0009", "han chinese": "HANCESTRO:0027",
    "hispanic": "HANCESTRO:0014", "latino": "HANCESTRO:0014",
    "hispanic or latino": "HANCESTRO:0014",
}
# Negations aren't ancestry terms — treat as explicit non-mappable, not a guess.
_ETHNICITY_UNKNOWN = _UNKNOWN | {
    "not hispanic or latino", "non-hispanic", "not_latinx", "not latino",
}


# ---------------------------------------------------------------------------
# Developmental stage → coarse controlled labels (HsapDv/MmusDv grounding = v2).
# Value-driven rejection of tumor stage (I–IV, 1a/2b…) leaking into this key.
# ---------------------------------------------------------------------------
_DEV_MAP = {
    "adult": "adult", "adults": "adult", "young adult": "adult", "aged": "adult",
    "embryo": "embryonic", "embryonic": "embryonic",
    "fetal": "fetal", "fetus": "fetal", "foetal": "fetal",
    "juvenile": "juvenile",
    "neonate": "neonatal", "newborn": "neonatal", "neonatal": "neonatal",
    "larva": "larval", "larval": "larval", "third instar larvae": "larval",
    "3rd instar larvae": "larval",
    "pupa": "pupal", "pupal": "pupal", "pupae": "pupal",
}
_ROMAN_STAGE = re.compile(r"^(i{1,3}|iv|v|vi{1,3})[abc]?$")


def map_dev_stage(values: set[str]) -> tuple[list[str], str, list[str]]:
    if not values:
        return [], "absent", []
    labels: list[str] = []
    reasons: list[str] = []
    saw_unknown = False
    for v in values:
        t = _norm_token(v)
        if t in _UNKNOWN:
            saw_unknown = True
            reasons.append("unknown")
            continue
        lab = _DEV_MAP.get(t)
        if lab is None:
            if re.fullmatch(r"e\d+(\.\d+)?", t):
                lab = "embryonic"
            elif re.fullmatch(r"l[1-4]", t) or "instar" in t:
                lab = "larval"
            elif re.fullmatch(r"p\d+", t):
                lab = "postnatal"
        if lab:
            if lab not in labels:
                labels.append(lab)
            reasons.append("exact")
        elif _ROMAN_STAGE.fullmatch(t) or re.fullmatch(r"\d+[abc]?", t):
            reasons.append("tumor_stage")  # cancer stage in the dev-stage key → reject
        else:
            reasons.append("miss")
    if labels:
        return sorted(labels), "mapped", reasons
    if saw_unknown:
        return [], "unknown", reasons
    return [], "unmapped", reasons


# ---------------------------------------------------------------------------
# Assay → controlled labels. Coarse category from GEO's `type` enum; fine assay
# from free-text keywords. EFO grounding deferred (it needs extraction, tier-4).
# ---------------------------------------------------------------------------
_ASSAY_CATEGORY = [
    (re.compile(r"expression profiling by high throughput sequencing"), "expression (seq)"),
    (re.compile(r"expression profiling by array"), "expression (array)"),
    (re.compile(r"genome binding/occupancy profiling by high throughput sequencing"),
     "genome binding (seq)"),
    (re.compile(r"genome binding/occupancy profiling by genome tiling array"),
     "genome binding (array)"),
    (re.compile(r"non-coding rna profiling by high throughput sequencing"), "ncRNA (seq)"),
    (re.compile(r"non-coding rna profiling by array"), "ncRNA (array)"),
    (re.compile(r"methylation profiling by high throughput sequencing"), "methylation (seq)"),
    (re.compile(r"methylation profiling by (genome tiling )?array"), "methylation (array)"),
    (re.compile(r"genome variation profiling"), "genome variation"),
    (re.compile(r"expression profiling by rt-?pcr"), "expression (RT-PCR)"),
    (re.compile(r"^other$|;\s*other"), "other"),
]
_ASSAY_FINE = [
    (re.compile(r"10x|chromium|10 ?x genomics"), "10x Chromium"),
    (re.compile(r"drop-?seq"), "Drop-seq"),
    (re.compile(r"smart-?seq ?2|smartseq2"), "Smart-seq2"),
    (re.compile(r"split-?seq"), "SPLiT-seq"),
    (re.compile(r"cel-?seq"), "CEL-seq"),
    (re.compile(r"\bscrna|single[ -]cell rna"), "scRNA-seq"),
    (re.compile(r"\bsnrna|single[ -]nucleus"), "snRNA-seq"),
    (re.compile(r"chip-?seq"), "ChIP-seq"),
    (re.compile(r"cut ?& ?run|cut and run"), "CUT&RUN"),
    (re.compile(r"cut ?& ?tag|cut and tag"), "CUT&Tag"),
    (re.compile(r"atac-?seq"), "ATAC-seq"),
    (re.compile(r"bisulfite|wgbs|\brrbs\b|methyl-?seq"), "bisulfite-seq"),
    (re.compile(r"ribo-?seq|ribosome profiling"), "Ribo-seq"),
    (re.compile(r"clip-?seq|hits-?clip|par-?clip|iclip"), "CLIP-seq"),
    (re.compile(r"\bhi-?c\b"), "Hi-C"),
    (re.compile(r"visium|slide-?seq|merfish|spatial transcriptom"), "spatial transcriptomics"),
    (re.compile(r"nanopore"), "Nanopore"),
    (re.compile(r"pacbio|\bsmrt\b"), "PacBio"),
]


def map_assay(type_text: str, free_text: str) -> tuple[list[str], list[str], str]:
    """→ (coarse_categories, fine_labels, status). status: detailed|category|absent."""
    categories: list[str] = []
    if type_text:
        tt = type_text.lower()
        for pat, label in _ASSAY_CATEGORY:
            if pat.search(tt) and label not in categories:
                categories.append(label)
    fine: list[str] = []
    if free_text:
        ft = free_text.lower()
        for pat, label in _ASSAY_FINE:
            if pat.search(ft) and label not in fine:
                fine.append(label)
    if fine:
        return categories, fine, "detailed"
    if categories:
        return categories, [], "category"
    return [], [], "absent"


# ---------------------------------------------------------------------------
# Age → numeric normalization (not an ontology). Extract number + unit.
# ---------------------------------------------------------------------------
_AGE_UNIT = {
    "year": "year", "years": "year", "yr": "year", "yrs": "year", "y": "year",
    "yo": "year", "year-old": "year", "years-old": "year",
    "month": "month", "months": "month", "mo": "month", "mon": "month",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week", "w": "week",
    "day": "day", "days": "day", "d": "day",
    "hour": "hour", "hours": "hour", "hr": "hour", "h": "hour",
}
_AGE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z-]+)?")


def map_age(values: set[str], cap: int = 40) -> tuple[list[str], str]:
    """→ (normalized "N unit" tokens, status). Bare numbers kept unit-less."""
    if not values:
        return [], "absent"
    norm: set[str] = set()
    for v in values:
        t = _norm_token(v)
        m = _AGE_RE.match(t)
        if not m:
            continue
        num, unit = m.group(1), m.group(2)
        if unit:
            u = _AGE_UNIT.get(unit)
            if u:
                norm.add(f"{num} {u}")
        else:
            norm.add(num)  # unit-less; e.g. human years by convention
    if norm:
        return sorted(norm)[:cap], "parsed"
    return [], "unmapped"


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------
# Fields backed by a curated exact-lookup table over characteristics values.
_CURATED_FIELDS = {
    "tissue": UBERON,
    "cell_type": CL,
    "cell_line": CELLOSAURUS,
    "disease": MONDO,
    "ethnicity": HANCESTRO,
}


def _connect():
    import psycopg

    return psycopg.connect(DSN)


def migrate() -> int:
    """Add the normalization columns (idempotent)."""
    cols = []
    for f in ("organism", "sex", "tissue", "cell_type", "cell_line",
              "disease", "ethnicity", "dev_stage"):
        cols.append(f"ADD COLUMN IF NOT EXISTS {f}_ids TEXT[]")
        cols.append(f"ADD COLUMN IF NOT EXISTS {f}_status TEXT")
    cols += [
        "ADD COLUMN IF NOT EXISTS assay_categories TEXT[]",
        "ADD COLUMN IF NOT EXISTS assay_labels TEXT[]",
        "ADD COLUMN IF NOT EXISTS assay_status TEXT",
        "ADD COLUMN IF NOT EXISTS age_norm TEXT[]",
        "ADD COLUMN IF NOT EXISTS age_status TEXT",
    ]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE series\n    " + ",\n    ".join(cols) + ";")
        conn.commit()
    print(f"ensured {len(cols)} normalization columns", flush=True)
    return 0


# dev_stage stores labels in the *_ids column (keeps the schema uniform); status
# still distinguishes absent/unmapped/mapped.
_STATUS_FIELDS = ["organism", "sex", "tissue", "cell_type", "cell_line",
                  "disease", "ethnicity", "dev_stage", "assay", "age"]


def normalize_row(row: dict) -> dict:
    """Pure mapping of one series row → the normalized column values."""
    out: dict = {}
    out["organism_ids"], out["organism_status"] = map_organisms(row.get("organisms"))
    fields = parse_characteristics(row.get("characteristics"))
    out["sex_ids"], out["sex_status"], _ = map_sex_field(fields.get("sex", set()))
    for f, table in _CURATED_FIELDS.items():
        unk = _ETHNICITY_UNKNOWN if f == "ethnicity" else _UNKNOWN
        ids, status, _ = _map_curated(fields.get(f, set()), table, unknown=unk)
        out[f"{f}_ids"], out[f"{f}_status"] = ids, status
    out["dev_stage_ids"], out["dev_stage_status"], _ = map_dev_stage(fields.get("dev_stage", set()))
    free = " ".join(str(row.get(k) or "") for k in ("title", "summary", "overall_design"))
    cats, labels, astatus = map_assay(row.get("type") or "", (row.get("type") or "") + " " + free)
    out["assay_categories"], out["assay_labels"], out["assay_status"] = cats, labels, astatus
    out["age_norm"], out["age_status"] = map_age(fields.get("age", set()))
    return out


_UPDATE_COLS = (
    [f"{f}_ids" for f in ("organism", "sex", "tissue", "cell_type", "cell_line",
                          "disease", "ethnicity", "dev_stage")]
    + [f"{f}_status" for f in ("organism", "sex", "tissue", "cell_type", "cell_line",
                               "disease", "ethnicity", "dev_stage")]
    + ["assay_categories", "assay_labels", "assay_status", "age_norm", "age_status"]
)


def run(limit: int | None = None, batch: int = 5000) -> int:
    """Map every field for every row and write the results back."""
    import time

    migrate()
    read = _connect()
    write = _connect()
    counts: dict[str, int] = defaultdict(int)
    n = 0
    t0 = time.time()
    set_clause = ", ".join(f"{c}=%s" for c in _UPDATE_COLS)
    update_sql = f"UPDATE series SET {set_clause} WHERE id=%s"
    with read.cursor(name="norm_scan") as scan, write.cursor() as wcur:
        scan.itersize = batch
        sql = ("SELECT id, organisms, characteristics, title, summary, "
               "overall_design, type FROM series ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        scan.execute(sql)
        params: list[tuple] = []
        for sid, organisms, characteristics, title, summary, design, type_ in scan:
            o = normalize_row({
                "organisms": organisms, "characteristics": characteristics,
                "title": title, "summary": summary, "overall_design": design, "type": type_,
            })
            row_vals = [o[c] or None for c in _UPDATE_COLS]
            params.append(tuple(row_vals) + (sid,))
            n += 1
            for f in _STATUS_FIELDS:
                if o[f"{f}_status"] in ("mapped", "detailed", "category", "parsed"):
                    counts[f] += 1
            if len(params) >= batch:
                wcur.executemany(update_sql, params)
                write.commit()
                params.clear()
                print(f"  {n:,} rows ({time.time()-t0:.0f}s)", flush=True)
        if params:
            wcur.executemany(update_sql, params)
            write.commit()
    read.close()
    write.close()
    print(f"\ndone: {n:,} rows in {time.time()-t0:.0f}s", flush=True)
    for f in _STATUS_FIELDS:
        print(f"  {f:11} mapped {counts[f]:>8,} ({100*counts[f]/max(n,1):4.0f}%)", flush=True)
    return 0


def report() -> int:
    """Per-field status distribution + a few id spot-counts."""
    with _connect() as conn, conn.cursor() as cur:
        for f in _STATUS_FIELDS:
            cur.execute(
                f"SELECT COALESCE({f}_status,'(null)'), count(*) FROM series "
                f"GROUP BY 1 ORDER BY 2 DESC"
            )
            rows = cur.fetchall()
            total = sum(c for _, c in rows)
            parts = "  ".join(f"{s}={c:,}" for s, c in rows)
            print(f"{f:11} | {parts}", flush=True)

        print("\n=== top mapped ids (sample fields) ===", flush=True)
        for f, lab in [("tissue", "UBERON"), ("disease", "MONDO/PATO"),
                       ("cell_line", "CVCL"), ("ethnicity", "HANCESTRO")]:
            cur.execute(
                f"SELECT unnest({f}_ids) x, count(*) FROM series "
                f"WHERE {f}_ids IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6"
            )
            top = "  ".join(f"{x}({c:,})" for x, c in cur.fetchall())
            print(f"  {f} [{lab}]: {top}", flush=True)

        print("\n=== assay fine labels ===", flush=True)
        cur.execute(
            "SELECT unnest(assay_labels) x, count(*) FROM series "
            "WHERE assay_labels IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
        )
        for x, c in cur.fetchall():
            print(f"  {x:26} {c:>7,}", flush=True)
    return 0


DEMO = {
    "sex": ["female", "Male", "famale", "both", "0", "68M", "C57BL/6", "hermaphrodite"],
    "tissue": ["liver", "Whole Blood", "breast cancer", "PBMC", "leaf", "brain"],
    "cell_type": ["macrophage", "CD4+ T cells", "mESC", "breast cancer cell line"],
    "cell_line": ["HeLa", "MCF-7", "LNCaP", "SomeNovelLine-X"],
    "disease": ["healthy", "hepatocellular carcinoma (HCC)", "AML", "cancer", "CD"],
    "ethnicity": ["Caucasian", "African American", "not hispanic or latino", "Chinese"],
    "dev_stage": ["adult", "E14.5", "III", "L4", "1a"],
    "age": ["56", "8 weeks", "P21", "adult"],
}


def demo() -> int:
    """Show each field's mapper verdict on tricky real values from the data."""
    for field, vals in DEMO.items():
        print(f"\n=== {field} ===", flush=True)
        for v in vals:
            if field == "sex":
                ids, reason, conf = map_sex_value(v)
                out = f"{', '.join(ids) or '(none)':26} {reason} ({conf:.2f})"
            elif field == "dev_stage":
                ids, status, reasons = map_dev_stage({v})
                out = f"{', '.join(ids) or '(none)':26} {status} / {reasons[0]}"
            elif field == "age":
                ids, status = map_age({v})
                out = f"{', '.join(ids) or '(none)':26} {status}"
            else:
                unk = _ETHNICITY_UNKNOWN if field == "ethnicity" else _UNKNOWN
                ids, status, reasons = _map_curated({v}, _CURATED_FIELDS[field], unknown=unk)
                out = f"{', '.join(ids) or '(none)':26} {status} / {reasons[0]}"
            print(f"  {v:32} -> {out}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tier-1/2 ontology normalization.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    rp = sub.add_parser("run")
    rp.add_argument("--limit", type=int, default=None)
    sub.add_parser("report")
    sub.add_parser("demo")
    a = p.parse_args(argv)
    if a.cmd == "migrate":
        return migrate()
    if a.cmd == "run":
        return run(limit=a.limit)
    if a.cmd == "report":
        return report()
    if a.cmd == "demo":
        return demo()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

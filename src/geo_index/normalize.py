"""Tier-1/2 ontology normalization spike: organism → NCBITaxon, sex → PATO.

The first slice of the cascade in ``wiki/22-Ontology-Normalization.md``: the
cheap, high-precision tiers (hand rules + exact lookup) for the two fields the
data says are tractable that way. It reads the already-loaded ``series`` table
(see ``pg_hybrid.py``), maps two fields, and writes back ontology-ID arrays plus
a per-field *status* so facets and eval can tell three things apart that look
identical if you only count IDs:

    absent    — the field was never reported for this series
    unmapped  — a value was reported but nothing in the cascade claimed it
    mapped    — at least one value grounded to a real ontology ID

That absent/unmapped split is deliberate: ~80% of series simply don't report
sex, so lumping "not reported" into "we failed" would make coverage look far
worse than it is.

Two data-driven design choices this spike exists to prove out (see the wiki):
  * **Value-driven, not key-driven, for sex.** The ``sex:`` key is polluted with
    strain (``sex: C57BL/6``), age (``sex: 68M``), stage (``sex: adult``), and
    bare numeric codes (``sex: 0/1/2/…``). We validate each *value* against the
    sex value-space and reject the rest to ``unmapped`` with a reason — we never
    trust the key alone, and we never guess a PATO ID from an ambiguous number.
  * **Both ``sex`` and ``gender`` keys** feed the same field.

Organism is the near-deterministic freebie: values are already clean binomial
names, so a curated NCBITaxon table (tier-1) covers the vast majority; the long
tail stays ``unmapped`` (tier-2 OLS/OAK exact lookup would generalize it — v2).

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
# Ontology term constants
# ---------------------------------------------------------------------------
PATO_MALE = "PATO:0000384"
PATO_FEMALE = "PATO:0000383"
PATO_HERMAPHRODITE = "PATO:0001340"

# ---------------------------------------------------------------------------
# characteristics parsing
# ---------------------------------------------------------------------------
# The stored blob is samples joined by " | ", tag fields within a sample by ";",
# each tag "key: value". (build_series_docs._blob rebuilt it this way.) Curators
# use several spellings for the same concept, so map keys onto a canonical field.
KEY_SYNONYMS = {
    "sex": "sex",
    "gender": "sex",
}


def parse_characteristics(blob: str | None) -> dict[str, set[str]]:
    """Parse an aggregated characteristics blob into {canonical_key: {values}}.

    Only keys we care about (``KEY_SYNONYMS``) are kept. Values are de-duped per
    field across all samples in the series — matching the series-aggregation
    contract (a series *contains* these values; see wiki/24).
    """
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


# ---------------------------------------------------------------------------
# Sex → PATO (tier-1 hand rules, value-driven with rejection)
# ---------------------------------------------------------------------------
# Exact tokens (after normalization) that name a sex. Kept deliberately tight:
# precision over recall for tier-1. Everything not here is rejected, not guessed.
_SEX_EXACT = {
    "m": PATO_MALE, "male": PATO_MALE, "males": PATO_MALE, "man": PATO_MALE,
    "men": PATO_MALE, "boy": PATO_MALE, "xy": PATO_MALE, "♂": PATO_MALE,
    "f": PATO_FEMALE, "female": PATO_FEMALE, "females": PATO_FEMALE,
    "woman": PATO_FEMALE, "women": PATO_FEMALE, "girl": PATO_FEMALE,
    "fem": PATO_FEMALE, "xx": PATO_FEMALE, "♀": PATO_FEMALE,
    "hermaphrodite": PATO_HERMAPHRODITE, "hermaphrodites": PATO_HERMAPHRODITE,
    "herm": PATO_HERMAPHRODITE,
}
# Values that explicitly say "both sexes present" — emit both IDs.
_SEX_MIXED = {
    "both", "both sexes", "mixed", "mix", "mixed sex", "male and female",
    "female and male", "male & female", "m/f", "f/m", "m+f", "mf",
    "male/female", "female/male", "pooled", "pool",
}
# Values that explicitly say "not known/applicable" — an honest unknown, which
# is different from a value we simply failed to map.
_SEX_UNKNOWN = {
    "", "unknown", "unk", "n/a", "na", "not applicable", "not collected",
    "not available", "not determined", "not recorded", "not reported",
    "undetermined", "unspecified", "none", "not known", "missing", "nd",
    "?", "--", "-", ".",
}
# Fuzzy target set for 1-edit misspellings (famale, femaie, femal, mael …).
_SEX_FUZZY = {"male": PATO_MALE, "female": PATO_FEMALE}
_DEV_STAGE_WORDS = {"adult", "fetal", "fetus", "embryo", "embryonic", "larva",
                    "larval", "pupa", "juvenile", "neonate", "newborn"}


def _norm_token(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip().lower())


def _levenshtein_le1(a: str, b: str) -> bool:
    """True if edit distance(a, b) <= 1. Cheap early-exit version."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:  # single substitution
        return sum(x != y for x, y in zip(a, b)) == 1
    # single indel: the shorter must be the longer with one char removed
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
    """Map one raw sex value → (pato_ids, reason, confidence).

    reason is one of: exact | mixed | fuzzy | unknown | numeric_code |
    leaked_age | leaked_stage | leaked_strain | unrecognized.
    """
    t = _norm_token(raw)
    if t in _SEX_UNKNOWN:
        return [], "unknown", 0.0
    if t in _SEX_EXACT:
        return [_SEX_EXACT[t]], "exact", 1.0
    if t in _SEX_MIXED:
        return [PATO_MALE, PATO_FEMALE], "mixed", 0.9
    # --- rejections: value is not in the sex space (order matters) ---------
    if t in _DEV_STAGE_WORDS:
        return [], "leaked_stage", 0.0
    if "/" in t or re.search(r"bl/?6|balb|c57|c3h|129|cd-?1|nod|wistar|sprague", t):
        return [], "leaked_strain", 0.0  # "c57bl/6", "balb/c" in the sex slot
    if re.fullmatch(r"\d+(\.\d+)?", t):
        # bare numbers are ambiguous codes / leaked counts (range ran to 13) —
        # never guess male/female from a number.
        return [], "numeric_code", 0.0
    if re.search(r"\d", t):
        return [], "leaked_age", 0.0  # "68m", "72", "32yr" — age in the sex slot
    # --- tier-3 lite: 1-edit misspelling of male/female --------------------
    if len(t) >= 4 and t.isalpha():
        for target, pid in _SEX_FUZZY.items():
            if _levenshtein_le1(t, target):
                return [pid], "fuzzy", 0.75
    return [], "unrecognized", 0.0


def map_sex_field(values: set[str]) -> tuple[list[str], str, list[str]]:
    """Aggregate a series' sex values → (sorted_ids, status, reasons).

    status: absent | mapped | unknown | unmapped.
    """
    if not values:
        return [], "absent", []
    ids: set[str] = set()
    reasons: list[str] = []
    saw_unknown = False
    for v in values:
        vids, reason, _conf = map_sex_value(v)
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
# Organism → NCBITaxon (tier-1 curated table)
# ---------------------------------------------------------------------------
# Covers the head of the distribution (the top ~40 species are >98% of rows).
# The long tail stays unmapped — tier-2 OLS/OAK exact lookup generalizes it (v2).
NCBITAXON = {
    "homo sapiens": "NCBITaxon:9606",
    "mus musculus": "NCBITaxon:10090",
    "rattus norvegicus": "NCBITaxon:10116",
    "arabidopsis thaliana": "NCBITaxon:3702",
    "drosophila melanogaster": "NCBITaxon:7227",
    "saccharomyces cerevisiae": "NCBITaxon:4932",
    "caenorhabditis elegans": "NCBITaxon:6239",
    "danio rerio": "NCBITaxon:7955",
    "sus scrofa": "NCBITaxon:9823",
    "bos taurus": "NCBITaxon:9913",
    "oryza sativa": "NCBITaxon:4530",
    "gallus gallus": "NCBITaxon:9031",
    "escherichia coli": "NCBITaxon:562",
    "schizosaccharomyces pombe": "NCBITaxon:4896",
    "zea mays": "NCBITaxon:4577",
    "macaca mulatta": "NCBITaxon:9544",
    "canis lupus familiaris": "NCBITaxon:9615",
    "pan troglodytes": "NCBITaxon:9598",
    "xenopus laevis": "NCBITaxon:8355",
    "xenopus tropicalis": "NCBITaxon:8364",
    "ovis aries": "NCBITaxon:9940",
    "equus caballus": "NCBITaxon:9796",
    "oryctolagus cuniculus": "NCBITaxon:9986",
    "gallus gallus domesticus": "NCBITaxon:9031",
    "plasmodium falciparum": "NCBITaxon:5833",
    "mycobacterium tuberculosis": "NCBITaxon:1773",
    "pseudomonas aeruginosa": "NCBITaxon:287",
    "bacillus subtilis": "NCBITaxon:1423",
    "solanum lycopersicum": "NCBITaxon:4081",
    "solanum tuberosum": "NCBITaxon:4113",
    "glycine max": "NCBITaxon:3847",
    "triticum aestivum": "NCBITaxon:4565",
    "hordeum vulgare": "NCBITaxon:4513",
    "medicago truncatula": "NCBITaxon:3880",
    "nicotiana tabacum": "NCBITaxon:4097",
    "chlamydomonas reinhardtii": "NCBITaxon:3055",
    "dictyostelium discoideum": "NCBITaxon:44689",
    "aspergillus fumigatus": "NCBITaxon:746128",
    "candida albicans": "NCBITaxon:5476",
    "macaca fascicularis": "NCBITaxon:9541",
    "cricetulus griseus": "NCBITaxon:10029",
    "mesocricetus auratus": "NCBITaxon:10036",
}


def map_organisms(raw: str | None) -> tuple[list[str], str]:
    """Map the comma-joined ``organisms`` column → (ncbitaxon_ids, status).

    status: absent | mapped | unmapped. (mapped as long as *any* organism
    grounded; unmapped only when values existed but none did.)
    """
    if not raw or not raw.strip():
        return [], "absent"
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return [], "absent"
    ids = []
    for n in names:
        tid = NCBITAXON.get(n.lower())
        if tid and tid not in ids:
            ids.append(tid)
    if ids:
        return ids, "mapped"
    return [], "unmapped"


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------
def _connect():
    import psycopg

    return psycopg.connect(DSN)


def migrate() -> int:
    """Add the normalization columns (idempotent)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE series
                ADD COLUMN IF NOT EXISTS organism_ids    TEXT[],
                ADD COLUMN IF NOT EXISTS organism_status  TEXT,
                ADD COLUMN IF NOT EXISTS sex_ids          TEXT[],
                ADD COLUMN IF NOT EXISTS sex_status       TEXT;
            """
        )
        conn.commit()
    print("added columns: organism_ids, organism_status, sex_ids, sex_status", flush=True)
    return 0


def run(limit: int | None = None, batch: int = 5000) -> int:
    """Map organism + sex for every row and write the results back."""
    import time

    migrate()
    read = _connect()
    write = _connect()
    n = mapped_org = mapped_sex = 0
    t0 = time.time()
    update_sql = (
        "UPDATE series SET organism_ids=%s, organism_status=%s, "
        "sex_ids=%s, sex_status=%s WHERE id=%s"
    )
    with read.cursor(name="norm_scan") as scan, write.cursor() as wcur:
        scan.itersize = batch
        sql = "SELECT id, organisms, characteristics FROM series ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        scan.execute(sql)
        params: list[tuple] = []
        for sid, organisms, characteristics in scan:
            org_ids, org_status = map_organisms(organisms)
            fields = parse_characteristics(characteristics)
            sex_ids, sex_status, _ = map_sex_field(fields.get("sex", set()))
            params.append((org_ids or None, org_status, sex_ids or None, sex_status, sid))
            n += 1
            mapped_org += org_status == "mapped"
            mapped_sex += sex_status == "mapped"
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
    print(
        f"done: {n:,} rows in {time.time()-t0:.0f}s | "
        f"organism mapped {mapped_org:,} ({100*mapped_org/max(n,1):.0f}%) | "
        f"sex mapped {mapped_sex:,} ({100*mapped_sex/max(n,1):.0f}%)",
        flush=True,
    )
    return 0


def report() -> int:
    """Coverage report over the populated columns."""
    with _connect() as conn, conn.cursor() as cur:
        print("\n=== organism_status ===", flush=True)
        cur.execute(
            "SELECT COALESCE(organism_status,'(null)'), count(*) "
            "FROM series GROUP BY 1 ORDER BY 2 DESC"
        )
        for status, c in cur.fetchall():
            print(f"  {status:10} {c:>8,}", flush=True)

        print("\n=== sex_status ===", flush=True)
        cur.execute(
            "SELECT COALESCE(sex_status,'(null)'), count(*) "
            "FROM series GROUP BY 1 ORDER BY 2 DESC"
        )
        for status, c in cur.fetchall():
            print(f"  {status:10} {c:>8,}", flush=True)

        print("\n=== sex PATO id counts (a series can carry both) ===", flush=True)
        cur.execute(
            "SELECT unnest(sex_ids) pid, count(*) FROM series "
            "WHERE sex_ids IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
        )
        for pid, c in cur.fetchall():
            print(f"  {pid:18} {c:>8,}", flush=True)

        print("\n=== top raw organism values still unmapped ===", flush=True)
        cur.execute(
            "SELECT organisms, count(*) FROM series WHERE organism_status='unmapped' "
            "AND organisms <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
        )
        for val, c in cur.fetchall():
            print(f"  {c:>6,}  {val[:60]}", flush=True)
    return 0


TRICKY = [
    "female", "Male", "F", "m", "sex:F", "famale", "Femaie", "both",
    "male and female", "unknown", "n/a", "0", "1", "68M", "32", "adult",
    "C57BL/6", "BALB/c", "hermaphrodite", "asexual", "XY",
]


def demo() -> int:
    """Show the sex mapper's verdict on the tricky real values from the data."""
    print(f"{'raw value':20} {'-> PATO ids':26} reason  conf", flush=True)
    print("-" * 64, flush=True)
    for v in TRICKY:
        v2 = v.split(":", 1)[1] if ":" in v else v
        ids, reason, conf = map_sex_value(v2)
        print(f"{v:20} {', '.join(ids) or '(none)':26} {reason:13} {conf:.2f}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tier-1/2 normalization: organism + sex.")
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

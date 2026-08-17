"""Which coding scheme a filing is written under.

Down here rather than in an extractor because every layer needs it and none
owns it: statements picks a coding workbook with it, summary picks a layout
and scopes entity detection with it. Same one-way core rule - nothing here
imports an extractor.

"""
from pathlib import Path

# data/ sits at the repo root - THREE levels up from src/core/, not two. This
# table moved here from what was then src/acctfinder.py (now
# financialReports/statements.py), where two levels was correct; the
# move silently pointed every coding-workbook path at src/data/ until this was
# anchored explicitly. No test caught it, because summary mode never loads a
# workbook - only the per-statement modes do.
_DATA = Path(__file__).resolve().parent.parent.parent / "data"


# ---------------------------------------------------------------------------
# Industry-category coding dictionaries.
#
# The coding workbook comes in 3 industry-specific files instead of one
# unified scheme, since the same code number can mean a different account
# depending on industry (e.g. code 58200 is a bad-debt-provision line for
# 金融業 but an insurance-specific cost line for a filing on the 保險業
# scheme - confirmed against real filings from different periods/entities).
# 金控業 = financial holding companies (母公司為金控); 金融業 = banks;
# 保險業 = life (人壽) and property/casualty (產險) insurers.
# ---------------------------------------------------------------------------

INDUSTRY_CODING_FILES = {
    "金控業": str(_DATA / "金控業.xlsx"),
    "金融業": str(_DATA / "金融業.xlsx"),
    "保險業": str(_DATA / "保險業.xlsx"),
}

# Checked against the reporting entity's own full legal name (as printed in
# the filing itself), not a bare industry word - "銀行" alone would false-
# positive on ordinary balance-sheet line items every entity type has (e.g.
# "銀行存款"/"存放銀行同業"), so these require the specific company-type
# suffix that only appears as part of an entity's actual registered name.
# Checked in this order (保險業 first) since a FHC's own filing sometimes
# also mentions a subsidiary bank/insurer by name - the reporting entity's
# OWN suffix is what should win, and 人壽/產物保險 names never double as a
# 金控/銀行 name, so there's no ordering conflict in practice.
INDUSTRY_CATEGORY_KEYWORDS = [
    ("保險業", ["人壽保險股份有限公司", "產物保險股份有限公司", "人壽保險公司", "產物保險公司"]),
    ("金控業", ["金融控股股份有限公司", "金融控股公司"]),
    ("金融業", ["商業銀行股份有限公司", "商業銀行"]),
]


def detect_industry_category(folder):
    """Auto-detect which of INDUSTRY_CATEGORY_KEYWORDS the filing's own
    reporting entity belongs to, by scanning the first few .md files
    (sorted) for the entity's full legal name - the cover page doesn't
    always carry it (confirmed: one real filing's page 007 balance sheet
    didn't, but page 001's cover title did), so several files are checked
    rather than just the first. Returns the category name, or None if no
    file matches any pattern."""
    paths = sorted(Path(folder).rglob("*.md"))[:5]
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in paths)
    for category, keywords in INDUSTRY_CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return None

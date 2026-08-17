"""Which coding scheme a filing is written under.

Down here rather than in an extractor because every layer needs it and none
owns it: statements picks a coding workbook with it, summary picks a layout
and scopes entity detection with it. Same one-way core rule - nothing here
imports an extractor.

"""
from pathlib import Path

# data/ sits at the repo root - THREE levels up from src/core/, NOT two. Two
# levels silently points every coding-workbook path at src/data/, and no test
# catches it: summary mode never loads a workbook.
_DATA = Path(__file__).resolve().parent.parent.parent / "data"


# THREE industry-specific workbooks, not one unified scheme: the same code
# number means a different account depending on industry (58200 is 呆帳提存
# under 金融業 but an insurance cost line under 保險業).
#   → docs/knowledge/industry-and-layout.md#三份產業科目字典
INDUSTRY_CODING_FILES = {
    "金控業": str(_DATA / "金控業.xlsx"),
    "金融業": str(_DATA / "金融業.xlsx"),
    "保險業": str(_DATA / "保險業.xlsx"),
}

# Matched against the reporting entity's own FULL LEGAL NAME, never a bare
# industry word: "銀行" alone false-positives on ordinary line items every
# entity type has ("銀行存款"/"存放銀行同業"). Order matters - 保險業 first,
# because a FHC filing also names its subsidiaries and the reporting entity's
# OWN suffix must win.
#   → docs/knowledge/industry-and-layout.md#產業怎麼判定
INDUSTRY_CATEGORY_KEYWORDS = [
    ("保險業", ["人壽保險股份有限公司", "產物保險股份有限公司", "人壽保險公司", "產物保險公司"]),
    ("金控業", ["金融控股股份有限公司", "金融控股公司"]),
    ("金融業", ["商業銀行股份有限公司", "商業銀行"]),
]


def detect_industry_category(folder):
    """Auto-detect which of INDUSTRY_CATEGORY_KEYWORDS the filing's own
    reporting entity belongs to, by scanning the first FIVE .md files
    (sorted) for its full legal name - the cover page does not always carry
    it. Returns the category name, or None if nothing matches."""
    paths = sorted(Path(folder).rglob("*.md"))[:5]
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in paths)
    for category, keywords in INDUSTRY_CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return None

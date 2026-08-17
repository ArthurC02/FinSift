"""
Search a folder of converted markdown (.md) earnings-call transcripts for
configurable terms and pull an associated value from the tables within them
- the conference-call counterpart to statements.py's coded
financial-statement extraction.

Earnings-call terms are a completely different vocabulary from financial-
statement account codes (e.g. loan growth guidance, NIM outlook, credit cost
commentary, rather than fixed 5-digit codes), so this is a SEPARATE entry
point from statements.py rather than another mode of it - the user
explicitly runs one or the other depending on whether a given batch of .md
files came from a quarterly financial report or an earnings call.

Matching is two-layer, per the term dictionary's design (see
Bank_Term_Weighted_Decomposition.xlsx, sheet "Method Legend & Rules"):
  1. "exact" - a flat list of exact/alias strings (the term's standard
     name, acronym, and well-established whole-phrase variants). Substring
     match; if any alias hits, the row is accepted immediately.
  2. "composite" - only evaluated when no alias hits. A set of weighted
     sub-terms (e.g. a qualifier and a core concept); each sub-term whose
     synonym set is found in the row contributes its weight, and the row
     is accepted once the summed weight reaches the term's threshold. This
     mirrors financial_keyword_finder.py's original exact/composite
     KeywordSpec design, since the two problems (fuzzy term identification
     in prose/tables) are the same shape.
An optional "negative_terms" list - present for a few explicitly
mutually-exclusive pairs (e.g. 稅前淨利 vs 稅後淨利, ROA vs ROE) - rejects a
row outright if any of those strings appear in it, regardless of an
otherwise-passing alias or composite score.

Once a term's row is identified, VALUE extraction reuses the same table-
parsing / continuation-folding machinery built for statements.py
(imported directly, since both live in this project and should stay in
sync rather than duplicate logic) - con-call .md files are assumed, per
instruction pending a real sample, to share the same underlying pipe-table
structure as the financial-report .md files.

Usage:
    python decks.py <term> --folder <folder> --config con_call_terms.json [--period N] [--export csv] [-v]

--folder may be omitted, opening a folder-picker dialog instead.

<term> may be omitted to search every term in the config, or be a
comma/space-separated list of term names, matching financial_keyword_finder's
CLI convention.
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.text import _contains_any, page_num
from core.numbers import parse_numeric, format_pct, format_maybe_pct
from core.tables import build_raw_lines, restrict_section, parse_pipe_tables
# Imported from the modules that actually own these, not through the
# statements facade. Reaching through statements pulled the whole fin_report
# stack (summary -> ratios -> entities) in to get four names, and made this
# module look like it depended on summary extraction, which it does not.
from financialReports.entities import BANK_PROFILES, detect_bank
from financialReports.ratios import derive_quarter_num
from financialReports.statements import pick_folder

# Windows consoles often default to cp1252, which can't print CJK output.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass
class Component:
    terms: list
    weight: float


@dataclass
class TermSpec:
    name: str
    type: str = "exact"  # "exact" or "composite"
    aliases: list = field(default_factory=list)
    components: list = field(default_factory=list)  # list[Component], "composite" only
    threshold: float = 0.8
    negative_terms: list = field(default_factory=list)
    search_start: list = None  # optional section-scoping markers (e.g. a specific speaker segment)
    search_end: list = None

    @classmethod
    def from_dict(cls, name, d):
        components = []
        for c in d.get("components", []):
            # Component(**c) on its own raised a bare TypeError naming neither
            # the file, the term nor the field - see load_terms, which turns
            # what's raised here into a message that says where to look.
            if not isinstance(c, dict):
                raise ValueError(f"component must be an object, got {type(c).__name__}")
            missing = sorted({"terms", "weight"} - set(c))
            extra = sorted(set(c) - {"terms", "weight"})
            if missing or extra:
                raise ValueError(
                    f"component {c!r} is malformed"
                    + (f"; missing {missing}" if missing else "")
                    + (f"; unexpected {extra}" if extra else ""))
            components.append(Component(**c))

        # An empty alias is a substring of every string, so one blank entry
        # silently makes its term match EVERY row in the folder at strength 2,
        # outranking any composite. Same for a blank negative term, which
        # would veto everything instead.
        for field, values in (("aliases", d.get("aliases", [])),
                              ("negative_terms", d.get("negative_terms", []))):
            for v in values:
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"{field} contains a blank or non-string entry: {v!r}")

        return cls(
            name=name,
            type=d.get("type", "exact"),
            aliases=d.get("aliases", []),
            components=components,
            threshold=d.get("threshold", 0.8),
            negative_terms=d.get("negative_terms", []),
            search_start=d.get("search_start"),
            search_end=d.get("search_end"),
        )


def load_terms(config_path):
    """Load term definitions from a JSON config. See
    Bank_Term_Weighted_Decomposition.xlsx for the source dictionary this was
    generated from, and con_call_terms_example.json for a minimal
    hand-written example of the same shape."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    terms = {}
    for name, d in raw.items():
        if name.startswith("_"):
            continue
        try:
            terms[name] = TermSpec.from_dict(name, d)
        except (TypeError, ValueError) as e:
            # Say which file and which term. A bare TypeError from
            # Component(**c) gave the user nothing to search a 400-term config
            # for.
            raise ValueError(f"{config_path}: term '{name}' is invalid - {e}") from e
    return terms


def match_strength(term_spec, text):
    """0 = no match (or vetoed by a negative term); 1 = composite (weighted
    sub-term score cleared threshold, Layer 2); 2 = substring alias hit
    (Layer 1); 3 = exact alias hit (the whole, stripped text equals an
    alias exactly). Exact beats substring beats composite - a generic
    total-level term like 淨收益 is a literal substring of many unrelated
    compound line items (手續費淨收益合計, 利息淨收益, ...), so accepting
    the first substring hit found anywhere in the folder can silently grab
    the wrong row; preferring the strongest match found across the whole
    folder (see find_term_value) avoids that."""
    if term_spec.negative_terms and _contains_any(text, term_spec.negative_terms):
        return 0
    if term_spec.aliases:
        stripped_lower = text.strip().lower()
        if any(stripped_lower == a.lower() for a in term_spec.aliases):
            return 3
        if _contains_any(text, term_spec.aliases):
            return 2
    if term_spec.type == "composite" and term_spec.components:
        score = sum(c.weight for c in term_spec.components if _contains_any(text, c.terms))
        if score >= term_spec.threshold:
            return 1
    return 0


# ---------------------------------------------------------------------------
# Header-aware, orientation-detecting table extraction.
#
# Verified against a real earnings-call deck (52-page 國泰世華銀行 4Q25
# analyst meeting): con-call slide tables come in (at least) two different
# orientations, sometimes both on the same page:
#   - "row_period": each DATA ROW is one period (e.g. FY24, FY25), and the
#     HEADER names each metric/category as its own column (a loan-structure
#     table: 企業放款, 房屋貸款, ... each with its own 金額 column and a
#     separate 占比 column right after it).
#   - "col_period": each DATA ROW is one metric/entity (e.g. 整體逾放比,
#     Spread, NIM, or a subsidiary name), and the HEADER names each period
#     as its own column - the same shape already built for 玉山金控's
#     transposed 獲利能力 table in statements.py.
# Real periods are also heterogeneous granularity in the same table (FY25,
# 1Q25, 1H25, 9M25, 4Q25 all appear together across the deck), and a table
# can have non-period columns mixed in among real period columns (e.g. a
# "FY25/FY24 % Chg" growth column, or a "企業放款占比" percentage-share
# column sitting right next to "企業放款"'s own absolute-value column) -
# so orientation is detected by majority vote (most, not all, of an axis's
# cells parsing as a period), and a period-label parse failure on a given
# column/row just excludes that one column/row rather than breaking
# detection for the whole table.
#
# This replaces an earlier row-only/positional design (assuming one row per
# term, values listed most-recent-first like the financial-statement
# tables) - real con-call slides don't follow that convention at all.
# ---------------------------------------------------------------------------

_FY_RE = re.compile(r"^FY(\d{2,4})$", re.IGNORECASE)
_Q_RE = re.compile(r"^(\d)Q(\d{2,4})$", re.IGNORECASE)
_H_RE = re.compile(r"^(\d)H(\d{2,4})$", re.IGNORECASE)
_M_RE = re.compile(r"^(\d{1,2})M(\d{2,4})$", re.IGNORECASE)
# 'Dec 24', 'Mar 25' - CTBC's quarter-end date convention, confirmed against
# a real deck (中國信託 4Q25 analyst meeting). The separator may also be a
# hyphen ('Dec-25'), confirmed in a real 富邦 deck.
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]{3})[a-z]*\.?[\s\-]+(\d{2,4})$")
_MONTH_RANK = {"jan": 1, "feb": 1, "mar": 1, "apr": 2, "may": 2, "jun": 2,
               "jul": 3, "aug": 3, "sep": 3, "oct": 4, "nov": 4, "dec": 4}
# Bare 4-digit year ('2021'-'2025', no FY prefix) - also confirmed in the
# same deck (a subsidiary's annual figures table used plain years).
_BARE_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# '2025.12' / '2025.09' - year-and-month period-end convention, confirmed in
# a real 玉山 deck (存放款業務比較 table). Ranked by the month's quarter, the
# same way 'Dec 25' is, since both denote a period-END balance date.
_YEAR_MONTH_RE = re.compile(r"^((?:19|20)\d{2})[./\-](0?[1-9]|1[0-2])$")

# Decks routinely mark their newest ("查核數"/audited) column with a trailing
# footnote reference glued directly onto the period label - either a Unicode
# superscript digit ('2Q25¹') or a literal '<sup>N</sup>' tag ('Jun 25<sup>1</sup>'),
# confirmed in a real 中信金 2Q25 deck. Left unstripped, this breaks every
# period regex below (all anchored with $), silently excluding the newest
# column from ranking and making the tool report the second-newest quarter
# as if it were current.
_FOOTNOTE_TAG_RE = re.compile(r"(?:<sup>\d+</sup>)+$", re.IGNORECASE)
_FOOTNOTE_SUP_RE = re.compile(r"[¹²³⁰⁴-⁹]+$")


def _strip_period_footnote(cell):
    cell = _FOOTNOTE_TAG_RE.sub("", cell)
    cell = _FOOTNOTE_SUP_RE.sub("", cell)
    return cell.strip()


def _normalize_year(y):
    """Two-digit -> 20xx, three-digit -> ROC (民國) -> Western, four-digit as-is.

    A 3-digit year in these documents is always a ROC year: 民國114 = 2025.
    Leaving it at 114 put it below every 19xx/20xx label, so a ROC-dated column
    could never win as "most recent" and mixed-notation decks sorted wrongly.
    """
    y = int(y)
    if y < 100:
        return 2000 + y
    if y < 200:
        return y + 1911
    return y


def parse_period_label(cell):
    """Parse a period label ('FY25', '4Q25', '1H25', '9M25', 'Dec 24',
    bare '2025') into a sortable (year, completeness_rank) tuple, or None
    if `cell` isn't (purely) a period label - e.g. 'FY25/FY24 % Chg' or
    '企業放款占比' correctly fail and are excluded rather than misread.
    completeness_rank orders sub-year granularities within the same year on
    roughly a quarter-equivalent scale, with a full year (FY, bare year, or
    4Q/Dec as its same-point-in-time equivalent) ranked highest for that
    year."""
    cell = _strip_period_footnote(cell.strip())
    m = _FY_RE.match(cell)
    if m:
        return (_normalize_year(m.group(1)), 5)
    if _BARE_YEAR_RE.match(cell):
        return (int(cell), 5)
    m = _Q_RE.match(cell)
    if m:
        q, y = int(m.group(1)), _normalize_year(m.group(2))
        return (y, q) if q in (1, 2, 3, 4) else None
    m = _H_RE.match(cell)
    if m:
        h, y = int(m.group(1)), _normalize_year(m.group(2))
        return (y, h * 2) if h in (1, 2) else None
    m = _M_RE.match(cell)
    if m:
        months, y = int(m.group(1)), _normalize_year(m.group(2))
        return (y, months / 3)
    m = _MONTH_YEAR_RE.match(cell)
    if m:
        mon = m.group(1).lower()
        if mon in _MONTH_RANK:
            return (_normalize_year(m.group(2)), _MONTH_RANK[mon])
    m = _YEAR_MONTH_RE.match(cell)
    if m:
        # month -> quarter, matching _MONTH_RANK's mapping
        return (int(m.group(1)), (int(m.group(2)) + 2) // 3)
    return None


def _majority_are_periods(cells):
    non_blank = [c for c in cells if c.strip()]
    if not non_blank:
        return False
    hits = sum(1 for c in non_blank if parse_period_label(c) is not None)
    return hits > 0 and hits >= len(non_blank) / 2


def _detect_period_column(rows, max_check_cols=3):
    """For row_period orientation, find which column index holds period
    labels across most rows - not assumed to always be column 0, since some
    tables have a leading qualifier column before the actual period column
    (e.g. '項目'='單季'/'全年' before '期間', confirmed in a real CTBC deck's
    NIM table). Returns the best-scoring column index, or None if no column
    has a majority of rows parsing as periods."""
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    best_col, best_hits = None, 0
    for col in range(min(max_check_cols, n_cols)):
        cells = [r[col] for r in rows if col < len(r) and r[col].strip()]
        if not cells:
            continue
        hits = sum(1 for c in cells if parse_period_label(c) is not None)
        if hits > best_hits and hits >= len(cells) / 2:
            best_col, best_hits = col, hits
    return best_col


def detect_orientation(table):
    """Return ('row_period', period_col_idx) if data rows have a column of
    mostly period labels, ('col_period', None) if header cells (excluding
    the first) are mostly period labels, or None if neither axis looks like
    periods (table skipped rather than guessed at)."""
    period_col = _detect_period_column(table["rows"])
    if period_col is not None:
        return ("row_period", period_col)
    header = table["header"]
    if len(header) > 1 and _majority_are_periods(header[1:]):
        return ("col_period", None)
    return None


_SHARE_OR_GROWTH_RE = re.compile(
    r"占|佔|比重|比率|Ratio|Share|%|％|Chg|成長率|年增|季增|增率|YoY|QoQ|Growth",
    re.IGNORECASE,
)


def _is_share_or_growth_column(header_cell):
    return bool(_SHARE_OR_GROWTH_RE.search(header_cell))


_PERCENT_CELL_RE = re.compile(r"[%％]")


def _is_percent_cell(raw_cell):
    """True if a raw table cell is written as a percentage. Balance-type
    terms (loan balances - currency amounts) must never take a value from
    one: parse_numeric strips the '%' sign, so '0.08%' becomes 0.08 and is
    afterwards indistinguishable from a real balance. In a real 富邦 deck
    that let 房貸 match the 房貸 column of a 業務別逾放比 (NPL-ratio-by-
    business) table and report 0.08, and let 法說會放款餘額合計 match the
    ratio row '逾期放款／總放款' and report 0.12."""
    return bool(_PERCENT_CELL_RE.search(raw_cell))




# A header[0] cell (or slide heading) counts as naming an ENTITY only if it
# contains one of these company-type markers. This is deliberately an
# allowlist rather than a blocklist of generic axis labels (項目/期間/年度/
# ...): a blocklist silently treats any unlisted generic label as a company
# name, which is exactly how '| Quarterly | 4Q22 | ... |' in a real 國泰 deck
# came to be read as an entity named "Quarterly" and outranked by a
# genuinely bank-named table, making NIM resolve to the annual FY25 figure
# instead of the 4Q25 one sitting in that very table.
_ENTITY_NAME_RE = re.compile(
    r"銀行|金控|控股|人壽|產險|證券|投信|投顧|保險|公司|Bank|FHC|Holdings|Financial|Life|Securities|Insurance",
    re.IGNORECASE,
)

# Preferred entity when a figure exists identically in more than one
# entity's table (e.g. a bank subsidiary's own 營業費用 vs its FHC parent's
# consolidated 營業費用) - this term dictionary is built around bank-level
# concepts throughout (存放比, 放款結構, NIM, ...), so the bank-named table
# is preferred over one merely containing "金控"/holding-company language.
_BANK_LABEL_HINT = "銀行"

# Repo root is THREE levels up from src/<package>/, not two. This module
# moved down a directory; the same expression silently pointed at src/
# instead. See core/industry.py, where exactly this bit once.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The PRIMARY bank subsidiary each deck is about - see BANK_PROFILES'
# primary_entities field for why picking the wrong subsidiary is worse than
# reporting nothing. Derived rather than held here so that adding an entity
# is one edit in one place, and so statements's profile validation covers it.
PRIMARY_BANK_ENTITIES = {name: profile["primary_entities"]
                         for name, profile in BANK_PROFILES.items()}


def entity_tier(entity, primary_aliases):
    """Rank an entity name for bank-level term matching:
      2 = the deck's primary bank subsidiary (e.g. 台北富邦銀行)
      1 = no entity named - a generic/unscoped table, the common case
      0 = some OTHER named company (another bank subsidiary, the FHC
          parent, an insurance/securities arm) - must not supply
          bank-level figures.
    primary_aliases may be None (bank unknown), in which case any
    bank-named entity is treated as primary, preserving the older
    _BANK_LABEL_HINT-only behaviour rather than rejecting everything."""
    if not entity:
        return 1
    if primary_aliases is None:
        return 2 if _BANK_LABEL_HINT in entity else (0 if "金控" in entity else 1)
    if any(alias in entity for alias in primary_aliases):
        return 2
    return 0 if _ENTITY_NAME_RE.search(entity) else 1

_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _heading_topic(heading_text):
    """Strip a leading '公司名－' prefix from a slide title (e.g.
    '國泰世華銀行－外幣放款' -> '外幣放款'), so heading matching isn't
    thrown off by the company name always being present in the title."""
    parts = re.split(r"[－\-–—]", heading_text, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else heading_text.strip()


def collect_headings(lines):
    """Return list of (line_idx, raw_text, topic_text) for every markdown
    heading line ('#'..'######') in `lines`. raw_text is the heading as
    written (e.g. '富邦金控－財務摘要'); topic_text has the leading
    '公司名－' prefix stripped (see _heading_topic), for term-matching use
    where the always-present company name would throw off matching."""
    headings = []
    for idx, (_pn, line) in enumerate(lines):
        m = _HEADING_LINE_RE.match(line)
        if m:
            raw = m.group(1).strip()
            headings.append((idx, raw, _heading_topic(raw)))
    return headings


def nearest_heading(headings, table_line_idx):
    """The (raw_text, topic_text) of the last heading appearing before
    `table_line_idx`, or None if there isn't one - used when a table's own
    row/column labels are too generic to identify a term (e.g. '放款餘額'/
    '占全行放款'), but the term is actually the slide's section title above
    the table (e.g. '## 外幣放款'), a pattern confirmed in a real
    earnings-call deck. raw_text is also used for entity detection (e.g.
    spotting a '金控'/FHC-parent-labeled slide) since topic_text has the
    company name stripped out."""
    preceding = [h for h in headings if h[0] < table_line_idx]
    if not preceding:
        return None
    _idx, raw, topic = preceding[-1]
    return raw, topic


def _rank_periods(period_items, prefer_quarterly=False):
    """period_items: list of (period_key, row_or_col). Returns them sorted
    most-to-least preferred, so a caller can walk down the list and use the
    first one that actually has a usable (non-blank) value, rather than
    committing to a single 'best' choice that might turn out blank for this
    particular row/column (e.g. a ratio-only row with figures for FY23-25
    but nothing in the 4Q24/4Q25 columns that other rows in the same table
    do use).

    Ordinarily this is just most-recent-period-first. With prefer_quarterly,
    a same-year rank-4 entry (a true single Q4, or an equivalent month label
    like 'Dec 25') is ranked above a same-year rank-5 one (FY/bare-year/12M
    cumulative) - both close on the same date, and the curated summary's
    '單季' (single-quarter) output wants the genuine single-quarter figure,
    not the cumulative one. This does NOT extend to rank 1-3 (1Q/1H/9M) -
    those are genuinely earlier/less-complete periods within the year, not
    equally-valid alternatives to the annual figure, so e.g. '9M25' still
    correctly ranks below 'FY25' rather than being preferred just for being
    < rank 5."""
    if not prefer_quarterly:
        return sorted(period_items, key=lambda t: t[0], reverse=True)
    latest_year = max(k[0] for k, _ in period_items)

    def sort_key(item):
        k, _ = item
        # A genuine single-quarter label (4Q25, or a month label like Dec 25)
        # gets an INTEGER rank; an N-month cumulative label gets months/3, a
        # float - and 12M25 lands on exactly 4.0. Only the integer form is a
        # real single quarter, so without the type check a 12-month CUMULATIVE
        # figure was pulled ahead of FY25 and shown in the 單季 column.
        is_q4_this_year = 1 if (k[0] == latest_year and k[1] == 4
                                and isinstance(k[1], int)) else 0
        return (is_q4_this_year, k)

    return sorted(period_items, key=sort_key, reverse=True)


def _row_sections(rows):
    """For a col_period table, map each row index to the nearest preceding
    'bare' row's text (non-blank first cell, every other cell blank) - e.g.
    a '台幣'/'外幣'/'整體' row acting as an inline sub-section header for
    the metric rows beneath it (confirmed in a real 中信金 deck, where
    '放款利率'/'存款利率' repeat verbatim once per currency section with no
    currency wording in the metric row's own label). None for rows with no
    such preceding header, or for a header row itself. Lets negative_terms
    veto a candidate by its enclosing section even when the row's own label
    text has nothing to veto on."""
    sections = {}
    current = None
    for idx, row in enumerate(rows):
        if row and row[0].strip() and all(not c.strip() for c in row[1:]):
            current = row[0].strip()
            sections[idx] = None
            continue
        sections[idx] = current
    return sections


# ---------------------------------------------------------------------------
# Table unit detection.
#
# Decks do NOT use one unit throughout - confirmed in a real 中信金 deck
# where the loan-balance table on one page is 新台幣-百萬元 while the
# 外幣放款成長率 table it gets combined with is 新台幣拾億元, a 1000x gap.
# Any cross-table arithmetic (see LOAN_RECOMPOSITION) is therefore wrong
# unless both sides are converted to a common unit first. Everything is
# normalised to 十億元 (NT$ billions), the unit every bank's headline loan
# table already uses (confirmed for all 4 banks' Q4 2025 decks: NT$BN,
# 新台幣拾億元, NT$十億元, 新臺幣拾億元 - so normalisation is a no-op there
# and only kicks in on the mixed-unit decks it exists to fix).
#
# Longer tokens are listed BEFORE their own substrings ('拾億'/'十億' before
# '億') so the more specific unit wins the match.
# ---------------------------------------------------------------------------
_UNIT_TO_BILLIONS = [
    ("兆", 1e3),
    ("拾億", 1.0), ("十億", 1.0),
    ("百萬", 1e-3),
    ("仟元", 1e-6), ("千元", 1e-6),
    ("億", 1e-1),
    ("billion", 1.0), ("bn", 1.0),
    ("million", 1e-3), ("mn", 1e-3),
    ("thousand", 1e-6),
]

# A unit is only read off text that actually looks like a unit DECLARATION -
# without this guard a data row like '淨值(億元)' or a prose mention would be
# mistaken for one, silently rescaling a whole table by 10x.
_UNIT_DECL_RE = re.compile(r"單位|新台幣|新臺幣|NT\$|NTD", re.IGNORECASE)


def _unit_scale_from_text(text):
    if not text or not _UNIT_DECL_RE.search(text):
        return None
    low = text.lower()
    for token, scale in _UNIT_TO_BILLIONS:
        if token in text or token in low:
            return scale
    return None


def detect_unit_scale(lines, table, lookback=8):
    """Multiplier converting `table`'s figures to 十億元. Checks the table's
    own leading header cell first (some decks put the unit there, e.g.
    '| 新台幣-百萬元,% | 2022 | ...'), then walks backwards through the lines
    just above the table for a '單位：...' style declaration. Defaults to 1.0
    (assume 十億元) when nothing is declared - the conservative choice, since
    it leaves already-correct output unchanged rather than rescaling on a
    guess."""
    texts = []
    header = table.get("header") or []
    if header:
        texts.append(header[0])
    idx = table["line_idx"]
    for i in range(idx - 1, max(-1, idx - 1 - lookback), -1):
        texts.append(lines[i][1])
    for text in texts:
        scale = _unit_scale_from_text(text)
        if scale is not None:
            return scale
    return 1.0


def find_value_in_table(table, term_spec, desired_period_label=None, prefer_absolute=True,
                         heading=None, prefer_quarterly=False, require_absolute=False):
    """Locate term_spec's value within one parsed table, using whichever
    orientation is detected. desired_period_label: an exact period-label
    string (e.g. 'FY25') to require, or None for the latest period present.
    prefer_absolute: when multiple header columns textually match the term
    (e.g. '企業放款' matching both '企業放款' and '企業放款占比'), prefer
    the one that doesn't look like a percentage/share/growth column, then
    break remaining ties by shortest header text (most exact match).
    prefer_quarterly: see _pick_latest_period - pass True for ratio terms
    whose curated output is meant to be a single quarter's figure.
    heading: the nearest preceding section title (see nearest_heading) -
    used as a fallback (col_period orientation only) when no row label
    identifies the term but the table's own section heading does (e.g. a
    '## 外幣放款' slide whose table rows are just generically labeled
    '放款餘額'/'占全行放款') - confirmed against a real earnings-call deck.
    Returns (value, matched_label, period_label, strength, entity, is_percent)
    or None - strength lets callers prefer the best match found across an
    entire folder rather than just the first one encountered (see
    find_term_value); entity is the table's header[0] text when it names a
    specific company (e.g. '國泰世華銀行' vs '國泰金控' - the same figure,
    like 營業費用, can legitimately appear once per entity in a multi-entity
    appendix table), or None when header[0] is just a generic column label.
    is_percent is True when the matched cell's raw text carries a '%'/'％'
    sign - parse_numeric() strips that sign off, so without this flag a
    generic ad-hoc term lookup (unlike the curated summary, which already
    knows which of its terms are ratios) has no way to tell whether a
    printed '1.27' means '1.27%' or a plain count/balance of 1.27 - see
    format_maybe_pct(), used by callers that don't already know the term's
    kind. heading may be the (raw_text, topic_text) tuple from
    nearest_heading(), or a plain string/None for callers that don't need
    the entity-detection fallback."""
    detected = detect_orientation(table)
    if detected is None:
        return None
    orientation, period_col = detected
    header, rows = table["header"], table["rows"]

    heading_raw, heading = (heading if isinstance(heading, tuple) else (heading, heading))

    # A term's negative_terms veto its own row/column label (see
    # match_strength); apply them to the slide heading too, since the
    # heading is the label's context. In a real 富邦 deck a column headed
    # plainly '存放比' sits under '外幣放款及債券投資佔外幣存款比例' - an
    # FX-only ratio that the label alone gives no way to tell apart from
    # the overall loan-to-deposit ratio the term means.
    if term_spec.negative_terms and heading_raw and _contains_any(heading_raw, term_spec.negative_terms):
        return None

    entity = None
    if header and header[0].strip() and not parse_period_label(header[0]):
        h0 = header[0].strip()
        if _ENTITY_NAME_RE.search(h0):
            entity = h0
    if entity is None and heading_raw and _ENTITY_NAME_RE.search(heading_raw):
        # The table's own header[0] doesn't name a company (it's a generic
        # axis label like "項目"/"Quarterly"), so fall back to the RAW
        # section heading when IT names one (e.g. "富邦華一銀行－財務摘要"),
        # so another subsidiary's table can still be told apart from the
        # primary bank's downstream (see entity_tier / _best_match_in_file).
        # Deliberately uses heading_raw, not the topic-stripped `heading`,
        # since topic-stripping removes exactly the company-name prefix
        # this check needs.
        entity = heading_raw

    if orientation == "row_period":
        candidates = [(idx, h, match_strength(term_spec, h)) for idx, h in enumerate(header) if idx != period_col]
        candidates = [(idx, h, s) for idx, h, s in candidates if s > 0]
        if require_absolute:
            candidates = [(idx, h, s) for idx, h, s in candidates if not _is_share_or_growth_column(h)]
        if candidates:
            if prefer_absolute and len(candidates) > 1:
                non_share = [(idx, h, s) for idx, h, s in candidates if not _is_share_or_growth_column(h)]
                if non_share:
                    candidates = non_share
            best_strength = max(s for _, _, s in candidates)
            candidates = [(idx, h, s) for idx, h, s in candidates if s == best_strength]
            col_idx, col_header, strength = min(candidates, key=lambda t: len(t[1]))
        else:
            # No column header names the term - fall back to the slide's own
            # section heading, mirroring the col_period branch (e.g. a
            # '## 外幣放款' slide whose only value column is generically
            # labelled '餘額'). Deliberately requires EXACTLY ONE candidate
            # value column (after dropping the period column and any
            # share/growth column): with two or more the heading tells us
            # the topic but not which column it refers to, and guessing
            # there is how a currency-split table like 富邦's
            # '企業授信餘額（依幣別）' (台幣授信 | 外幣授信) would silently
            # yield half the figure. Ambiguous -> no match, not a guess.
            if not heading:
                return None
            strength = match_strength(term_spec, heading)
            if strength == 0:
                return None
            value_cols = [(idx, h) for idx, h in enumerate(header)
                          if idx != period_col and h.strip() and not _is_share_or_growth_column(h)]
            if len(value_cols) != 1:
                return None
            col_idx, _only_col = value_cols[0]
            col_header = heading  # report the heading, as the col_period branch does

        period_rows = [(parse_period_label(r[period_col]), r) for r in rows
                        if len(r) > period_col and parse_period_label(r[period_col])]
        if not period_rows:
            return None
        if desired_period_label is not None:
            period_rows = [(k, r) for k, r in period_rows if r[period_col].strip() == desired_period_label]
            if not period_rows:
                return None
        for key, row in _rank_periods(period_rows, prefer_quarterly):
            if col_idx >= len(row):
                continue
            if require_absolute and _is_percent_cell(row[col_idx]):
                continue
            value = parse_numeric(row[col_idx])
            if value is not None:
                return (value, col_header.strip(), row[period_col].strip(), strength, entity,
                        _is_percent_cell(row[col_idx]))
        return None

    candidates = [(idx, r, match_strength(term_spec, r[0])) for idx, r in enumerate(rows) if r]
    candidates = [(idx, r, s) for idx, r, s in candidates if s > 0]
    if term_spec.negative_terms:
        sections = _row_sections(rows)
        candidates = [(idx, r, s) for idx, r, s in candidates
                      if not (sections.get(idx) and _contains_any(sections[idx], term_spec.negative_terms))]
    if require_absolute:
        # Mirrors the row_period branch's header filtering: without this, a
        # share/percentage-labeled row (e.g. '外幣放款佔全行放款') can win
        # the tie-break over the real absolute-value row it shares a match
        # strength with, and then every period in THAT row gets filtered
        # out by the require_absolute check below, yielding None instead of
        # ever trying the correct row (confirmed in a real 國泰金 deck).
        absolute_only = [(idx, r, s) for idx, r, s in candidates if not _is_share_or_growth_column(r[0])]
        if absolute_only:
            candidates = absolute_only

    matched_label = None
    if candidates:
        best_strength = max(s for _, _, s in candidates)
        candidates = [(idx, r, s) for idx, r, s in candidates if s == best_strength]
        row_idx, row, strength = min(candidates, key=lambda t: len(t[1][0]))
        matched_label = row[0].strip()
    elif heading:
        strength = match_strength(term_spec, heading)
        if strength == 0:
            return None
        # Mirrors the row_period branch's "exactly one value column" rule:
        # safe only when the table is genuinely about ONE thing the heading
        # names (e.g. a '## 外幣放款' slide whose rows are just absolute vs.
        # share views of the same figure). If several distinct, unrelated
        # line items remain (e.g. a general '各類放款佔比' breakdown where
        # the heading only mentions the term in a passing footnote), picking
        # "the first one" is a guess, not a match - confirmed in a real
        # 中信金 deck where this previously grabbed an unrelated corporate-
        # loan row for a term (信用卡循環) that had no row of its own.
        non_share = [r for r in rows if r and r[0].strip()
                     and not all(not c.strip() for c in r[1:])
                     and not _is_share_or_growth_column(r[0])]
        if len(non_share) != 1:
            return None
        row = non_share[0]
        matched_label = heading.strip()
    else:
        return None

    period_cols = [(idx, parse_period_label(h)) for idx, h in enumerate(header) if idx > 0]
    period_cols = [(k, idx) for idx, k in period_cols if k is not None]
    if not period_cols:
        return None
    if desired_period_label is not None:
        period_cols = [(k, idx) for k, idx in period_cols if header[idx].strip() == desired_period_label]
        if not period_cols:
            return None
    for key, col_idx in _rank_periods(period_cols, prefer_quarterly):
        if col_idx >= len(row):
            continue
        if require_absolute and _is_percent_cell(row[col_idx]):
            continue
        value = parse_numeric(row[col_idx])
        if value is not None:
            return (value, matched_label, header[col_idx].strip(), strength, entity,
                    _is_percent_cell(row[col_idx]))
    return None


def _rank_key(strength, entity, period_label=None, prefer_quarterly=False, primary_aliases=None):
    """Sort key preferring, in order: higher match strength; then the deck's
    primary bank subsidiary's own table over an unscoped one (see
    entity_tier - a table belonging to some OTHER named company scores 0
    and is rejected outright by callers, not merely deprioritised); then,
    if prefer_quarterly, a result whose period is sub-annual (quarter/half/
    9-month) over one that's only annual - two equally-good alias matches
    for the same term (e.g. '營收' with only annual data vs '營業收入' with
    quarterly data) shouldn't silently resolve to whichever file sorts
    first when the whole point is to compare quarter-to-quarter figures
    (e.g. CIR's 營業費用 and 淨收益 inputs must come from the same
    granularity, not a quarterly expense against an annual revenue)."""
    tier = entity_tier(entity, primary_aliases)
    quarterly_bonus = 0
    if prefer_quarterly and period_label:
        key = parse_period_label(period_label)
        if key is not None and key[1] < 5:
            quarterly_bonus = 1
    return (strength, tier, quarterly_bonus)


def _best_match_in_file(doc_path, term_spec, prefer_quarterly=False, primary_aliases=None,
                         require_absolute=False):
    """Search every table in a single file for term_spec and return the best
    local match: (rank_key, value, matched_label, period_label, entity,
    is_percent, unit_scale), or None if term_spec doesn't appear in this
    file at all. unit_scale is the multiplier converting that table's
    figures to 十億元 (see detect_unit_scale) - carried alongside rather
    than applied here, so ratio terms (which are unitless) and the CIR
    inputs (a ratio of two figures, where a shared unit cancels) can ignore
    it while balance terms normalise by it.
    Tables belonging to a named entity that ISN'T the deck's primary bank
    (entity_tier 0 - another bank subsidiary, the FHC parent, an insurance/
    securities arm) are skipped outright rather than ranked low: their row
    labels are identical to the primary bank's (存放比, 總放款, 營業費用) but
    the figures are a different company's, often in a different currency."""
    lines = build_raw_lines(doc_path)
    if term_spec.search_start:
        lines = restrict_section(lines, term_spec.search_start, term_spec.search_end)
        if lines is None:
            return None
    headings = collect_headings(lines)
    best = None
    for table in parse_pipe_tables(lines):
        heading = nearest_heading(headings, table["line_idx"])
        result = find_value_in_table(table, term_spec, heading=heading, prefer_quarterly=prefer_quarterly,
                                      require_absolute=require_absolute)
        if result is None:
            continue
        value, matched_label, period_label, strength, entity, is_percent = result
        key = _rank_key(strength, entity, period_label, prefer_quarterly, primary_aliases)
        if key[1] == 0:
            continue
        if best is None or key > best[0]:
            best = (key, value, matched_label, period_label, entity, is_percent,
                    detect_unit_scale(lines, table))
    return best


def find_term_value(folder, term_spec, verbose=False, prefer_quarterly=False, primary_aliases=None,
                     require_absolute=False):
    """Search every .md file/table in `folder` for term_spec and return the
    BEST match found across the whole folder (see _rank_key: highest
    match_strength first, then a bank-named entity over a non-bank one),
    not just the first one encountered - a generic term can be an exact or
    substring match in more than one entity's table (e.g. 營業費用 appears,
    identically named, in both a bank subsidiary's own figures and its FHC
    parent's consolidated figures), and the first-found one isn't
    necessarily the right one. Stops early only once a same-entity-type
    exact match is found (rank (3, 1) - nothing can outrank it).
    prefer_quarterly: see find_value_in_table/_pick_latest_period. Returns
    (matched_label, value, source_file, period_label, is_percent,
    unit_scale), or None if term_spec doesn't appear in any table.
    NOTE the first two are the OPPOSITE way round from find_value_in_table,
    which this wraps: that one returns (value, matched_label, ...). Both are
    6-tuples, both are consumed positionally, and the two orders are pinned
    side by side in test_l2_lookup.py - do not "fix" one to match the other
    without changing every caller.
    unit_scale is the multiplier to 十億元 for the table the value came off
    (see detect_unit_scale); the raw as-printed value is returned unscaled
    so unitless callers can ignore it."""
    best = None  # (rank_key, value, matched_label, source_file, period_label, is_percent, unit_scale)
    best_possible = (3, 2, 1) if prefer_quarterly else (3, 2, 0)
    for doc_path in sorted(Path(folder).rglob("*.md")):
        local = _best_match_in_file(doc_path, term_spec, prefer_quarterly=prefer_quarterly,
                                     primary_aliases=primary_aliases, require_absolute=require_absolute)
        if local is None:
            continue
        key, value, matched_label, period_label, _entity, is_percent, unit_scale = local
        if best is None or key > best[0]:
            best = (key, value, matched_label, doc_path.name, period_label, is_percent, unit_scale)
        if best is not None and best[0] == best_possible:
            break
    if best is None:
        return None
    _key, value, matched_label, source_file, period_label, is_percent, unit_scale = best
    if verbose:
        unit_note = "" if unit_scale == 1.0 else f" [x{unit_scale:g} -> 十億元]"
        print(f"[{source_file}] '{term_spec.name}' -> '{matched_label}' @ {period_label} = {value!r}{unit_note}")
    return matched_label, value, source_file, period_label, is_percent, unit_scale


def extract_term(folder, term_spec, verbose=False):
    """Scan every .md file/table in `folder` for term_spec and return every
    match found (unlike find_term_value, which returns only the single
    strongest). Returns a list of {term, label_in_doc, value, source_file,
    period_label, is_percent} - is_percent (see find_value_in_table) lets
    the caller print '%' on values it wouldn't otherwise know are ratios
    (unlike the curated summary, this ad-hoc search has no RATIO_TERMS/
    BALANCE_TERMS list to tell it in advance)."""
    results = []
    for doc_path in sorted(Path(folder).rglob("*.md")):
        lines = build_raw_lines(doc_path)
        if term_spec.search_start:
            restricted = restrict_section(lines, term_spec.search_start, term_spec.search_end)
            if restricted is None:
                continue
            lines = restricted
        headings = collect_headings(lines)
        for table in parse_pipe_tables(lines):
            heading = nearest_heading(headings, table["line_idx"])
            result = find_value_in_table(table, term_spec, heading=heading)
            if result is None:
                continue
            value, matched_label, period_label, _strength, _entity, is_percent = result
            results.append({
                "term": term_spec.name, "label_in_doc": matched_label, "value": value,
                "source_file": doc_path.name, "period_label": period_label, "is_percent": is_percent,
            })
            if verbose:
                print(f"[{doc_path.name}] '{term_spec.name}' matched '{matched_label}' "
                      f"@ {period_label} -> {value!r}{'%' if is_percent else ''}")
    return results


# ---------------------------------------------------------------------------
# Curated summary: a fixed, business-relevant subset of the 32-term
# dictionary, rather than every term (several of which - 總資產, 淨收益,
# 稅前/稅後淨利, ROA/ROE, etc. - overlap conceptually with statements.py's
# financial-report extraction and aren't wanted in con-call output). This is
# the default mode when no term is given, matching statements.py's
# curated `summary` design: both ratio-type and balance-type terms get only
# the latest-quarter, as-reported value. An earlier version of this code
# additionally scaled ratio terms by x4/quarter_num to produce an
# "annualized" figure, on the assumption that a con-call table's ratios are
# cumulative year-to-date flows the same way financial-statement income
# figures are. Verification against real decks (中信金 2Q25/1Q26, 國泰金
# 2Q25) disproved that assumption for every ratio term here: NIM, 存放比,
# 放款均率, 存款均率, and the computed CIR are all already-annualized
# rates/point-in-time ratios (confirmed by comparing the same deck's 1Q/1H/
# 9M/FY columns, which stay in the same magnitude rather than scaling with
# period length) - so x4/quarter_num scaling produced impossible values
# (e.g. a >100% loan-to-deposit ratio, a >100% cost-income ratio) that don't
# correspond to anything in the source. Removed; only the as-reported value
# is shown, same as balance-type terms.
#
# Each term is looked up ONCE per folder (first match found, scanning files
# in sorted order) rather than accumulating every occurrence across every
# file, so the summary doesn't repeat the same term for every page it
# happens to appear on.
# ---------------------------------------------------------------------------

RATIO_TERMS = ["NIM", "放款均率", "存款均率", "存放利差"]
# CIR moved to the fin_report summary (statements.SUMMARY_LAYOUT), computed
# directly as abs(營業費用)/淨收益 from the SAME filing, no crosscheck - the
# con-call deck's own 營業費用/營業收入 table turned out to be a different
# scope (bank-level operating figures at deck-reported scale, e.g. 中信's
# own page showed 4Q25 revenue/opex an order of magnitude different from
# the individual-basis fin_report figures) that doesn't reconcile with
# fin_report's individual-entity CIR, so a con-call-only CIR here was
# comparing two genuinely different quantities under the same name.
# 其他放款 was dropped as an OUTPUT row: under LOAN_RECOMPOSITION below its
# value is always folded into 個人放款 (北富銀's 其他 column, 中信's
# 信用貸款與其他), so keeping it as its own row would double-count. It stays
# in HELPER_TERMS because 中信's 個人放款 formula still reads it as an input.
BALANCE_TERMS = ["企業放款", "房貸", "個人放款", "信用卡循環",
                 "法說會放款餘額合計", "法說會外幣放款"]
# Looked up like balance terms (same matching, same require_absolute), but
# never shown - they exist only to feed LOAN_RECOMPOSITION's formulas.
HELPER_TERMS = ["其他放款", "政府放款", "信貸", "其他個人授信其他",
                "海外子行", "海外分行", "OBU_DBU", "個人擔保貸款", "小額信貸"]

# 逾放比率/備抵呆帳/逾期放款 - bank-WIDE ratios (all loans, not credit-card
# specific - confirmed against a real download; an earlier version of this
# wrongly sourced two similarly-named but different metrics off the
# credit-card sheet instead), read from the SAME FSC "本國銀行資產品質評估
# 分析統計表" sheet as 逾期放款總額, no con-call source at all. Already
# percent-scale numbers there - see disclosures.NPL_RATIO_HEADER/
# NPL_COVERAGE_HEADER - passed straight through as "ratio" kind rows.
NPL_RATIO_TERM = "逾放比率"
NPL_COVERAGE_TERM = "備抵呆帳/逾期放款"

# This project's short bank names -> the exact legal names the FSC regulator
# spreadsheets use in their own bank-name column (see disclosures.TARGET_BANKS).
#
# DELIBERATELY NOT extended to the six entities added to BANK_PROFILES from
# their 114Q4 filings. The key here has to be the string the FSC's own
# spreadsheet prints, which is not necessarily a filing's registered name and
# cannot be established without reading a real regulator file - and this repo
# must not reach banking.gov.tw to find out (see AGENTS.md). Guessing
# "兆豐國際商業銀行" and being one character off looks exactly like a bank the
# regulator didn't publish that month. So an unmapped entity is refused rather
# than guessed, and now says so (see gov_name_note) instead of producing
# unexplained N/A rows.
_GOV_BANK_NAMES = {
    "北富銀": "台北富邦商業銀行",
    "國泰": "國泰世華商業銀行",
    "玉山": "玉山商業銀行",
    "中信": "中國信託商業銀行",
}


def gov_name_note(bank):
    """Why the regulator-sourced rows are N/A for `bank`, or "" if they
    shouldn't be. Same reasoning as the fin_report side: an N/A row and a
    correctly-extracted one are indistinguishable once exported, and "this
    entity was never mapped to a regulator name" is a different problem from
    "the regulator dataset was unreachable"."""
    if not bank:
        return "no entity resolved for this deck, so no regulator lookup was attempted"
    if bank not in _GOV_BANK_NAMES:
        return (f"'{bank}' has no FSC regulator name mapped (see _GOV_BANK_NAMES) - "
                f"the mapping needs the exact string the regulator's own spreadsheet "
                f"prints, which has not been confirmed for this entity")
    return ""


def _add(*values):
    """Sum, propagating None: if any input is missing the whole formula is
    unresolvable, which must surface as N/A rather than a partial total
    silently computed from whichever components happened to match."""
    if any(v is None for v in values):
        return None
    return sum(values)


def _sub(a, b):
    return None if a is None or b is None else a - b


# ---------------------------------------------------------------------------
# Per-bank loan recomposition.
#
# Every deck breaks its loan book down differently, and none of them match
# the shape this project wants (企業/房貸/個人/信用卡循環 as four disjoint
# buckets that sum to the total). The raw matched values therefore need
# per-bank arithmetic before they mean the same thing across banks:
#
#   北富銀: 個人放款 as printed BUNDLES 房貸 + 信貸 + 其他 + 信用卡循環, so
#           房貸/信用卡循環 must be pulled back out, leaving 信貸 + 其他.
#           企業授信 excludes 政府 lending, which belongs in the corporate
#           bucket here. Verified: 1172.2 + 154.0 + 1209.0 + 11.5 = 2546.7,
#           matching the deck's own 授信餘額合計 2546.6 (rounding).
#   中信:   figures are group-wide and include 海外子行 (overseas
#           subsidiaries), which this project scopes out; 企業放款 as printed
#           is TWD-only so FX lending has to be added back. 信用貸款與其他
#           still contains 信用卡循環, which the deck never breaks out (hence
#           the regulator fallback below). Verified: 1699 + 1393 + 299.1 +
#           17.9 = 3409.0 = the deck's 總放款 4246 - 海外子行 837.
#   玉山:   房貸 as printed covers only 房屋貸款; 個人擔保貸款 (fully
#           collateralised personal lending, per the deck's own footnote) is
#           the same economic bucket here. 個人放款 as printed bundles all
#           three personal categories, so the unsecured piece is 小額信貸
#           alone. Total is the sum of the four recomposed buckets, which
#           excludes 海外子行 - consistent with 中信's treatment.
#   國泰:   left alone - its deck already publishes the four buckets disjoint
#           and they sum exactly to its stated 放款總額 (942.6 + 1404.9 +
#           477.7 + 22.2 = 2847.4). Only 信用卡循環 is filled from the
#           regulator dataset, since the deck reports 信用卡放款 instead.
#
# Each formula reads from the RAW (pre-recomposition) values dict, so
# formulas never see each other's output and can be written in any order.
#
# Deliberately NOT folded into statements.BANK_PROFILES with the other
# per-entity tables. Two reasons. It is logic, not data - lambdas nested in a
# dict literal, the one construct static import checking can't see (which is
# why tools/undefined.py walks dicts and why the L2 suite executes all nine),
# so moving it carries risk the other tables don't. And it degrades safely on
# its own: an entity with no entry here simply gets no recomposition, which is
# the correct default (國泰 already has none), whereas a missing composites or
# aliases entry produces a wrong or absent number. The profile completeness
# check therefore has nothing to enforce about it.
# ---------------------------------------------------------------------------
LOAN_RECOMPOSITION = {
    "北富銀": {
        "企業放款": (lambda v: _add(v["企業放款"], v["政府放款"]),
                     "企業授信 + 政府"),
        "個人放款": (lambda v: _add(v["信貸"], v["其他個人授信其他"]),
                     "信貸 + 其他（其他個人授信餘額表）"),
    },
    "中信": {
        "企業放款": (lambda v: _sub(_add(v["企業放款"], v["法說會外幣放款"]), v["海外子行"]),
                     "台幣法人放款 + 外幣放款 − 海外子行"),
        "個人放款": (lambda v: _sub(v["其他放款"], v["信用卡循環"]),
                     "信用貸款與其他 − 信用卡循環"),
        "法說會放款餘額合計": (lambda v: _sub(v["法說會放款餘額合計"], v["海外子行"]),
                                "總放款 − 海外子行"),
        # Built additively from the deck's own 外幣放款成長率-依分子行
        # breakdown rather than as (總外幣放款 − 海外子行): the subtractive
        # form gave 670 vs this form's 682 for Q4 2025, because the
        # breakdown's three rows don't sum exactly to the headline total.
        # The additive form is the one that matches the intended scope
        # (onshore + offshore branches, excluding overseas subsidiaries).
        "法說會外幣放款": (lambda v: _add(v["海外分行"], v["OBU_DBU"]),
                            "海外分行 + OBU+DBU"),
    },
    "玉山": {
        "房貸": (lambda v: _add(v["房貸"], v["個人擔保貸款"]),
                 "房屋貸款 + 個人擔保貸款"),
        "個人放款": (lambda v: v["小額信貸"], "小額信貸"),
        "法說會放款餘額合計": (lambda v: _add(v["企業放款"],
                                                _add(v["房貸"], v["個人擔保貸款"]),
                                                v["小額信貸"], v["信用卡循環"]),
                                "企業放款 + 房貸 + 小額信貸 + 信用卡循環"),
    },
    "國泰": {},
}

# The four recomposed buckets should sum to the recomposed total. Verified
# against real Q4 2025 decks for all 4 banks: 中信 and 玉山 reconcile to the
# cent, 北富銀 is off 0.1 and 國泰 0.03 purely from the decks' own one-decimal
# rounding. Anything beyond this tolerance means a component matched the
# wrong row or a formula's assumption stopped holding for a new filing -
# surfaced as a note rather than silently accepted (same principle as
# statements's 資產=負債+權益 check).
# Sized for the decks' own rounding, not for arithmetic error: 中信's
# standalone loan table prints whole 拾億元 integers, so 4 components plus a
# total carry up to ~±2.5 of accumulated rounding - and that deck's OWN rows
# already don't tie (its components sum to 4,044 against a printed 總放款 of
# 4,043). A tolerance below this fires on the source's rounding rather than
# on anything the extractor did.
_LOAN_RECONCILE_TOLERANCE = 2.5
_LOAN_COMPONENTS = ["企業放款", "房貸", "個人放款", "信用卡循環"]


_CN_QUARTER_ORDINAL = {"一": 1, "二": 2, "三": 3, "四": 4,
                       "1": 1, "2": 2, "3": 3, "4": 4}
# '第四季' / '第 4 季' - the ordinal may be a Chinese numeral or an Arabic
# digit, with or without surrounding spaces (real decks use both: 國泰 and
# 中信 write 第四季, 玉山 writes '第 4 季').
_QUARTER_TITLE_RE = re.compile(r"第\s*([一二三四1-4])\s*季")
# '2025年全年法人說明會' - a full-year deck (confirmed in a real 富邦 deck)
# is the Q4 deck; it reports the same period, just named differently.
_FULL_YEAR_TITLE_RE = re.compile(r"全年|FY\d{2,4}|Full[- ]?Year", re.IGNORECASE)


def detect_con_call_quarter(folder):
    """Derive the current quarter number (1-4) from the first .md file in
    `folder` (sorted - typically the cover page). Con-call decks state the
    quarter directly in the title (e.g. '2025年第四季法人說明會' = Q4 2025)
    using the Western calendar year, not the ROC-calendar convention
    statements.py's derive_quarter_num assumes for financial statements -
    so this looks for that ordinal pattern instead of reusing it. A deck
    titled '全年'/full-year is treated as Q4, since that is the period it
    covers. Falls back to derive_quarter_num (ROC-date parsing) if no such
    title is found, in case a deck instead states a plain ROC-style date."""
    paths = sorted(Path(folder).rglob("*.md"))
    if not paths:
        return None
    text = paths[0].read_text(encoding="utf-8", errors="ignore")
    m = _QUARTER_TITLE_RE.search(text)
    if m:
        return _CN_QUARTER_ORDINAL[m.group(1)]
    if _FULL_YEAR_TITLE_RE.search(text):
        return 4
    return derive_quarter_num(paths[0])


# '2025年第四季法人說明會' - the Western calendar year in a deck's own title,
# needed to pick the right month of the FSC regulator datasets (which are
# indexed by ROC year). Bounded to 20xx so a stray 4-digit figure elsewhere
# on the cover page can't be mistaken for it.
_WESTERN_YEAR_RE = re.compile(r"(20\d{2})\s*年")


def detect_con_call_year(folder):
    """Western calendar year of the deck's reporting period, from the first
    .md file's title text. None if not stated - callers then fall back to
    the regulator datasets' latest published month instead of guessing."""
    paths = sorted(Path(folder).rglob("*.md"))
    if not paths:
        return None
    m = _WESTERN_YEAR_RE.search(paths[0].read_text(encoding="utf-8", errors="ignore"))
    return int(m.group(1)) if m else None


def collect_con_call_summary(folder, terms, verbose=False, bank=None):
    """Build the curated con-call summary. Returns a list of row dicts with
    TWO different shapes - write_summary_csv branches on `kind`, so anything
    else reading these rows has to as well:
      - ratio terms: {term, kind: "ratio", individual, period_label,
                      matched_label, source_file, note}
        (the value lives in `individual`, NOT in `value` - there is no
        `value` key on a ratio row at all)
      - balance terms: {term, kind: "balance", value, period_label,
                        matched_label, source_file, is_percent, note}
    `note` is on BOTH shapes (ratio rows carry it too) and reaches the
    exported CSV, so it is part of this contract rather than an internal
    detail. It carries the loan-reconciliation warning (see
    _LOAN_RECONCILE_TOLERANCE) on the loan total, and on the
    regulator-sourced rows (逾放比率, 備抵呆帳/逾期放款, and 信用卡循環 when
    the deck itself disclosed nothing) the reason that lookup produced
    nothing - see gov_name_note.
    CIR is no longer produced here - see statements.SUMMARY_LAYOUT (moved to
    the fin_report summary, computed directly from the same filing's
    營業費用/淨收益 with no crosscheck against this deck's figures - see
    RATIO_TERMS' comment for why). `bank` scopes matching to that bank's own
    subsidiary table
    (see PRIMARY_BANK_ENTITIES); auto-detected from the deck when omitted.
    period_label and matched_label are carried through to the output so the
    period a figure came from, and the row/column label it was read off, are
    both checkable without re-running in verbose mode."""
    quarter_num = detect_con_call_quarter(folder)
    if bank is None:
        bank = detect_bank(folder)
    primary_aliases = PRIMARY_BANK_ENTITIES.get(bank) if bank else None
    if verbose:
        print(f"Detected quarter: {quarter_num}; bank: {bank} "
              f"(primary entity: {primary_aliases})")

    rows = []
    for name in RATIO_TERMS:
        found = find_term_value(folder, terms[name], verbose=verbose,
                                 prefer_quarterly=True, primary_aliases=primary_aliases)
        rows.append({
            "term": name, "kind": "ratio", "note": "",
            "individual": found[1] if found else None,
            "matched_label": found[0] if found else None,
            "period_label": found[3] if found else None,
            "source_file": found[2] if found else None,
        })

    # Look up the output balance terms AND the recomposition helper terms in
    # one pass, keeping each one's full match record - the per-bank formulas
    # below read raw values, but the surviving rows still need their original
    # matched_label/period/source for auditability.
    raw = {}
    for name in BALANCE_TERMS + HELPER_TERMS:
        found = find_term_value(folder, terms[name], verbose=verbose,
                                 primary_aliases=primary_aliases, require_absolute=True)
        # Normalise to 十億元 before anything downstream touches the number:
        # LOAN_RECOMPOSITION combines figures ACROSS tables, and decks do not
        # use one unit throughout (see detect_unit_scale) - a 百萬元 loan
        # table minus a 拾億元 subsidiary table is off by 1000x otherwise.
        scaled = None
        if found and found[1] is not None:
            scaled = found[1] * found[5]
        raw[name] = {
            "value": scaled,
            "matched_label": found[0] if found else None,
            "period_label": found[3] if found else None,
            "source_file": found[2] if found else None,
            # require_absolute=True already excludes percent cells for
            # balance terms, so this should always be False in practice -
            # kept as a safety net rather than assuming it can never happen.
            "is_percent": found[4] if found else False,
        }

    # 信用卡循環 fallback: several decks never publish it standalone (中信
    # folds it into 信用貸款與其他; 國泰 reports only an aggregate 信用卡放款),
    # yet 中信's 個人放款 formula subtracts it. The FSC regulator dataset
    # publishes it for every bank monthly, so fill from there when - and only
    # when - the deck itself didn't provide it, converting 千元 to the 十億元
    # the loan tables use. 逾放比率/備抵呆帳/逾期放款 come from a DIFFERENT
    # FSC sheet (fetch_overdue_loans - the bank-wide NPL disclosure, not the
    # credit-card one) and have no con-call source at all. Both fetched once
    # per run, whenever the bank is known (not only when 信用卡循環 is missing).
    cc = npl = None
    western_year = detect_con_call_year(folder)
    need_gov = raw["信用卡循環"]["value"] is None
    if bank:
        try:
            from regulatorDatasets import disclosures
            legal = _GOV_BANK_NAMES.get(bank)
            if legal:
                if western_year and quarter_num:
                    y, m = disclosures.roc_year(western_year), disclosures.quarter_end_month(quarter_num)
                else:
                    y = m = None
                cc = disclosures.fetch_credit_card_revolving(y, m, banks=[legal], verbose=verbose)
                npl = disclosures.fetch_overdue_loans(y, m, banks=[legal], verbose=verbose)
                if cc:
                    cc["values_billions"] = {b: disclosures.thousands_to_billions(v)
                                              for b, v in cc["values"].items()}
        except Exception as e:  # network/site/parse - never sink the whole run
            if verbose:
                print(f"Regulator dataset unavailable ({e}) - 信用卡循環 fallback and "
                      f"逾放比率/備抵呆帳/逾期放款 will be N/A.")
            cc = npl = None

    npl_ratio_value = npl_ratio_period = None
    coverage_value = coverage_period = None
    if cc:
        legal = _GOV_BANK_NAMES.get(bank)
        if need_gov and cc["values_billions"].get(legal) is not None:
            raw["信用卡循環"] = {
                "value": cc["values_billions"][legal],
                "matched_label": f"循環信用餘額（金管會{cc['period']}）",
                "period_label": cc["period"], "source_file": None, "is_percent": False,
            }
            if verbose:
                print(f"信用卡循環 not in deck - using regulator figure "
                      f"{raw['信用卡循環']['value']:.2f} 十億元 ({cc['period']})")
    if npl:
        legal = _GOV_BANK_NAMES.get(bank)
        # Already percent-scale numbers in the source (see
        # disclosures.NPL_RATIO_HEADER/NPL_COVERAGE_HEADER) - passed through
        # as-is, never rescaled, so they display via format_pct exactly as
        # the regulator published them.
        if npl.get("npl_ratios", {}).get(legal) is not None:
            npl_ratio_value = npl["npl_ratios"][legal]
            npl_ratio_period = npl["period"]
        if npl.get("coverage_ratios", {}).get(legal) is not None:
            coverage_value = npl["coverage_ratios"][legal]
            coverage_period = npl["period"]

    # Per-bank recomposition (see LOAN_RECOMPOSITION). Formulas read the RAW
    # values captured above, so none of them can see another's output.
    raw_values = {k: v["value"] for k, v in raw.items()}
    recomposed = {}
    for term, (formula, description) in LOAN_RECOMPOSITION.get(bank, {}).items():
        recomposed[term] = (formula(raw_values), description)

    cc_note = gov_name_note(bank) if raw["信用卡循環"]["value"] is None else ""
    for name in BALANCE_TERMS:
        row = dict(raw[name], term=name, kind="balance",
                   note=cc_note if name == "信用卡循環" else "")
        if name in recomposed:
            value, description = recomposed[name]
            row["value"] = value
            # Once recomposed, the document's own row label no longer
            # describes the reported figure - show the formula instead, so
            # the number stays auditable back to its inputs.
            row["matched_label"] = f"重組：{description}"
        rows.append(row)

    # Reconciliation check on the recomposed loan book (see
    # _LOAN_RECONCILE_TOLERANCE).
    by_term = {r["term"]: r for r in rows if r["kind"] == "balance"}
    total_row = by_term.get("法說會放款餘額合計")
    parts = [by_term[t]["value"] for t in _LOAN_COMPONENTS if t in by_term]
    if total_row and total_row["value"] is not None and all(p is not None for p in parts):
        diff = sum(parts) - total_row["value"]
        if abs(diff) > _LOAN_RECONCILE_TOLERANCE:
            total_row["note"] = (
                f"components sum to {sum(parts):,.1f} vs total {total_row['value']:,.1f} "
                f"(off by {diff:,.1f}) - a component may have matched the wrong row, or a "
                f"recomposition rule may not hold for this filing")

    # These two exist only if the regulator lookup ran. When it didn't, say
    # which reason - an unmapped entity is a different problem from a
    # dataset that was unreachable, and they need different fixes.
    gov_note = gov_name_note(bank)
    for term, value, period in ((NPL_RATIO_TERM, npl_ratio_value, npl_ratio_period),
                                 (NPL_COVERAGE_TERM, coverage_value, coverage_period)):
        rows.append({
            "term": term, "kind": "ratio", "individual": value,
            "matched_label": f"金管會公布（{period}）" if value is not None else None,
            "period_label": period, "source_file": None,
            "note": gov_note if value is None else "",
        })
    return rows


def print_summary_rows(rows):
    for r in rows:
        where = f"{r.get('matched_label') or '-'} @ {r.get('period_label') or '-'} ({page_num(r['source_file'])})"
        if r["kind"] == "ratio":
            print(f"{r['term']}\t{format_pct(r['individual'])}\t{where}")
        else:
            print(f"{r['term']}\t{format_maybe_pct(r['value'], r.get('is_percent', False))}\t{where}")
        # Notes carry the loan-book reconciliation warning and the regulator
        # dataset's "requested month not published, used an earlier one"
        # flag - both are the kind of thing that must not stay buried.
        if r.get("note"):
            print(f"  NOTE: {r['note']}")


def write_summary_csv(folder, rows):
    out_path = Path(folder) / "con_call_summary_export.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "value", "label_in_doc", "period", "page", "note"])
        for r in rows:
            value = format_pct(r["individual"]) if r["kind"] == "ratio" else format_maybe_pct(r["value"], r.get("is_percent", False))
            writer.writerow([r["term"], value,
                              r.get("matched_label") or "", r.get("period_label") or "",
                              page_num(r["source_file"]), r.get("note") or ""])
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # --folder (not a positional) since `term` is also optional - two optional
    # positionals in a row would be ambiguous (a single token would always be
    # consumed by the first one, folder, rather than falling through to term).
    ap.add_argument("--folder", default=None,
                     help="Folder containing earnings-call .md files. If omitted, a "
                          "folder-picker dialog opens.")
    ap.add_argument(
        "term", nargs="?", default=None,
        help="Term name(s) from the config, comma/space separated. 'summary' (or omitting this "
             "argument) runs the curated business-relevant subset instead of every dictionary term.",
    )
    ap.add_argument("--config", default=str(_REPO_ROOT / "data" / "con_call_terms.json"),
                     help="Path to the term config JSON (default: the bundled data/con_call_terms.json)")
    ap.add_argument("--export", choices=["csv"], help="Write results to a CSV file instead of stdout")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print per-file/per-term detail")
    args = ap.parse_args()

    if args.folder is None:
        args.folder = pick_folder()
        if args.folder is None:
            ap.error("No folder selected.")

    terms = load_terms(args.config)

    if args.term is None or args.term.strip() == "summary":
        rows = collect_con_call_summary(args.folder, terms, verbose=args.verbose)
        if args.export == "csv":
            out_path = write_summary_csv(args.folder, rows)
            print(f"Wrote {len(rows)} row(s) to {out_path}")
            return
        print_summary_rows(rows)
        return

    names = [t for t in re.split(r"[,\s]+", args.term.strip()) if t]
    all_results = []
    for name in names:
        if name not in terms:
            raise KeyError(f"Unknown term '{name}'. Known: {list(terms)}")
        all_results.extend(extract_term(args.folder, terms[name], verbose=args.verbose))

    if not all_results:
        print("No matching terms found in any file.")
        return

    if args.export == "csv":
        out_path = Path(args.folder) / "con_call_export.csv"
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["term", "label_in_doc", "value", "period_label", "page"])
            for r in all_results:
                writer.writerow([r["term"], r["label_in_doc"], format_maybe_pct(r["value"], r["is_percent"]),
                                  r["period_label"], page_num(r["source_file"])])
        print(f"Wrote {len(all_results)} row(s) to {out_path}")
        return

    for r in all_results:
        print(f"{r['term']}\t{r['label_in_doc']}\t{format_maybe_pct(r['value'], r['is_percent'])}\t@{r['period_label']}\t({page_num(r['source_file'])})")


if __name__ == "__main__":
    main()

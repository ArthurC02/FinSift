"""Which row or column of a table is which reporting period.

Separate from matching because it answers a different question: matching asks
"is this the term I want", this asks "and which period is this number from".
Decks put periods on either axis and label them a dozen ways (4Q25, FY25,
114年12月, 2025上半年), so both the axis and the label need deciding before any
value can be read. Depends on nothing else in the package.
"""
import re




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

"""Which row or column of a table is which reporting period.

Separate from matching because it answers a different question: matching asks
"is this the term I want", this asks "and which period is this number from".
Decks put periods on either axis and label them a dozen ways (4Q25, FY25,
114年12月, 2025上半年), so both the axis and the label need deciding before any
value can be read. Depends on nothing else in the package.
"""
import re




# ---------------------------------------------------------------------------
# Period labels, and which axis carries them.
#
# Two orientations, sometimes both on one page; heterogeneous granularity in
# one table (FY25/1Q25/1H25/9M25/4Q25); and non-period columns mixed in among
# the real ones. So orientation is a MAJORITY vote and a parse failure
# excludes one column/row rather than the whole table.
#
# A cell that isn't purely a period label ('FY25/FY24 % Chg', '企業放款占比')
# must FAIL to parse - being excluded is the correct outcome, not a loss.
#   → docs/knowledge/reading-tables.md#期別標籤有幾種寫法
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



# Decks glue a footnote reference onto the NEWEST column's period label
# ('2Q25¹', 'Jun 25<sup>1</sup>'). Every period regex below is anchored with
# $, so leaving it unstripped silently excludes the newest column from
# ranking and reports the second-newest quarter as current.
#   → docs/knowledge/reading-tables.md#腳註上標
_FOOTNOTE_TAG_RE = re.compile(r"(?:<sup>\d+</sup>)+$", re.IGNORECASE)


_FOOTNOTE_SUP_RE = re.compile(r"[¹²³⁰⁴-⁹]+$")




def _strip_period_footnote(cell):
    cell = _FOOTNOTE_TAG_RE.sub("", cell)
    cell = _FOOTNOTE_SUP_RE.sub("", cell)
    return cell.strip()




def _normalize_year(y):
    """Two-digit -> 20xx, three-digit -> ROC (民國) -> Western, four-digit as-is.

    A 3-digit year here is always ROC: 民國114 = 2025. Left at 114 it sorts
    below every 19xx/20xx label, so a ROC-dated column can never win as "most
    recent". → docs/knowledge/reading-tables.md#民國年西元年季度
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
    labels across most rows. NOT assumed to be column 0 - some tables carry a
    leading qualifier column ('項目'='單季'/'全年') before the period column.
    Returns the best-scoring index, or None if no column has a majority."""
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
    mostly period labels, ('col_period', None) if header cells (excluding the
    first) are mostly period labels, or None if NEITHER axis looks like
    periods - the table is then skipped, never guessed at.
      → docs/knowledge/reading-tables.md#哪一軸是期別"""
    period_col = _detect_period_column(table["rows"])
    if period_col is not None:
        return ("row_period", period_col)
    header = table["header"]
    if len(header) > 1 and _majority_are_periods(header[1:]):
        return ("col_period", None)
    return None




def _rank_periods(period_items, prefer_quarterly=False):
    """period_items: list of (period_key, row_or_col), returned sorted
    most-to-least preferred - a LIST, not one 'best', so a caller can walk
    down to the first entry that actually has a non-blank value.

    prefer_quarterly ranks a same-year rank-4 entry (a true single Q4, or an
    equivalent month label) above a same-year rank-5 one (FY/bare-year/12M).
    It does NOT extend to ranks 1-3 - '9M25' is a genuinely earlier period,
    not an alternative to the annual figure.
      → docs/knowledge/reading-tables.md#單季與累計的取捨"""
    if not prefer_quarterly:
        return sorted(period_items, key=lambda t: t[0], reverse=True)
    latest_year = max(k[0] for k, _ in period_items)

    def sort_key(item):
        k, _ = item
        # KEEP the isinstance check. A real single quarter gets an INTEGER
        # rank; an N-month cumulative gets months/3, a float - and 12M25 lands
        # on exactly 4.0, so without it a 12-month CUMULATIVE figure outranks
        # FY25 and shows up in the 單季 column.
        is_q4_this_year = 1 if (k[0] == latest_year and k[1] == 4
                                and isinstance(k[1], int)) else 0
        return (is_q4_this_year, k)

    return sorted(period_items, key=sort_key, reverse=True)

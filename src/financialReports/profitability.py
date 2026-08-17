"""Parse the 獲利能力 disclosure table - the ratios the FILER states itself.

Kept apart from ratios.py because this is a reading problem and that is an
arithmetic one. Two real layouts have to be supported (row=entity/column=metric
and row=metric/column=period), each with its own heading, entity and period
conventions, and none of that has anything to do with what the numbers are
later used for.

Nothing here computes a ratio. ratios.py imports this; the reverse would be a
cycle.
"""
import re
from pathlib import Path

from core.numbers import parse_numeric
from core.tables import (build_raw_lines, restrict_section, parse_pipe_tables,
                         _split_row, _is_table_divider)
from core.text import despace_cjk, _contains_any, _is_toc_like, strip_footnote


_ROC_DATE_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月")




def derive_quarter_num(doc_path):
    """Parse the first ROC-calendar 'NNN年N月' date mentioned in the
    document (its title/header prose gives the current period's month) and
    return which quarter that month falls in (1-4), or None if no such date
    is found."""
    text = doc_path.read_text(encoding="utf-8", errors="ignore")
    m = _ROC_DATE_RE.search(text)
    if not m:
        return None
    month = int(m.group(2))
    return (month - 1) // 3 + 1




# ---------------------------------------------------------------------------
# Reported profitability table (獲利能力): ROA/ROE as directly disclosed by
# the filer, rather than computed. This is the authoritative source when
# present - a dedicated section listing 資產報酬率 (ROA) and 淨值報酬率 (ROE),
# each split into 稅前/稅後 (pretax/posttax), plus 純益率 (profit margin), for
# the consolidated group (合併) and every subsidiary, for the current quarter
# and the same quarter last year. Only 稅後 (posttax) figures are surfaced in
# the output (pretax is parsed internally where needed to keep column
# position correct, but isn't reported) - both the as-disclosed (cumulative
# year-to-date) figure and an annualized version (x 4/quarter number) are
# shown, since the disclosed figure is NOT itself annualized (e.g. a Q1
# filing's ROA reflects only ~1 quarter of return).
#
# Two layouts are supported, both seen in real filings:
#
# 1. Row = entity, column = metric (verified against real Cathay .md data,
#    where the header is plain prose above the table, not a real table
#    header row):
#      | 合併獲利能力 | 0.27 | 0.22 | 5.13 | 4.23 | 43.64 |
#      | 本 公 司 | 3.25 | 3.29 | 4.20 | 4.25 | 98.29 |
#    Column order is fixed by the regulatory disclosure format: ROA稅前,
#    ROA稅後, ROE稅前, ROE稅後, 純益率. Period (current quarter vs. same
#    quarter last year) is a block-level distinction (one table per period,
#    introduced by a ROC date-RANGE prose line). The 合併獲利能力
#    (consolidated) row is always cleanly pipe-delimited even where
#    subsidiary rows below it get corrupted by a stray parenthesis/pipe
#    artifact, so entity rows are reconstructed via continuation-folding
#    (like account-code rows) and values are picked by scanning tokens, not
#    trusting fixed cell counts.
#
# 2. Row = metric, column = period (seen in an E.Sun/玉山金控 filing, one
#    small table per entity, each introduced by a numbered heading line like
#    "1. 玉山金控及子公司"):
#      項目          114年12月31日  113年12月31日
#      資產報酬率  稅前   0.95          0.84
#                  稅後   0.80          0.68
#      淨值報酬率  稅前   15.54         13.17
#                  稅後   13.05         10.68
#      純益率      37.48         34.34
#    Entity is a block-level distinction (one table per entity); period is
#    the column distinction, identified from single ROC dates in the header.
#    NOTE: this path is built from that filing's raw PDF text (via pypdf),
#    not yet verified against the actual .md conversion for this layout -
#    treat as provisional pending validation against real converted output.
#    It assumes (per user confirmation) that a real header row is present
#    inside the table for this layout, unlike layout 1 above.
#
# Orientation is detected per-table from header content: a header containing
# ROC dates -> layout 2 (period columns); a header/first-row containing
# ROA/ROE/稅前/稅後-style text -> layout 1 (metric columns, entity rows).
# ---------------------------------------------------------------------------


_PROFITABILITY_SECTION_RE = re.compile(r"獲利能力")



# The company-keyword branch is anchored: an ordinary account name like
# 存放銀行同業 contains 銀行 but continues past it, and used to be read as an
# entity row - swallowing the rows beneath it as continuations. The first
# three alternatives stay PREFIX matches on purpose, so labels like
# 本公司及子公司 keep working; anchoring those too would trade this false
# positive for a false negative, which corrupts the primary path instead.
_ENTITY_ROW_RE = re.compile(r"^(合併|本\s*公\s*司|國\s*泰|.+(?:銀行|人壽|產險|證券|保險)(?:股份有限公司|公司)?$)")



_PERIOD_RANGE_RE = re.compile(
    r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*至\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)



# Metric tokens are small decimals (not comma-grouped), optionally negative
# in parens, or a bare "-" placeholder for "not available" - a bare "-"
# still consumes a position so the fixed ROA稅前/稅後/ROE稅前/稅後/純益率
# column order is preserved even when one metric is missing.
# The placeholder alternative covers the full-width dashes too: a "—" used to
# match nothing, so it held no position and every metric after it in the row
# was read one column early.
_METRIC_TOKEN_RE = re.compile(r"\(?\s*-?\d+(?:\.\d+)?\s*\)?|[-\u2014\u2013]")



_METRIC_NAMES = ["roa_pretax", "roa_posttax", "roe_pretax", "roe_posttax", "profit_margin"]



# Single ROC date, e.g. "114年12月31日" - used both to find period-label
# months (for annualization) and, in layout 2, to identify a table's period
# header columns.
_SINGLE_DATE_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")



# Numbered heading line introducing one entity's table in layout 2, e.g.
# "1. 玉山金控及子公司" (after despacing).
_ENTITY_HEADING_RE = re.compile(r"^\s*\d+[.．]\s*(.+?)\s*$")



_ROW_METRIC_PATTERNS = {
    "roa_posttax": (re.compile(r"資產報酬率"), re.compile(r"稅\s*後")),
    "roa_pretax": (re.compile(r"資產報酬率"), re.compile(r"稅\s*前")),
    "roe_posttax": (re.compile(r"淨值報酬率"), re.compile(r"稅\s*後")),
    "roe_pretax": (re.compile(r"淨值報酬率"), re.compile(r"稅\s*前")),
}


_PROFIT_MARGIN_ROW_RE = re.compile(r"純益率")




def quarter_num_from_period_label(label):
    """Derive which quarter (1-4) a period label refers to. Handles both a
    ROC date-range label ('115年1月1日至3月31日' - uses the END month) and a
    single ROC date label ('114年12月31日' - uses that month)."""
    if not label:
        return None
    m = _PERIOD_RANGE_RE.search(label)
    if m:
        return (int(m.group(4)) - 1) // 3 + 1
    m2 = _SINGLE_DATE_RE.search(label)
    if m2:
        return (int(m2.group(2)) - 1) // 3 + 1
    return None




def find_profitability_files(folder):
    """Return sorted list of .md files under `folder` whose text mentions a
    獲利能力 section at all (a loose first pass; files without an actual
    data table are filtered out later when no entity rows are found)."""
    return [p for p in sorted(Path(folder).rglob("*.md"))
            if _PROFITABILITY_SECTION_RE.search(p.read_text(encoding="utf-8", errors="ignore"))]




def is_metric_column_layout(lines):
    """Layout 1 check: does the first pipe-table row look like an entity
    name (row=entity, column=metric - the verified Cathay-style layout)?"""
    for _page_num, line in lines:
        if "|" not in line or _is_table_divider(line):
            continue
        cells = _split_row(line)
        if not cells:
            continue
        first = strip_footnote(cells[0])
        return bool(_ENTITY_ROW_RE.match(first))
    return False




def parse_single_date(cell):
    m = _SINGLE_DATE_RE.search(cell)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))




_DOT_DATE_RE = re.compile(r"^(\d{2,3})\.(\d{1,2})\.(\d{1,2})$")


# A bare fiscal year, no month/day at all ('114年度' - confirmed in real
# 國泰/北富銀 filings) - always the full year, so keyed as its year-end
# date (year, 12, 31), the same (year, month, day) shape every other
# format here resolves to.
_FISCAL_YEAR_RE = re.compile(r"^(\d{2,3})\s*年度$")




def parse_period_header_date(cell):
    """Parse a profitability-table column header into a (year, month, day)
    sort key, trying every header date format confirmed in a real filing:
    a single ROC year-month-day ('114年12月31日' - parse_single_date), a
    dot-separated ROC date ('114.12.31' - a real 中信 filing), a period
    RANGE ('115年1月1日至3月31日' - a real 國泰 filing, keyed off its END
    date, the same convention quarter_num_from_period_label uses), or a
    bare fiscal year ('114年度' - a real 國泰/北富銀 filing). None if the
    cell matches none of these."""
    cell = cell.strip()
    # Checked BEFORE parse_single_date: _SINGLE_DATE_RE is unanchored
    # (.search(), not .match()), so on a RANGE string like '114年1月1日至
    # 3月31日' it would spuriously match just the embedded START date
    # ('114年1月1日') and silently mislabel the period by its first day
    # instead of its correct quarter-end - confirmed on a real 國泰/北富銀
    # Q1 filing (value still came out right, since both the current and
    # prior-year columns got the same wrong-but-consistent treatment, but
    # the displayed period label was wrong).
    m = _PERIOD_RANGE_RE.search(cell)
    if m:
        return (int(m.group(1)), int(m.group(4)), int(m.group(5)))
    key = parse_single_date(cell)
    if key:
        return key
    m = _DOT_DATE_RE.match(cell)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _FISCAL_YEAR_RE.match(cell)
    if m:
        return (int(m.group(1)), 12, 31)
    return None




# A numbered heading only names an ENTITY if it reads like a company name.
# '1. 玉山金控及子公司' does; '1. 前言' and '2. 重要會計政策之說明' do not, yet
# _ENTITY_HEADING_RE matches any numbered line at all - so a 前言 on page one
# used to make every layout-3 table in the file look like layout 2's, and
# layout 3 skipped the lot.
# Duplicates decks._ENTITY_NAME_RE's vocabulary. Both belong in core/,
# but moving them is a refactor and this is a bug-fix commit - see the note
# added to TEST_DESIGN §7.
_ENTITY_HEADING_NAME_RE = re.compile(
    r"銀行|金控|控股|人壽|產險|保險|證券|投信|投顧|公司|Bank|FHC|Holdings|Financial|Life|Securities|Insurance")




def _has_entity_heading_before(lines, line_idx):
    """True if some earlier line is a numbered heading that names a company -
    i.e. a table layout 2 has already claimed, which layout 3 must not
    double-count.

    Deliberately narrows only THIS check, not layout 2's own attribution: if
    the two disagree about a heading, the same table is extracted twice with
    the same figures, once under the heading's text and once with entity=None.
    _select_profitability_entry prefers the entity=None copy (always in
    scope), so the worst case is a redundant entry, never a lost one.
    """
    for k in range(line_idx - 1, -1, -1):
        m = _ENTITY_HEADING_RE.match(lines[k][1])
        if m and _ENTITY_HEADING_NAME_RE.search(m.group(1)):
            return True
    return False




def extract_single_entity_profitability_tables(doc_path, verbose=False):
    """Layout 3 extractor: a flat single-entity 獲利能力 table - the shape
    actually used by every real INDIVIDUAL (個體) bank filing's own
    footnote disclosure seen so far (confirmed in real 中信/國泰/玉山
    filings), unlike layout 1 (entity-name row labels) or layout 2 (a
    numbered '1. some entity' heading before the table) which both assume
    a multi-entity comparison table. Here there is exactly one entity - the
    filing's own bank - so every row is attributed to it (entity: None;
    the caller already knows which bank the folder belongs to). Skips any
    table layout 2 already claimed (a numbered entity heading precedes it),
    so the same table is never double-counted under both layouts. Returns
    a list of {entity: None, period_label, quarter_num, roa_posttax,
    roe_posttax, profit_margin, source_file}."""
    lines = build_raw_lines(doc_path)
    results = []
    for table in parse_pipe_tables(lines):
        if _has_entity_heading_before(lines, table["line_idx"]):
            continue
        header = table["header"]
        period_cols = [(idx, parse_period_header_date(cell)) for idx, cell in enumerate(header)]
        period_cols = [(idx, key) for idx, key in period_cols if key is not None]
        if not period_cols:
            continue  # not a profitability (or any date-columned) table
        if not any(classify_metric_row(row) for row in table["rows"]):
            continue  # has date columns, but no ROA/ROE/profit-margin row - unrelated table
        period_cols.sort(key=lambda t: t[1], reverse=True)

        per_period = {idx: {} for idx, _key in period_cols}
        for row in table["rows"]:
            metric = classify_metric_row(row)
            if metric is None:
                continue
            for idx, _key in period_cols:
                if idx < len(row):
                    per_period[idx][metric] = parse_numeric(row[idx])

        for idx, (year, month, day) in period_cols:
            metrics = per_period[idx]
            results.append({
                "entity": None,
                "period_label": f"{year}年{month}月{day}日",
                "quarter_num": (month - 1) // 3 + 1,
                "roa_posttax": metrics.get("roa_posttax"),
                "roe_posttax": metrics.get("roe_posttax"),
                "profit_margin": metrics.get("profit_margin"),
                "source_file": doc_path.name,
            })
        if verbose and period_cols:
            print(f"[{doc_path.name}] layout-3 (single-entity) profitability table: {len(period_cols)} period(s)")
    return results




def classify_metric_row(cells):
    """Return one of 'roa_posttax', 'roa_pretax', 'roe_posttax',
    'roe_pretax', 'profit_margin', or None, from a layout-2 row's leading
    label cell(s) (metric name and 稅前/稅後 may be split across 2 cells)."""
    label = " ".join(cells[:2])
    if _PROFIT_MARGIN_ROW_RE.search(label):
        return "profit_margin"
    for name, (metric_re, tax_re) in _ROW_METRIC_PATTERNS.items():
        if metric_re.search(label) and tax_re.search(label):
            return name
    return None




def extract_transposed_entity_tables(doc_path, verbose=False):
    """Layout 2 extractor: metrics as rows, periods as columns, one table
    per entity, entity identified by the nearest preceding numbered heading
    line (e.g. '1. 玉山金控及子公司'). See the module-level comment above for
    the verification caveat on this layout. Returns a list of
    {entity, period_label, quarter_num, roa_posttax, roe_posttax,
    profit_margin, source_file}."""
    lines = build_raw_lines(doc_path)
    results = []
    for table in parse_pipe_tables(lines):
        header = table["header"]
        # parse_period_header_date, not parse_single_date: the latter is
        # unanchored, so on a RANGE header ("115年1月1日至3月31日") it matched
        # the embedded START date and the period was labelled by its first
        # day instead of its quarter end. Layout 3 already used the
        # range-aware parser; this extractor never got the same fix.
        period_cols = [(idx, parse_period_header_date(cell)) for idx, cell in enumerate(header)]
        period_cols = [(idx, key) for idx, key in period_cols if key is not None]
        if not period_cols:
            continue  # not a period-columns (layout 2) table
        period_cols.sort(key=lambda t: t[1], reverse=True)  # most recent period first

        entity = None
        for k in range(table["line_idx"] - 1, -1, -1):
            m = _ENTITY_HEADING_RE.match(lines[k][1])
            if m:
                entity = m.group(1).strip()
                break
        if entity is None:
            continue  # can't attribute this table to an entity; skip rather than guess

        per_period = {idx: {} for idx, _key in period_cols}
        for row in table["rows"]:
            metric = classify_metric_row(row)
            if metric is None:
                continue
            for idx, _key in period_cols:
                if idx < len(row):
                    per_period[idx][metric] = parse_numeric(row[idx])

        for idx, (year, month, day) in period_cols:
            metrics = per_period[idx]
            period_label = f"{year}年{month}月{day}日"
            results.append({
                "entity": entity,
                "period_label": period_label,
                "quarter_num": (month - 1) // 3 + 1,
                "roa_posttax": metrics.get("roa_posttax"),
                "roe_posttax": metrics.get("roe_posttax"),
                "profit_margin": metrics.get("profit_margin"),
                "source_file": doc_path.name,
            })
        if verbose:
            print(f"[{doc_path.name}] transposed profitability table for entity '{entity}': "
                  f"{len(period_cols)} period(s)")
    return results




def group_rows_by_entity(lines):
    """Same continuation-folding idea as group_rows_by_code, but keyed on an
    entity label (footnote-stripped) recognized by _ENTITY_ROW_RE instead of
    a coding-dictionary code, since 獲利能力 entity rows aren't in the coding
    workbook at all."""
    entries = []
    current = None  # (entity, cells)
    for _page_num, line in lines:
        if "|" not in line or _is_table_divider(line):
            continue
        cells = _split_row(line)
        if not cells:
            continue
        first = strip_footnote(cells[0])
        if _ENTITY_ROW_RE.match(first):
            if current is not None:
                entries.append(current)
            current = (first, list(cells))
        elif current is not None:
            current[1].extend(cells)
    if current is not None:
        entries.append(current)
    return entries




def extract_metrics(cells):
    """Return {roa_pretax, roa_posttax, roe_pretax, roe_posttax,
    profit_margin} parsed positionally from an entity row's cells (skipping
    the leading label cell), None for any metric missing or unavailable."""
    text = " ".join(cells[1:])
    tokens = _METRIC_TOKEN_RE.findall(text)
    result = {}
    for i, name in enumerate(_METRIC_NAMES):
        result[name] = parse_numeric(tokens[i]) if i < len(tokens) else None
    return result




def find_profitability_entries(folder, verbose=False):
    """Scan `folder` for 獲利能力 disclosure tables in either supported
    layout. Returns a flat list of {entity, period_label, quarter_num,
    roa_posttax, roe_posttax, profit_margin, source_file}, one entry per
    (entity, period) found. Empty list if no file has either layout's data
    table (a bare mention of 獲利能力 in passing doesn't count)."""
    results = []
    for doc_path in find_profitability_files(folder):
        lines = build_raw_lines(doc_path)

        # Layout 1: row=entity/column=metric, one table per period block
        # (block boundaries are ROC date-RANGE prose lines).
        boundaries = [i for i, (_pn, line) in enumerate(lines) if _PERIOD_RANGE_RE.search(line)]
        for bi, start in enumerate(boundaries):
            end = boundaries[bi + 1] if bi + 1 < len(boundaries) else len(lines)
            block_lines = lines[start:end]
            if not is_metric_column_layout(block_lines):
                continue
            m = _PERIOD_RANGE_RE.search(block_lines[0][1])
            period_label = m.group(0) if m else None
            quarter_num = quarter_num_from_period_label(period_label)
            entries = group_rows_by_entity(block_lines)
            for entity, cells in entries:
                metrics = extract_metrics(cells)
                results.append({
                    "entity": entity, "period_label": period_label, "quarter_num": quarter_num,
                    "roa_posttax": metrics["roa_posttax"], "roe_posttax": metrics["roe_posttax"],
                    "profit_margin": metrics["profit_margin"], "source_file": doc_path.name,
                })
            if entries and verbose:
                print(f"[{doc_path.name}] layout-1 profitability block '{period_label}': {len(entries)} entit(y/ies)")

        # Layout 2: row=metric/column=period, one table per entity.
        results.extend(extract_transposed_entity_tables(doc_path, verbose=verbose))

        # Layout 3: row=metric/column=period, single entity (no numbered
        # heading) - the common shape in an individual (個體) filing.
        results.extend(extract_single_entity_profitability_tables(doc_path, verbose=verbose))
    return results



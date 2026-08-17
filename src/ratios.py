"""ROA/ROE: the filer's own disclosed 獲利能力 table, and the manual formula.

Two jobs that are really one: parse whichever of the two disclosure layouts a
filing uses, and - independently - recompute the same ratios from the balance
sheet and income statement so the two can be checked against each other.
Neither number is allowed to silently overwrite the other; see collect_roa_roe.

Depends on core/ and on entities (compute_ratios needs the per-entity code
overrides and the label fallbacks). Must not import summary - summary imports
this.
"""
import csv
import re
from pathlib import Path

from core.lookup import find_code_value
from core.numbers import parse_numeric, nth_value, format_value, annualize, format_pct
from core.tables import build_raw_lines, restrict_section, parse_pipe_tables, _split_row, _is_table_divider
from core.text import despace_cjk, _contains_any, _is_toc_like, page_num, strip_footnote
from entities import BANK_NAME_ALIASES, SUMMARY_CODE_OVERRIDES, SUMMARY_LABEL_FALLBACKS




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
# Duplicates callfinder._ENTITY_NAME_RE's vocabulary. Both belong in core/,
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




def compute_ratios(folder, bank, coding_path=None, verbose=False):
    """Compute ROA(稅後年化) and ROE(稅後年化) from the same account codes
    and per-bank override/label-fallback logic SUMMARY_LAYOUT already uses
    (10000=資產, 30000=權益, 64000/63000=稅後淨利 per bank) via
    find_code_value. It used to go through a section-marker-restricted
    lookup (find_statement_rows, since deleted as dead code), which turned
    out to be unreliable in practice (a real 國泰 filing's income-statement
    page never repeats its own '...綜合損益表' section title, so the marker
    search silently found nothing even though the code rows were right
    there) and, separately, used codes (19999/39999/69000) left over from
    before this project's industry-coding-dictionary rewrite - confirmed
    absent from the current 金控業/金融業/保險業 workbooks entirely (that
    mapping was RATIO_CODES, also now deleted). Raises RuntimeError with a
    clear message if any required code/quarter can't be found.
    coding_path is unused now (kept as a parameter for backward
    compatibility with existing callers) since find_code_value doesn't
    need a coding dictionary - it matches raw document codes directly."""
    overrides = SUMMARY_CODE_OVERRIDES.get(bank, {})
    net_income_code = overrides.get("64000", "64000")

    def get(code, period, label, label_fallback=None):
        found = find_code_value(folder, code, period=period, verbose=verbose, label_fallback=label_fallback)
        if found is None:
            raise RuntimeError(f"Code {code} ({label}) not found in any file.")
        _label, value, source_file = found
        if value is None:
            raise RuntimeError(f"Code {code} ({label}) was found but had no period-{period} value.")
        return value, source_file

    net_income, inc_source = get(net_income_code, 1, "本期稅後淨利（淨損）",
                                  SUMMARY_LABEL_FALLBACKS.get("64000"))
    assets_cur, bs_source = get("10000", 1, "資產總計, current quarter", SUMMARY_LABEL_FALLBACKS.get("10000"))
    assets_prev, _ = get("10000", 2, "資產總計, last quarter", SUMMARY_LABEL_FALLBACKS.get("10000"))
    equity_cur, _ = get("30000", 1, "權益總計, current quarter", SUMMARY_LABEL_FALLBACKS.get("30000"))
    equity_prev, _ = get("30000", 2, "權益總計, last quarter", SUMMARY_LABEL_FALLBACKS.get("30000"))

    bs_path = next(Path(folder).rglob(bs_source), None)
    quarter_num = derive_quarter_num(bs_path) if bs_path else None
    if quarter_num is None:
        raise RuntimeError(f"Couldn't determine the current quarter number from {bs_source}.")

    # net_income IS a year-to-date cumulative figure, not a standalone
    # current-quarter one - confirmed directly from real filings' own
    # income-statement column headers: a Q4/annual filing's column reads
    # "114年度金額" (FY114 amount - the FULL YEAR, e.g. a real 國泰 filing),
    # while a Q2 filing's column reads "一月一日至六月三十日" (Jan 1 - Jun
    # 30, i.e. H1 CUMULATIVE, not Q2 alone - a real 中信 filing). An earlier
    # version of this function assumed the opposite (single-quarter, not
    # cumulative) and annualized with a flat x4 regardless of quarter - for
    # a Q4 filing (already the full year) that quadruples an already-annual
    # figure, which is exactly the ~4x-too-high result three different
    # banks' Q4 crosschecks showed before this fix. Dividing by quarter_num
    # first correctly reduces to a no-op at quarter_num=4 (already annual)
    # and projects a partial-year cumulative figure to a full year otherwise.
    # A zero average balance is a data problem of the same class as a missing
    # code, so it has to surface the same way. Letting the division raise
    # ZeroDivisionError instead broke this function's documented contract
    # ("Raises RuntimeError ..."), and since ZeroDivisionError is NOT a
    # subclass of RuntimeError, collect_roa_roe's `except RuntimeError` let it
    # through and took down the whole run rather than degrading to
    # "cross-check unavailable" the way every other lookup failure does.
    avg_assets = (assets_cur + assets_prev) / 2
    avg_equity = (equity_cur + equity_prev) / 2
    for label, denom in (("資產總計 (10000)", avg_assets), ("權益總計 (30000)", avg_equity)):
        if denom == 0:
            raise RuntimeError(f"Average {label} across the two periods is 0 - "
                               f"can't compute a ratio from it.")

    roa = net_income / avg_assets / quarter_num * 4
    roe = net_income / avg_equity / quarter_num * 4

    if verbose:
        print(f"Balance sheet: {bs_source} | Income statement: {inc_source} | quarter: Q{quarter_num}")
        print(f"  net_income ({net_income_code}) = {format_value(net_income)}")
        print(f"  total_assets (10000)  = {format_value(assets_cur)} (current), {format_value(assets_prev)} (last quarter)")
        print(f"  total_equity (30000)  = {format_value(equity_cur)} (current), {format_value(equity_prev)} (last quarter)")

    return {"roa": roa, "roe": roe, "quarter_num": quarter_num}




def _select_profitability_entry(entries, bank):
    """Pick the most-recent, best-scoped entry from find_profitability_entries'
    output. entity=None (layout 3 - a filing's single entity) always counts
    as in-scope, since there's nothing else it could be; otherwise prefer an
    entry whose entity text names `bank` (via BANK_NAME_ALIASES), falling
    back to any entity if none matches. Among the scoped candidates, prefers
    one that actually has a value, then the most recent period. None if
    `entries` is empty."""
    if not entries:
        return None
    aliases = BANK_NAME_ALIASES.get(bank, []) if bank else []
    scoped = [e for e in entries
              if e.get("entity") is None or (aliases and any(a in e["entity"] for a in aliases))]
    pool = scoped or entries
    with_value = [e for e in pool if e["roa_posttax"] is not None or e["roe_posttax"] is not None]
    pool = with_value or pool

    def period_key(e):
        m = parse_single_date(e["period_label"]) if e["period_label"] else None
        return m or (0, 0, 0)

    return max(pool, key=period_key)




# Two as-disclosed ROA/ROE readings for the SAME quarter shouldn't diverge
# by more than this factor if they're measuring the same thing the same
# way; a bigger gap flags a likely convention mismatch (e.g. one figure is
# a raw cumulative-YTD number, the other already annualized) worth a human
# double-check, rather than silently picking one.
_ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR = 2.0



# Wide, deliberately generous plausibility bounds - catch a grossly
# implausible value (e.g. a decimal/percent parsing bug producing ~92%
# instead of ~0.92%), not legitimate variation. Grounded in real observed
# data across all 4 currently-supported banks this session: ROA(稅後)
# ranged 0.24%-1.12%, ROE(稅後) ranged 3.33%-15.02%. Allows negative
# values too, since a quarterly loss is a real result, not a bug - this is
# a sanity check on magnitude, not a claim about which sign is expected.
_ROA_PLAUSIBLE_MIN, _ROA_PLAUSIBLE_MAX = -5.0, 5.0     # percent


_ROE_PLAUSIBLE_MIN, _ROE_PLAUSIBLE_MAX = -50.0, 50.0   # percent




def collect_roa_roe(folder, bank, coding=None, concall_roa=None, concall_roe=None, verbose=False):
    """ROA/ROE for the curated fin_report summary, in priority order:
      1. the filing's own reported 獲利能力 disclosure table (any of the 3
         layouts - see find_profitability_entries), used AS DISCLOSED, with
         NO further annualizing applied to it. An earlier version of this
         project always scaled the disclosed figure by x4/quarter_num,
         assuming Taiwanese banks uniformly report a not-yet-annualized
         cumulative rate here. Cross-checking real filings for 3 different
         banks across 2 periods each disproved that: 中信 and 玉山's Q1
         disclosed rate was roughly the SAME magnitude as their own Q4
         rate (already annualized), while 國泰's Q1 rate was roughly 1/4 of
         its Q4 rate (genuinely not yet annualized) - i.e. the convention
         is NOT consistent across banks, so blindly scaling is wrong for
         at least some of them. Showing the as-disclosed number, with an
         independent cross-check alongside it (see below), is the only
         choice that doesn't risk silently fabricating a wrong figure.
      2. concall_roa/concall_roe - an earnings-call deck's own reported
         figure, supplied by the caller (runfinder.py) rather than looked
         up here, since this module can't import callfinder.py's term
         matching without a circular dependency.
      3. this fin folder's own manual formula (compute_ratios) as a last
         resort, clearly labeled as an approximation.
    Regardless of which source wins, the manual formula is ALSO computed
    (when derivable) and returned as a cross-check value - never used to
    silently override a disclosed/concall figure - with a note when it
    diverges from the primary value by more than
    _ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR, since that gap itself is useful
    signal (see above) rather than something to hide.
    Returns {"roa": row_or_None, "roe": row_or_None}, each row a dict with
    term, value, matched_label, source_file, crosscheck_value, note."""
    entries = find_profitability_entries(folder, verbose=verbose)
    entry = _select_profitability_entry(entries, bank)

    manual = None
    try:
        manual = compute_ratios(folder, bank, coding, verbose=verbose)
    except RuntimeError as e:
        if verbose:
            print(f"ROA/ROE manual-formula cross-check unavailable: {e}")

    def build(term, metric_key, concall_value, manual_key):
        crosscheck = manual[manual_key] * 100 if manual else None  # manual formula is a plain ratio, not %
        if entry is not None and entry[metric_key] is not None:
            value, source, label, source_file = (
                entry[metric_key], "fin_report disclosure",
                f"{term}(年) 稅後 @ {entry['period_label']}", entry["source_file"],
            )
        elif concall_value is not None:
            value, source, label, source_file = concall_value, "concall", term, None
        elif crosscheck is not None:
            value, source, label, source_file = crosscheck, "fin_report manual formula (approximated)", term, None
            crosscheck = None  # it IS the value here, not a separate cross-check of itself
        else:
            return None

        notes = []
        if crosscheck is not None and value:
            # Compare MAGNITUDES, and treat opposite signs as divergent
            # outright. The numerator used to be max(value, crosscheck) with
            # no abs(), so a loss quarter - a perfectly ordinary input, the
            # plausible range runs down to -5%/-50% - could produce a ratio of
            # 1.0 or even a negative one and silently pass: max(-3.0, 1.0) is
            # 1.0, max(-3.0, -1.0) is -1.0. Opposite signs mean one source
            # says profit and the other says loss, which is the largest
            # disagreement there is and was likewise never reported.
            magnitude_ratio = max(abs(value), abs(crosscheck)) / min(abs(value), abs(crosscheck) or 1e-9)
            if (value > 0) != (crosscheck > 0) or magnitude_ratio > _ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR:
                # Don't assert WHY they diverge - the manual formula's own
                # single-quarter-net-income x4 assumption is itself unverified
                # for a Q4/annual filing (where "this period's" net income
                # code may cover the full year, not just Q4 alone), so a gap
                # here isn't reliable evidence the disclosed figure is wrong.
                notes.append(f"cross-check diverges: manual formula gives {crosscheck:.2f}% vs {value:.2f}% "
                             f"as-disclosed - could be a real discrepancy, or just this manual formula's own "
                             f"assumptions not holding for this filing; treat as a prompt to look closer, not "
                             f"as evidence either number is wrong")

        # Plausibility bound - a SEPARATE signal from the cross-check
        # divergence above (that flags disagreement BETWEEN two
        # measurements of the same thing; this flags a value that's
        # implausible in absolute terms regardless of source or agreement).
        # Deliberately wide (see the bounds' own comment), so this should
        # almost never fire on real data - both notes are kept if both fire,
        # rather than one overwriting the other.
        lo, hi = (_ROA_PLAUSIBLE_MIN, _ROA_PLAUSIBLE_MAX) if term == "ROA" else (_ROE_PLAUSIBLE_MIN, _ROE_PLAUSIBLE_MAX)
        if value is not None and not (lo <= value <= hi):
            notes.append(f"implausible value: {value:.2f}% is outside the expected [{lo}%, {hi}%] range for "
                         f"{term} - likely a parsing/scale/sign error, worth a manual check")

        note = "; ".join(notes)
        return {"term": term, "value": value, "matched_label": label, "source_file": source_file,
                "crosscheck_value": crosscheck, "note": note}

    return {
        "roa": build("ROA", "roa_posttax", concall_roa, "roa"),
        "roe": build("ROE", "roe_posttax", concall_roe, "roe"),
    }




def collect_ratio_rows(folder, bank, coding, verbose=False):
    """Run the ROA/ROE extraction (reported 獲利能力 table, falling back to
    the manual formula). Returns (rows, used_fallback) where rows is a list
    of dicts with period/entity/quarter/roa_posttax/roa_posttax_annualized/
    roe_posttax/roe_posttax_annualized/profit_margin/source_file."""
    entries = find_profitability_entries(folder, verbose=verbose)
    if entries:
        rows = []
        for e in entries:
            rows.append({
                "period": e["period_label"], "entity": e["entity"] or "(this filing)",
                "quarter": e["quarter_num"],
                "roa_posttax": e["roa_posttax"], "roa_posttax_annualized": annualize(e["roa_posttax"], e["quarter_num"]),
                "roe_posttax": e["roe_posttax"], "roe_posttax_annualized": annualize(e["roe_posttax"], e["quarter_num"]),
                "profit_margin": e["profit_margin"], "source_file": e["source_file"],
            })
        return rows, False

    if verbose:
        print("No 獲利能力 disclosure table found; falling back to manual formula.")
    try:
        result = compute_ratios(folder, bank, coding, verbose=verbose)
    except RuntimeError as e:
        print(f"NOTE: manual ROA/ROE fallback also failed - {e}")
        return [], True
    row = {
        "period": None, "entity": "(approximated)", "quarter": result["quarter_num"],
        "roa_posttax": None, "roa_posttax_annualized": result["roa"],
        "roe_posttax": None, "roe_posttax_annualized": result["roe"],
        "profit_margin": None, "source_file": None,
    }
    return [row], True




RATIO_COLUMNS = ["roa_posttax", "roa_posttax_annualized", "roe_posttax", "roe_posttax_annualized", "profit_margin"]




def print_ratio_rows(rows, used_fallback):
    print("\n=== ratios ===")
    if used_fallback and not rows:
        return  # collect_ratio_rows already printed why both the reported table and the fallback failed
    if used_fallback:
        print("NOTE: no reported 獲利能力 table found in this folder - the figures below are "
              "an approximation computed from balance sheet / income statement codes, not a "
              "company-reported number.")
        r = rows[0]
        print(f"ROA(稅後年化, approximated)\t{r['roa_posttax_annualized']:.2%}")
        print(f"ROE(稅後年化, approximated)\t{r['roe_posttax_annualized']:.2%}")
        return
    print("Reported profitability (獲利能力), directly from filing - 稅後 (posttax) only:")
    for r in rows:
        metrics_str = " ".join(f"{c}={format_pct(r[c])}" for c in RATIO_COLUMNS)
        print(f"{r['period']}\t{r['entity']}\t{metrics_str}\t({page_num(r['source_file'])})")




def write_ratio_csv(folder, rows, used_fallback):
    out_path = Path(folder) / "profitability_export.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if used_fallback and not rows:
            writer.writerow(["error", "no reported 獲利能力 table and manual fallback also failed - see console output"])
        elif used_fallback:
            writer.writerow(["metric", "value_annualized_approximated"])
            r = rows[0]
            writer.writerow(["ROA(稅後年化)", f"{r['roa_posttax_annualized']:.2%}"])
            writer.writerow(["ROE(稅後年化)", f"{r['roe_posttax_annualized']:.2%}"])
        else:
            writer.writerow(["period", "entity", "quarter"] + RATIO_COLUMNS + ["page"])
            for r in rows:
                writer.writerow([r["period"], r["entity"], r["quarter"]] +
                                 [format_pct(r[c]) for c in RATIO_COLUMNS] + [page_num(r["source_file"])])
    return out_path

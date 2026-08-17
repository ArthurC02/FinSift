"""Markdown table and account-row parsing shared by both extractors.

The only intra-core dependency: tables -> text (despace_cjk, _contains_any,
_is_toc_like). Nothing here knows what a financial statement is.
"""
import re

from core.text import _contains_any, _is_toc_like, despace_cjk


# ---------------------------------------------------------------------------
# Markdown table parsing (adapted from financial_keyword_finder/keyword_finder.py)
# ---------------------------------------------------------------------------


def build_raw_lines(path):
    """Return list[(page_num, line)] for a .md file. Markdown has no page
    concept, so page_num is always None. Despaced, blank lines dropped, and
    any dual-column-group balance sheet table (see _split_dual_column_tables)
    unfolded into two separate single-code-column blocks - every downstream
    consumer (group_rows_by_code, find_value_by_label, the profitability-
    table extractors) sees a normal-shaped table either way."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            out.append((None, despace_cjk(line)))
    return _split_dual_column_tables(out)


_CODE_HEADER_RE = re.compile(r"代碼$")


def _split_dual_column_tables(lines):
    """Detect a table whose header row has a REPEATED code-column marker
    (a cell ending in '代碼') and split its data rows into two independent
    contiguous blocks - a real, common balance-sheet layout confirmed in
    real 中信/玉山 individual filings: assets (its own code+label+value
    columns) and liabilities+equity (a SECOND, separate code+label+value
    column group) packed side by side into ONE physical table row, e.g.
    CTBC's '| 資產代碼 | 資產 | ... | 負債及權益代碼 | 負債及權益 | ... |' or
    玉山's plain '代碼' repeated verbatim for both sides. Every downstream
    matcher (group_rows_by_code, find_value_by_label) only ever looks at a
    row's first two cells for its code/label, so the second (right-hand)
    section was previously completely invisible to them - a code or label
    living there could never be found, no matter what it was. Splitting
    into two SEPARATE contiguous blocks (all left-half rows, then all
    right-half rows), rather than interleaving them, keeps continuation-
    folding correct within each side (a footnote-wrapped label on one side
    never gets folded across into the other side's still-open entry).
    Tables with only one code-column header (i.e. every other table this
    project has ever seen) pass through byte-identical - detection
    requires the marker to appear MORE THAN ONCE in the same header row, so
    there's no false-positive risk for the normal single-column-group case.
    `lines`: build_raw_lines()-shaped [(page_num, line), ...]. Returns the
    same shape, with only the affected tables' data-row spans replaced."""
    tables = parse_pipe_tables(lines)
    replacements = []  # (start_idx, end_idx, new_block) - end_idx exclusive
    for table in tables:
        header = table["header"]
        code_positions = [i for i, h in enumerate(header) if _CODE_HEADER_RE.search(h.strip())]
        if len(code_positions) < 2:
            continue
        split_idx = code_positions[1]
        data_start = table["line_idx"] + 2  # header, then the divider row
        data_end = data_start + len(table["rows"])
        left_block, right_block = [], []
        for row in table["rows"]:
            left, right = row[:split_idx], row[split_idx:]
            if any(c.strip() for c in left):
                left_block.append((None, "| " + " | ".join(left) + " |"))
            if any(c.strip() for c in right):
                right_block.append((None, "| " + " | ".join(right) + " |"))
        replacements.append((data_start, data_end, left_block + right_block))

    if not replacements:
        return lines
    new_lines = list(lines)
    # Splice from the end backwards so an earlier replacement's index isn't
    # thrown off by a later one changing the list's length first.
    for start, end, block in sorted(replacements, key=lambda r: r[0], reverse=True):
        new_lines[start:end] = block
    return new_lines


def restrict_section(lines, start_markers, end_markers):
    """Keep only lines from the first (non-TOC) line matching a start
    marker up to (not including) the next line matching an end marker.
    Returns None if no start marker is found in this file at all - callers
    should skip the file in that case, rather than risk scanning unrelated
    content under the wrong statement's section."""
    start_idx = next(
        (i for i, (_, line) in enumerate(lines)
         if not _is_toc_like(line) and _contains_any(line, start_markers)),
        None,
    )
    if start_idx is None:
        return None
    end_idx = len(lines)
    if end_markers:
        end_idx = next(
            (i for i in range(start_idx + 1, len(lines))
             if not _is_toc_like(lines[i][1]) and _contains_any(lines[i][1], end_markers)),
            len(lines),
        )
    return lines[start_idx:end_idx]


def _is_table_divider(line):
    """True only for a markdown TABLE divider ('|---|---:|'), not for a
    standalone horizontal rule ('---'), which is a section separator.
    Requiring a pipe is what tells them apart - and it matters: a bare
    '---' immediately after a table's last row made parse_pipe_tables'
    "a pipe row whose NEXT line is a divider must be the next table's
    header" guard fire, silently DROPPING that last row. Since decks list
    periods oldest-first, the dropped row was the newest quarter, so the
    whole table then resolved to the prior quarter (confirmed in a real
    中信金 2Q25 deck: 整體利差 returned 1Q25's 1.87% because the 2Q25 row
    sitting above a '---' separator was never parsed)."""
    if "|" not in line:
        return False
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells if c)


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


# ---------------------------------------------------------------------------
# Row grouping and value parsing.
#
# These converted statements can't be parsed via a conventional
# header-row + divider table structure: divider rows ("|---|...") are
# frequently misplaced (they show up after the FIRST data row rather than
# after a real header), so a header/divider-based table parser would
# misclassify that first data row as a header and silently drop it. There's
# also no usable dated header at all - periods appear only as prose above
# the table or in an unrelated mini-table, never as parseable column
# headers on the actual data rows.
#
# What IS consistent across balance sheet / income statement / cash flow
# pages in this filer's format:
#   - Every real data row's line begins with an account code cell that
#     matches the coding dictionary exactly.
#   - A long account name sometimes wraps onto one or more following
#     physical lines (still pipe-delimited, but with a blank/empty code
#     cell) before the value cells appear.
#   - Numeric period columns are always listed most-recent-period-first,
#     left to right, each formatted with comma-grouping (e.g.
#     "14,450,034,484"); percentage columns alongside them never have a
#     comma (values stay under 1000).
#
# So rather than parsing "tables", account rows are reconstructed directly:
# a new entry starts on any pipe-line whose first cell is a known code, and
# any following pipe-lines are folded into that same entry until the next
# known-code line appears. The target period's value is then just the Nth
# comma-grouped number found across the entry's cells (N=1 for the most
# recent period, the default).
# ---------------------------------------------------------------------------

_CODE_SHAPE_RE = re.compile(r"^[A-Za-z0-9]{3,8}$")


def _looks_like_code(cell):
    """True if `cell` is shaped like an account code (short alphanumeric
    token, e.g. '10000', '4xxxx', 'A00010') rather than continuation text
    (a wrapped account name's footnote-reference fragment, e.g.
    '（附註四、五、七、三一及三二）', which contains full-width punctuation
    and CJK characters and never matches this shape)."""
    return bool(_CODE_SHAPE_RE.match(cell))


_PERCENT_HEADER_RE = re.compile(r"^[%％]$|[%％]\s*$")


def percent_stride_map(lines):
    """Map each line index to the value-column STRIDE of its enclosing table.

    These filings alternate value, percent, value, percent - so reading every
    OTHER numeric cell finds the periods. But a table with no share column at
    all lists its periods consecutively, and striding past every second one
    made period 2 permanently unreachable there (the ROA/ROE cross-check
    silently disappeared as a result).

    The row itself cannot tell the two apart: the '%' appears only in the
    table's HEADER, never in a data cell, so
    ['10000', '資產總計', '6,120,884', '100.0'] and
    ['10000', '資產總計', '6,120,884', '5,900,000'] are the same shape. The
    header is the only available signal, hence this map.

    A pipe row immediately followed by a divider row is a table header (the
    same rule parse_pipe_tables uses). Lines before any header - and any line
    whose enclosing table couldn't be identified - keep stride 2, so anything
    this can't read behaves exactly as it did before.
    """
    strides, stride = [], 2
    for i, (_page_num, line) in enumerate(lines):
        if ("|" in line and i + 1 < len(lines)
                and _is_table_divider(lines[i + 1][1]) and not _is_table_divider(line)):
            cells = _split_row(line)
            stride = 2 if any(_PERCENT_HEADER_RE.search(c.strip()) for c in cells) else 1
        strides.append(stride)
    return strides


def group_rows_by_code(lines, code_dict):
    """lines: list[(page_num, line)], already restricted to one statement's
    section. Returns a list of (code, page_num, cells, stride):
      - cells: the flattened list of every cell from the code's line and any
        immediately following wrapped-continuation lines.
      - stride: the value-column stride of the table the code's own row came
        from (see percent_stride_map - pass it to nth_value so a table with
        no share column reads its periods consecutively instead of skipping
        every second one).
    Pinned by test_l1_tables.py: this used to be documented as a 3-tuple with
    the stride mentioned only in a trailing clause, which is how a shape claim
    goes stale without anything going red.

    A continuation line is one whose leading cell is either blank or isn't
    code-shaped (see _looks_like_code - covers a wrapped account name's
    footnote-only second physical line, e.g. '（附註...）', confirmed
    against a real filing). A leading cell that DOES look like a code ends
    the current entry, whether or not that code is itself in `code_dict`.
    This distinction matters because find_code_value() calls this with a
    code_dict containing just ONE code: under the old rule ("continue
    unless the next line's code IS one we're tracking"), every subsequent
    row in the table - each with its own perfectly normal, differently-
    coded leading cell - was misread as a "continuation" of the target row
    and folded in, contaminating nth_value()'s scan with numbers from
    unrelated later rows."""
    lines = list(lines)
    strides = percent_stride_map(lines)
    entries = []
    current = None  # (code, page_num, cells, stride)
    for i, (page_num, line) in enumerate(lines):
        if "|" not in line:
            continue
        if _is_table_divider(line):
            continue
        cells = _split_row(line)
        if not cells:
            continue
        first = cells[0].strip()
        if first and first in code_dict:
            if current is not None:
                entries.append(current)
            current = (first, page_num, list(cells), strides[i])
        elif first and _looks_like_code(first):
            # some other, differently-coded row - not a continuation of
            # `current`, and not one we're tracking either.
            if current is not None:
                entries.append(current)
            current = None
        elif current is not None:
            current[2].extend(cells)
    if current is not None:
        entries.append(current)
    return entries


def parse_pipe_tables(lines):
    """Standard markdown table parser: a header line immediately followed by
    a divider line, then consecutive pipe-delimited data rows. Used for
    layout 2 (row=metric/column=period), which assumes - per confirmation
    from the user about their conversion tool - that column headers are
    captured inside the table itself, unlike the messier prose-header
    layout 1 data already handled elsewhere in this file. Returns a list of
    {"header": [...], "rows": [[...]], "line_idx": int} (line_idx = index
    into `lines` of the header row, used to locate a preceding heading)."""
    tables = []
    i, n = 0, len(lines)
    while i < n:
        _pn, line = lines[i]
        if "|" in line and i + 1 < n and _is_table_divider(lines[i + 1][1]):
            header = _split_row(line)
            rows = []
            j = i + 2
            while j < n and "|" in lines[j][1] and not _is_table_divider(lines[j][1]):
                # A pipe line whose NEXT line is a divider is the header of
                # the FOLLOWING table, not a data row of this one. build_raw_lines
                # drops blank lines, so two tables separated only by a blank line
                # would otherwise merge - swallowing the second table's header as
                # a data row here and leaving its divider to break the outer scan,
                # losing that table entirely. That silently hid the quarterly NIM
                # table sitting under the annual one in a real 國泰 deck.
                if j + 1 < n and _is_table_divider(lines[j + 1][1]):
                    break
                rows.append(_split_row(lines[j][1]))
                j += 1
            tables.append({"header": header, "rows": rows, "line_idx": i})
            i = j
        else:
            i += 1
    return tables

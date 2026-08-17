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
    """Split a dual-column-group balance sheet (assets and liabilities+equity
    packed side by side into ONE physical row) into two independent blocks.
    Detected by a REPEATED code-column marker (a cell ending in '代碼') in the
    header; a single marker passes through byte-identical.

    Two SEPARATE contiguous blocks (all left-half rows, then all right-half),
    never interleaved - interleaving folds one side's wrapped label into the
    other side's still-open entry.
      → docs/knowledge/reading-tables.md#左右雙欄的資產負債表

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
    """True only for a markdown TABLE divider ('|---|---:|'), never for a
    standalone horizontal rule ('---'), which is a section separator.

    KEEP the pipe requirement: without it a bare '---' after a table's last
    row trips parse_pipe_tables' next-table-header guard and silently DROPS
    that row - and since decks list periods oldest-first, the dropped row is
    the newest quarter. → docs/knowledge/reading-tables.md#分隔列與水平線的差別"""
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
# Converted STATEMENTS cannot go through a header-row + divider table parser
# (misplaced dividers, no dated header anywhere). Account rows are
# reconstructed directly instead: a new entry starts on any pipe-line whose
# first cell is a known code, and following pipe-lines fold into it until the
# next code-shaped line.
#
# Decks are the opposite - their column headers ARE inside the table, so they
# go through parse_pipe_tables.
#   → docs/knowledge/reading-tables.md#為什麼財報不能用標準表格解析
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

    The ROW cannot tell stride 1 from stride 2: the '%' appears only in the
    table's HEADER, never in a data cell, so these are the same shape -
        ['10000', '資產總計', '6,120,884', '100.0']
        ['10000', '資產總計', '6,120,884', '5,900,000']
    The header is the only signal there is, hence this map.
      → docs/knowledge/reading-tables.md#值與百分比交錯stride

    A pipe row immediately followed by a divider row is a table header (the
    rule parse_pipe_tables uses). Anything unidentifiable keeps stride 2, the
    historical behaviour.
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
    Pinned by test_l1_tables.py (a 4-tuple, not 3 - the shape claim went stale
    once already without anything going red).

    A continuation line has a leading cell that is blank or NOT code-shaped. A
    cell that DOES look like a code ends the current entry, whether or not it
    is in `code_dict` - find_code_value passes a dict of ONE code, so a
    tracking-based rule folds every unrelated later row into the target and
    contaminates nth_value's scan.
      → docs/knowledge/reading-tables.md#續行的判準"""
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
    a divider line, then consecutive pipe-delimited data rows. For the
    layouts whose column headers really are inside the table (decks, and the
    transposed profitability tables) - not the prose-header statement layout
    handled above. Returns {"header", "rows", "line_idx"} per table
    (line_idx = the header row's index, used to locate a preceding heading)."""
    tables = []
    i, n = 0, len(lines)
    while i < n:
        _pn, line = lines[i]
        if "|" in line and i + 1 < n and _is_table_divider(lines[i + 1][1]):
            header = _split_row(line)
            rows = []
            j = i + 2
            while j < n and "|" in lines[j][1] and not _is_table_divider(lines[j][1]):
                # A pipe line whose NEXT line is a divider is the FOLLOWING
                # table's header, not a data row of this one - build_raw_lines
                # drops blank lines, so two tables separated only by a blank
                # line would otherwise merge and the second one vanish.
                #   → docs/knowledge/reading-tables.md#兩張表擠在一起
                if j + 1 < n and _is_table_divider(lines[j + 1][1]):
                    break
                rows.append(_split_row(lines[j][1]))
                j += 1
            tables.append({"header": header, "rows": rows, "line_idx": i})
            i = j
        else:
            i += 1
    return tables

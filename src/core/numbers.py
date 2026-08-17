"""Numeric parsing and display formatting shared by both extractors.

Depends on nothing else in core/ - these are pure value transforms.
"""
import re


_NUMERIC_STRIP_RE = re.compile(r"[,\$%％\s元仟千萬億NTnt]")


def parse_numeric(cell):
    """→ docs/knowledge/reading-tables.md#數字怎麼解析"""
    cell = cell.strip()
    if not cell or cell in ("-", "–", "—", "N/A", "NA", "n/a"):
        return None
    # Full-width parens as well as half-width - PDF conversions use either,
    # and a full-width negative parses as no number at all without this.
    negative = cell.startswith(("(", "（")) and cell.endswith((")", "）"))
    if negative:
        cell = cell[1:-1].strip()
    cleaned = _NUMERIC_STRIP_RE.sub("", cell).replace("−", "-")
    if not cleaned or cleaned == "-":
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if negative:
        value = -value
    return int(value) if value == int(value) else value


def nth_value(cells, occurrence, stride=2):
    """Return the `occurrence`-th (1-indexed) monetary value found across
    `cells`, left to right, skipping the leading code cell - or None if
    there are fewer than `occurrence` such values.

    Selection is POSITIONAL (walk numeric slots by `stride`), never by
    magnitude or comma-grouping: a comma-based guess skips a small
    no-separator value and lands on that row's PRIOR-year figure instead.
      → docs/knowledge/reading-tables.md#值與百分比交錯stride"""
    numeric_positions = [i for i, c in enumerate(cells[1:], start=1) if parse_numeric(c) is not None]
    # stride 2 = the usual value/percent alternation; stride 1 = a table with
    # no share column. The row alone can't tell them apart, so the caller
    # supplies it (core.tables.percent_stride_map); 2 is the historical default.
    value_positions = numeric_positions[0::stride]
    # `occurrence` is 1-indexed. Without the lower bound, 0 and negatives fall
    # through to Python's negative indexing and quietly return an OLDER period.
    if occurrence < 1 or occurrence > len(value_positions):
        return None
    return parse_numeric(cells[value_positions[occurrence - 1]])


def format_value(value):
    """Comma-group a numeric value for display (e.g. 14450034484 ->
    '14,450,034,484', -327473468 -> '-327,473,468'). N/A passes through."""
    if value is None:
        return "N/A"
    return f"{value:,}"


def annualize(value, quarter_num):
    """Scale a cumulative year-to-date ratio up to an annualized figure.
    None-safe (propagates None if the value or quarter number is unknown)."""
    if value is None or not quarter_num:
        return None
    return value * 4 / quarter_num


def format_pct(value):
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def format_maybe_pct(value, is_percent):
    """format_pct if is_percent, else format_value - for callers that don't
    know in advance whether a term is a ratio. parse_numeric strips the '%',
    so without this a matched '1.27%' prints as a bare '1.27', easily misread
    as 127% or 0.0127."""
    return format_pct(value) if is_percent else format_value(value)

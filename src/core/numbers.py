"""Numeric parsing and display formatting shared by both extractors.

Depends on nothing else in core/ - these are pure value transforms.
"""
import re


_NUMERIC_STRIP_RE = re.compile(r"[,\$%％\s元仟千萬億NTnt]")


def parse_numeric(cell):
    cell = cell.strip()
    if not cell or cell in ("-", "–", "—", "N/A", "NA", "n/a"):
        return None
    # Full-width parens as well as half-width: filings converted from PDF use
    # either, and only the half-width pair was recognised, so a full-width
    # negative parsed as no number at all and the row showed N/A.
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

    These filings' rows alternate value, percent, value, percent, ...
    (optionally followed by a trailing change-percent column) - so every
    OTHER numeric-parseable cell is a real value and the one right after
    it is that value's percent-of-total, never a value in its own right.
    Walking cells pairwise (value slot, then skip one percent slot) finds
    the occurrence-th value directly, and works regardless of magnitude -
    unlike an earlier version of this function that used a comma-grouping
    regex to guess 'value' vs 'percent' by whether the number had a
    thousands-separator. That guess broke on a real filing where a
    current-period value happened to be small enough (e.g. 64) to have no
    comma: the regex skipped straight past it to the NEXT comma'd number,
    which was that same row's PRIOR-year value instead, corrupting a
    downstream composite-term calculation by exactly that difference."""
    numeric_positions = [i for i, c in enumerate(cells[1:], start=1) if parse_numeric(c) is not None]
    # stride 2 = the usual value/percent/value/percent alternation; stride 1 =
    # a table with no share column, whose periods are listed consecutively.
    # The row alone can't tell them apart (the '%' lives in the header, never
    # in a data cell), so the caller supplies it - see
    # core.tables.percent_stride_map. Defaults to 2, the historical behaviour,
    # for any caller that has no table context.
    value_positions = numeric_positions[0::stride]
    # `occurrence` is 1-indexed. Without the lower bound, 0 and negatives fell
    # through to Python's negative indexing and quietly returned an OLDER
    # period (0 -> the oldest, -1 -> the first value), or raised IndexError
    # when the row was too short to reach that far back.
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
    """format_pct if is_percent, else format_value - for callers (like
    decks's ad-hoc term search) that don't already know in advance
    whether a term is a ratio, and so can't just always call format_pct.
    Without this, a matched cell like '1.27%' - parse_numeric strips the
    '%' sign, leaving the bare number 1.27 - would print as plain '1.27'
    with no indication it's a percentage, easily misread as 127% or 0.0127
    rather than the 1.27% it actually is."""
    return format_pct(value) if is_percent else format_value(value)

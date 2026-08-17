"""L0 characterization tests - TEST_DESIGN §3.1 / §3.2 / §3.3 (32 cases).

Expected values are CURRENT behaviour, not correct behaviour. Anything marked
`PINNED BUG` is wrong on purpose: if a refactor accidentally fixes it, these
must go red. Fixes belong in Phase 7, where the assertion gets flipped in the
same commit as the code change.
"""
import pytest

from financialReports.statements import _looks_like_code, nth_value, parse_numeric

# --------------------------------------------------------------------------
# §3.1 parse_numeric - ECT + BVT (16)
# --------------------------------------------------------------------------

PARSE_NUMERIC_CASES = [
    ("N1", "1,234", 1234),
    ("N2", "1.27", 1.27),
    ("N3", "(1,234)", -1234),
    # FIXED (was PINNED BUG #8): full-width parens were not recognised as a
    # negative, so the whole value was lost and the row showed N/A.
    ("N4", "（1,234）", -1234),
    ("N5", "−1234", -1234),
    ("N6", "1.27%", 1.27),
    ("N7", "14,450元", 14450),
    # STILL PINNED, deliberately - see test_N8_magnitude_suffix_is_stripped.
    ("N8", "2萬", 2),
    ("N9", "-", None),
    ("N10a", "—", None),
    ("N10b", "–", None),
    ("N11a", "N/A", None),
    ("N11b", "NA", None),
    ("N11c", "n/a", None),
    ("N12a", "", None),
    ("N12b", "   ", None),
    ("N13", "資產總計", None),
    ("N14", "0", 0),
]


@pytest.mark.parametrize("cell,expected", [c[1:] for c in PARSE_NUMERIC_CASES],
                         ids=[c[0] for c in PARSE_NUMERIC_CASES])
def test_parse_numeric(cell, expected):
    assert parse_numeric(cell) == expected


def test_N8_magnitude_suffix_is_stripped_by_design_not_by_accident():
    """The other half of §7 #8, deliberately NOT changed.

    A magnitude character in a cell is stripped rather than applied as a
    multiplier, so '2萬' reads as 2. Applying it would double-scale: units in
    these documents are declared at TABLE level (decks.detect_unit_scale
    reads '單位：新臺幣百萬元' from the header or the lines above the table)
    and every figure in that table is scaled once, there. A cell that restates
    its own unit - '1,234仟元' under a 仟元 header - is the common shape, and
    multiplying it would be a 1000x error on real filings.

    Which means '2萬' cannot be told from '1,234仟元' by the cell alone. Both
    stripping and multiplying are wrong for one of them; stripping is wrong
    for the rarer one. Pinned so a future refactor doesn't "fix" it into the
    dangerous direction.
    """
    assert parse_numeric("2萬") == 2
    assert parse_numeric("1,234仟元") == 1234
    assert parse_numeric("14,450元") == 14450


def test_parse_numeric_N1_returns_int():
    # The return TYPE changes with the value. Downstream Excel formatting keys
    # off isinstance(value, int) vs float, so the type is part of the contract.
    assert isinstance(parse_numeric("1,234"), int)


def test_parse_numeric_N2_returns_float():
    assert isinstance(parse_numeric("1.27"), float)


def test_parse_numeric_N15_float_string_collapses_to_int():
    # "0.0" parses to 0.0, and `value == int(value)` converts it to int.
    result = parse_numeric("0.0")
    assert result == 0 and isinstance(result, int)


def test_parse_numeric_N16_negative_zero_is_plain_zero():
    result = parse_numeric("(0)")
    assert isinstance(result, int) and str(result) == "0"


# --------------------------------------------------------------------------
# §3.2 nth_value - BVT (9). Highest-risk function in the codebase.
#
# `cells` is the WHOLE row including the leading code/label cell; nth_value
# skips cells[0] itself. Dropping that first cell from a fixture shifts every
# expected value by one position.
# --------------------------------------------------------------------------

PCT = ["C", "100", "10%", "90", "9%"]      # value/percent/value/percent
NOPCT = ["C", "100", "90"]                 # two periods, no percent columns

NTH_VALUE_CASES = [
    ("V1", PCT, 1, 100),
    ("V2", PCT, 2, 90),
    ("V3", NOPCT, 1, 100),
    # V4 still reads None at the DEFAULT stride, and that is now correct rather
    # than a bug: a bare row genuinely cannot say whether its second number is
    # the prior period or the first one's share. #1 was fixed by giving the
    # caller a way to say - see test_V4_fixed_* below.
    ("V4", NOPCT, 2, None),
    # FIXED (was PINNED BUG #2): occurrence is 1-indexed and now bounded below.
    # These used to fall through to Python's negative indexing and quietly
    # return an OLDER period - 0 gave the oldest, -1 gave the first value.
    ("V5", PCT, 0, None),
    ("V6", PCT, -1, None),
    ("V8", PCT, 3, None),
    ("V9", ["C", "N/A", "—"], 1, None),
]


@pytest.mark.parametrize("cells,occurrence,expected", [c[1:] for c in NTH_VALUE_CASES],
                         ids=[c[0] for c in NTH_VALUE_CASES])
def test_nth_value(cells, occurrence, expected):
    assert nth_value(cells, occurrence) == expected


def test_V4_fixed_a_no_percent_table_reads_its_second_period(monkeypatch):
    """FIXED (was PINNED BUG #1). The pairwise stride assumes
    value/percent/value/percent, so on a two-period table with no share column
    it skipped straight past period 2 - permanently N/A, which is what
    silently removed the ROA/ROE cross-check on those filings.

    The row alone is provably ambiguous: the '%' appears only in the table
    HEADER, never in a data cell, so NOPCT above is indistinguishable from one
    period plus its share. The stride now comes from the header instead.
    """
    assert nth_value(NOPCT, 2, stride=1) == 90
    assert nth_value(NOPCT, 1, stride=1) == 100
    # A table that DOES have share columns is untouched: stride 2 is the
    # default and every existing case above still holds.
    assert nth_value(PCT, 2) == 90


def test_nth_value_V7_negative_occurrence_on_single_value_returns_None():
    # FIXED (was PINNED BUG #2): the same missing guard, but with too few
    # values for the negative index to land it used to escape as an IndexError.
    assert nth_value(["C", "100"], -1) is None


# --------------------------------------------------------------------------
# §3.3 _looks_like_code - BVT + F2 shadowing (7)
# --------------------------------------------------------------------------

LOOKS_LIKE_CODE_CASES = [
    ("C1", "AB", False),          # len 2, below the 3-char floor
    ("C2", "A00", True),
    ("C3", "A0001234", True),     # len 8, the ceiling
    ("C4", "A00012345", False),   # len 9
    ("C5", "10000\n", True),      # `$` also matches before a trailing newline
    ("C6", "（附註四）", False),
]


@pytest.mark.parametrize("cell,expected", [c[1:] for c in LOOKS_LIKE_CODE_CASES],
                         ids=[c[0] for c in LOOKS_LIKE_CODE_CASES])
def test_looks_like_code(cell, expected):
    assert _looks_like_code(cell) is expected


def test_looks_like_code_C7_non_str_raises():
    """F2 / PINNED #19: fin.py defines _looks_like_code TWICE (L149 and
    L479) with DIFFERENT bodies. The live one is L479, which passes `cell`
    straight to re.match; L149's `str(cell).strip()` version has been dead
    since L479 was parsed.

    This is the only assertion that can tell the two apart. If a refactor
    collapses them onto the L149 body, this goes from TypeError to True and
    the test fails - which is the point. Phase 2.1 must delete the FIRST copy.
    """
    with pytest.raises(TypeError):
        _looks_like_code(10000)

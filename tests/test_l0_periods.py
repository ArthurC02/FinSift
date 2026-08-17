"""L0 characterization tests - TEST_DESIGN §3.5 / §3.6 / §3.7 (25 cases).

Period labels decide which column a figure is read from, so a change here
silently reports the wrong quarter rather than failing.
"""
import pytest

from earningsCalls import _normalize_year, _rank_periods, parse_period_label

# --------------------------------------------------------------------------
# §3.5 parse_period_label - ECT (14)
# --------------------------------------------------------------------------

PERIOD_CASES = [
    ("P1", "FY25", (2025, 5)),
    ("P2", "2025", (2025, 5)),
    ("P3", "2100", None),                # _BARE_YEAR_RE is bounded to (19|20)xx
    ("P4", "4Q25", (2025, 4)),
    ("P5", "0Q25", None),                # quarter lower bound
    ("P6", "5Q25", None),                # quarter upper bound
    ("P7", "1H25", (2025, 2)),
    ("P8", "9M25", (2025, 3.0)),         # months/3, so a float rank
    # 12M is a CUMULATIVE year-to-date label, but 12/3 lands it on rank 4 -
    # the same rank a genuine single Q4 gets. See R3 below for the damage.
    ("P9", "12M25", (2025, 4.0)),
    # PINNED BUG: nothing bounds the month count, so a nonsense 15M label
    # parses as if it were a full year.
    ("P10", "15M25", (2025, 5.0)),
    ("P11", "Dec-25", (2025, 4)),
    ("P12", "2025.12", (2025, 4)),
    ("P13", "2Q25¹", (2025, 2)),    # superscript footnote stripped
    ("P14", "Jun 25<sup>1</sup>", (2025, 2)),
]


@pytest.mark.parametrize("cell,expected", [c[1:] for c in PERIOD_CASES],
                         ids=[c[0] for c in PERIOD_CASES])
def test_parse_period_label(cell, expected):
    assert parse_period_label(cell) == expected


def test_non_period_headers_are_rejected():
    # These sit in real deck headers next to genuine period columns. Misreading
    # one as a period would make a change-percent column look like a value.
    assert parse_period_label("FY25/FY24 % Chg") is None
    assert parse_period_label("企業放款占比") is None


# --------------------------------------------------------------------------
# §3.6 _normalize_year - BVT (5). The boundary is 100.
# --------------------------------------------------------------------------

YEAR_CASES = [
    ("Y1", "25", 2025),
    ("Y2", "99", 2099),
    # FIXED (was PINNED BUG #12): a 3-digit year in these documents is a ROC
    # (民國) year. Left unconverted, 114 sorted below every 19xx/20xx label, so
    # a ROC-dated column could never win as "most recent".
    ("Y3", "100", 2011),     # 民國100 = 2011
    ("Y4", "114", 2025),     # 民國114 = 2025
    ("Y5", "2025", 2025),
    ("Y6", "1999", 1999),    # 4-digit years pass through untouched
]


@pytest.mark.parametrize("raw,expected", [c[1:] for c in YEAR_CASES],
                         ids=[c[0] for c in YEAR_CASES])
def test_normalize_year(raw, expected):
    assert _normalize_year(raw) == expected


# --------------------------------------------------------------------------
# §3.7 _rank_periods - Decision Table (6)
# --------------------------------------------------------------------------

def rank(names, prefer_quarterly):
    items = [(parse_period_label(n), n) for n in names]
    return [name for _key, name in _rank_periods(items, prefer_quarterly=prefer_quarterly)]


RANK_CASES = [
    ("R1", ["FY25", "4Q25"], False, ["FY25", "4Q25"]),
    ("R2", ["FY25", "4Q25"], True, ["4Q25", "FY25"]),
    # FIXED (was PINNED BUG #13): 12M25 is a CUMULATIVE full-year figure, but
    # P9 gives it rank 4.0, which prefer_quarterly used to read as a genuine
    # single quarter and pull ahead of FY25 - putting a cumulative number in
    # the 單季 column.
    ("R3", ["FY25", "12M25"], True, ["FY25", "12M25"]),
    ("R4", ["FY25", "9M25"], True, ["FY25", "9M25"]),   # rank 3 gets no boost
    ("R5", ["FY25", "FY24"], True, ["FY25", "FY24"]),
]


@pytest.mark.parametrize("names,prefer_quarterly,expected", [c[1:] for c in RANK_CASES],
                         ids=[c[0] for c in RANK_CASES])
def test_rank_periods(names, prefer_quarterly, expected):
    assert rank(names, prefer_quarterly) == expected


def test_quarter_ranks_are_ints_and_cumulative_ranks_are_floats():
    """The invariant _rank_periods' fix depends on. A genuine single-quarter or
    month label produces an INTEGER rank; an N-month cumulative label produces
    months/3, a float - and 12M lands on exactly 4.0, colliding with Q4's 4.
    The type is the only thing separating them, so pin it: making
    parse_period_label return 4.0 for 4Q25 would silently restore #13."""
    assert isinstance(parse_period_label("4Q25")[1], int)
    assert isinstance(parse_period_label("Dec-25")[1], int)
    assert isinstance(parse_period_label("12M25")[1], float)
    assert isinstance(parse_period_label("9M25")[1], float)


def test_R6_empty_input_raises():
    # Precondition, not a bug: max() over an empty sequence. Every caller
    # guards, but the guard is theirs, not this function's.
    with pytest.raises(ValueError):
        _rank_periods([], prefer_quarterly=True)

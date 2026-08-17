"""L0 characterization tests - TEST_DESIGN §3.4 and §3.8 (50 cases).

Small helpers, but every one of them sits on the path between a parsed cell
and what the user reads, so a sign flip or a formatting change here is a wrong
number rather than a crash.
"""
import pytest

import financialReports as fin
from regulatorDatasets import disclosures
from userInteractions import cli
from earningsCalls.decks import _add, _sub
from core.numbers import annualize, format_maybe_pct, format_pct, format_value
from core.tables import _is_table_divider, _split_row
from core.text import despace_cjk, page_num

# --------------------------------------------------------------------------
# §3.4 apply_cost_sign - Decision Table (6)
# --------------------------------------------------------------------------

COST_SIGN_CASES = [
    ("S1", None, "員工福利費用", True, None),
    ("S2", -800, "員工福利費用", True, 800),
    # A document that already prints '減：' stores the value cost-positive, so
    # flipping again would turn an expense into an apparent benefit.
    ("S3", 5678, "減：所得稅費用", True, 5678),
    # FIXED (was PINNED BUG #7): the check was a bare `"減" in label` rather
    # than a '減：' prefix, so 減損 (impairment) - an ordinary cost that merely
    # contains the character - was read as already-deducted and left negative,
    # displaying as if it were a reversal.
    ("S4", -800, "折舊及攤銷－減損", True, 800),
    ("S5", -800, None, True, 800),
    ("S6", 1000, "員工福利費用", False, 1000),
]


@pytest.mark.parametrize("value,label,is_cost,expected", [c[1:] for c in COST_SIGN_CASES],
                         ids=[c[0] for c in COST_SIGN_CASES])
def test_apply_cost_sign(value, label, is_cost, expected):
    assert fin.apply_cost_sign(value, label, is_cost) == expected


# --------------------------------------------------------------------------
# §3.8 the rest of L0 (44)
# --------------------------------------------------------------------------

DESPACE_CASES = [
    ("gap between CJK is removed", "資 產 總 計", "資產總計"),
    ("space between ASCII is kept", "a b c", "a b c"),
    ("mixed CJK/ASCII keeps both spaces", "資產 total 計", "資產 total 計"),
    ("empty", "", ""),
]


@pytest.mark.parametrize("text,expected", [c[1:] for c in DESPACE_CASES],
                         ids=[c[0] for c in DESPACE_CASES])
def test_despace_cjk(text, expected):
    assert despace_cjk(text) == expected


DIVIDER_CASES = [
    ("pipe divider", "|---|---|", True),
    # The distinction that matters - see T3 in test_l1_tables.py.
    ("bare horizontal rule", "---", False),
    ("empty cells count as a divider", "| |", True),
    ("no pipe at all", "no pipes", False),
    ("alignment colons", "|:-:|---:|", True),
]


@pytest.mark.parametrize("line,expected", [c[1:] for c in DIVIDER_CASES],
                         ids=[c[0] for c in DIVIDER_CASES])
def test_is_table_divider(line, expected):
    assert _is_table_divider(line) is expected


SPLIT_ROW_CASES = [
    ("leading and trailing pipes stripped", "| a | b |", ["a", "b"]),
    ("empty cell preserved as a position", "| a |  | c |", ["a", "", "c"]),
    ("single column", "| a |", ["a"]),
    ("no pipes at all is one cell", "abc", ["abc"]),
]


@pytest.mark.parametrize("line,expected", [c[1:] for c in SPLIT_ROW_CASES],
                         ids=[c[0] for c in SPLIT_ROW_CASES])
def test_split_row(line, expected):
    assert _split_row(line) == expected


FORMAT_CASES = [
    ("value None", lambda: format_value(None), "N/A"),
    ("value negative is comma-grouped", lambda: format_value(-327473468), "-327,473,468"),
    ("value zero", lambda: format_value(0), "0"),
    ("pct None", lambda: format_pct(None), "N/A"),
    ("pct two decimals", lambda: format_pct(1.27), "1.27%"),
    ("pct zero still gets decimals", lambda: format_pct(0), "0.00%"),
    ("maybe_pct percent branch", lambda: format_maybe_pct(1.27, True), "1.27%"),
    ("maybe_pct value branch", lambda: format_maybe_pct(1.27, False), "1.27"),
]


@pytest.mark.parametrize("call,expected", [c[1:] for c in FORMAT_CASES],
                         ids=[c[0] for c in FORMAT_CASES])
def test_formatters(call, expected):
    assert call() == expected


ANNUALIZE_CASES = [
    ("half year doubles", 1.0, 2, 2.0),
    ("None value propagates", None, 2, None),
    ("quarter 0 is falsy, not a division", 1.0, 0, None),
    ("full year is a no-op", 1.0, 4, 1.0),
]


@pytest.mark.parametrize("value,quarter,expected", [c[1:] for c in ANNUALIZE_CASES],
                         ids=[c[0] for c in ANNUALIZE_CASES])
def test_annualize(value, quarter, expected):
    assert annualize(value, quarter) == expected


ADD_SUB_CASES = [
    ("add sums", lambda: _add(1, 2, 3), 6),
    ("add propagates None", lambda: _add(1, None), None),
    # Boundary worth pinning: sum(()) is 0, so the no-argument call returns a
    # number rather than the None the None-propagation rule would suggest.
    ("add with no arguments is 0, not None", lambda: _add(), 0),
    ("sub subtracts", lambda: _sub(3, 1), 2),
    ("sub propagates None", lambda: _sub(None, 1), None),
]


@pytest.mark.parametrize("call,expected", [c[1:] for c in ADD_SUB_CASES],
                         ids=[c[0] for c in ADD_SUB_CASES])
def test_add_sub(call, expected):
    assert call() == expected


PAGE_NUM_CASES = [
    ("leading digits win", "013_xinp7x.md", "013"),
    ("no digits falls back to the stem", "cover.md", "cover"),
    ("None", None, ""),
    ("empty string", "", ""),
]


@pytest.mark.parametrize("source_file,expected", [c[1:] for c in PAGE_NUM_CASES],
                         ids=[c[0] for c in PAGE_NUM_CASES])
def test_page_num(source_file, expected):
    assert page_num(source_file) == expected


def test_sheet_name_uses_the_folder_basename():
    assert cli.sheet_name("C:/reports/中信 4Q25", set()) == "中信 4Q25"


def test_sheet_name_truncates_at_31_chars():
    assert cli.sheet_name("C:/reports/" + "A" * 40, set()) == "A" * 31


def test_sheet_name_replaces_characters_excel_rejects():
    assert cli.sheet_name("C:/reports/a:b?c*d[e]", set()) == "a_b_c_d_e_"


def test_sheet_name_deduplicates_against_taken():
    taken = set()
    assert cli.sheet_name("C:/reports/deck", taken) == "deck"
    assert cli.sheet_name("C:/reports/deck", taken) == "deck_2"
    assert cli.sheet_name("C:/reports/deck", taken) == "deck_3"


GOV_UNIT_CASES = [
    ("ROC year", lambda: disclosures.roc_year(2025), 114),
    ("Q1 ends in March", lambda: disclosures.quarter_end_month(1), 3),
    ("Q4 ends in December", lambda: disclosures.quarter_end_month(4), 12),
    ("quarter 5 is not a quarter", lambda: disclosures.quarter_end_month(5), None),
    ("千元 to 十億元", lambda: disclosures.thousands_to_billions(17911768), 17.911768),
    ("thousands_to_billions is None-safe", lambda: disclosures.thousands_to_billions(None), None),
]


@pytest.mark.parametrize("call,expected", [c[1:] for c in GOV_UNIT_CASES],
                         ids=[c[0] for c in GOV_UNIT_CASES])
def test_gov_units(call, expected):
    assert call() == expected

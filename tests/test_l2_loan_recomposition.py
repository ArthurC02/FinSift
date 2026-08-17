"""L2 characterization tests - TEST_DESIGN §5.5, failure mode F3 (12 cases).

Every formula in LOAN_RECOMPOSITION is a lambda nested inside a dict literal.
AST scanners and import checkers cannot see into them: the previous refactor
moved _add/_sub away, left these lambdas without the import, and passed 92
behavioural checks, a line-multiset diff, an AST missing-import scan and a
--help smoke test before anyone noticed. The only thing that catches it is
actually CALLING each one, which is what this file does - all 9 lambdas across
4 banks, plus the empty-dict bank and the reconciliation check.
"""
import pytest

import callfinder
from callfinder import LOAN_RECOMPOSITION, TermSpec

# 十億元, roughly the magnitudes real decks print.
RAW = {
    "企業放款": 1000.0, "政府放款": 172.2, "信貸": 154.0, "其他個人授信其他": 1209.0,
    "法說會外幣放款": 400.0, "海外子行": 837.0, "其他放款": 316.0, "信用卡循環": 17.9,
    "法說會放款餘額合計": 4246.0, "海外分行": 282.0, "OBU_DBU": 400.0,
    "房貸": 900.0, "個人擔保貸款": 100.0, "小額信貸": 299.1,
}


def formula(bank, term):
    return LOAN_RECOMPOSITION[bank][term][0]


FORMULA_CASES = [
    # 北富銀: 企業授信 excludes government lending; 個人放款 as printed bundles
    # 房貸/信用卡循環, so it is rebuilt from the pieces that are left.
    ("北富銀-企業放款", "北富銀", "企業放款", 1172.2),
    ("北富銀-個人放款", "北富銀", "個人放款", 1363.0),
    # 中信: group-wide figures, so overseas subsidiaries come back out.
    ("中信-企業放款", "中信", "企業放款", 563.0),
    ("中信-個人放款", "中信", "個人放款", 298.1),
    ("中信-放款餘額合計", "中信", "法說會放款餘額合計", 3409.0),
    ("中信-外幣放款", "中信", "法說會外幣放款", 682.0),
    # 玉山: 房貸 as printed covers only 房屋貸款.
    ("玉山-房貸", "玉山", "房貸", 1000.0),
    ("玉山-個人放款", "玉山", "個人放款", 299.1),
    ("玉山-放款餘額合計", "玉山", "法說會放款餘額合計", 2317.0),
]


@pytest.mark.parametrize("bank,term,expected", [c[1:] for c in FORMULA_CASES],
                         ids=[c[0] for c in FORMULA_CASES])
def test_loan_formula(bank, term, expected):
    assert formula(bank, term)(RAW) == pytest.approx(expected)


def test_formula_descriptions_are_present():
    # The second tuple element becomes the row's matched_label ("重組：..."),
    # which is the only thing making a recomposed number auditable.
    for bank, formulas in LOAN_RECOMPOSITION.items():
        for term, (_fn, description) in formulas.items():
            assert description, f"{bank}/{term} has no description"


def test_none_propagates_through_a_formula():
    # _add/_sub return None if any input is missing, so an unmatched component
    # surfaces as N/A instead of a partial total quietly computed from whatever
    # did match.
    raw = dict(RAW, 政府放款=None)
    assert formula("北富銀", "企業放款")(raw) is None


def test_cathay_has_no_recomposition():
    # 國泰's deck already publishes the four buckets disjoint. The empty dict is
    # deliberate, not an oversight - the loop below must leave values untouched.
    assert LOAN_RECOMPOSITION["國泰"] == {}


# --------------------------------------------------------------------------
# Reconciliation check (BVT on _LOAN_RECONCILE_TOLERANCE, which is `>`, not `>=`)
# --------------------------------------------------------------------------

ALL_TERMS = callfinder.RATIO_TERMS + callfinder.BALANCE_TERMS + callfinder.HELPER_TERMS


def run_summary(monkeypatch, tmp_path, values, bank="國泰"):
    """Drive collect_con_call_summary with canned matches instead of a deck.

    _GOV_BANK_NAMES is emptied so the regulator lookup can never reach the
    network - see TEST_DESIGN §6.4.
    """
    monkeypatch.setattr(callfinder, "_GOV_BANK_NAMES", {})
    monkeypatch.setattr(callfinder, "detect_con_call_quarter", lambda folder: 4)
    monkeypatch.setattr(callfinder, "detect_con_call_year", lambda folder: 2025)
    monkeypatch.setattr(
        callfinder, "find_term_value",
        lambda folder, spec, **kw: (
            None if values.get(spec.name) is None
            else (spec.name, values[spec.name], "007_x.md", "4Q25", False, 1.0)),
    )
    terms = {name: TermSpec(name=name) for name in ALL_TERMS}
    rows = callfinder.collect_con_call_summary(tmp_path, terms, bank=bank)
    return {r["term"]: r for r in rows}


BALANCED = {"企業放款": 942.6, "房貸": 1404.9, "個人放款": 477.7, "信用卡循環": 22.2}


def test_esun_formulas_read_raw_values_not_each_others_output(monkeypatch, tmp_path):
    """The 玉山 formulas, driven through the real recomposition loop.

    Calling each lambda directly with a fresh RAW dict cannot see whether the
    loop feeds results back in - and 玉山 is where that matters: its total
    recomputes 房貸 + 個人擔保貸款 from raw, while 房貸 is itself recomposed
    earlier in the same dict. Chaining would double-count the collateralised
    slice and give 2417.0 instead of 2317.0, with every direct-call test still
    green.
    """
    by_term = run_summary(monkeypatch, tmp_path, dict(RAW), bank="玉山")
    assert by_term["房貸"]["value"] == pytest.approx(1000.0)
    assert by_term["個人放款"]["value"] == pytest.approx(299.1)
    assert by_term["法說會放款餘額合計"]["value"] == pytest.approx(2317.0)
    assert by_term["房貸"]["matched_label"] == "重組：房屋貸款 + 個人擔保貸款"
    # 企業放款 isn't in 玉山's dict, so it keeps the deck's own label.
    assert by_term["企業放款"]["matched_label"] == "企業放款"
    # And the four buckets tie to the recomposed total exactly.
    assert by_term["法說會放款餘額合計"]["note"] == ""


def test_reconciliation_note_fires_above_tolerance(monkeypatch, tmp_path):
    # components sum to 2847.4; total 2844.4 -> off by 3.0
    by_term = run_summary(monkeypatch, tmp_path, dict(BALANCED, 法說會放款餘額合計=2844.4))
    assert "off by 3.0" in by_term["法說會放款餘額合計"]["note"]
    # 國泰's empty recomposition dict must leave every value untouched.
    assert by_term["企業放款"]["value"] == 942.6
    assert by_term["企業放款"]["matched_label"] == "企業放款"  # not "重組：..."


def test_reconciliation_note_silent_exactly_at_tolerance(monkeypatch, tmp_path):
    # off by exactly 2.5: the check is `> tolerance`, so this must stay quiet.
    by_term = run_summary(monkeypatch, tmp_path, dict(BALANCED, 法說會放款餘額合計=2844.9))
    assert by_term["法說會放款餘額合計"]["note"] == ""

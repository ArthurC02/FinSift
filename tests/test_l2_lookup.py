"""L2 characterization tests - TEST_DESIGN §5.1 (14 rules).

find_value_in_table is the widest decision surface in the project: two
orientations, a heading fallback per orientation, a share/growth filter, a
percent-cell filter and a negative-term veto, all interacting. Every rule
below is one column of the decision table.
"""
import pytest

from earningsCalls.callfinder import TermSpec, detect_orientation, find_term_value, find_value_in_table


def table(header, rows):
    return {"header": header, "rows": rows, "line_idx": 0}


def spec(aliases=("企業放款",), negative=()):
    return TermSpec(name="t", aliases=list(aliases), negative_terms=list(negative))


# heading arrives from nearest_heading() as (raw_text, topic_text).
def heading(text):
    return (text, text)


ROW_PERIOD = table(["期別", "企業放款", "企業放款占比"],
                   [["4Q25", "100", "10%"], ["3Q25", "90", "9%"]])
COL_PERIOD = table(["項目", "4Q25", "3Q25"], [["企業放款", "100", "90"]])


def test_orientation_detection():
    assert detect_orientation(ROW_PERIOD) == ("row_period", 0)
    assert detect_orientation(COL_PERIOD) == ("col_period", None)
    assert detect_orientation(table(["A", "B"], [["x", "1"]])) is None


def test_R1_no_orientation_detected():
    # Neither axis looks like periods - skipped rather than guessed at.
    assert find_value_in_table(table(["A", "B"], [["x", "1"]]), spec()) is None


def test_R2_negative_term_in_the_heading_vetoes_the_whole_table():
    # The heading is the label's context: a column plainly headed 存放比 under
    # an FX-only heading is not the overall ratio the term means.
    assert find_value_in_table(ROW_PERIOD, spec(negative=["外幣"]),
                               heading=heading("外幣放款明細")) is None


def test_R3_row_period_column_label_hit():
    assert find_value_in_table(ROW_PERIOD, spec()) == (100, "企業放款", "4Q25", 3, None, False)


def test_R4_row_period_share_column_is_filtered_before_the_candidate_check():
    """The asymmetry §5.1 flags. row_period applies require_absolute to
    `candidates` BEFORE testing `if candidates:`, so filtering everything out
    drops through to the heading fallback - which can then still return a
    value. See R9 for col_period, which behaves differently on purpose."""
    only_share = table(["期別", "企業放款占比"], [["4Q25", "10%"], ["3Q25", "9%"]])
    assert find_value_in_table(only_share, spec(), require_absolute=True) is None

    # Same filtering, but now a non-share value column and a matching heading
    # exist - the fallback fires and the value comes back.
    with_fallback = table(["期別", "企業放款占比", "餘額"], [["4Q25", "10%", "100"]])
    assert find_value_in_table(with_fallback, spec(), require_absolute=True,
                               heading=heading("企業放款")) == (100, "企業放款", "4Q25", 3, None, False)


def test_R5_row_period_heading_fallback_with_exactly_one_value_column():
    t = table(["期別", "餘額"], [["4Q25", "100"], ["3Q25", "90"]])
    value, label, period, _s, _e, _p = find_value_in_table(t, spec(), heading=heading("企業放款"))
    assert (value, label, period) == (100, "企業放款", "4Q25")


def test_R6_row_period_heading_fallback_refuses_two_value_columns():
    # 富邦's 企業授信餘額（依幣別） splits 台幣/外幣. Guessing one would report
    # half the figure, so ambiguity means no match.
    t = table(["期別", "台幣授信", "外幣授信"], [["4Q25", "60", "40"]])
    assert find_value_in_table(t, spec(), heading=heading("企業放款")) is None


def test_R7_row_period_heading_does_not_match_the_term():
    t = table(["期別", "餘額"], [["4Q25", "100"]])
    assert find_value_in_table(t, spec(), heading=heading("其他")) is None


def test_R8_col_period_row_label_hit():
    assert find_value_in_table(COL_PERIOD, spec()) == (100, "企業放款", "4Q25", 3, None, False)


def test_R9_col_period_keeps_share_rows_when_nothing_absolute_remains():
    """The other half of the R4 asymmetry. col_period only REPLACES candidates
    when the absolute-only list is non-empty, so an all-share candidate set
    survives, wins the tie-break, and then loses every period to the
    percent-cell filter - returning None without ever trying the heading
    fallback that R4's branch would have reached."""
    t = table(["項目", "4Q25"], [["企業放款占比", "10%"], ["餘額", "100"]])
    assert find_value_in_table(t, spec(), require_absolute=True,
                               heading=heading("企業放款")) is None

    # Drop the share row and the heading fallback works on the same data,
    # which is what makes the asymmetry observable rather than theoretical.
    t2 = table(["項目", "4Q25"], [["餘額", "100"]])
    assert find_value_in_table(t2, spec(), require_absolute=True,
                               heading=heading("企業放款")) == (100, "企業放款", "4Q25", 3, None, False)


def test_R10_col_period_heading_fallback_with_exactly_one_row():
    t = table(["項目", "4Q25"], [["餘額", "100"]])
    assert find_value_in_table(t, spec(), heading=heading("企業放款")) == (
        100, "企業放款", "4Q25", 3, None, False)


def test_R11_col_period_heading_fallback_refuses_two_rows():
    t = table(["項目", "4Q25"], [["台幣授信", "60"], ["外幣授信", "40"]])
    assert find_value_in_table(t, spec(), heading=heading("企業放款")) is None


def test_R12_col_period_no_row_hit_and_no_heading():
    t = table(["項目", "4Q25"], [["餘額", "100"]])
    assert find_value_in_table(t, spec()) is None


def test_R13_matched_row_has_no_parseable_number_for_any_period():
    assert find_value_in_table(table(["項目", "4Q25"], [["企業放款", "N/A"]]), spec()) is None


def test_R14_percent_cell_is_skipped_only_under_require_absolute():
    """parse_numeric strips the '%', so 0.08% would become an ordinary-looking
    balance of 0.08. In a real 富邦 deck this let 房貸 match an NPL-ratio
    table's 房貸 column."""
    t = table(["項目", "4Q25"], [["企業放款", "10%"]])
    assert find_value_in_table(t, spec(), require_absolute=True) is None
    # Without require_absolute the same cell is accepted, flagged is_percent.
    assert find_value_in_table(t, spec()) == (10, "企業放款", "4Q25", 3, None, True)


@pytest.mark.parametrize("header0,expected_entity", [
    ("國泰世華銀行", "國泰世華銀行"),   # names a company
    ("項目", None),                     # generic axis label
])
def test_entity_is_taken_from_header0_only_when_it_names_a_company(header0, expected_entity):
    # The same figure can legitimately appear once per entity in a
    # multi-entity appendix table, so entity is what tells them apart.
    t = table([header0, "4Q25"], [["企業放款", "100"]])
    assert find_value_in_table(t, spec())[4] == expected_entity


def test_the_two_lookup_tuples_are_deliberately_in_opposite_orders(tmp_path):
    """find_term_value wraps find_value_in_table and swaps the first two
    elements: (value, label, ...) becomes (label, value, ...).

    Both are 6-tuples and both are consumed positionally. Nothing else pins
    find_term_value's real order - test_l2_loan_recomposition.py only ever
    STUBS it, and that stub hardcodes label-first. So if the real function
    were ever tidied up to match the callee it wraps, every stubbed test
    would stay green while production silently swapped label and value.
    """
    (tmp_path / "001.md").write_text(
        "| 項目 | 4Q25 |\n|---|---|\n| 企業放款 | 100 |\n", encoding="utf-8")
    inner = find_value_in_table(COL_PERIOD, spec())
    outer = find_term_value(tmp_path, spec())
    assert inner[0] == 100 and inner[1] == "企業放款"        # value, label
    assert outer[0] == "企業放款" and outer[1] == 100        # label, value
    assert outer == ("企業放款", 100, "001.md", "4Q25", False, 1.0)

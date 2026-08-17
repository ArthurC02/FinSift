"""L1 characterization tests - TEST_DESIGN §4.6, failure mode F6 (4 cases).

fin and decks each define print_summary_rows and write_summary_csv.
Same names, different row schemas, different CSV headers, different output
filenames. A refactor that merges them by name alone breaks one caller while
every other test stays green.

What is pinned here is each entry point's OUTPUT CONTRACT, not the existence of
two separate function bodies: a shared dispatcher that handles both schemas
correctly is a legitimate refactor and must not be blocked.
"""
import csv

import financialReports as fin
from earningsCalls import decks

FIN_ROW = {
    "term": "資產總計", "value": 1234567, "is_percent": False,
    "matched_label": "資產總計", "source_file": "013_xinp7x.md",
    "crosscheck_value": 2.5, "note": "diverges from manual",
}

CALL_RATIO_ROW = {
    "term": "ROA", "kind": "ratio", "individual": 0.75,
    "matched_label": "ROA(稅後)", "period_label": "2Q25",
    "source_file": "007_abc.md", "note": "loan book off by 3.1",
}

CALL_VALUE_ROW = {
    "term": "淨收益", "kind": "value", "value": 98765, "is_percent": False,
    "matched_label": "淨收益合計", "period_label": "2Q25",
    "source_file": "007_abc.md",
}


def test_F6_acctfinder_print_summary_rows(capsys):
    fin.print_summary_rows([FIN_ROW])
    out = capsys.readouterr().out
    assert "=== summary ===" in out
    assert "資產總計\t1,234,567\t資產總計\t(013)" in out
    assert "cross-check: 2.50%" in out      # shown only because a note is set too
    assert "  NOTE: diverges from manual" in out

    fin.print_summary_rows([])
    assert "No matching codes found in any file." in capsys.readouterr().out


def test_F6_callfinder_print_summary_rows(capsys):
    # Different schema: kind/individual/period_label, and no "=== summary ===".
    decks.print_summary_rows([CALL_RATIO_ROW, CALL_VALUE_ROW])
    out = capsys.readouterr().out
    assert "ROA\t0.75%\tROA(稅後) @ 2Q25 (007)" in out
    assert "淨收益\t98,765\t淨收益合計 @ 2Q25 (007)" in out
    assert "  NOTE: loan book off by 3.1" in out


def test_F6_acctfinder_write_summary_csv(tmp_path):
    out_path = fin.write_summary_csv(tmp_path, [FIN_ROW])
    assert out_path.name == "summary_export.csv"
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["term", "value", "term_found", "page", "crosscheck_value", "note"]
    assert rows[1] == ["資產總計", "1,234,567", "資產總計", "013", "2.50%", "diverges from manual"]


def test_F6_callfinder_write_summary_csv(tmp_path):
    out_path = decks.write_summary_csv(tmp_path, [CALL_RATIO_ROW, CALL_VALUE_ROW])
    assert out_path.name == "con_call_summary_export.csv"
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["term", "value", "label_in_doc", "period", "page", "note"]
    assert rows[1] == ["ROA", "0.75%", "ROA(稅後)", "2Q25", "007", "loan book off by 3.1"]
    assert rows[2] == ["淨收益", "98,765", "淨收益合計", "2Q25", "007", ""]


# --------------------------------------------------------------------------
# TEST_DESIGN §6.1 E4 - the combined CSV's fallback ratio row
# --------------------------------------------------------------------------

FALLBACK_RATIO_ROW = {
    "period": None, "entity": "(approximated)", "quarter": 4,
    "roa_posttax": None, "roa_posttax_annualized": 0.0082,
    "roe_posttax": None, "roe_posttax_annualized": 0.0975,
    "profit_margin": None, "source_file": None,
}


def test_E4_combined_csv_fallback_row_keeps_both_ROA_and_ROE(tmp_path):
    """FIXED (was PINNED BUG #10). The fallback branch wrote one value plus
    four blanks: ROE was lost entirely, and ROA landed in the roa_posttax
    column rather than the annualized one. Both print_ratio_rows and
    write_ratio_csv have always emitted both figures - only this exporter
    disagreed."""
    out_path = fin.write_combined_csv(tmp_path, {}, [FALLBACK_RATIO_ROW], used_fallback=True)
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        header, row = list(csv.reader(f))
    values = dict(zip(header, row))
    assert values["roa_posttax_annualized"] == "0.82%"
    assert values["roe_posttax_annualized"] == "9.75%"
    assert values["roa_posttax"] == "" and values["roe_posttax"] == ""
    assert values["section"] == "ratios"


def test_E4b_non_fallback_rows_still_use_format_pct(tmp_path):
    # The disclosed figures are already percent-scale, so they must NOT go
    # through the fallback's :.2% conversion.
    disclosed = dict(FALLBACK_RATIO_ROW, roa_posttax=0.82, roa_posttax_annualized=0.82,
                     roe_posttax=9.75, roe_posttax_annualized=9.75, profit_margin=30.0,
                     period="114年12月31日", entity=None)
    out_path = fin.write_combined_csv(tmp_path, {}, [disclosed], used_fallback=False)
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        header, row = list(csv.reader(f))
    values = dict(zip(header, row))
    assert values["roa_posttax"] == "0.82%" and values["roe_posttax_annualized"] == "9.75%"

# --------------------------------------------------------------------------
# summary_coverage_warning - turning silent degradation into an audible one
# --------------------------------------------------------------------------

def _rows(found, missing):
    """`found` values + `missing` N/A rows, in summary_coverage_warning's shape."""
    return ([{"term": f"ok{i}", "value": 100} for i in range(found)]
            + [{"term": f"na{i}", "value": None} for i in range(missing)])


def test_C1_full_coverage_says_nothing():
    assert fin.summary_coverage_warning(_rows(18, 0)) is None


def test_C2_a_few_na_rows_are_ordinary():
    # 活存比 is N/A for every bank checked so far, and a filing that genuinely
    # doesn't disclose a line is a real result - neither may trip this.
    assert fin.summary_coverage_warning(_rows(16, 2)) is None


def test_C3_exactly_half_na_is_still_below_the_line():
    # BVT: the threshold fires on MORE than half, not on half.
    assert fin.summary_coverage_warning(_rows(9, 9)) is None
    assert fin.summary_coverage_warning(_rows(8, 10)) is not None


def test_C4_a_wholesale_failed_read_names_what_is_missing():
    warning = fin.summary_coverage_warning(_rows(2, 16), folder="fin_q4")
    assert "16 of 18" in warning
    assert "fin_q4" in warning
    assert "na0" in warning and "na15" in warning


def test_C5_no_rows_is_not_a_coverage_problem():
    # An empty result already prints "No matching codes found" on its own.
    assert fin.summary_coverage_warning([]) is None

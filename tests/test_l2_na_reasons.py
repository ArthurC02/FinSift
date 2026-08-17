"""L2 characterization tests - every N/A summary row says why it's N/A.

The reason used to exist only as a -v console line, so the csv/excel exports
- the artifacts anyone actually reads - couldn't tell "this filing doesn't
disclose the line" apart from "we failed to read this filing". These pin the
reason onto the row's own `note`, and pin that it survives into the export.
"""
import csv

import financialReports as fin

# Real entity name so detect_industry_category resolves, and nothing else:
# every code lookup then legitimately comes up empty.
EMPTY_FILING = "# 玉山商業銀行股份有限公司\n"


def summary(tmp_path, bank="玉山"):
    (tmp_path / "001.md").write_text(EMPTY_FILING, encoding="utf-8")
    rows = fin.collect_summary_rows(tmp_path, bank, industry="金融業")
    return rows, {r["term"]: r for r in rows}


def test_every_na_row_carries_a_reason(tmp_path):
    rows, _ = summary(tmp_path)
    unexplained = [r["term"] for r in rows if r["value"] is None and not r["note"]]
    assert unexplained == []


def test_each_kind_of_na_names_its_own_cause(tmp_path):
    _, by_term = summary(tmp_path)
    # "code" kind - the account code isn't anywhere in the folder
    assert by_term["總資產"]["note"] == "code 10000 (總資產) not found in any file"
    # "label" kind - text matching, no code involved
    assert "活期性存款比率" in by_term["活存比"]["note"]
    # composite - names the first component that's missing, not just the term
    assert "component code" in by_term["評價及已實現"]["note"]
    # the two derived rows, which have no layout entry of their own. CIR
    # points at the ROWS it needs, not the codes - it reads the built rows,
    # so naming codes here would send the reader to the wrong place.
    assert by_term["CIR"]["note"] == "CIR needs 淨收益 and 營業費用, which came back N/A above"
    assert "獲利能力" in by_term["ROA"]["note"]


def test_a_bank_with_no_composite_formula_says_so(tmp_path):
    """Distinct from a missing component: nothing was even looked up."""
    _, by_term = summary(tmp_path, bank="不存在銀行")
    assert by_term["評價及已實現"]["note"] == (
        "'評價及已實現' has no formula defined for bank '不存在銀行'")


def test_the_reason_reaches_the_exported_csv(tmp_path):
    """The whole point of putting it on the row rather than only on stdout."""
    rows, _ = summary(tmp_path)
    out = fin.write_summary_csv(tmp_path, rows)
    with open(out, encoding="utf-8-sig") as f:
        exported = {r["term"]: r["note"] for r in csv.DictReader(f)}
    assert exported["總資產"] == "code 10000 (總資產) not found in any file"


# --------------------------------------------------------------------------
# The other way a row avoids being N/A: SUMMARY_CODE_DERIVATIONS
# --------------------------------------------------------------------------

# 兆豐 and 第一銀行's real 114Q4 filings in this shape: 營業費用 is a section
# HEADER with no amounts, the three components follow, then 稅前淨利 - no
# 58400 row exists to match by code or by label.
NO_OPEX_TOTAL = EMPTY_FILING + """
| 代碼 | 項目 | 114年度金額 |
|---|---|---:|
| 4xxxx | 淨收益 | 1000 |
|  | 營業費用 |  |
| 58500 | 員工福利費用 | -300 |
| 59000 | 折舊及攤銷費用 | -50 |
| 59500 | 其他業務及管理費用 | -100 |
"""


def opex_row(tmp_path, text):
    (tmp_path / "001.md").write_text(text, encoding="utf-8")
    rows = fin.collect_summary_rows(tmp_path, "玉山", industry="金融業")
    return {r["term"]: r for r in rows}


def test_a_missing_opex_total_is_rebuilt_from_its_components(tmp_path):
    by_term = opex_row(tmp_path, NO_OPEX_TOTAL)
    # is_cost=True, so the negative components come back cost-positive
    assert by_term["營業費用"]["value"] == 450
    assert by_term["營業費用"]["note"].startswith("derived:")


def test_the_derived_total_feeds_CIR(tmp_path):
    """The point of deriving it. CIR reads the built rows, not the raw code
    index, so it must not disagree with the 營業費用 line printed above it."""
    by_term = opex_row(tmp_path, NO_OPEX_TOTAL)
    assert by_term["CIR"]["value"] == 45.0


def test_a_stated_total_wins_over_the_derivation(tmp_path):
    """Derivation is a last resort, not a recomputation - if the filing says
    the total, that is the number, even where it disagrees with the sum."""
    by_term = opex_row(tmp_path, NO_OPEX_TOTAL + "| 58400 | 營業費用合計 | -460 |\n")
    assert by_term["營業費用"]["value"] == 460
    assert by_term["營業費用"]["note"] == ""


def test_an_incomplete_component_set_is_still_N_A(tmp_path):
    """Half a total is worse than none - same rule composites already follow."""
    partial = NO_OPEX_TOTAL.replace("| 59000 | 折舊及攤銷費用 | -50 |\n", "")
    by_term = opex_row(tmp_path, partial)
    assert by_term["營業費用"]["value"] is None
    assert "not found in any file" in by_term["營業費用"]["note"]
    assert by_term["CIR"]["value"] is None
    assert "營業費用" in by_term["CIR"]["note"]


def test_淨收益合計_resolves_the_same_row_as_淨收益(tmp_path):
    """第一銀行 words the line with a 合計 suffix; the match is whole-cell
    exact, so the alias has to be listed rather than inferred."""
    by_term = opex_row(tmp_path, NO_OPEX_TOTAL.replace("| 4xxxx | 淨收益 | 1000 |",
                                                        "|  | 淨收益合計 | 1000 |"))
    assert by_term["淨收益"]["value"] == 1000


def test_a_resolved_row_still_has_an_empty_note(tmp_path):
    """The reason only appears on rows that are actually N/A - otherwise the
    note column would be noise on every line and get ignored."""
    (tmp_path / "001.md").write_text(
        EMPTY_FILING + "| 代碼 | 科目 | 金額 |\n|---|---|---|\n| 10000 | 資產總計 | 100 |\n",
        encoding="utf-8")
    by_term = {r["term"]: r for r in fin.collect_summary_rows(tmp_path, "玉山", industry="金融業")}
    assert by_term["總資產"]["value"] == 100
    assert by_term["總資產"]["note"] == ""

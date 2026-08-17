"""L1 characterization tests - TEST_DESIGN §4.4 and §4.5 (13 cases).

restrict_section decides which slice of a file a statement's rows are read
from; the coding-block parser decides what counts as a known account code.
Both are upstream of everything else, and both fail by returning a plausible
wrong answer rather than by raising.
"""
import pytest

import acctfinder as af
from acctfinder import _extract_coding_block, _find_coding_blocks, restrict_section


def md(*lines):
    return [(None, line) for line in lines]


# --------------------------------------------------------------------------
# §4.4 restrict_section (5)
# --------------------------------------------------------------------------

BS = ["資產負債表"]
IS = ["綜合損益表"]


def test_missing_start_marker_returns_None_not_empty():
    # None and [] mean different things to callers: None -> skip this file
    # entirely, [] -> the section exists but is empty. Collapsing them would
    # make every file get scanned under the wrong statement's markers.
    assert restrict_section(md("封面", "其他"), BS, IS) is None


def test_start_without_end_runs_to_end_of_file():
    lines = md("封面", "資產負債表", "| 10000 | 資產 |", "| 20000 | 負債 |")
    assert [l for _, l in restrict_section(lines, BS, IS)] == [
        "資產負債表", "| 10000 | 資產 |", "| 20000 | 負債 |"]


def test_end_marker_truncates_before_itself():
    lines = md("資產負債表", "| 10000 | 資產 |", "綜合損益表", "| 40000 | 收益 |")
    assert [l for _, l in restrict_section(lines, BS, IS)] == ["資產負債表", "| 10000 | 資產 |"]


def test_table_of_contents_line_is_not_a_start_marker():
    # A TOC entry like "一、資產負債表 3" names the section but isn't it.
    # Starting there would scan the whole document.
    lines = md("一、資產負債表 3", "封面", "資產負債表", "| 10000 | 資產 |")
    assert [l for _, l in restrict_section(lines, BS, IS)][0] == "資產負債表"


def test_empty_end_markers_run_to_end_of_file():
    lines = md("資產負債表", "| 10000 | 資產 |", "綜合損益表")
    assert len(restrict_section(lines, BS, [])) == 3


# --------------------------------------------------------------------------
# §4.5 coding-dictionary blocks (8)
#
# grid = the 2D value matrix _unmerge_fill produces from a worksheet.
# --------------------------------------------------------------------------

def test_K1_original_and_revised_blocks_are_adjacent_spans():
    grid = [["原會計項目及代碼", None, "修正後會計項目及代碼", None]]
    orig, rev, data_start = _find_coding_blocks(grid)
    assert orig == (0, 1)          # ends right before the revised block starts
    assert rev == (2, 3)
    assert data_start == 1


def test_K2_revised_only_leaves_no_original_block():
    grid = [["修正後會計項目及代碼", None]]
    orig, rev, _ = _find_coding_blocks(grid)
    assert orig is None and rev == (0, 1)


def test_K3_explanatory_column_truncates_the_span():
    grid = [["修正後會計項目及代碼", None, "修正說明", None]]
    orig, rev, _ = _find_coding_blocks(grid)
    assert orig is None and rev == (0, 1)   # stops before 修正說明's column


def test_K4_a_real_data_row_is_not_mistaken_for_a_header():
    # Historical regression: data rows inside the 10-row scan window used to
    # push data_start_row past themselves, silently eating real codes.
    grid = [["修正後會計項目及代碼", None],
            ["10000", "資產"],
            ["20000", "負債"]]
    _orig, rev, data_start = _find_coding_blocks(grid)
    assert data_start == 1
    assert _extract_coding_block(grid, rev, data_start) == {"10000": "資產", "20000": "負債"}


def test_K5_name_column_may_shift_between_rows():
    # At least one real sheet puts the name in a different column per row, so
    # the longest non-code text in the span wins rather than a fixed index.
    grid = [["修正後會計項目及代碼", None, None],
            ["10000", "資產", None],
            ["20000", None, "負債及權益"]]
    _orig, rev, data_start = _find_coding_blocks(grid)
    assert _extract_coding_block(grid, rev, data_start) == {"10000": "資產", "20000": "負債及權益"}


def test_K6_row_with_a_code_but_no_name_is_dropped():
    grid = [["修正後會計項目及代碼", None],
            ["10000", None],
            ["20000", "負債"]]
    _orig, rev, data_start = _find_coding_blocks(grid)
    assert _extract_coding_block(grid, rev, data_start) == {"20000": "負債"}


def _workbook(tmp_path, rows, merges=()):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "資產負債表"
    for row in rows:
        ws.append(list(row))
    for m in merges:
        ws.merge_cells(m)
    path = tmp_path / "coding.xlsx"
    wb.save(path)
    return path


def test_K7_merged_cells_are_filled_into_every_member(tmp_path):
    # openpyxl returns None for all but a merge's top-left cell, which would
    # hide most of these sheets' header text.
    path = _workbook(tmp_path,
                     [["修正後會計項目及代碼", None], ["10000", "資產"]],
                     merges=["A1:B1"])
    assert af.load_code_dictionary(path, "balance_sheet") == {"10000": "資產"}


def test_K8_revised_wins_over_original_on_conflict(tmp_path):
    path = _workbook(tmp_path, [
        ["原會計項目及代碼", None, "修正後會計項目及代碼", None],
        ["10000", "舊名稱", "10000", "新名稱"],
    ])
    assert af.load_code_dictionary(path, "balance_sheet")["10000"] == "新名稱"
